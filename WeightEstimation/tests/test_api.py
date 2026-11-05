"""Phase 4–5: predict(), Monte Carlo, stall coupling, CG, closure, schema, CLI."""
import json

import numpy as np
import pytest
from conftest import ROOT

from hs1_weights import certification as cert
from hs1_weights.api import WeightReport, export_schemas, predict
from hs1_weights.cli import main as cli_main
from hs1_weights.closure import close_mc, close_point
from hs1_weights.config import HS1Config, load_config
from hs1_weights.errors import MissingInputError

EXAMPLE = ROOT / "examples" / "hs1_reference.json"


@pytest.fixture(scope="module")
def cfg() -> HS1Config:
    return load_config(str(EXAMPLE))


@pytest.fixture(scope="module")
def report(cfg) -> WeightReport:
    return predict(cfg, n_samples=2000, seed=0)


def _mod(cfg, **upd):
    return HS1Config.model_validate({**cfg.model_dump(), **upd})


def test_report_complete_and_json(report):
    d = json.loads(json.dumps(report.model_dump()))
    assert set(d) >= {"components", "empty_weight", "mtow", "cg", "envelope", "stall_area_min_m2", "analogs",
                      "rule_flags", "sobol", "assumptions", "unverified_criteria", "versions"}
    tree = {"wing", "htail", "vtail", "fuselage", "main_gear", "nose_gear", "nacelles_mounts", "motors", "inverters",
            "battery_pack", "battery_bays", "thermal_mgmt", "hv_harness", "propellers", "flight_controls",
            "electrical_lv", "avionics_installed", "furnishings", "ecs_anti_ice", "fixed_items"}
    assert set(report.components) == tree          # G1: doc 06 component tree fully populated
    assert len(report.unverified_criteria) == len(cert.unverified_criteria())


def test_percentiles_ordered_and_sum(report):
    for r in report.components.values():
        assert r.mass_kg.p10 <= r.mass_kg.p50 <= r.mass_kg.p90
    e = report.empty_weight
    assert e.p10 < e.p50 < e.p90
    assert report.mtow.p50 == pytest.approx(e.p50 + 172.0)


def test_deterministic_given_seed(cfg, report):
    again = predict(cfg, n_samples=2000, seed=0)
    assert again.empty_weight == report.empty_weight
    other = predict(cfg, n_samples=2000, seed=1, explain=False)
    assert other.empty_weight != report.empty_weight


def test_governing_load_cases_from_envelope(report):
    assert report.components["wing"].governing_load_case.startswith("n_ult+ = 6.00 (ASTM_F2245")
    assert "18.0 g forward (FAR23_L1)" in report.components["battery_bays"].governing_load_case
    assert report.envelope["dynamic_seat_required"] is True


def test_calibration_status_reported(report):
    assert report.calibration["engine"] == "prior"
    assert any("drawn from their priors" in a for a in report.assumptions)
    for comp in ("battery_pack", "battery_bays", "motors"):
        assert not report.components[comp].calibrated and report.components[comp].k is None
    assert report.components["wing"].calibrated and report.components["wing"].k is not None


def test_pack_hand_value_under_fixed_eta(cfg):
    from hs1_weights.config import Param
    pack = cfg.powertrain.pack.model_copy(update={"eta_c2p": Param(value=0.625)})
    c = _mod(cfg, powertrain={**cfg.powertrain.model_dump(), "pack": pack.model_dump()})
    r = predict(c, n_samples=2000, seed=0, explain=False)
    assert r.components["battery_pack"].mass_kg.p50 == pytest.approx(320.0)


def test_pack_level_declaration(cfg):
    pack = {**cfg.powertrain.pack.model_dump(), "level": "pack", "specific_energy_wh_per_kg": {"value": 200.0}}
    c = _mod(cfg, powertrain={**cfg.powertrain.model_dump(), "pack": pack})
    r = predict(c, n_samples=2000, seed=0, explain=False)
    assert r.components["battery_pack"].mass_kg.p50 == pytest.approx(300.0)
    with pytest.raises(ValueError, match="eta_c2p must be omitted"):
        _mod(cfg, powertrain={**cfg.powertrain.model_dump(), "pack": {**pack, "eta_c2p": 0.6}})


def test_stall_area_r9(report):
    s = report.stall_area_min_m2
    assert s["VS1_59kt_clean"]["p50"] == pytest.approx(cert.min_area_for_stall(report.mtow.p50, 59.0, 1.5), rel=1e-9)
    assert s["VS0_61kt_landing"]["p50"] == pytest.approx(cert.min_area_for_stall(report.mtow.p50, 61.0, 2.0),
                                                         rel=1e-9)


def test_blown_clmax_needs_unblown_and_warns(cfg):
    with pytest.raises(MissingInputError, match="cl_max_clean_unblown"):
        predict(_mod(cfg, cl_max_clean=2.4, cl_max_includes_blowing=True), 2000, explain=False)
    r = predict(_mod(cfg, cl_max_clean=2.4, cl_max_land=3.0, cl_max_includes_blowing=True, cl_max_clean_unblown=1.5,
                     cl_max_land_unblown=2.0), 2000, explain=False)
    assert r.stall_area_min_m2["cl_max_clean_unblown"] == 1.5
    assert any(f.id == "STALL-BLOWN" for f in r.rule_flags)


