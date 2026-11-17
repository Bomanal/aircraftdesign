"""Phase 4 reasoning layer and calibrated prediction, on SYNTHETIC fleets."""
import numpy as np
import pytest
from conftest import ROOT

from hs1_weights.api import predict
from hs1_weights.calibration import calibrate
from hs1_weights.calibration.gp_residual import feature_vector, fit_gp, gp_predict
from hs1_weights.calibration.simulate import simulate_fleet
from hs1_weights.config import load_config
from hs1_weights.mc import UncertainInput
from hs1_weights.reasoning import rules as R
from hs1_weights.reasoning.sensitivity import sobol, top


@pytest.fixture(scope="module")
def synth():
    rows, comps, _ = simulate_fleet(40, seed=5, n_breakdown=21)
    cal = calibrate(rows, comps, "synthetic-test", engine="map", n_draws=2000)
    return rows, comps, cal


@pytest.fixture(scope="module")
def cfg():
    return load_config(str(ROOT / "examples" / "hs1_reference.json"))


def test_sobol_linear_model_analytic():
    """f = 1·x1 + 2·x2 + 0·x3, x ~ U(0,1): S_i = a_i² / Σa² = (0.2, 0.8, 0)."""
    ins = [UncertainInput(f"x{i}", lambda u: u) for i in (1, 2, 3)]
    res = sobol(lambda v: {"y": v["x1"] + 2 * v["x2"] + 0 * v["x3"]}, ins, {}, 4096, 0, ["y"])["y"]
    assert res["S1"]["x1"] == pytest.approx(0.2, abs=0.03) and res["S1"]["x2"] == pytest.approx(0.8, abs=0.03)
    assert res["ST"]["x3"] == pytest.approx(0.0, abs=1e-12)
    assert list(top(res["ST"], 1, always=["x3"])) == ["x2", "x3"]


def test_calibrated_predict_with_analogs(cfg, synth):
    rows, comps, cal = synth
    rep = predict(cfg, 2000, 0, calibration=cal, fleet=(rows, comps))
    assert rep.calibration["engine"] == "map_laplace" and rep.versions["calibration_artefact"] == cal.artefact_hash
    assert len(rep.analogs) == 5 and all(not a.tier_c for a in rep.analogs)
    a = rep.analogs[0]
    assert a.explanation.startswith(f"HS1 vs. {a.type_name}:") and "battery_pack" in a.delta_by_component
    assert set(a.delta_by_driver) == {"n_ult", "W_dg", "S_w", "q"}
    total = sum(a.delta_by_component.values())
    assert f"{total:+.0f} kg" in a.explanation
    flags = {f.id: f for f in rep.rule_flags}
    assert "leverage" in flags["C7"].values
    # HS1 is carbon; the synthetic fleet is glass/aluminium, so the C1 analog cell is (correctly) empty
    assert flags["C1"].message == "C1 not evaluated: analog cell has 0 < 5 records"


def test_k_with_calibration_differs_from_prior(cfg, synth):
    _, _, cal = synth
    a = predict(cfg, 2000, 0, explain=False)
    b = predict(cfg, 2000, 0, calibration=cal, explain=False)
    assert a.components["wing"].k.sd != b.components["wing"].k.sd


def test_gp_residual_round_trip_and_guard(cfg, synth):
    rows, _, cal = synth
    rng = np.random.default_rng(0)
    X = np.array([feature_vector(r.mtow, r.wing_area, r.span ** 2 / r.wing_area, r.q_d, 1.5 * r.n_limit_pos, 2,
                                 "piston", r.material_primary, r.year) for r in rows.itertuples()])
    y = 0.03 * np.sin(X[:, 1] * 3) + rng.normal(0, 0.005, len(X))
    gp = fit_gp(X, y, seed=0)
    m1, s1 = gp_predict(gp, X[:3])
    m2, s2 = gp_predict(gp, X[:3])
    assert np.array_equal(m1, m2) and (s1 > 0).all()
    cal.gp = dict(gp, guard_passed=False)
    rep = predict(cfg, 2000, 0, calibration=cal, explain=False)
    assert any("GP residual not applied out of class" in s for s in rep.assumptions)
    cfg2 = cfg.model_copy(update={"gp_out_of_class": True})
    rep2 = predict(cfg2, 2000, 0, calibration=cal, explain=False)
    assert any(s.startswith("GP residual applied") for s in rep2.assumptions)
    cal.gp = None


def test_rules_unit():
    assert R.c3_stall(700.0, 11.0, 1.5, 2.0).severity == "info"
    assert R.c3_stall(900.0, 8.0, 1.5, 2.0).severity == "error"
    assert R.c5_pack(310.0, 300.0, "cell").severity == "error"
    assert R.c9_bays(30.0, 60.0, (0.1, 0.4)).severity == "warn"
    assert R.c9_bays(20.0, 60.0, (0.1, 0.4)).severity == "info"
    assert R.c6_assumption_variance({"a": 0.3, "b": 0.5}, {"a"}).values == {"a": 0.3}
    assert R.c7_leverage({"leverage": 5.0, "leverage_mean": 1.0}).message.startswith("extrapolating")
