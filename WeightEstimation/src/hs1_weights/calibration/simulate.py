"""SYNTHETIC fleet generator for simulation studies and tests (recover known k; check identifiability).

Output rows follow the ``normalized.parquet`` schema but are invented: ids start with ``synthetic_`` and the
manifest records ``synthetic=True``. Never use them as calibration data for HS1 (CLAUDE.md rule 2).
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from ..components.base import K_GROUP
from ..units import KT, RHO0
from .fleet import build_fleet_matrix

TRUE_K_DEFAULT = {"wing": 1.05, "tail": 0.95, "fuselage": 1.10, "gear": 0.90, "engine": 1.0, "fuel_system": 1.0,
                  "flight_controls": 1.0, "electrical": 0.9, "avionics": 1.0, "furnishings": 1.0, "ecs": 1.0}
COMPOSITE_K = {"wing": 0.85, "tail": 0.83, "fuselage": 0.90, "gear": 0.95}


def _row(i: int, rng: np.random.Generator, composite: bool, censored: bool) -> dict[str, Any]:
    mtow = 600.0 if censored else float(rng.uniform(650.0, 1300.0))
    ws = rng.uniform(520.0, 800.0)
    S = mtow * 9.80665 / ws
    A = rng.uniform(6.5, 9.0)
    b = math.sqrt(A * S)
    vc = rng.uniform(95.0, 135.0) * KT
    vne = vc * rng.uniform(1.35, 1.5)
    L = rng.uniform(6.2, 7.8)
    W, H = rng.uniform(1.05, 1.25), rng.uniform(1.2, 1.4)
    from ..data.derive import fuselage_wetted_area_torenbeek
    mat = "composite_glass" if composite else "metal_aluminium"
    cert = "astm_f2245_lsa" if censored else "far23_normal"
    n_pos = 4.0 if censored else 3.8
    return {
        "id": f"synthetic_{i:03d}", "manufacturer": f"SynthCo{i % 7}", "type_name": f"Synthetic {i}",
        "variant": "S", "year": int(rng.integers(1965, 2020)), "cert": cert, "cert_used": cert, "seats": 2,
        "status": "production", "powertrain": "piston_avgas", "n_engines": 1, "material_primary": mat,
        "material_by_component": json.dumps({"gear": "metal_aluminium"} if composite else {}),
        "gear": "fixed_tricycle", "high_wing": False, "pressurized": False, "t_tail": False, "fuel_type": "avgas",
        "fuel_tanks": 2, "sigma_rel": 0.03, "mtow_censored": censored, "design_weight_kg": None if censored else mtow,
        "design_weight_latent": censored, "calibration_eligible": True, "mtow": mtow, "empty_weight": np.nan,
        "landing_weight": None, "n_limit_pos": n_pos, "n_limit_neg": -0.4 * n_pos if not censored else -2.0,
        "v_c": vc, "v_d": vne / 0.9, "v_h": vc * 1.08, "q_c": 0.5 * RHO0 * vc ** 2,
        "q_d": 0.5 * RHO0 * (vne / 0.9) ** 2,
        "wing_area": S, "span": b, "sweep_qc": 0.0, "taper": float(rng.uniform(0.5, 1.0)), "root_tc": 0.15,
        "htail_area": 0.19 * S, "htail_aspect_ratio": 4.5, "htail_sweep_qc": 0.0, "htail_taper": 0.7,
        "htail_tc": 0.12, "vtail_area": 0.10 * S, "vtail_aspect_ratio": 1.5, "vtail_sweep_qc": 0.5, "vtail_taper": 0.5,
        "vtail_tc": 0.12, "tail_arm": 0.62 * L, "fuselage_length": L, "fuselage_structural_length": 0.92 * L,
        "fuselage_max_width": W, "fuselage_max_height": H,
        "fuselage_wetted_area": fuselage_wetted_area_torenbeek(L, W, H),
        "main_gear_length": 0.5, "nose_gear_length": 0.45, "engine_dry_mass": float(rng.uniform(60.0, 120.0)),
        "fuel_capacity_total": float(rng.uniform(80.0, 200.0)), "fuel_capacity_usable": np.nan,
        "fuel_integral_volume": 0.0, "avionics_uninstalled_mass": float(rng.uniform(5.0, 15.0)),
    }


def simulate_fleet(n: int, seed: int = 0, frac_composite: float = 0.4, n_breakdown: int = 25,
                   n_censored: int = 0, sigma_model: float = 0.04,
                   true_k: Mapping[str, float] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Return (rows, component breakdowns, truth). truth holds the k used per group and material."""
    rng = np.random.default_rng(seed)
    tk = dict(TRUE_K_DEFAULT, **(true_k or {}))
    rows = []
    for i in range(n):
        r = _row(i, rng, rng.random() < frac_composite, i < n_censored)
        r["fuel_capacity_usable"] = 0.95 * r["fuel_capacity_total"]
        rows.append(r)
    df = pd.DataFrame(rows)
    df["empty_weight"] = 1.0           # placeholder so the fleet matrix can be built
    fm = build_fleet_matrix(df)
    comps = []
    w_true_u = rng.uniform(0.0, 0.15, n)          # latent structural margin for censored rows
    for j in range(fm.n):
        ui = int(round(w_true_u[j] / 0.02)) if fm.latent[j] else 0
        total = 0.0
        masses = {}
        for c_i, c in enumerate(fm.comps):
            if not fm.present[j, c_i]:
                continue
            g = K_GROUP[c]
            k = tk[g] * (COMPOSITE_K[g] if g in COMPOSITE_K and fm.material[j].get(c, "").startswith("composite")
                         else 1.0)
            masses[c] = k * fm.F[j, c_i, ui]
            total += masses[c]
        df.loc[df["id"] == fm.ids[j], "empty_weight"] = total * math.exp(rng.normal(0.0, sigma_model)
                                                                         + rng.normal(0.0, 0.03))
        if j < n_breakdown:
            for c in ("wing", "fuselage", "htail", "vtail", "main_gear", "nose_gear"):
                comps.append({"id": fm.ids[j], "component": c, "mass_kg": masses[c] * math.exp(rng.normal(0, 0.02)),
                              "source": "synthetic", "tier": "A", "material": fm.material[j].get(c, ""),
                              "calibration_eligible": True})
    cdf = pd.DataFrame(comps, columns=["id", "component", "mass_kg", "source", "tier", "material",
                                       "calibration_eligible"])
    return df, cdf, {"true_k": tk, "composite_k": COMPOSITE_K, "sigma_model": sigma_model, "synthetic": True}
