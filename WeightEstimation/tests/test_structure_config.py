"""Aircraft structure config: catalogue, validation, overlay build, relative layout and CLI (docs/14)."""
import json
import math

import pytest
from conftest import ROOT

from hs1_weights import structure_config as sc

WPP = ROOT.parent / "WPPestimation"
RUNS = WPP / "runs" / "runs.json"
pytestmark = pytest.mark.skipif(not RUNS.exists(), reason="WPPestimation not present")


@pytest.fixture(scope="module")
def run():
    from hs1_weights.adapters import eacsize as ea
    return ea.load_run(RUNS, "R0003")


def test_catalogue_well_formed(run):
    cat = sc.catalogue(run)
    ids = [p["id"] for p in cat]
    assert len(ids) == len(set(ids)) and len(cat) >= 60
    assert {p["group"] for p in cat} == set(sc.GROUPS)
    for p in cat:
        assert p["kind"] in ("choice", "range", "band") and p["label"] and p["source"]
        if p["kind"] == "choice":
            assert p["default"] in [o["value"] for o in p["options"]]
        elif p["kind"] == "range":
            assert p["min"] <= p["default"] <= p["max"], p["id"]
        else:
            lo, hi = p["default"]
            assert p["min"] <= lo <= hi <= p["max"], p["id"]
        for k in p.get("requires", {}):
            assert k in ids


def test_defaults_centred_on_eacsize(run):
    cat = {p["id"]: p for p in sc.catalogue(run)}
    g = run["results"]["design"]["geometry"]
    assert cat["tail.arm_frac"]["default"] == pytest.approx(g["tail_arm"] / g["fus_len"], abs=1e-3)
    assert cat["wing.a_per_rad"]["default"] == pytest.approx(sc.helmbold_lift_slope(g["AR"]), abs=0.01)
    assert cat["tail.arm_frac"]["source"] == sc.SRC_RUN


def test_default_selection_builds_valid_config_and_predicts(run):
    from hs1_weights.adapters import eacsize as ea
    from hs1_weights.api import predict
    cfg = ea.config_from_eacsize(run, sc.build_overlay({}, run))
    L = run["results"]["design"]["geometry"]["fus_len"]
    assert cfg.stations["wing"].x_m == pytest.approx(2.25 / sc.L_REF_M * L, abs=1e-3 * L)  # fractions to 3 dp
    assert cfg.tail_arm_m == pytest.approx(run["results"]["design"]["geometry"]["tail_arm"])  # follows eacsize
    assert sum(m.p_cont_kw for m in cfg.powertrain.motors) == pytest.approx(
        run["results"]["summary"]["installed_power_kw"])
    assert any("Aircraft structure config v1: selection" in p for p in cfg.provenance)
    rep = predict(cfg, n_samples=400, seed=0)
    old = predict(ea.config_from_eacsize(run, json.loads((ROOT / "examples" / "eacsize_overlay.json").read_text())),
                  n_samples=400, seed=0)
    # defaults are the old placeholders, rescaled to the eacsize fuselage: same empty weight within 1%
    assert rep.empty_weight.p50 == pytest.approx(old.empty_weight.p50, rel=0.01)


def test_validation_errors_name_the_parameter(run):
    with pytest.raises(sc.StructureConfigError) as e:
        sc.validate({"htail.taper": 1.5, "dep.layout": "quad", "motor.sp_motor_kw_per_kg": [6, 4], "zzz": 1,
                     "mass.light_pilot_kg": "heavy", "harness.f_ins": [1.0]}, run)
    msg = " | ".join(e.value.errors)
    for pid in ("htail.taper", "dep.layout", "motor.sp_motor_kw_per_kg", "zzz", "mass.light_pilot_kg", "harness.f_ins"):
        assert pid in msg
    with pytest.raises(sc.StructureConfigError, match="nose gear"):
        sc.validate({"cg.nose_gear_x_frac": 0.3, "cg.main_gear_x_frac": 0.25}, run)
    with pytest.raises(sc.StructureConfigError, match="last bay"):
        sc.validate({"bays.count": "4", "bays.x_first_frac": 0.6, "bays.pitch_frac": 0.2}, run)
    with pytest.raises(sc.StructureConfigError):
        sc.validate(["not", "a", "dict"], run)  # type: ignore[arg-type]


@pytest.mark.parametrize("layout,share,expect", [
    ("hs1_3", 0.6, {"wing_L": 0.3, "wing_R": 0.3, "aft": 0.4}), ("hs1_3", 0.9, {"wing_L": 0.45, "wing_R": 0.45,
                                                                                "aft": 0.1}),
    ("wing_2", 0.6, {"wing_L": 0.5, "wing_R": 0.5}), ("nose_1", 0.6, {"nose": 1.0}), ("aft_1", 0.6, {"aft": 1.0})])
def test_power_shares_sum_to_one(run, layout, share, expect):
    from hs1_weights.adapters import eacsize as ea
    sel = sc.validate({"dep.layout": layout, "dep.wing_share": share}, run)
    sh = sc.power_shares(sel)
    assert sh == pytest.approx(expect) and math.isclose(sum(sh.values()), 1.0)
    cfg = ea.config_from_eacsize(run, sc.build_overlay(sel, run))
    assert [m.id for m in cfg.powertrain.motors] == list(expect)
    assert [h.motor for h in cfg.powertrain.harness] == list(expect)
    if layout == "nose_1":       # irrelevant wing parameters do not count as changes
        assert "dep.wing_share" not in sc.changed(sel, run)


