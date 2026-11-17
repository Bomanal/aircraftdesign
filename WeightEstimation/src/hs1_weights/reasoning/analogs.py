"""Analog retrieval and component-wise difference explanation (doc 09 §1, R11).

Feature space (standardised): log W_dg, log S, A, log q_D, n_ult, seats, powertrain, primary material, gear type,
year. Distance: Mahalanobis with the fleet covariance (pseudo-inverse). The k = 5 nearest calibration-eligible
records are returned, plus up to 2 tier-C electric analogs, labelled as such.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..calibration.fleet import (
    CERT_LEVEL,
    ELECTRIC_LIKE,
    MATERIALS,
    POWERTRAIN_GROUP,
    POWERTRAIN_LEVELS,
    component_material,
    era_of,
    fleet_components,
    fleet_context,
    fleet_uncalibrated,
)
from ..calibration.posterior import FloatArr, Posterior
from ..calibration.structure import effect_names_for
from ..components import K_GROUP, evaluate_tree
from ..errors import MissingInputError

GEARS = ("fixed_tricycle", "fixed_tailwheel", "retractable", "amphibian")
DRIVER_ORDER = ("n_ult", "W_dg", "S_w", "q")     # documented one-at-a-time attribution order (structure)
STRUCTURE = ("wing", "htail", "vtail", "fuselage", "main_gear", "nose_gear")


def _cat(value: str, levels: tuple[str, ...]) -> list[float]:
    return [1.0 if value == lv else 0.0 for lv in levels]


def features(w_dg: float, S: float, A: float, q_D: float, n_ult: float, seats: int, powertrain: str, material: str,
             gear: str, year: float) -> FloatArr:
    return np.array([math.log(w_dg), math.log(S), A, math.log(q_D), n_ult, float(seats)]
                    + _cat(powertrain, POWERTRAIN_LEVELS) + _cat(material, MATERIALS) + _cat(gear, GEARS)
                    + [float(year)])


def fleet_features(rows: pd.DataFrame) -> tuple[pd.DataFrame, FloatArr]:
    keep, X = [], []
    for _, r in rows.iterrows():
        vals = [r.get(k) for k in ("mtow", "wing_area", "span", "q_d", "n_limit_pos", "year")]
        if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals):
            continue
        w = r["design_weight_kg"] if pd.notna(r.get("design_weight_kg")) else r["mtow"]
        n_neg = r.get("n_limit_neg")
        n_ult = 1.5 * max(r["n_limit_pos"], abs(n_neg) if pd.notna(n_neg) else 0.0)
        X.append(features(w, r["wing_area"], r["span"] ** 2 / r["wing_area"], r["q_d"], n_ult, int(r["seats"]),
                          POWERTRAIN_GROUP[r["powertrain"]], r["material_primary"], r["gear"], r["year"]))
        keep.append(r)
    return pd.DataFrame(keep).reset_index(drop=True), np.array(X)


@dataclass
class Analog:
    id: str
    type_name: str
    distance: float
    tier_c: bool
    empty_weight_kg: float | None
    mtow_kg: float | None
    delta_by_component: dict[str, float] = field(default_factory=dict)
    delta_by_driver: dict[str, float] = field(default_factory=dict)
    explanation: str = ""


def retrieve(x_hs1: FloatArr, rows: pd.DataFrame, k: int = 5, k_tier_c: int = 2,
             ) -> tuple[list[tuple[pd.Series, float, bool]], dict[str, Any]]:
    """Nearest eligible records (+ tier-C electric). Also returns leverage info for rule C7."""
    fr, X = fleet_features(rows)
    if len(fr) < 2:
        return [], {"n_fleet": len(fr)}
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    Z, z = (X - mu) / sd, (x_hs1 - mu) / sd
    VI = np.linalg.pinv(np.cov(Z, rowvar=False))
    d = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", Z - z, VI, Z - z), 0.0))
    elig = fr["calibration_eligible"].to_numpy(bool)
    tier_c = (~elig) & fr["sigma_rel"].isna().to_numpy() & fr["powertrain"].isin(ELECTRIC_LIKE).to_numpy()
    out = [(fr.iloc[i], float(d[i]), False) for i in np.argsort(d) if elig[i]][:k]
    out += [(fr.iloc[i], float(d[i]), True) for i in np.argsort(d) if tier_c[i]][:k_tier_c]
    Xd = np.hstack([np.ones((len(Z), 1)), Z])
    H = np.linalg.pinv(Xd.T @ Xd)
    lev_fleet = np.einsum("ij,jk,ik->i", Xd, H, Xd)
    xz = np.concatenate([[1.0], z])
    lev = float(xz @ H @ xz)
    return out, {"n_fleet": len(fr), "leverage": lev, "leverage_mean": float(lev_fleet.mean())}


def analog_masses(row: pd.Series, post: Posterior) -> dict[str, float]:
    """L1 at the analog's own inputs with posterior-mean k (doc 09 §1)."""
    r = row.to_dict()
    w = r["design_weight_kg"] if pd.notna(r.get("design_weight_kg")) else r["mtow"]
    ctx, _ = fleet_context(r, float(w))
    comps = fleet_components(r)
    tree = evaluate_tree(ctx, only=comps)
    out = {}
    for c, res in tree.items():
        es = effect_names_for(K_GROUP[c], component_material(r, c), CERT_LEVEL[str(r.get("cert_used") or r["cert"])],
                              era_of(r.get("year")), None, POWERTRAIN_GROUP[r["powertrain"]], post.collapse_struct)
        out[c] = float(np.asarray(res.mass_kg)) * math.exp(sum(post.mean(e) for e in es))
    try:
        U, _ = fleet_uncalibrated(r)
        if U:
            out["electric_pack_and_motor"] = U
    except MissingInputError:
        pass
    return out


def explain(hs1_masses: Mapping[str, float], analog: Mapping[str, float], name: str) -> tuple[dict[str, float], str]:
    comps = sorted(set(hs1_masses) | set(analog))
    delta = {c: hs1_masses.get(c, 0.0) - analog.get(c, 0.0) for c in comps}
    total = sum(delta.values())
    parts = ", ".join(f"{c} {v:+.0f}" for c, v in sorted(delta.items(), key=lambda kv: -abs(kv[1])) if abs(v) >= 0.5)
    return delta, f"HS1 vs. {name}: {total:+.0f} kg = {parts}"


def driver_attribution(row: pd.Series, hs1_inputs: Mapping[str, float]) -> dict[str, float]:
    """One-at-a-time swap of analog → HS1 values in DRIVER_ORDER on the structural components (k = 1)."""
    r = row.to_dict()
    w = r["design_weight_kg"] if pd.notna(r.get("design_weight_kg")) else r["mtow"]
    ctx, _ = fleet_context(r, float(w))
    comps = tuple(c for c in STRUCTURE if c in fleet_components(r))

    def total(c: Any) -> float:
        try:
            return float(sum(np.asarray(v.mass_kg) for v in evaluate_tree(c, only=comps).values()))
        except MissingInputError:
            return float("nan")
    out, prev = {}, total(ctx)
    for drv in DRIVER_ORDER:
        if drv not in hs1_inputs:
            continue
        setattr(ctx, drv, hs1_inputs[drv])
        if drv == "W_dg":
            ctx.W_l = hs1_inputs[drv]
        if drv == "S_w":
            ctx.b = math.sqrt(hs1_inputs["A"] * hs1_inputs["S_w"])
        cur = total(ctx)
        out[drv] = cur - prev
        prev = cur
    return out
