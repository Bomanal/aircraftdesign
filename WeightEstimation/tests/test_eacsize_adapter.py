"""WPPestimation/eacsize -> hs1_weights adapter. Uses the repository's own eacsize runs and code."""
import json

import numpy as np
import pytest
from conftest import ROOT

from hs1_weights.adapters import eacsize as ea
from hs1_weights.api import predict
from hs1_weights.cli import main as cli_main
from hs1_weights.closure import close_point

WPP = ROOT.parent / "WPPestimation"
RUNS = WPP / "runs" / "runs.json"
pytestmark = pytest.mark.skipif(not RUNS.exists(), reason="WPPestimation/runs/runs.json not present")
OVERLAY = json.loads((ROOT / "examples" / "eacsize_overlay.json").read_text())


@pytest.fixture(scope="module")
def run():
    return ea.load_run(RUNS, "R0003")


def test_load_run_selection():
    with pytest.raises(ValueError, match="pass run="):
        ea.load_run(RUNS)
    with pytest.raises(KeyError, match="not found"):
        ea.load_run(RUNS, "R9999")


def test_mapping_and_provenance(run):
    cfg = ea.config_from_eacsize(run, OVERLAY)
    s, geo = run["results"]["summary"], run["results"]["design"]["geometry"]
    assert cfg.mass_guess_kg == s["mtom_kg"] and cfg.wing.area_m2 == s["wing_area_m2"]
    assert cfg.fuselage.wetted_area_m2 == geo["S_wet_fus"] and cfg.tail_arm_m == geo["tail_arm"]
    assert cfg.powertrain.pack.level == "pack" and cfg.powertrain.pack.energy_kwh.value == s["battery_kwh"]
    assert sum(m.p_cont_kw for m in cfg.powertrain.motors) == pytest.approx(s["installed_power_kw"])
    assert cfg.powertrain.motors[0].eta_chain.value == pytest.approx(0.95 * 0.98 * 0.99)
    assert any("eacsize run R0003" in p for p in cfg.provenance)
    rep = predict(cfg, 2000, 0, explain=False)
    assert any(a.startswith("input provenance: input from WPPestimation/eacsize run R0003") for a in rep.assumptions)
    # pack-level Wh/kg and the same energy -> the same pack mass as eacsize (runs.json rounds to 6 decimals)
    assert rep.components["battery_pack"].mass_kg.p50 == pytest.approx(s["battery_kg"], rel=1e-6)


def test_overlay_override_logged(run):
    cfg = ea.config_from_eacsize(run, {**OVERLAY, "vc_kt": 130.0})
    assert cfg.vc_kt == 130.0 and "overlay overrides eacsize value: vc_kt" in cfg.provenance


def test_bad_power_shares(run):
    ov = json.loads(json.dumps(OVERLAY))
    ov["powertrain"]["motors"][0]["power_share"] = 0.9
    with pytest.raises(ValueError, match="sum to 1"):
        ea.config_from_eacsize(run, ov)


def test_energy_fn_matches_eacsize_and_rises_with_mass(run):
    e = ea.energy_fn_from_eacsize(run, WPP)
    # eacsize sized R0003 at fixed MTOM from remaining mass; the mission need at that mass is below it
    assert 0 < e(750.0) < e(850.0)
    arr = e(np.array([750.0, 850.0, 750.0]))
    assert arr[0] == pytest.approx(e(750.0)) and arr[2] == arr[0]
    assert e(float("inf")) == float("inf")


def test_closure_with_eacsize_energy(run):
    cfg = ea.config_from_eacsize(run, OVERLAY)
    r = close_point(cfg, ea.energy_fn_from_eacsize(run, WPP), cfg.payload_kg, final_report=False)
    assert r.converged and r.mtow_kg == pytest.approx(r.empty_weight_kg + cfg.payload_kg, abs=0.1)


def test_cli_from_eacsize(tmp_path, capsys):
    cfg_out, rep_out = tmp_path / "cfg.json", tmp_path / "rep.json"
    assert cli_main(["from-eacsize", "--runs", str(RUNS), "--run", "R0003", "--overlay",
                     str(ROOT / "examples" / "eacsize_overlay.json"), "--wpp", str(WPP), "--close",
                     "--write-config", str(cfg_out), "--out", str(rep_out)]) == 0
    txt = capsys.readouterr().out
    assert "eacsize vs hs1_weights" in txt and "closure (eacsize mission energy): converged=True" in txt
    from hs1_weights.config import HS1Config
    HS1Config.model_validate(json.loads(cfg_out.read_text()))      # saved config re-loads
    assert json.loads(rep_out.read_text())["closure_point"]["converged"]
