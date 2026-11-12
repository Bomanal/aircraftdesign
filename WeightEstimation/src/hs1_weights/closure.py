"""MTOW closure loop for coupling to ``hs1_design`` (doc 10 §1, P5-2).

``energy_required(W_dg_kg) -> Wh`` is supplied by hs1_design's energy-based mission model (30-min reserve;
no Breguet). Battery mass sits inside W_E and there is no fuel term: W_dg_new = W_E + payload.

* ``close_point``: iterate on the W_E P50 until |ΔW| < tol (max 50 iterations; damping 0.5 if oscillating).
* ``close_mc``: every Monte Carlo sample closes its own loop with its own sampled parameters and its own
  certification envelope, giving MTOW (and optionally range) distributions at fixed payload. The loop is
  vectorised over samples with numpy, which stands in for the joblib parallelism doc 10 mentions.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .api import Dist, WeightReport, predict
from .calibration.fit import CalibrationResult
from .calibration.posterior import FloatArr, Posterior
from .config import HS1Config
from .mc import Evaluator, Loads, loads_at

EnergyFn = Callable[[Any], Any]
W_CAP_FACTOR = 5.0      # a Monte Carlo sample whose W_dg exceeds this multiple of the guess is a snowball


@dataclass
class ClosureResult:
    converged: bool
    iterations: int
    mtow_kg: float
    empty_weight_kg: float
    energy_wh: float
    history: list[dict[str, float]] = field(default_factory=list)
    report: WeightReport | None = None


def _with(cfg: HS1Config, w_dg: float, e_wh: float) -> HS1Config:
    pack = cfg.powertrain.pack.model_copy(update={"energy_kwh": cfg.powertrain.pack.energy_kwh.model_copy(
        update={"value": e_wh / 1e3, "dist": "fixed", "low": None, "high": None, "mean": None, "sd": None,
                "mode": None})})
    pt = cfg.powertrain.model_copy(update={"pack": pack})
    return cfg.model_copy(update={"mass_guess_kg": w_dg, "powertrain": pt})


def close_point(cfg: HS1Config, energy_required: EnergyFn, payload_kg: float, tol: float = 0.1, max_iter: int = 50,
                n_samples: int = 2000, seed: int = 0, calibration: CalibrationResult | str | Path | None = None,
                final_report: bool = True) -> ClosureResult:
    w = cfg.mass_guess_kg
    hist: list[dict[str, float]] = []
    damp, last_delta, converged = 1.0, 0.0, False
    e = w_e = 0.0
    for it in range(1, max_iter + 1):
        e = float(energy_required(w))
        rep = predict(_with(cfg, w, e), n_samples, seed, calibration, explain=False)
        w_e = rep.empty_weight.p50
        w_new = w_e + payload_kg
        delta = w_new - w
        hist.append({"iteration": it, "W_dg": w, "E_Wh": e, "W_E_p50": w_e, "W_dg_new": w_new})
        if abs(delta) < tol:
            converged, w = True, w_new
            break
        if last_delta and np.sign(delta) != np.sign(last_delta):
            damp = 0.5
        w, last_delta = w + damp * delta, delta
    report = None
    if final_report:
        c = _with(cfg, w, float(energy_required(w))).model_copy(update={"payload_kg": payload_kg})
        report = predict(c, n_samples, seed, calibration)
    return ClosureResult(converged, len(hist), w, w_e, e, hist, report)


def _loads_vec(cfg: HS1Config, w: FloatArr) -> Loads:
    per = [loads_at(cfg, float(x)) for x in w]
    first = per[0]
    return Loads(np.array([p.n_ult for p in per]), first.n_ult_case + " (per sample)", first.q,
                 np.array([p.q_D for p in per]), np.array([p.V_D for p in per]), first.n_mount, first.n_mount_case,
                 first.envelope, first.per_basis, first.notes)


def close_mc(cfg: HS1Config, energy_required: EnergyFn, payload_kg: float, n_samples: int = 2000, seed: int = 0,
             calibration: CalibrationResult | None = None, tol: float = 0.1, max_iter: int = 50,
             range_fn: Callable[[FloatArr, FloatArr], FloatArr] | None = None) -> dict[str, Any]:
    """Monte Carlo closure: MTOW (and range, if range_fn(W_dg, E_Wh) is given) distributions at fixed payload."""
    post = calibration.posterior if calibration else Posterior.from_priors()
    ev = Evaluator.create(cfg, post)
    rng = np.random.default_rng(seed)
    v = ev.sample(n_samples, rng)
    w = np.full(n_samples, cfg.mass_guess_kg)
    damp = np.ones(n_samples)
    last = np.zeros(n_samples)
    done = np.zeros(n_samples, dtype=bool)
    diverged = np.zeros(n_samples, dtype=bool)
    w_cap = W_CAP_FACTOR * cfg.mass_guess_kg
    it = 0
    w_e = np.zeros(n_samples)
    e = np.zeros(n_samples)
    for it in range(1, max_iter + 1):
        w_eval = np.where(diverged, cfg.mass_guess_kg, w)       # frozen samples are evaluated at a safe mass
        e = np.asarray(energy_required(w_eval), dtype=float) * np.ones(n_samples)
        diverged |= ~np.isfinite(e) | (w > w_cap)
        e = np.where(diverged, np.nan, e)
        v["powertrain.pack.energy_kwh"] = np.where(diverged, 1.0, e / 1e3)
        _, w_e, _, _ = ev.evaluate(v, _loads_vec(cfg, w_eval), w_eval)
        delta = w_e + payload_kg - w
        done = (np.abs(delta) < tol) & ~diverged
        if (done | diverged).all():
            w = np.where(done, w_e + payload_kg, w)
            break
        damp = np.where((last != 0) & (np.sign(delta) != np.sign(last)), 0.5, damp)
        w = np.where(done | diverged, w, w + damp * delta)
        last = delta
    ok = done
    out: dict[str, Any] = {"iterations": it, "converged_fraction": float(ok.mean()),
                           "diverged_fraction": float(diverged.mean()), "n_samples": n_samples,
                           "payload_kg": payload_kg}
    if ok.any():
        out.update({"mtow": Dist.of(w[ok]).model_dump(), "empty_weight": Dist.of(w_e[ok]).model_dump(),
                    "energy_wh": Dist.of(e[ok]).model_dump()})
        if range_fn is not None:
            out["range"] = Dist.of(range_fn(w[ok], e[ok])).model_dump()
    if diverged.any():
        q = np.where(diverged, np.inf, np.where(ok, w, np.nan))
        out["mtow_quantiles_incl_diverged"] = {f"p{p}": float(np.nanpercentile(q, p)) for p in (10, 50, 90)}
        out["note"] = (f"{diverged.sum()} of {n_samples} samples did not close (weight snowball: infeasible mission "
                       f"energy or MTOW > {W_CAP_FACTOR:g} x guess). 'mtow' covers closed samples only; "
                       "'mtow_quantiles_incl_diverged' counts the diverged ones as unbounded.")
    return out


# --- coupled closure: the configuration itself changes with W_dg (full coupling to a sizing tool) ------------
ConfigAt = Callable[[float], "tuple[HS1Config, float]"]
GRID_LO, GRID_HI, GRID_N = 0.5, 3.0, 48     # coupled Monte Carlo mass grid, as multiples of the start mass


@dataclass
class CoupledResult(ClosureResult):
    config: HS1Config | None = None
    diverged: bool = False


def close_point_coupled(config_at: ConfigAt, w0: float, payload_kg: float, tol: float = 0.1, max_iter: int = 50,
                        n_samples: int = 2000, seed: int = 0,
                        calibration: CalibrationResult | str | Path | None = None,
                        final_report: bool = True) -> CoupledResult:
    """Point closure where every iteration rebuilds the configuration at the current W_dg.

    ``config_at(W)`` returns the HS1Config re-sized at W (geometry, power, pack energy) and the pack energy E.
    Iterates on the W_E P50 until |ΔW| < tol (max 50 iterations; damping 0.5 if oscillating). An infinite E
    (mission infeasible at that mass) stops the loop as diverged.
    """
    w = w0
    hist: list[dict[str, float]] = []
    damp, last_delta, converged, diverged = 1.0, 0.0, False, False
    e = w_e = 0.0
    cfg: HS1Config | None = None
    for it in range(1, max_iter + 1):
        cfg, e = config_at(w)
        if not np.isfinite(e):
            diverged = True
            hist.append({"iteration": it, "W_dg": w, "E_Wh": float("inf")})
            break
        rep = predict(cfg, n_samples, seed, calibration, explain=False)
        w_e = rep.empty_weight.p50
        w_new = w_e + payload_kg
        delta = w_new - w
        hist.append({"iteration": it, "W_dg": w, "E_Wh": e, "W_E_p50": w_e, "W_dg_new": w_new,
                     "wing_area_m2": cfg.wing.area_m2, "span_m": cfg.wing.span_m,
                     "power_kw": sum(m.p_cont_kw for m in cfg.powertrain.motors),
                     "fuselage_length_m": cfg.fuselage.length_m})
        if abs(delta) < tol:
            converged, w = True, w_new
            break
        if last_delta and np.sign(delta) != np.sign(last_delta):
            damp = 0.5
        w, last_delta = w + damp * delta, delta
    report = None
    if converged:
        cfg, e = config_at(w)
        if final_report:
            report = predict(cfg.model_copy(update={"payload_kg": payload_kg}), n_samples, seed, calibration)
    return CoupledResult(converged, len(hist), w, w_e, e, hist, report, cfg, diverged)


def close_mc_coupled(config_at: ConfigAt, w0: float, payload_kg: float, n_samples: int = 2000, seed: int = 0,
                     calibration: CalibrationResult | None = None, lo: float = GRID_LO, hi: float = GRID_HI,
                     n_grid: int = GRID_N) -> dict[str, Any]:
    """Monte Carlo closure with full coupling, solved on a mass grid.

    Re-sizing the aircraft for every sample on every iteration is too slow, so the re-sized configuration is
    tabulated on ``n_grid`` masses in [lo, hi] x w0. Every sample (same parameter and k draws at every grid
    point: common random numbers) is evaluated at each grid mass, giving its own residual
    r(W) = W_E(W) + payload - W. Its closed MTOW is the first downward zero crossing of r, by linear
    interpolation. Samples with no crossing inside the grid (weight snowball, or mission infeasible) are reported
    as diverged, never dropped silently.
    """
    post = calibration.posterior if calibration else Posterior.from_priors()
    grid_all = np.linspace(lo * w0, hi * w0, n_grid)
    cfgs: list[HS1Config] = []
    energies: list[float] = []
    for g in grid_all:
        c, e = config_at(float(g))
        if not np.isfinite(e):
            break                                   # E(W) is monotone: everything heavier is infeasible too
        cfgs.append(c)
        energies.append(e)
    grid = grid_all[:len(cfgs)]
    out: dict[str, Any] = {"method": "mass grid", "grid": {"lo_kg": float(grid_all[0]), "hi_kg": float(grid_all[-1]),
                                                           "n": n_grid, "feasible_up_to_kg": float(grid[-1])
                                                           if len(grid) else None},
                           "n_samples": n_samples, "payload_kg": payload_kg}
    if len(cfgs) < 2:
        out.update(converged_fraction=0.0, diverged_fraction=1.0,
                   note="mission infeasible over the whole mass grid: no sample can close")
        return out
    rng = np.random.default_rng(seed)
    v = Evaluator.create(cfgs[0], post).sample(n_samples, rng)
    we = np.empty((n_samples, len(cfgs)))
    for j, (c, e, g) in enumerate(zip(cfgs, energies, grid, strict=False)):
        ev = Evaluator.create(c, post)
        vj = dict(v)
        vj["powertrain.pack.energy_kwh"] = np.full(n_samples, e / 1e3)
        _, we[:, j], _, _ = ev.evaluate(vj, loads_at(c, float(g)), float(g))
    r = we + payload_kg - grid[None, :]
    cross = (r[:, :-1] >= 0) & (r[:, 1:] < 0)
    has = cross.any(axis=1)
    below = r[:, 0] < 0
    j0 = np.argmax(cross, axis=1)
    idx = np.arange(n_samples)
    r0, r1 = r[idx, j0], r[idx, j0 + 1]
    t = np.where(has, r0 / np.where(has, r0 - r1, 1.0), np.nan)
    w = np.where(has, grid[j0] + t * (grid[j0 + 1] - grid[j0]), np.nan)
    ok = has & ~below

    def at(vals: list[float]) -> FloatArr:
        a = np.asarray(vals, dtype=float)
        return np.asarray(a[j0] + t * (a[j0 + 1] - a[j0]), dtype=float)
    out.update(converged_fraction=float(ok.mean()), diverged_fraction=float((~has & ~below).mean()),
               below_grid_fraction=float(below.mean()))
    if ok.any():
        out.update({
            "mtow": Dist.of(w[ok]).model_dump(), "empty_weight": Dist.of(w[ok] - payload_kg).model_dump(),
            "energy_wh": Dist.of(at(energies)[ok]).model_dump(),
            "wing_area_m2": Dist.of(at([c.wing.area_m2 for c in cfgs])[ok]).model_dump(),
            "installed_power_kw": Dist.of(at([sum(m.p_cont_kw for m in c.powertrain.motors) for c in cfgs])[ok])
            .model_dump()})
    n_bad = int((~ok).sum())
    if n_bad:
        q = np.where(ok, w, np.inf)
        out["mtow_quantiles_incl_diverged"] = {f"p{p}": float(np.percentile(q, p)) for p in (10, 50, 90)}
        out["note"] = (f"{n_bad} of {n_samples} samples did not close inside the {grid_all[0]:.0f}–"
                       f"{grid_all[-1]:.0f} kg grid (weight snowball or infeasible mission). 'mtow' covers closed "
                       "samples only; 'mtow_quantiles_incl_diverged' counts the others as unbounded.")
    return out
