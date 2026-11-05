"""Statistical validation harness: LOO, LOCO, raw-method benchmark, interval calibration (doc 08 §5, P3-4).

Thresholds (doc 08 §5 / doc 01 §5) are evaluated and returned as pass/fail; nothing is tuned to pass them.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from ..components.base import K_GROUP
from .fleet import COMPOSITES, FleetMatrix, build_fleet_matrix
from .posterior import FloatArr, Posterior, k_draws
from .structure import U_MAX, U_SD, build_structure, interp_F

THRESHOLDS = {"loo_mape_metal": 0.07, "loo_mape_composite": 0.10, "loco_electric_mape": 0.15,
              "loco_electric_cov90": 0.80, "benchmark_rel_improvement": 0.30, "coverage_tol": 0.07}
LEVELS = (0.5, 0.8, 0.9)


def predictive_samples(post: Posterior, fm: FleetMatrix, j: int, n: int, rng: np.random.Generator) -> FloatArr:
    """Posterior-predictive draws of the observed W_E of record j (model error + measurement error)."""
    comps = [c for i, c in enumerate(fm.comps) if fm.present[j, i]]
    k, sm, _ = k_draws(post, comps, fm.material[j], fm.cert_level[j], fm.era[j], fm.manufacturer[j],
                       fm.powertrain[j], n, rng)
    if fm.latent[j]:
        u = np.abs(rng.normal(0.0, U_SD, 4 * n))
        u = u[u <= U_MAX][:n]
        while len(u) < n:
            u = np.concatenate([u, [U_MAX * rng.random()]])
    else:
        u = np.zeros(n)
    Fu = interp_F(np, np.repeat(fm.F[j:j + 1], n, axis=0), u)        # [n, C]
    W = sum(k[c] * Fu[:, fm.comps.index(c)] for c in comps) + fm.U[j]
    return np.asarray(W * np.exp(rng.normal(0.0, 1.0, n) * np.sqrt(sm ** 2 + fm.sigma_meas[j] ** 2)), dtype=float)


def metrics(obs: FloatArr, samples: Sequence[FloatArr]) -> dict[str, Any]:
    if len(obs) == 0:
        return {"n": 0}
    p50 = np.array([np.median(s) for s in samples])
    out: dict[str, Any] = {"n": int(len(obs)), "mape": float(np.mean(np.abs(p50 - obs) / obs))}
    for lv in LEVELS:
        lo = np.array([np.quantile(s, 0.5 - lv / 2) for s in samples])
        hi = np.array([np.quantile(s, 0.5 + lv / 2) for s in samples])
        out[f"cov{int(lv * 100)}"] = float(np.mean((obs >= lo) & (obs <= hi)))
    out["pit"] = [float(np.mean(s <= o)) for s, o in zip(samples, obs, strict=False)]
    return out


Fitter = Callable[[FleetMatrix], Posterior]


def map_fitter(seed: int = 0, n_draws: int = 2000) -> Fitter:
    from .ridge_fallback import fit_map, laplace_draws

    def fit(fm: FleetMatrix) -> Posterior:
        S = build_structure(fm)
        from .structure import prior_table
        return Posterior(laplace_draws(S, fit_map(S), n_draws, seed), prior_table(S.collapse_struct),
                         S.collapse_struct, "map_laplace", S.manufacturers)
    return fit


def holdout(fm: FleetMatrix, test: np.ndarray, fitter: Fitter, n: int, seed: int) -> tuple[FloatArr, list[FloatArr]]:
    post = fitter(fm.subset(~test))
    rng = np.random.default_rng(seed)
    idx = [int(j) for j in np.flatnonzero(test) if fm.aircraft_ok[j]]
    return np.exp(fm.log_obs[idx]), [predictive_samples(post, fm, j, n, rng) for j in idx]


def loo(fm: FleetMatrix, fitter: Fitter, n: int = 2000, seed: int = 0,
        which: np.ndarray | None = None) -> dict[str, Any]:
    """Leave-one-out over aircraft-level-evaluable records (optionally a subset)."""
    obs, samp, ids = [], [], []
    for j in np.flatnonzero(fm.aircraft_ok if which is None else (which & fm.aircraft_ok)):
        test = np.zeros(fm.n, dtype=bool)
        test[j] = True
        o, s = holdout(fm, test, fitter, n, seed + int(j))
        obs += list(o)
        samp += s
        ids.append(fm.ids[j])
    m = metrics(np.array(obs), samp)
    m["ids"] = ids
    return m


def loco(fm: FleetMatrix, in_class: np.ndarray, fitter: Fitter, n: int = 2000, seed: int = 0) -> dict[str, Any]:
    o, s = holdout(fm, in_class, fitter, n, seed)
    return metrics(o, s)


def raw_method_mape(rows: pd.DataFrame, components: pd.DataFrame | None, methods: Mapping[str, str]) -> float:
    """MAPE of an uncalibrated method (k = 1) on aircraft-level-evaluable records."""
    fm = build_fleet_matrix(rows, components, methods)
    ok = fm.aircraft_ok
    pred = (fm.F[:, :, 0] * fm.present).sum(axis=1) + fm.U
    obs = np.exp(fm.log_obs)
    return float(np.mean(np.abs(pred[ok] - obs[ok]) / obs[ok]))


def material_class(fm: FleetMatrix, j: int) -> str:
    m = fm.material[j].get("wing", "")
    return "metal" if m == "metal_aluminium" else "composite" if m in COMPOSITES else "other"


def validation_report(rows: pd.DataFrame, components: pd.DataFrame | None, fitter: Fitter | None = None,
                      n: int = 2000, seed: int = 0,
                      raw_methods: Mapping[str, Mapping[str, str]] | None = None) -> dict[str, Any]:
    """Doc 08 §5 table with thresholds evaluated. Empty classes are reported as not evaluated."""
    fitter = fitter or map_fitter(seed)
    fm = build_fleet_matrix(rows, components)
    cls = np.array([material_class(fm, j) for j in range(fm.n)])
    elec = np.array([p == "electric" for p in fm.powertrain])
    rep: dict[str, Any] = {"thresholds": THRESHOLDS, "n_records": fm.n}
    rep["loo_metal"] = loo(fm, fitter, n, seed, cls == "metal") if (cls == "metal").any() else {"n": 0}
    rep["loo_composite"] = loo(fm, fitter, n, seed, cls == "composite") if (cls == "composite").any() else {"n": 0}
    rep["loco_electric"] = (loco(fm, elec, fitter, n, seed) if elec.any() and (~elec).any() else {"n": 0})
    comp2 = (cls == "composite") & ~elec
    rep["loco_composite_2seat"] = loco(fm, comp2, fitter, n, seed) if comp2.any() and (~comp2).any() else {"n": 0}
    raw_methods = raw_methods or {"raymer_ga": {}, "cessna": dict.fromkeys(
        ("wing", "htail", "vtail", "fuselage", "main_gear", "nose_gear"), "cessna")}
    rep["benchmark_raw_mape"] = {k: raw_method_mape(rows, components, v) for k, v in raw_methods.items()}
    in_class = [rep[k] for k in ("loo_metal", "loo_composite") if rep[k].get("n")]
    if in_class:
        pooled = sum(r["mape"] * r["n"] for r in in_class) / sum(r["n"] for r in in_class)
        best_raw = min(rep["benchmark_raw_mape"].values())
        rep["benchmark_rel_improvement"] = 1.0 - pooled / best_raw
    checks: dict[str, bool | None] = {
        "loo_metal_mape": _le(rep["loo_metal"], "mape", THRESHOLDS["loo_mape_metal"]),
        "loo_composite_mape": _le(rep["loo_composite"], "mape", THRESHOLDS["loo_mape_composite"]),
        "loco_electric_mape": _le(rep["loco_electric"], "mape", THRESHOLDS["loco_electric_mape"]),
        "loco_electric_cov90": (None if not rep["loco_electric"].get("n")
                                else rep["loco_electric"]["cov90"] >= THRESHOLDS["loco_electric_cov90"]),
        "benchmark": (None if "benchmark_rel_improvement" not in rep
                      else rep["benchmark_rel_improvement"] >= THRESHOLDS["benchmark_rel_improvement"]),
    }
    for name in ("loo_metal", "loo_composite"):
        r = rep[name]
        checks[f"{name}_coverage"] = (None if not r.get("n") else all(
            abs(r[f"cov{int(lv * 100)}"] - lv) <= THRESHOLDS["coverage_tol"] for lv in LEVELS))
    if "pip_velis_electro" in fm.ids:
        j = fm.ids.index("pip_velis_electro")
        test = np.zeros(fm.n, dtype=bool)
        test[j] = True
        o, s = holdout(fm, test, fitter, n, seed)
        checks["velis_anchor_in_90pi"] = bool(np.quantile(s[0], 0.05) <= o[0] <= np.quantile(s[0], 0.95)) if s else None
    rep["checks"] = checks
    return rep


def _le(r: Mapping[str, Any], key: str, thr: float) -> bool | None:
    return None if not r.get("n") else bool(r[key] <= thr)


def prior_predictive(fm: FleetMatrix, n: int = 500, seed: int = 0) -> dict[str, Any]:
    """P3-1: prior predictive W_E/MTOW per record; checks no negative masses and the 0.3–0.9 band."""
    post = Posterior.from_priors(build_structure(fm).collapse_struct)
    rng = np.random.default_rng(seed)
    ratios = np.array([predictive_samples(post, fm, j, n, rng) / fm.mtow[j] for j in range(fm.n)
                       if fm.aircraft_ok[j]])
    if ratios.size == 0:
        return {"n": 0}
    med = np.median(ratios, axis=1)
    return {"n": int(len(med)), "any_negative": bool((ratios < 0).any()), "median_ratio_min": float(med.min()),
            "median_ratio_max": float(med.max()), "within_0.3_0.9": bool(((med >= 0.3) & (med <= 0.9)).all()),
            "k_groups": sorted({K_GROUP[c] for c in fm.comps})}