def test_astm_only_has_no_mount_factor(cfg):
    with pytest.raises(MissingInputError, match="n_mount"):
        predict(_mod(cfg, cert_bases=["ASTM_F2245"]), 2000, explain=False)


def test_missing_material_raises(cfg):
    mats = {k: v for k, v in cfg.material_by_component.items() if k != "fuselage"}
    with pytest.raises(MissingInputError, match="fuselage"):
        predict(_mod(cfg, material_by_component=mats), 2000, explain=False)


def test_vc_below_min_is_error_flag(cfg):
    r = predict(_mod(cfg, vc_kt=60.0, vh_kt=150.0), 2000, explain=False)
    c4 = next(f for f in r.rule_flags if f.id == "C4")
    assert c4.severity == "error"


def test_n_bays_sensitivity_increasing(report):
    s = report.n_bays_sensitivity["by_n_bays"]
    w = [s[k]["W_E_p50"] for k in sorted(s, key=int)]
    assert all(b > a for a, b in zip(w, w[1:], strict=False))


def test_sobol_top_and_c6(report):
    assert "powertrain.pack.eta_c2p" in report.sobol
    st = report.sobol_detail["W_E"]["ST"]
    assert all(-0.05 <= v <= 1.2 for v in st.values())
    c6 = next(f for f in report.rule_flags if f.id == "C6")
    assert c6.severity == "warn" and "powertrain.pack.eta_c2p" in c6.values


def test_cg_cases(report, cfg):
    cases = report.cg["cases"]
    assert set(cases) == {"empty", "occupants_2x86kg", "single_pilot_light", "bay0_removed", "bay1_removed"}
    assert cases["occupants_2x86kg"]["mass_kg"]["p50"] == pytest.approx(report.empty_weight.p50 + 172.0, rel=0.02)
    # removing the forward bay moves the CG aft; removing the aft bay moves it forward
    assert cases["bay0_removed"]["x_m"]["p50"] > cases["empty"]["x_m"]["p50"] > cases["bay1_removed"]["x_m"]["p50"]
    assert "range_pct_mac" in report.cg
    st = dict(cfg.stations)
    st.pop("htail")
    with pytest.raises(MissingInputError, match="stations.htail"):
        predict(_mod(cfg, stations={k: v.model_dump() for k, v in st.items()}), 2000, explain=False)


def test_closure_reference_converges(cfg):
    r = close_point(cfg, lambda w: 60e3, 172.0, final_report=False)
    assert r.converged and r.iterations < 50
    assert r.mtow_kg == pytest.approx(r.empty_weight_kg + 172.0, abs=0.1)
    m = close_mc(cfg, lambda w: 60e3, 172.0, n_samples=2000)
    assert m["converged_fraction"] == 1.0
    assert m["mtow"]["p50"] == pytest.approx(r.mtow_kg, rel=0.01)
    heavier = close_point(cfg, lambda w: 60e3, 250.0, final_report=False)
    assert heavier.mtow_kg > r.mtow_kg


def test_closure_with_energy_coupling(cfg):
    r = close_point(cfg, lambda w: 60e3 * (w / 700.0), 172.0, final_report=False)
    assert r.converged and r.energy_wh > 60e3
    m = close_mc(cfg, lambda w: 60e3 * (w / 700.0), 172.0, n_samples=2000,
                 range_fn=lambda w, e: e / w)
    assert "range" in m and m["converged_fraction"] == 1.0


def test_schema_export(tmp_path):
    paths = export_schemas(tmp_path)
    for p in paths:
        assert json.loads(p.read_text())["title"] in ("HS1Config", "WeightReport")


def test_cli(tmp_path, capsys):
    out = tmp_path / "r.json"
    assert cli_main(["predict", str(EXAMPLE), "--n", "2000", "--out", str(out)]) == 0
    assert "EMPTY WEIGHT" in capsys.readouterr().out and json.loads(out.read_text())["empty_weight"]
    assert cli_main(["registry"]) == 0
    assert "torenbeek" in capsys.readouterr().out


def test_mc_samples_at_least_r8(report):
    assert report.versions["n_samples"] >= 2000


def test_param_distributions():
    from hs1_weights.config import Param
    u = np.linspace(0.01, 0.99, 99)
    assert np.allclose(Param(dist="uniform", low=1, high=3).ppf(u), 1 + 2 * u)
    assert Param(dist="normal", mean=5, sd=1).point() == pytest.approx(5)
    assert Param(dist="lognormal", mean=2, sd=0.1).point() == pytest.approx(2)
    assert Param(dist="triangular", low=0, mode=1, high=2).point() == pytest.approx(1)
    with pytest.raises(ValueError):
        Param(dist="uniform", low=3, high=1)
