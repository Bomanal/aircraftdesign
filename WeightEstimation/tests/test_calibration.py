"""Phase 3 calibration tests on SYNTHETIC fleets with known technology factors."""
import numpy as np
import pytest

from hs1_weights.calibration import build_fleet_matrix, calibrate, k_draws, load_artefact, write_artefact
from hs1_weights.calibration.model import available as nuts_available
from hs1_weights.calibration.posterior import Posterior
from hs1_weights.calibration.ridge_fallback import fit_map
from hs1_weights.calibration.simulate import COMPOSITE_K, simulate_fleet
from hs1_weights.calibration.structure import build_structure, log_posterior, prior_table
from hs1_weights.calibration.validate import loco, loo, map_fitter, prior_predictive, validation_report

STRUCT = ["wing", "htail", "fuselage", "main_gear"]


@pytest.fixture(scope="module")
def fleet():
    rows, comps, truth = simulate_fleet(50, seed=3, n_breakdown=22, n_censored=4)
    return rows, comps, truth, build_fleet_matrix(rows, comps)


@pytest.fixture(scope="module")
def map_result(fleet):
    rows, comps, _, fm = fleet
    return calibrate(rows, comps, "synthetic-test", engine="map", fm=fm, n_draws=3000)


def _k_median(post, mat, n=3000):
    mats = {c: mat for c in STRUCT[:3]} | {"main_gear": "metal_aluminium"}
    k, _, _ = k_draws(post, STRUCT, mats, "far23_normal", "1980_2005", None, "piston", n, np.random.default_rng(0))
    return {c: float(np.median(v)) for c, v in k.items()}


def test_fleet_matrix_shapes(fleet):
    _, _, _, fm = fleet
    assert fm.F.shape == (50, len(fm.comps), 11) and fm.aircraft_ok.all() and fm.latent.sum() == 4
    lat = np.flatnonzero(fm.latent)[0]
    assert fm.F[lat, fm.comps.index("wing"), -1] > fm.F[lat, fm.comps.index("wing"), 0]   # f rises with u


def test_missing_inputs_exclude_from_aircraft_likelihood(fleet):
    rows, comps, _, _ = fleet
    r2 = rows.copy()
    r2.loc[0, "tail_arm"] = np.nan
    fm = build_fleet_matrix(r2, comps)
    assert not fm.aircraft_ok[0] and "L_t" in fm.excluded["synthetic_000"]


def test_log_posterior_numpy_finite(fleet):
    S = build_structure(fleet[3])
    x0 = np.concatenate([S.mu, [np.log(0.05)], np.full(len(S.comp_groups), np.log(0.1)), np.zeros(len(S.latent_idx))])
    assert np.isfinite(log_posterior(np, S, x0))


def test_map_recovers_known_k(map_result):
    metal = _k_median(map_result.posterior, "metal_aluminium")
    comp = _k_median(map_result.posterior, "composite_glass")
    truth = {"wing": 1.05, "htail": 0.95, "fuselage": 1.10, "main_gear": 0.90}
    for c, t in truth.items():
        assert metal[c] == pytest.approx(t, rel=0.04), c
        g = {"wing": "wing", "htail": "tail", "fuselage": "fuselage", "main_gear": "gear"}[c]
        tc = t * (COMPOSITE_K[g] if c != "main_gear" else 1.0)
        assert comp[c] == pytest.approx(tc, rel=0.05), c


def test_map_is_deterministic(fleet):
    S = build_structure(fleet[3])
    a, b = fit_map(S), fit_map(S)
    assert np.array_equal(a.x, b.x)


def test_map_without_jax_agrees(fleet):
    S = build_structure(fleet[3])
    a, b = fit_map(S, use_jax=False), fit_map(S)
    E = len(S.effects)
    assert np.allclose(a.x[:E], b.x[:E], atol=2e-3)