def test_choices_reach_the_config(run):
    from hs1_weights.adapters import eacsize as ea
    sel = {"tail.type": "t_tail", "harness.conductor": "Al", "thermal.cooling": "air", "bus.v": "800",
           "bays.count": "3", "bays.split": "forward", "equip.brs": "none", "mat.wing": "metal_aluminium",
           "tail.arm_frac": 0.6, "motor.sp_motor_kw_per_kg": [5.0, 5.0]}
    cfg = ea.config_from_eacsize(run, sc.build_overlay(sel, run))
    assert cfg.t_tail and cfg.powertrain.v_bus == 800 and cfg.powertrain.thermal.cooling == "air"
    assert {h.conductor for h in cfg.powertrain.harness} == {"Al"}
    fr = [b.energy_fraction for b in cfg.battery_bays.bays]
    assert len(fr) == 3 and fr[0] == pytest.approx(1.5 / 3.5) and sum(fr) == pytest.approx(1.0)
    assert not cfg.brs_fitted and "brs" not in [e.name for e in cfg.equipment]
    assert cfg.material_by_component["wing"].value == "metal_aluminium"
    assert cfg.tail_arm_m == pytest.approx(0.6 * cfg.fuselage.length_m)       # changed: overrides eacsize
    assert any("overlay overrides eacsize value: tail_arm_m" in p for p in cfg.provenance)
    sp = cfg.powertrain.motors[0].sp_motor_kw_per_kg
    assert sp.dist == "fixed" and sp.value == 5.0


def test_selection_hash_and_changed(run):
    d = sc.default_selection(run)
    assert sc.changed(d, run) == [] and sc.selection_hash(d) == sc.selection_hash(sc.validate({}, run))
    s2 = sc.validate({"htail.taper": 0.5}, run)
    assert sc.changed(s2, run) == ["htail.taper"] and sc.selection_hash(s2) != sc.selection_hash(d)


def test_resolve_relative():
    d = {"a": {"x_frac": 0.5, "z_m": 0.2}, "b": [{"frac": 0.25, "of": "semi_span"}], "c": {"frac": 2, "of": "span"},
         "p": {"dist": "uniform", "low": 1, "high": 2}}
    out, n = sc.resolve_relative(d, 8.0, 10.0)
    assert out == {"a": {"x_m": 4.0, "z_m": 0.2}, "b": [1.25], "c": 20.0, "p": d["p"]} and n == 3
    with pytest.raises(ValueError):
        sc.resolve_relative({"frac": 1, "of": "chord"}, 8.0, 10.0)


def test_full_coupling_stations_follow_resized_fuselage(run):
    from hs1_weights.adapters import eacsize as ea
    config_at, _ = ea.full_coupling(run, sc.build_overlay({}, run), WPP)
    (c1, _), (c2, _) = config_at(750.0), config_at(950.0)
    assert c2.fuselage.length_m > c1.fuselage.length_m * 1.02      # eacsize re-sized the fuselage
    for c in (c1, c2):
        L = c.fuselage.length_m
        assert c.stations["htail"].x_m / L == pytest.approx(6.6 / sc.L_REF_M, abs=1e-3)
        assert c.gear.x_main_m / L == pytest.approx(2.85 / sc.L_REF_M, abs=1e-3)
        assert c.battery_bays.bays[0].station.x_m / L == pytest.approx(2.2 / sc.L_REF_M, abs=1e-3)
        assert c.powertrain.motors[0].y_m / (c.wing.span_m / 2) == pytest.approx(2.2 / sc.SEMI_SPAN_REF_M, abs=1e-3)


def test_load_selection_formats(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"selection": {"htail.taper": 0.5}, "hash": "x"}))
    assert sc.load_selection(str(p)) == {"htail.taper": 0.5}
    assert sc.load_selection({"htail.taper": 0.6}) == {"htail.taper": 0.6}


def test_cli_structure_config_and_from_eacsize(tmp_path, capsys):
    from hs1_weights.cli import main
    main(["structure-config", "--runs", str(RUNS), "--run", "R0003"])
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] and d["run_id"] == "R0003" and len(d["catalogue"]) == len(d["defaults"])
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"htail.taper": 9}))
    with pytest.raises(SystemExit):
        main(["structure-config", "--runs", str(RUNS), "--run", "R0003", "--selection", str(bad)])
    assert "htail.taper" in capsys.readouterr().out
    sel = tmp_path / "sel.json"
    sel.write_text(json.dumps({"dep.layout": "wing_2"}))
    out = tmp_path / "o.json"
    main(["from-eacsize", "--runs", str(RUNS), "--run", "R0003", "--structure-config", str(sel), "--wpp", str(WPP),
          "--n", "300", "--out", str(out)])
    res = json.loads(out.read_text())
    assert res["structure_config"]["source"] == "selection" and res["structure_config"]["changed"] == ["dep.layout"]
    assert res["overlay"] is None
    assert any(f"selection {res['structure_config']['hash']}" in s for s in res["report"]["assumptions"])
