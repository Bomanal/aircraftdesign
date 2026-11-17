"""Aircraft structure config: the iterable HS1 layout and structure inputs that eacsize does not model.

In conceptual design the HS1 detail behind the weight model is not known; it gets iterated. That detail covers:
- DEP split, quick-swap bays and equipment;
- materials, CG stations and tail shape;
- the bottom-up priors for motors, inverters, harness, thermal and mounts.

This module replaces the hand-written overlay (``examples/eacsize_overlay.json``) with a **catalogue of
parameters**. Each parameter is one of three kinds:

- ``choice``: categorical options, each with a short description;
- ``range``: one value inside [min, max], with the default centred on the eacsize run's own value where eacsize
  has one;
- ``band``: a [low, high] interval inside [min, max] for an uncertain bottom-up quantity. It maps to a uniform
  distribution, so the Monte Carlo carries the uncertainty.

A **selection** is a flat ``{parameter id: value}`` dict. Any parameter left out takes its default.
``build_overlay(selection, run)`` turns a selection into the overlay that
``adapters.eacsize.config_from_eacsize`` merges onto the eacsize-mapped fields.

Layout quantities are written **relative** to the aircraft, so they follow eacsize when it re-sizes the aircraft
in full coupling:
- stations as ``{"x_frac": f, "z_m": z}`` (x = f · fuselage length);
- lengths as ``{"frac": f, "of": "fuselage_length" | "span" | "semi_span"}``.

``resolve_relative`` converts them to metres against the fuselage and span of the configuration being built.

Standard library only by design: the eacsize dashboard (itself stdlib-only) loads this file directly with
``importlib`` to serve the catalogue and validate selections.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any

VERSION = 1
L_REF_M = 7.0            # fuselage length of examples/hs1_reference.json, where the placeholder stations were drawn
SEMI_SPAN_REF_M = 5.25   # semi-span of the same reference layout

GROUPS = ("Wing", "Empennage", "Fuselage", "DEP layout", "Motors & propellers", "Harness & bus", "Thermal",
          "Systems", "Battery bays", "Equipment", "Materials", "Mass & CG stations")

MATERIALS = (("composite_carbon", "Carbon composite", "Raymer composite factor prior applies"),
             ("composite_glass", "Glass composite", "Raymer composite factor prior applies"),
             ("composite_mixed", "Mixed composite", "Raymer composite factor prior applies"),
             ("metal_aluminium", "Aluminium", "baseline of the Raymer GA equations"),
             ("steel_tube_fabric", "Steel tube and fabric", "weakly informative prior"),
             ("wood", "Wood", "weakly informative prior"))

LAYOUTS = {
    "hs1_3": ("2 wing tractors + aft pusher", "HS1 reference DEP: wing_L, wing_R and an aft fuselage motor"),
    "wing_2": ("2 wing tractors", "wing_L and wing_R share the installed power equally"),
    "nose_1": ("Single nose tractor", "one fuselage motor at the nose"),
    "aft_1": ("Single aft pusher", "one fuselage motor at the tail"),
}
WING_LAYOUTS = ["hs1_3", "wing_2"]
FUS_LAYOUTS = ["hs1_3", "nose_1", "aft_1"]

# Equipment placeholders (examples/hs1_reference.json): name -> (category, mass kg, min, max, x_m, z_m)
EQUIPMENT = {
    "efis": ("avionics", 3.5, 1.5, 10.0, 1.2, 0.7), "com_radio": ("avionics", 1.5, 0.5, 4.0, 1.2, 0.7),
    "transponder": ("avionics", 1.0, 0.5, 3.0, 1.3, 0.6), "seats": ("seats", 16.0, 8.0, 30.0, 2.0, 0.3),
    "interior": ("interior", 12.0, 4.0, 30.0, 2.1, 0.5), "brs": ("brs", 12.0, 6.0, 25.0, 2.6, 0.9),
    "lv_battery": ("lv_battery", 3.0, 1.0, 8.0, 1.0, 0.4), "lighting": ("lighting", 1.5, 0.5, 5.0, 3.0, 0.5),
    "hv_protection": ("hv_protection", 4.0, 1.0, 10.0, 2.8, 0.3),
}
# CG stations that are not exposed as parameters: reference placeholders (x_m, z_m), scaled with fuselage length
FIXED_STATIONS = {
    "fuselage": (3.0, 0.6), "flight_controls": (3.5, 0.4), "avionics_installed": (1.25, 0.7),
    "electrical_lv": (1.5, 0.4), "ecs_anti_ice": (1.5, 0.4), "thermal_mgmt": (1.0, 0.3), "hv_harness": (3.0, 0.3),
    "inverters": (4.0, 0.5),
}
Z = {"wing": 0.1, "htail": 0.8, "vtail": 1.3, "main_gear": -0.4, "nose_gear": -0.4, "seat": 0.5, "wing_motor": 0.3,
     "fus_motor": 0.6, "bay": 0.2}

SRC_RUN = "eacsize run"
SRC_SCALED = "HS1 reference placeholder, scaled to the eacsize aircraft"
SRC_PH = "HS1 reference placeholder"


class StructureConfigError(ValueError):
    """Invalid selection; ``errors`` lists one message per offending parameter."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


