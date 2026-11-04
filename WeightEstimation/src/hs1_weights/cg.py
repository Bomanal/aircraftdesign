"""CG from component masses and stations, for the doc 10 §3 loading cases (R13, P5-3).

Stations come from layout input; a missing station raises MissingInputError naming it (no default fractions:
doc 12 SC-13). CG is computed per Monte Carlo sample and reported as P10/P50/P90 in metres and %MAC.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from .calibration.posterior import FloatArr
from .components import ComponentResult
from .config import HS1Config, Station
from .errors import MissingInputError

ITEMISED = {"furnishings", "fixed_items", "battery_pack", "battery_bays", "motors", "inverters", "propellers",
            "nacelles_mounts"}


def _st(cfg: HS1Config, key: str) -> Station:
    if cfg.stations is None or key not in cfg.stations:
        raise MissingInputError(f"stations.{key}", "cg")
    return cfg.stations[key]


def mass_items(cfg: HS1Config, masses: Mapping[str, FloatArr], tree: Mapping[str, ComponentResult],
               ) -> list[tuple[str, FloatArr, float, float]]:
    """(name, mass samples, x, z) for every mass in W_E."""
    out: list[tuple[str, FloatArr, float, float]] = []
    for c, m in masses.items():
        if c in ITEMISED or (c == "avionics_installed" and tree[c].method == "equipment_list"):
            continue
        s = _st(cfg, c)
        out.append((c, m, s.x_m, s.z_m))
    n = len(next(iter(masses.values())))
    scale = {c: masses[c] / np.maximum(np.asarray(tree[c].mass_kg, dtype=float), 1e-12) for c in masses}
    for e in cfg.equipment:
        comp = {"seats": "furnishings", "interior": "furnishings", "avionics": "avionics_installed"}.get(
            e.category, "fixed_items")
        if comp == "avionics_installed" and tree[comp].method != "equipment_list":
            continue
        if e.station is None:
            raise MissingInputError(f"equipment.{e.name}.station", "cg")
        each = np.broadcast_to(np.asarray(tree[comp].extra["each_kg"][e.name], dtype=float), (n,))
        out.append((e.name, each * scale[comp], e.station.x_m, e.station.z_m))
    bays = cfg.battery_bays.bays
    pack = masses["battery_pack"]
    for i, b in enumerate(bays):
        out.append((f"pack_bay{i}", pack * b.energy_fraction, b.station.x_m, b.station.z_m))
        out.append((f"bay_hw{i}", masses["battery_bays"] / len(bays), b.station.x_m, b.station.z_m))
    for comp in ("motors", "inverters", "propellers", "nacelles_mounts"):
        tot = np.maximum(np.asarray(sum(np.asarray(v, dtype=float) for v in _each(tree[comp]).values()),
                                    dtype=float), 1e-12)
        for mot in cfg.powertrain.motors:
            if comp == "inverters" and not mot.inverter_in_nacelle:
                s = _st(cfg, "inverters")
            else:
                if mot.station is None:
                    raise MissingInputError(f"powertrain.motors.{mot.id}.station", "cg")
                s = mot.station
            share = np.asarray(_each(tree[comp])[mot.id], dtype=np.float64) / tot
            out.append((f"{comp}:{mot.id}", masses[comp] * share, s.x_m, s.z_m))
    return out


def _each(r: ComponentResult) -> dict[str, Any]:
    if "each_kg" in r.extra:
        return dict(r.extra["each_kg"])
    mount, fair = r.extra["mount_kg"], r.extra["fairing_kg"]
    return {k: np.asarray(mount[k]) + np.asarray(fair[k]) for k in mount}


def _cg(items: list[tuple[str, FloatArr, float, float]]) -> tuple[FloatArr, FloatArr, FloatArr]:
    m = sum(i[1] for i in items)
    x = sum(i[1] * i[2] for i in items) / m
    z = sum(i[1] * i[3] for i in items) / m
    return np.asarray(m), np.asarray(x), np.asarray(z)


def _q(a: FloatArr) -> dict[str, float]:
    return {"p10": float(np.percentile(a, 10)), "p50": float(np.percentile(a, 50)), "p90": float(np.percentile(a, 90))}


def compute_cg(cfg: HS1Config, masses: Mapping[str, FloatArr], tree: Mapping[str, ComponentResult]) -> dict[str, Any]:
    items = mass_items(cfg, masses, tree)
    n = len(items[0][1])
    cases: dict[str, list[tuple[str, FloatArr, float, float]]] = {"empty": items}
    seat = cfg.seat_station
    if seat is None:
        raise MissingInputError("seat_station", "cg loading cases")
    occ = np.full(n, cfg.n_occupants * cfg.occupant_kg)
    cases[f"occupants_{cfg.n_occupants}x{cfg.occupant_kg:g}kg"] = items + [("occupants", occ, seat.x_m, seat.z_m)]
    if cfg.light_pilot_kg is None:
        raise MissingInputError("light_pilot_kg", "cg single-pilot-light case")
    cases["single_pilot_light"] = items + [("pilot", np.full(n, cfg.light_pilot_kg), seat.x_m, seat.z_m)]
    for i in range(len(cfg.battery_bays.bays)):
        cases[f"bay{i}_removed"] = [it for it in items if it[0] != f"pack_bay{i}"]
    mac = cfg.wing.cbar_m
    if mac is None:
        from .data.derive import mac_trapezoidal
        mac = mac_trapezoidal(cfg.wing.area_m2, cfg.wing.span_m, cfg.wing.taper)
    out: dict[str, Any] = {"cases": {}, "notes": []}
    x_main = cfg.gear.x_main_m
    for name, its in cases.items():
        m, x, z = _cg(its)
        rec: dict[str, Any] = {"mass_kg": _q(m), "x_m": _q(x), "z_m": _q(z)}
        if cfg.wing.x_lemac_m is not None:
            rec["pct_mac"] = _q(100.0 * (x - cfg.wing.x_lemac_m) / mac)
        if x_main is not None and cfg.gear.type.value == "fixed_tricycle":
            rec["tip_over_risk_frac"] = float(np.mean(x >= x_main))
        out["cases"][name] = rec
    if cfg.wing.x_lemac_m is not None:
        p50 = [c["pct_mac"]["p50"] for c in out["cases"].values()]
        out["range_pct_mac"] = {"min_p50": min(p50), "max_p50": max(p50)}
    else:
        out["notes"].append("wing.x_lemac_m not given: CG reported in metres only")
    if x_main is None:
        out["notes"].append("gear.x_main_m not given: tip-over check not evaluated")
    return out
