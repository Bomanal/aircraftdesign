"""Consistency rules C1–C9 (doc 09 §2). Rules flag; they never modify a number in the numeric path.

A rule that cannot be evaluated (e.g. no fleet data yet) returns an ``info`` flag saying so, rather than passing
silently.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .. import certification as cert
from ..calibration.fleet import POWERTRAIN_GROUP
from ..data.normalize import seats_class


@dataclass
class Flag:
    id: str
    severity: str           # info | warn | error
    message: str
    values: dict[str, Any] = field(default_factory=dict)


def c1_analog_cell(we_mtow_p50: float, fleet: pd.DataFrame | None, seats: int, material: str) -> Flag:
    if fleet is None or fleet.empty:
        return Flag("C1", "info", "C1 not evaluated: no fleet dataset")
    cell = fleet[(fleet["calibration_eligible"]) & (fleet["seats"].map(seats_class) == seats_class(seats))
                 & (fleet["material_primary"] == material)]
    if len(cell) < 5:
        return Flag("C1", "info", f"C1 not evaluated: analog cell has {len(cell)} < 5 records")
    r = (cell["empty_weight"] / cell["mtow"]).to_numpy()
    lo, hi = np.percentile(r, [5, 95])
    sev = "warn" if not lo <= we_mtow_p50 <= hi else "info"
    return Flag("C1", sev, f"W_E/MTOW {we_mtow_p50:.3f} vs analog cell [P5, P95] = [{lo:.3f}, {hi:.3f}]",
                {"value": we_mtow_p50, "p5": float(lo), "p95": float(hi), "n": len(cell)})


def c2_component_fractions(fractions: Mapping[str, float], fleet_components: pd.DataFrame | None,
                           fleet: pd.DataFrame | None) -> Flag:
    if fleet_components is None or fleet_components.empty or fleet is None:
        return Flag("C2", "info", "C2 not evaluated: no published component breakdowns")
    ew = fleet.set_index("id")["empty_weight"]
    out = {}
    for c, f in fractions.items():
        sub = fleet_components[fleet_components["component"] == c]
        if len(sub) < 5:
            continue
        fr = sub["mass_kg"].to_numpy() / ew.loc[sub["id"]].to_numpy()
        z = (f - fr.mean()) / fr.std() if fr.std() > 0 else 0.0
        if abs(z) > 2:
            out[c] = {"fraction": f, "class_mean": float(fr.mean()), "z": float(z)}
    if not out:
        return Flag("C2", "info", "C2: calibrated component fractions within 2σ of class (where evaluable)")
    return Flag("C2", "warn", f"component fractions > 2σ from class: {sorted(out)}", out)


def c3_stall(mtow: float, area: float, cl_clean: float, cl_land: float) -> Flag:
    vs1 = cert.stall_speed(mtow, area, cl_clean) / cert.KT
    vs0 = cert.stall_speed(mtow, area, cl_land) / cert.KT
    bad = vs1 > cert.SPORT_PILOT_VS1_MAX.value or vs0 > 61.0
    return Flag("C3", "error" if bad else "info",
                f"V_S1 {vs1:.1f} KCAS (limit 59), V_S0 {vs0:.1f} KCAS (limit 61) at MTOW {mtow:.0f} kg, power idle",
                {"VS1_kt": vs1, "VS0_kt": vs0, "mtow_kg": mtow})


def c4_vc_min(all_checks: Mapping[str, Mapping[str, bool]]) -> Flag:
    bad = [b for b, c in all_checks.items() if not c.get("vc_meets_min", True)]
    return Flag("C4", "error" if bad else "info",
                f"V_C below V_C,min for {bad}" if bad else "V_C meets V_C,min for all bases", {"bases": bad})


def c5_pack(pack_wh_per_kg: float, e_spec: float, level: str) -> Flag:
    bad = pack_wh_per_kg > e_spec * (1 + 1e-9)
    return Flag("C5", "error" if bad else "info",
                f"pack-level {pack_wh_per_kg:.0f} Wh/kg vs configured {level}-level {e_spec:.0f} Wh/kg",
                {"pack_wh_per_kg": pack_wh_per_kg, "configured": e_spec})


def c6_assumption_variance(st: Mapping[str, float] | None, assumption_inputs: set[str]) -> Flag:
    if st is None:
        return Flag("C6", "info", "C6 not evaluated: Sobol indices not computed")
    big = {k: v for k, v in st.items() if k in assumption_inputs and v > 0.10}
    if big:
        return Flag("C6", "warn", f"placeholder priors drive > 10% of W_E variance: {sorted(big)}", big)
    return Flag("C6", "info", "no placeholder prior contributes > 10% of W_E variance")


def c7_leverage(info: Mapping[str, Any]) -> Flag:
    if "leverage" not in info:
        return Flag("C7", "info", "C7 not evaluated: fewer than 2 fleet records with features")
    lev, mean = info["leverage"], info["leverage_mean"]
    if lev > 3 * mean:
        return Flag("C7", "info", f"extrapolating: leverage {lev:.2f} > 3 × fleet mean {mean:.2f}",
                    {"leverage": lev, "mean": mean})
    return Flag("C7", "info", f"inside fleet support: leverage {lev:.2f} (fleet mean {mean:.2f})",
                {"leverage": lev, "mean": mean})


def c8_unverified(governing: Mapping[str, list[str]]) -> Flag:
    """governing: quantity -> criterion names behind it, e.g. ["ASTM_F2245.safety_factor", ...]."""
    unverified = cert.unverified_criteria()
    used = {q: [u.split(":")[0] for u in unverified if any(u.startswith(c + ":") or u.startswith(c + ".")
                                                         for c in crits)]
            for q, crits in governing.items()}
    used = {q: v for q, v in used.items() if v}
    msg = (f"governing load cases use criteria flagged verify=True: {sorted(used)}" if used
           else "governing load cases use only verified criteria")
    return Flag("C8", "info", msg, used)


def c9_bays(bay_kg: float, energy_kwh: float, band: tuple[float, float] | None) -> Flag:
    v = bay_kg / energy_kwh
    if band is None:
        return Flag("C9", "info", f"C9 not evaluated: no plausibility band configured (bays {v:.2f} kg/kWh)",
                    {"kg_per_kwh": v})
    bad = not band[0] <= v <= band[1]
    return Flag("C9", "warn" if bad else "info", f"battery bays {v:.2f} kg/kWh vs band {band}",
                {"kg_per_kwh": v, "band": list(band)})


def powertrain_group(p: str) -> str:
    return POWERTRAIN_GROUP.get(p, p)