def test_latent_u_within_bounds(map_result):
    us = [v for k, v in map_result.posterior.draws.items() if k.startswith("u_")]
    assert len(us) == 4 and all((u >= 0).all() and (u <= 0.2).all() for u in us)


def test_identifiability_and_collapse(fleet):
    rows, comps, _, _ = fleet
    few = comps[comps["id"].isin(sorted(set(comps["id"]))[:5])]
    res = calibrate(rows, few, "synthetic-test", engine="map", n_draws=500)
    assert res.metadata["collapse_struct"] and "theta_struct" in res.posterior.draws
    assert "collapsed" in res.metadata["collapse_note"]


def test_artefact_round_trip(map_result, tmp_path):
    import datetime as dt
    p = write_artefact(map_result, tmp_path, dt.date(2026, 11, 3))
    assert p.name.startswith("calibration_20261103_") and p.suffix in (".nc", ".json")
    back = load_artefact(p)
    assert back.artefact_hash == map_result.artefact_hash
    for k, v in map_result.posterior.draws.items():
        np.testing.assert_allclose(back.posterior.draws[k], v)
    assert back.metadata["prior_spec"]["theta_wing"] == {"kind": "normal", "loc": 0.0, "scale": 0.15,
                                                          "basis": "doc 07 §3.4 (theta_wing); other theta_i: SC-7"}


def test_prior_posterior_draws_match_priors():
    post = Posterior.from_priors()
    rng = np.random.default_rng(0)
    d = post.joint(["alpha_wing_composite_carbon", "sigma_model"], 20000, rng)
    assert np.mean(d["alpha_wing_composite_carbon"]) == pytest.approx(np.log(0.85), abs=0.005)
    assert (d["sigma_model"] >= 0).all()


def test_prior_table_matches_doc07():
    p = prior_table()
    assert p["alpha_tail_composite_glass"].loc == pytest.approx(np.log(0.83))
    assert p["alpha_gear_wood"].scale == 0.20 and p["sigma_model"].scale == 0.08
    assert p["sigma_comp_wing"].scale == 0.15 and p["tau_m"].scale == 0.05


def test_prior_predictive_sane(fleet):
    r = prior_predictive(fleet[3], n=300)
    assert not r["any_negative"] and r["n"] == 50


def test_loco_harness_runs(fleet):
    _, _, _, fm = fleet
    cls = np.array([fm.material[j]["wing"].startswith("composite") for j in range(fm.n)])
    m = loco(fm, cls, map_fitter(0, 1000), n=1000)
    assert m["n"] == cls.sum() and m["mape"] < 0.15 and 0.0 <= m["cov90"] <= 1.0


@pytest.mark.slow
def test_loo_and_report(fleet):
    rows, comps, _, fm = fleet
    rep = validation_report(rows.iloc[:30], comps[comps["id"].isin(rows["id"].iloc[:30])], n=1000)
    assert rep["loo_metal"]["n"] > 0 and "benchmark_rel_improvement" in rep
    assert rep["checks"]["loo_metal_mape"] is not None
    m = loo(fm, map_fitter(0, 1000), n=1000, which=np.arange(fm.n) < 10)
    assert m["n"] == 10


@pytest.mark.slow
@pytest.mark.skipif(not nuts_available(), reason="numpyro/jax not installed")
def test_nuts_agrees_with_map_within_2pct(fleet, map_result):
    """P3-2: MAP/Laplace posterior means of k agree with NUTS within 2%."""
    rows, comps, _, fm = fleet
    nuts = calibrate(rows, comps, "synthetic-test", engine="nuts", fm=fm, num_warmup=500, num_samples=500,
                     num_chains=2)
    assert nuts.diagnostics["rhat_max"] < 1.05
    for mat in ("metal_aluminium", "composite_glass"):
        a, b = _k_median(map_result.posterior, mat), _k_median(nuts.posterior, mat)
        for c in STRUCT:
            assert a[c] == pytest.approx(b[c], rel=0.02), (mat, c)