# --- catalogue -------------------------------------------------------------------------------------------------
def _p(pid: str, label: str, group: str, kind: str, default: Any, source: str, unit: str = "", note: str = "",
       lo: float | None = None, hi: float | None = None, step: float | None = None,
       options: list[dict[str, str]] | None = None, requires: dict[str, list[str]] | None = None,
       follows_eacsize: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = {"id": pid, "label": label, "group": group, "kind": kind, "unit": unit, "default": default,
                         "source": source, "note": note}
    if kind in ("range", "band"):
        d.update(min=lo, max=hi, step=step)
    if options is not None:
        d["options"] = options
    if requires:
        d["requires"] = requires
    if follows_eacsize:
        d["follows_eacsize"] = True
    return d


def _opt(value: str, label: str, note: str = "") -> dict[str, str]:
    return {"value": value, "label": label, "note": note}


def _rel(x_m: float, ref: float = L_REF_M) -> float:
    return round(x_m / ref, 3)


def run_reference(run: dict[str, Any]) -> dict[str, float]:
    """The eacsize quantities the catalogue is centred on."""
    g = run["results"]["design"]["geometry"]
    span = float(run["results"]["summary"]["span_m"])
    return {"fus_len_m": float(g["fus_len"]), "span_m": span, "aspect_ratio": float(g["AR"]),
            "tail_arm_m": float(g["tail_arm"]), "mac_m": float(g["mac"])}


def helmbold_lift_slope(aspect_ratio: float) -> float:
    """Wing lift-curve slope (1/rad), Helmbold: a = 2πA / (2 + √(A² + 4)) (unswept, low Mach)."""
    return 2.0 * math.pi * aspect_ratio / (2.0 + math.sqrt(aspect_ratio ** 2 + 4.0))


def catalogue(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Every parameter with its kind, bounds or options, default (centred on the run where eacsize has a value)
    and source."""
    ref = run_reference(run)
    a0 = round(helmbold_lift_slope(ref["aspect_ratio"]), 2)
    arm = round(ref["tail_arm_m"] / ref["fus_len_m"], 3)
    xf = "× fuselage length"
    wing_req = {"dep.layout": WING_LAYOUTS}
    fus_req = {"dep.layout": FUS_LAYOUTS}
    W, E, F, D, M, H, T, S, B, Q, MT, C = GROUPS
    cat = [
        _p("wing.sweep_qc_deg", "Wing quarter-chord sweep", W, "range", 0.0, SRC_PH, "deg", lo=-5, hi=15, step=1),
        _p("wing.a_per_rad", "Wing lift-curve slope", W, "range", a0,
           f"eacsize run: Helmbold at AR {ref['aspect_ratio']:.2f}", "1/rad", "used for the gust load factor",
           lo=round(0.8 * a0, 2), hi=round(1.2 * a0, 2), step=0.05),
        _p("wing.x_lemac_frac", "Leading edge of MAC (x)", W, "range", _rel(2.05), SRC_SCALED, xf,
           "for CG in %MAC", lo=0.15, hi=0.45, step=0.005),
        _p("tail.type", "Tail arrangement", E, "choice", "conventional", SRC_PH, options=[
            _opt("conventional", "Conventional", "horizontal tail on the fuselage"),
            _opt("t_tail", "T-tail", "Raymer H_t/H_v = 1: heavier vertical tail")]),
        _p("tail.arm_frac", "Tail arm", E, "range", arm, SRC_RUN, xf,
           "Left at the default, eacsize's own tail arm is used, and it follows re-sizing. A changed value overrides "
           "it, but eacsize's tail areas stay sized for its own arm.", lo=0.40, hi=0.75, step=0.005,
           follows_eacsize=True),
        _p("htail.aspect_ratio", "Horizontal tail aspect ratio", E, "range", 4.5, SRC_PH, lo=3.0, hi=6.5, step=0.1),
        _p("htail.taper", "Horizontal tail taper", E, "range", 0.7, SRC_PH, lo=0.3, hi=1.0, step=0.05),
        _p("htail.sweep_qc_deg", "Horizontal tail sweep", E, "range", 0.0, SRC_PH, "deg", lo=0, hi=20, step=1),
        _p("htail.tc", "Horizontal tail t/c", E, "range", 0.12, SRC_PH, lo=0.08, hi=0.16, step=0.005),
        _p("vtail.aspect_ratio", "Vertical tail aspect ratio", E, "range", 1.5, SRC_PH, lo=0.8, hi=2.5, step=0.1),
        _p("vtail.taper", "Vertical tail taper", E, "range", 0.5, SRC_PH, lo=0.3, hi=1.0, step=0.05),
        _p("vtail.sweep_qc_deg", "Vertical tail sweep", E, "range", 30.0, SRC_PH, "deg", lo=0, hi=50, step=1),
        _p("vtail.tc", "Vertical tail t/c", E, "range", 0.12, SRC_PH, lo=0.08, hi=0.16, step=0.005),
        _p("fuselage.structural_length_frac", "Fuselage structural length", F, "range", _rel(6.5), SRC_SCALED, xf,
           "Raymer L (flight controls); excludes spinner and tail cone", lo=0.75, hi=1.0, step=0.005),
        _p("dep.layout", "Propulsor layout", D, "choice", "hs1_3", SRC_PH,
           options=[_opt(k, v[0], v[1]) for k, v in LAYOUTS.items()]),
        _p("dep.wing_share", "Power share on the wing motors", D, "range", 0.6, SRC_PH, "of installed",
           "both wing motors together; the aft motor takes the rest", lo=0.2, hi=0.9, step=0.05,
           requires={"dep.layout": ["hs1_3"]}),
        _p("dep.wing_y_frac", "Wing motor spanwise station", D, "range", _rel(2.2, SEMI_SPAN_REF_M), SRC_SCALED,
           "× semi-span", "inertia relief and harness length", lo=0.15, hi=0.9, step=0.01, requires=wing_req),
        _p("dep.wing_motor_x_frac", "Wing motor station (x)", D, "range", _rel(1.6), SRC_SCALED, xf, lo=0.1, hi=0.5,
           step=0.005, requires=wing_req),
        _p("dep.wing_inverter", "Wing motor inverters", D, "choice", "nacelle", SRC_PH, requires=wing_req, options=[
            _opt("nacelle", "In the nacelle", "inverter mass on the wing (inertia relief)"),
            _opt("fuselage", "In the fuselage", "inverter mass at the inverters station")]),
        _p("dep.aft_motor_x_frac", "Aft motor station (x)", D, "range", _rel(6.6), SRC_SCALED, xf, lo=0.8, hi=1.0,
           step=0.005, requires={"dep.layout": ["hs1_3", "aft_1"]}),
        _p("dep.nose_motor_x_frac", "Nose motor station (x)", D, "range", 0.03, "assumed (no reference value)", xf,
           lo=0.0, hi=0.15, step=0.005, requires={"dep.layout": ["nose_1"]}),
        _p("motor.sp_motor_kw_per_kg", "Motor specific power", M, "band", [4.0, 6.0], SRC_PH, "kW/kg",
           "uniform between low and high; replace with the vendor datasheet", lo=2.0, hi=12.0, step=0.5),
        _p("motor.sp_inv_kw_per_kg", "Inverter specific power", M, "band", [12.0, 20.0], SRC_PH, "kW/kg",
           lo=5.0, hi=40.0, step=1.0),
        _p("prop.wing_mass_kg", "Wing propeller mass (each)", M, "band", [3.3, 4.7], SRC_PH, "kg",
           "placeholder N(4.0, 0.4) as a uniform band of equal spread", lo=1.0, hi=15.0, step=0.1, requires=wing_req),
        _p("prop.fus_mass_kg", "Fuselage propeller mass", M, "band", [5.0, 7.0], SRC_PH, "kg",
           "placeholder N(6.0, 0.6) as a uniform band of equal spread", lo=1.0, hi=20.0, step=0.1, requires=fus_req),
        _p("fairing.wing_kg", "Wing nacelle fairing mass (each)", M, "band", [1.5, 3.5], SRC_PH, "kg", lo=0.0, hi=8.0,
           step=0.1, requires=wing_req),
        _p("fairing.fus_kg", "Fuselage motor fairing mass", M, "band", [1.0, 3.0], SRC_PH, "kg", lo=0.0, hi=8.0,
           step=0.1, requires=fus_req),
        _p("bus.v", "HV bus voltage", H, "choice", "400", SRC_PH, "V", options=[
            _opt("270", "270 V", "lower voltage: heavier conductors"), _opt("400", "400 V", "HS1 reference"),
            _opt("800", "800 V", "lighter conductors, more insulation")]),
        _p("harness.conductor", "Conductor", H, "choice", "Cu", SRC_PH, options=[
            _opt("Cu", "Copper", "reference"), _opt("Al", "Aluminium", "lighter per ampacity, larger section")]),
        _p("harness.length_frac", "Conductor length per motor run", H, "range", _rel(7.0), SRC_SCALED, xf,
           "total conductor length per motor, as a fraction of fuselage length", lo=0.4, hi=2.0, step=0.05),
        _p("harness.j_allow_a_per_mm2", "Allowable current density", H, "band", [3.0, 6.0], SRC_PH, "A/mm²",
           lo=1.0, hi=10.0, step=0.5),
        _p("harness.f_ins", "Insulation and shielding factor", H, "band", [1.3, 1.8], SRC_PH, lo=1.0, hi=2.5,
           step=0.05),
        _p("thermal.cooling", "Cooling", T, "choice", "liquid", SRC_PH, options=[
            _opt("liquid", "Liquid", "pumps, coolant, radiators"), _opt("air", "Air", "ducts and fans")]),
        _p("thermal.k_th_kg_per_kw", "Thermal system mass per kW rejected", T, "band", [1.5, 3.0], SRC_PH, "kg/kW",
           lo=0.5, hi=6.0, step=0.1),
        _p("pack.heat_kw", "Pack heat at design C-rate (hot day)", T, "band", [2.0, 5.0], SRC_PH, "kW", lo=0.5,
           hi=15.0, step=0.5),
        _p("systems.lv_system_kg", "LV system mass", S, "band", [4.0, 8.0], SRC_PH, "kg", lo=1.0, hi=20.0, step=0.5),
        _p("systems.c_mount_kg_per_kn", "Motor mount mass per kN design load", S, "band", [0.4, 0.9], SRC_PH,
           "kg/kN", lo=0.1, hi=2.0, step=0.05),
        _p("bays.count", "Number of quick-swap battery bays", B, "choice", "2", SRC_PH,
           options=[_opt(str(n), f"{n} bay{'s' if n > 1 else ''}", "") for n in (1, 2, 3, 4)]),
        _p("bays.split", "Energy split between bays", B, "choice", "equal", SRC_PH, options=[
            _opt("equal", "Equal", "every bay holds the same energy"),
            _opt("forward", "Forward-biased", "first bay holds 1.5× the others"),
            _opt("aft", "Aft-biased", "last bay holds 1.5× the others")]),
        _p("bays.x_first_frac", "First bay station (x)", B, "range", _rel(2.2), SRC_SCALED, xf, lo=0.15, hi=0.6,
           step=0.005),
        _p("bays.pitch_frac", "Bay pitch", B, "range", _rel(0.5), SRC_SCALED, xf, lo=0.03, hi=0.2, step=0.005),
        _p("bays.hw_mass_per_bay_kg", "Bay hardware mass (each)", B, "band", [3.0, 6.0], SRC_PH, "kg", lo=1.0, hi=15.0,
           step=0.5),
        _p("bays.c_ret_kg_per_kn", "18 g retention structure per kN", B, "band", [0.15, 0.4], SRC_PH, "kg/kN",
           lo=0.05, hi=1.0, step=0.05),
        _p("bays.cutout_area_m2", "Total bay cut-out area", B, "range", 0.6, SRC_PH, "m²", lo=0.0, hi=2.0, step=0.05),
        _p("bays.k_cut", "Cut-out reinforcement factor", B, "band", [0.5, 2.0], SRC_PH, lo=0.2, hi=4.0, step=0.1),
        _p("equip.brs", "Ballistic recovery system", Q, "choice", "fitted", SRC_PH, options=[
            _opt("fitted", "Fitted", "BRS item included"), _opt("none", "Not fitted", "")]),
    ]
    for name, (catg, m, lo, hi, _x, _z) in EQUIPMENT.items():
        cat.append(_p(f"equip.{name}_kg", f"{name.replace('_', ' ')} mass", Q, "range", m, SRC_PH, "kg",
                      f"category {catg}; estimate until a vendor mass exists", lo=lo, hi=hi, step=0.1,
                      requires={"equip.brs": ["fitted"]} if name == "brs" else None))
    defaults_mat = {"wing": "composite_carbon", "htail": "composite_carbon", "vtail": "composite_carbon",
                    "fuselage": "composite_carbon", "gear": "metal_aluminium"}
    for comp, dm in defaults_mat.items():
        cat.append(_p(f"mat.{comp}", f"{comp} material", MT, "choice", dm, SRC_PH,
                      options=[_opt(v, lab, n) for v, lab, n in MATERIALS]))
    cat += [
        _p("mass.light_pilot_kg", "Light pilot (CG case)", C, "range", 60.0, SRC_PH, "kg", lo=45.0, hi=90.0, step=1.0),
        _p("cg.seat_x_frac", "Seat station (x)", C, "range", _rel(2.0), SRC_SCALED, xf, lo=0.15, hi=0.5, step=0.005),
        _p("cg.wing_x_frac", "Wing station (x)", C, "range", _rel(2.25), SRC_SCALED, xf, lo=0.15, hi=0.55,
           step=0.005),
        _p("cg.main_gear_x_frac", "Main gear station (x)", C, "range", _rel(2.85), SRC_SCALED, xf,
           "also the tip-over check position", lo=0.2, hi=0.7, step=0.005),
        _p("cg.nose_gear_x_frac", "Nose gear station (x)", C, "range", _rel(0.6), SRC_SCALED, xf, lo=0.0, hi=0.3,
           step=0.005),
        _p("cg.htail_x_frac", "Horizontal tail station (x)", C, "range", _rel(6.6), SRC_SCALED, xf, lo=0.75, hi=1.0,
           step=0.005),
        _p("cg.vtail_x_frac", "Vertical tail station (x)", C, "range", _rel(6.5), SRC_SCALED, xf, lo=0.75, hi=1.0,
           step=0.005),
    ]
    return cat


def default_selection(run: dict[str, Any]) -> dict[str, Any]:
    return {p["id"]: copy.deepcopy(p["default"]) for p in catalogue(run)}


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def applies(p: dict[str, Any], sel: dict[str, Any]) -> bool:
    """False when the parameter's ``requires`` condition (e.g. a DEP layout) is not met by the selection."""
    return all(str(sel.get(k)) in vals for k, vals in p.get("requires", {}).items())


def validate(selection: dict[str, Any] | None, run: dict[str, Any]) -> dict[str, Any]:
    """Complete, checked selection (defaults filled in). Raises StructureConfigError listing every problem."""
    cat = {p["id"]: p for p in catalogue(run)}
    sel = default_selection(run)
    errs: list[str] = []
    if selection is not None and not isinstance(selection, dict):
        raise StructureConfigError(["selection must be an object of {parameter id: value}"])
    for pid, v in (selection or {}).items():
        p = cat.get(pid)
        if p is None:
            errs.append(f"{pid}: unknown parameter")
            continue
        lo, hi = float(p.get("min") or 0.0), float(p.get("max") or 0.0)
        if p["kind"] == "choice":
            allowed = [o["value"] for o in p["options"]]
            if str(v) not in allowed:
                errs.append(f"{pid}: {v!r} is not one of {allowed}")
                continue
            sel[pid] = str(v)
        elif p["kind"] == "range":
            if not _num(v):
                errs.append(f"{pid}: expected a number, got {v!r}")
                continue
            if not lo <= float(v) <= hi:
                errs.append(f"{pid}: {v} outside [{lo}, {hi}] {p['unit']}".rstrip())
                continue
            sel[pid] = float(v)
        else:  # band
            if not (isinstance(v, (list, tuple)) and len(v) == 2 and all(_num(x) for x in v)):
                errs.append(f"{pid}: expected [low, high], got {v!r}")
                continue
            a, b = float(v[0]), float(v[1])
            if a > b:
                errs.append(f"{pid}: low {a} > high {b}")
                continue
            if not (lo <= a and b <= hi):
                errs.append(f"{pid}: [{a}, {b}] outside [{lo}, {hi}] {p['unit']}".rstrip())
                continue
            sel[pid] = [a, b]
    if not errs:
        if sel["cg.nose_gear_x_frac"] >= sel["cg.main_gear_x_frac"]:
            errs.append("cg.nose_gear_x_frac: nose gear must be ahead of the main gear")
        last = sel["bays.x_first_frac"] + (int(sel["bays.count"]) - 1) * sel["bays.pitch_frac"]
        if last > 0.95:
            errs.append(f"bays.pitch_frac: last bay at {last:.3f} × fuselage length is beyond 0.95; reduce "
                        "pitch, count or first-bay station")
    if errs:
        raise StructureConfigError(errs)
    return sel


def selection_hash(sel: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(sel, sort_keys=True).encode()).hexdigest()[:12]


def changed(sel: dict[str, Any], run: dict[str, Any]) -> list[str]:
    """Parameter ids whose selected value differs from the default (applicable parameters only)."""
    d = default_selection(run)
    cat = {p["id"]: p for p in catalogue(run)}
    return [k for k in d if applies(cat[k], sel) and json.dumps(sel[k]) != json.dumps(d[k])]


def power_shares(sel: dict[str, Any]) -> dict[str, float]:
    """Motor id -> share of installed power for the selected layout (sums to 1 by construction)."""
    lay = sel["dep.layout"]
    if lay == "hs1_3":
        w = float(sel["dep.wing_share"])
        return {"wing_L": w / 2, "wing_R": w / 2, "aft": 1.0 - w}
    if lay == "wing_2":
        return {"wing_L": 0.5, "wing_R": 0.5}
    return {"nose": 1.0} if lay == "nose_1" else {"aft": 1.0}


def bay_fractions(n: int, split: str) -> list[float]:
    w = [1.0] * n
    if n > 1 and split == "forward":
        w[0] = 1.5
    elif n > 1 and split == "aft":
        w[-1] = 1.5
    s = sum(w)
    return [x / s for x in w]


def _uni(band: list[float], note: str = "Aircraft structure config band") -> dict[str, Any]:
    a, b = band
    if a == b:
        return {"dist": "fixed", "value": a, "assumption": True, "note": note}
    return {"dist": "uniform", "low": a, "high": b, "assumption": True, "note": note}


def _st(x_frac: float, z_m: float) -> dict[str, float]:
    return {"x_frac": round(float(x_frac), 6), "z_m": z_m}


def _len(frac: float, of: str = "fuselage_length") -> dict[str, Any]:
    return {"frac": round(float(frac), 6), "of": of}


def build_overlay(selection: dict[str, Any] | None, run: dict[str, Any]) -> dict[str, Any]:
    """Overlay for ``adapters.eacsize.config_from_eacsize`` from a (partial) selection."""
    s = validate(selection, run)
    h, ch = selection_hash(s), changed(s, run)
    shares = power_shares(s)
    wing_inv = s["dep.wing_inverter"] == "nacelle"
    motors, harness = [], []
    for mid, share in shares.items():
        on_wing = mid.startswith("wing")
        m: dict[str, Any] = {
            "id": mid, "location": "wing" if on_wing else "fuselage", "power_share": share,
            "inverter_in_nacelle": wing_inv if on_wing else False,
            "sp_motor_kw_per_kg": _uni(s["motor.sp_motor_kw_per_kg"]),
            "sp_inv_kw_per_kg": _uni(s["motor.sp_inv_kw_per_kg"]),
            "prop_mass_kg": _uni(s["prop.wing_mass_kg" if on_wing else "prop.fus_mass_kg"]),
            "fairing_mass_kg": _uni(s["fairing.wing_kg" if on_wing else "fairing.fus_kg"]),
        }
        if on_wing:
            m["y_m"] = _len(s["dep.wing_y_frac"], "semi_span")
            m["station"] = _st(s["dep.wing_motor_x_frac"], Z["wing_motor"])
        else:
            x = s["dep.nose_motor_x_frac"] if mid == "nose" else s["dep.aft_motor_x_frac"]
            m["station"] = _st(x, Z["fus_motor"])
        motors.append(m)
        harness.append({"motor": mid, "length_m": _len(s["harness.length_frac"]), "conductor": s["harness.conductor"],
                        "j_allow_a_per_mm2": _uni(s["harness.j_allow_a_per_mm2"]), "f_ins": _uni(s["harness.f_ins"])})
    n_bays = int(s["bays.count"])
    bays = [{"station": _st(s["bays.x_first_frac"] + i * s["bays.pitch_frac"], Z["bay"]), "energy_fraction": f}
            for i, f in enumerate(bay_fractions(n_bays, s["bays.split"]))]
    brs = s["equip.brs"] == "fitted"
    equipment = [{"name": n, "category": c, "mass_kg": s[f"equip.{n}_kg"], "estimate": True,
                  "station": _st(_rel(x), z)} for n, (c, _m, _lo, _hi, x, z) in EQUIPMENT.items()
                 if brs or n != "brs"]
    stations = {k: _st(_rel(x), z) for k, (x, z) in FIXED_STATIONS.items()}
    stations.update(wing=_st(s["cg.wing_x_frac"], Z["wing"]), htail=_st(s["cg.htail_x_frac"], Z["htail"]),
                    vtail=_st(s["cg.vtail_x_frac"], Z["vtail"]),
                    main_gear=_st(s["cg.main_gear_x_frac"], Z["main_gear"]),
                    nose_gear=_st(s["cg.nose_gear_x_frac"], Z["nose_gear"]))
    ov: dict[str, Any] = {
        "_comment": f"Generated by hs1_weights.structure_config v{VERSION} from selection {h}",
        "wing": {"sweep_qc_deg": s["wing.sweep_qc_deg"], "a_per_rad": s["wing.a_per_rad"],
                 "x_lemac_m": _len(s["wing.x_lemac_frac"])},
        "htail": {k: s[f"htail.{k}"] for k in ("aspect_ratio", "taper", "sweep_qc_deg", "tc")},
        "vtail": {k: s[f"vtail.{k}"] for k in ("aspect_ratio", "taper", "sweep_qc_deg", "tc")},
        "t_tail": s["tail.type"] == "t_tail",
        "fuselage": {"structural_length_m": _len(s["fuselage.structural_length_frac"])},
        "gear": {"x_main_m": _len(s["cg.main_gear_x_frac"]), "x_nose_m": _len(s["cg.nose_gear_x_frac"])},
        "powertrain": {
            "motors": motors, "pack": {"heat_kw": _uni(s["pack.heat_kw"])}, "v_bus": float(s["bus.v"]),
            "harness": harness,
            "thermal": {"cooling": s["thermal.cooling"], "k_th_kg_per_kw": _uni(s["thermal.k_th_kg_per_kw"])},
            "lv_system_kg": _uni(s["systems.lv_system_kg"]),
            "c_mount_kg_per_kn": _uni(s["systems.c_mount_kg_per_kn"]),
        },
        "battery_bays": {"bays": bays, "hw_mass_per_bay_kg": _uni(s["bays.hw_mass_per_bay_kg"]),
                         "c_ret_kg_per_kn": _uni(s["bays.c_ret_kg_per_kn"]),
                         "cutout_area_m2": s["bays.cutout_area_m2"], "k_cut": _uni(s["bays.k_cut"])},
        "equipment": equipment, "brs_fitted": brs,
        "material_by_component": {c: s[f"mat.{c}"] for c in ("wing", "htail", "vtail", "fuselage", "gear")},
        "light_pilot_kg": s["mass.light_pilot_kg"], "seat_station": _st(s["cg.seat_x_frac"], Z["seat"]),
        "stations": stations,
        "provenance": [
            f"Aircraft structure config v{VERSION}: selection {h}; {len(ch)} of {len(s)} parameters changed from "
            f"default" + (f" ({', '.join(ch)})" if ch else " (all at placeholder-centred defaults)"),
            "structure config: stations, x_LEMAC, structural length, gear x and harness lengths are fractions of the "
            "fuselage length (wing motor y of the semi-span), resolved against eacsize's aircraft at each build",
        ],
    }
    if "tail.arm_frac" in ch:
        ov["tail_arm_m"] = _len(s["tail.arm_frac"])
    return ov


def resolve_relative(d: Any, fuselage_length_m: float, span_m: float) -> tuple[Any, int]:
    """Replace ``{"x_frac", "z_m"}`` stations and ``{"frac", "of"}`` lengths by metres. Returns (resolved, count)."""
    refs = {"fuselage_length": fuselage_length_m, "span": span_m, "semi_span": span_m / 2.0}
    n = 0

    def walk(v: Any) -> Any:
        nonlocal n
        if isinstance(v, dict):
            if set(v) == {"frac", "of"}:
                if v["of"] not in refs:
                    raise ValueError(f"relative length 'of' must be one of {sorted(refs)}, got {v['of']!r}")
                n += 1
                return float(v["frac"]) * refs[v["of"]]
            if "x_frac" in v:
                n += 1
                out = {k: walk(x) for k, x in v.items() if k != "x_frac"}
                out["x_m"] = float(v["x_frac"]) * fuselage_length_m
                return out
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v
    return walk(d), n


def load_selection(path_or_obj: Any) -> dict[str, Any]:
    """A selection from a file path or an object, either bare ``{id: value}`` or ``{"selection": {...}}``."""
    obj = path_or_obj
    if isinstance(path_or_obj, str):
        with open(path_or_obj, encoding="utf-8") as f:
            obj = json.load(f)
    if isinstance(obj, dict) and isinstance(obj.get("selection"), dict):
        obj = obj["selection"]
    if not isinstance(obj, dict):
        raise StructureConfigError(["selection must be an object of {parameter id: value}"])
    return dict(obj)


def describe(run: dict[str, Any], selection: dict[str, Any] | None = None) -> dict[str, Any]:
    """Catalogue, defaults and (validated) selection for a run, as served to the dashboard."""
    sel = validate(selection, run)
    return {"version": VERSION, "groups": list(GROUPS), "catalogue": catalogue(run),
            "defaults": default_selection(run), "selection": sel, "hash": selection_hash(sel),
            "changed": changed(sel, run), "reference": run_reference(run)}
