"""Normalization routines N1–N12 (doc 05).

Each routine is a pure function ``(record, context) -> (record', log_entries)``. Routines never mutate their
input; they return a deep copy. Running the pipeline twice gives the same records (idempotence, doc 08 §4).

The seed's single-record ``normalize()`` (N3 partial, N5, N9) is kept at the bottom for compatibility.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .. import certification as cert
from ..schema import (
    AircraftRecord,
    CertTag,
    EmptyWeightDef,
    GearType,
    Material,
    Powertrain,
    Source,
    Tier,
    V,
)
from ..units import AIRSPEED_UNITS, RHO0, UNIT_TO_SI, G, is_si, to_si
from .derive import aspect_ratio, fuselage_wetted_area_torenbeek, mac_trapezoidal, tc_from_airfoil

OIL_DENSITY = 0.88       # kg/L (N3)
UNUSABLE_FUEL_FRAC = 0.03  # of total fuel when unusable is not published (N3)
FUEL_DENSITY = {"avgas": 0.72, "jet_a": 0.80, "mogas": 0.74}  # kg/L (N3)

# Category ceilings that produce MTOW censoring (kg). Tolerance +/-1.5 kg (N5).
REGULATORY_CAPS = {
    300.0: "EU single-seat UL",
    450.0: "EU UL (legacy, 2-seat landplane)",
    472.5: "EU UL with ballistic rescue (legacy)",
    495.0: "EU UL amphibian (legacy)",
    525.0: "DE/AT UL variant",
    560.0: "EU UL (national 2-seat)",
    598.7: "US LSA 1,320 lb (pre-MOSAIC)",
    600.0: "EU UL 600 kg / CS-LSA",
    648.6: "US LSA seaplane 1,430 lb (pre-MOSAIC)",
    650.0: "CS-LSA seaplane",
    750.0: "CS-VLA / JAR-VLA",
}
CAP_TOL = 1.5

# Relative (1-sigma) measurement uncertainty on empty weight (N9)
TIER_SIGMA = {Tier.A: 0.02, Tier.B: 0.04, Tier.C: None}
DEF_SIGMA = {
    EmptyWeightDef.BASIC_GAMA: 0.0,
    EmptyWeightDef.LICENSED: 0.01,
    EmptyWeightDef.STANDARD_NO_OPTIONS: 0.03,
    EmptyWeightDef.MARKETING: 0.06,
    EmptyWeightDef.UNKNOWN: 0.03,
}
LICENSED_UNCORRECTED_SIGMA = 0.03
DRY_SIGMA_ADD = 0.01
PROTOTYPE_SIGMA = 0.04
ELECTRIC_PACK_UNCLEAR_SIGMA = 0.03   # N11: sigma_def >= 0.03
SPREAD_WARN = 0.03                   # N2: inter-source spread > 3% warns
OUTLIER_Z = 3.5                      # N10
CATEGORY_TAGS = (CertTag.ASTM_F2245, CertTag.EU_UL, CertTag.CS_VLA, CertTag.CS_LSA)
ELECTRIC_LIKE = (Powertrain.ELECTRIC, Powertrain.HYBRID_SERIES, Powertrain.HYBRID_PARALLEL)

# Plausibility bands for N1 km/h-vs-mph resolution (m/s). Used only to choose between two readings.
_SPEED_BANDS = {"v_s0": (12.0, 45.0), "v_s1": (12.0, 50.0), "v_c": (30.0, 160.0), "v_no": (30.0, 160.0),
                "v_ne": (35.0, 200.0), "v_h": (30.0, 180.0), "v_d": (35.0, 220.0)}
AMBIGUOUS_KMH_MPH = "km/h|mph"


# --- state ---------------------------------------------------------------------
@dataclass(frozen=True)
class NV:
    """A normalized value: SI, with provenance. Derived values carry derived_from + method (rule 6)."""
    value: float
    unit: str
    sources: tuple[str, ...]
    confidence: float = 0.9
    note: str = ""
    derived_from: tuple[str, ...] = ()
    method: str = ""


@dataclass(frozen=True)
class LogEntry:
    record_id: str
    routine: str
    field: str
    before: Any
    after: Any
    rule: str

    def to_dict(self) -> dict[str, Any]:
        return {"record_id": self.record_id, "routine": self.routine, "field": self.field,
                "before": self.before, "after": self.after, "rule": self.rule}


@dataclass
class NormRecord:
    id: str
    type_name: str
    variant: str
    manufacturer: str
    tcds_model: str
    year: int | None
    cert: CertTag
    seats: int
    status: str
    powertrain: Powertrain
    n_engines: int
    material_primary: Material
    material_by_component: dict[str, Material]
    gear: GearType
    high_wing: bool
    pressurized: bool
    t_tail: bool
    ballistic_parachute: bool
    ew_def: EmptyWeightDef
    ew_excludes_unusable: bool
    ew_includes_pack: bool | None
    fuel_type: str
    fuel_tanks: int | None
    airfoil_root: str
    airfoil_tip: str
    constant_chord_3view: bool
    fields: dict[str, NV]
    alternates: dict[str, list[NV]]
    components: list[tuple[str, NV, Material | None]]
    cert_categories: dict[str, NV]
    cert_used: str = ""
    sigma_parts: dict[str, float] = field(default_factory=dict)
    sigma_rel: float | None = None
    mtow_censored: bool = False
    cap_label: str = ""
    design_weight_kg: float | None = None
    design_weight_latent: bool = False
    ew_corrected: bool = False
    duplicate_of: str = ""
    outlier: bool = False
    outlier_z: float | None = None
    outlier_confirmed: bool = False
    calibration_eligible: bool = False
    ineligible_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def get(self, name: str) -> float | None:
        nv = self.fields.get(name)
        return None if nv is None else nv.value

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)


@dataclass
class NormContext:
    sources: dict[str, Source]
    confirmed_outliers: frozenset[str] = frozenset()

    def tier(self, source_id: str) -> Tier:
        s = self.sources.get(source_id)
        return s.tier if s is not None else Tier.C


Routine = Callable[[NormRecord, NormContext], tuple[NormRecord, list[LogEntry]]]

_SCALAR_FIELDS = (
    "mtow", "empty_weight", "structural_design_weight", "max_landing_weight", "fuel_capacity_usable",
    "fuel_capacity_total", "fuel_unusable", "fuel_integral_volume", "oil_capacity", "battery_energy_total",
    "battery_mass_installed", "avionics_uninstalled_mass", "wing_area", "span", "root_tc", "tip_tc", "taper",
    "sweep_qc", "fuselage_length", "fuselage_max_width", "fuselage_max_height", "htail_area", "vtail_area",
    "htail_span", "htail_taper", "htail_tc", "htail_sweep_qc", "vtail_span", "vtail_taper", "vtail_tc",
    "vtail_sweep_qc", "fuselage_structural_length",
    "tail_arm", "main_gear_length", "nose_gear_length", "fuselage_wetted_area", "n_limit_pos", "n_limit_neg",
    "v_ne", "v_c", "v_no", "v_d", "v_h", "v_s0", "v_s1", "rated_power_total", "engine_dry_mass",
)


def _nv(v: V) -> NV:
    return NV(v.value, v.unit, (v.source,), v.confidence, v.note)


def from_record(rec: AircraftRecord) -> NormRecord:
    """Lift a raw record into normalization state (values still in published units until N1)."""
    flds = {n: _nv(getattr(rec, n)) for n in _SCALAR_FIELDS if getattr(rec, n) is not None}
    return NormRecord(
        id=rec.id, type_name=rec.type_name, variant=rec.variant, manufacturer=rec.manufacturer,
        tcds_model=rec.tcds_model, year=rec.year_first_flight, cert=rec.cert, seats=rec.seats,
        status=rec.production_status, powertrain=rec.powertrain, n_engines=rec.n_motors_or_engines,
        material_primary=rec.material_primary, material_by_component=dict(rec.material_by_component),
        gear=rec.gear, high_wing=rec.high_wing, pressurized=rec.pressurized, t_tail=rec.t_tail,
        ballistic_parachute=rec.ballistic_parachute, ew_def=rec.empty_weight_def,
        ew_excludes_unusable=rec.empty_weight_excludes_unusable_fuel, ew_includes_pack=rec.empty_weight_includes_pack,
        fuel_type=rec.fuel_type, fuel_tanks=rec.fuel_tanks, airfoil_root=rec.airfoil_root,
        airfoil_tip=rec.airfoil_tip, constant_chord_3view=rec.constant_chord_3view, fields=flds,
        alternates={k: [_nv(v) for v in vs] for k, vs in rec.alternates.items()},
        components=[(c.name, _nv(c.mass), c.material) for c in rec.components],
        cert_categories={k: _nv(v) for k, v in rec.cert_categories.items()},
    )


def _copy(r: NormRecord) -> NormRecord:
    return copy.deepcopy(r)


# --- N1 units and airspeed conventions ----------------------------------------
def _convert(name: str, nv: NV, r: NormRecord, logs: list[LogEntry], speeds_si: dict[str, float]) -> NV:
    if is_si(nv.unit):
        return nv
    if nv.unit == AMBIGUOUS_KMH_MPH:
        lo, hi = _SPEED_BANDS.get(name, (10.0, 250.0))
        cands = {"km/h": nv.value / 3.6, "mph": nv.value * 0.44704}
        ok = [u for u, v in cands.items() if lo <= v <= hi and _speed_order_ok(name, v, speeds_si)]
        if len(ok) == 1:
            u = ok[0]
            note = (nv.note + f"; resolved {u} by plausibility").strip("; ")
            out = NV(cands[u], "m/s", nv.sources, nv.confidence, note)
            logs.append(LogEntry(r.id, "N1", name, f"{nv.value} {nv.unit}", out.value, f"km/h-vs-mph resolved as {u}"))
            return out
        out = NV(cands["km/h"], "m/s", nv.sources, min(nv.confidence, 0.5),
                 (nv.note + "; km/h-vs-mph unresolved, km/h assumed").strip("; "))
        r.warn(f"N1: {name} km/h-vs-mph ambiguous; confidence set <= 0.5")
        logs.append(LogEntry(r.id, "N1", name, f"{nv.value} {nv.unit}", out.value, "ambiguous; km/h; confidence<=0.5"))
        return out
    val, si = to_si(nv.value, nv.unit)
    note = nv.note
    if nv.unit == "KIAS":
        note = (note + "; IAS").strip("; ")
        r.warn(f"N1: {name} published as KIAS; IAS-CAS difference treated as negligible")
    out = NV(val, si, nv.sources, nv.confidence, note, nv.derived_from, nv.method)
    logs.append(LogEntry(r.id, "N1", name, f"{nv.value} {nv.unit}", f"{val} {si}", "unit conversion"))
    return out


def _speed_order_ok(name: str, v: float, known: dict[str, float]) -> bool:
    order = ["v_s0", "v_s1", "v_c", "v_ne", "v_d"]
    if name not in order:
        return True
    i = order.index(name)
    return all(not (order.index(k) < i and kv > v) and not (order.index(k) > i and kv < v)
               for k, kv in known.items() if k in order and k != name)


def n1_units(rec: NormRecord, ctx: NormContext) -> tuple[NormRecord, list[LogEntry]]:
    r = _copy(rec)
    logs: list[LogEntry] = []
    speeds_si = {k: nv.value * UNIT_TO_SI[nv.unit][0] for k, nv in r.fields.items()
                 if nv.unit in AIRSPEED_UNITS}
    for name in list(r.fields):
        r.fields[name] = _convert(name, r.fields[name], r, logs, speeds_si)
    for name, vs in r.alternates.items():
        r.alternates[name] = [_convert(name, v, r, logs, speeds_si) for v in vs]
    r.components = [(n, _convert(f"components.{n}", v, r, logs, speeds_si), m) for n, v, m in r.components]
    r.cert_categories = {k: _convert(f"cert_categories.{k}", v, r, logs, speeds_si)
                         for k, v in r.cert_categories.items()}
    return r, logs


# --- N2 variant resolution and de-duplication ---------------------------------
_TIER_RANK = {Tier.A: 0, Tier.B: 1, Tier.C: 2}


def n2_select(rec: NormRecord, ctx: NormContext) -> tuple[NormRecord, list[LogEntry]]:
    """Per-record source selection: highest tier, then latest date, then highest confidence."""
    r = _copy(rec)
    logs: list[LogEntry] = []
    for name, alts in r.alternates.items():
        cands = list(dict.fromkeys(([r.fields[name]] if name in r.fields else []) + list(alts)))
        r.alternates[name] = cands          # keep every candidate so re-runs see the same set
        if len(cands) < 2:
            if cands and name not in r.fields:
                r.fields[name] = cands[0]
            continue

        def key(nv: NV) -> tuple[int, str, float]:
            s = ctx.sources.get(nv.sources[0])
            date = s.accessed if s else ""
            return (_TIER_RANK[ctx.tier(nv.sources[0])], _neg_date(date), -nv.confidence)
        best = sorted(cands, key=key)[0]
        vals = [c.value for c in cands]
        spread = (max(vals) - min(vals)) / abs(best.value) if best.value else 0.0
        if r.fields.get(name) != best:
            cur = r.fields.get(name)
            logs.append(LogEntry(r.id, "N2", name, None if cur is None else cur.value, best.value,
                                 "selected highest tier / latest / highest confidence"))
        r.fields[name] = best
        if name == "empty_weight":
            r.sigma_parts["spread"] = spread if spread > SPREAD_WARN else 0.0
        if spread > SPREAD_WARN:
            r.warn(f"N2: {name} inter-source spread {spread:.1%} > 3%: sigma widened")
            logs.append(LogEntry(r.id, "N2", name, vals, spread, "inter-source spread > 3%"))
    return r, logs


def _neg_date(d: str) -> str:
    # sort ascending on a transformed string so the latest ISO date comes first
    return "".join(chr(0x10FFFF - ord(c)) for c in d) if d else "\U0010ffff"


def n2_dedup(records: list[NormRecord]) -> tuple[list[NormRecord], list[LogEntry]]:
    """Key (manufacturer, TCDS model, MTOW). Later duplicates are marked, never dropped."""
    out: list[NormRecord] = []
    logs: list[LogEntry] = []
    seen: dict[tuple[str, str, float], str] = {}
    for rec in records:
        r = _copy(rec)
        mtow = r.get("mtow")
        if r.manufacturer and r.tcds_model and mtow is not None:
            key = (r.manufacturer.lower(), r.tcds_model.lower(), round(mtow, 1))
            if key in seen:
                r.duplicate_of = seen[key]
                r.warn(f"N2: duplicate of {seen[key]} (same manufacturer, TCDS model, MTOW)")
                logs.append(LogEntry(r.id, "N2", "id", r.id, seen[key], "duplicate key"))
            else:
                seen[key] = r.id
        out.append(r)
    return out, logs


# --- N3 empty-weight definition -> basic empty (GAMA) --------------------------
def n3_empty_weight(rec: NormRecord, ctx: NormContext) -> tuple[NormRecord, list[LogEntry]]:
    r = _copy(rec)
    logs: list[LogEntry] = []
    ew = r.fields.get("empty_weight")
    sig = DEF_SIGMA[r.ew_def]
    if ew is None:
        r.sigma_parts["def"] = sig
        return r, logs
    if r.ew_corrected:      # idempotence: correction already applied
        return r, logs
    val, used, method = ew.value, list(ew.sources), []
    if r.ew_def == EmptyWeightDef.LICENSED:
        oil = r.fields.get("oil_capacity")
        if oil is not None:
            val += oil.value * OIL_DENSITY
            used += list(oil.sources)
            method.append("N3 licensed+oil")
            logs.append(LogEntry(r.id, "N3", "empty_weight", ew.value, val, "licensed + oil_L x 0.88"))
        else:
            sig = LICENSED_UNCORRECTED_SIGMA
            r.warn("licensed empty weight, oil quantity unknown: not corrected, variance inflated")
    dry_add = 0.0
    if r.ew_excludes_unusable:
        unus, tot = r.fields.get("fuel_unusable"), r.fields.get("fuel_capacity_total")
        dens = FUEL_DENSITY.get(r.fuel_type)
        if dens is None:
            r.warn("N3: dry empty weight but fuel_type unknown: not corrected")
            dry_add = LICENSED_UNCORRECTED_SIGMA
        elif unus is not None:
            val += unus.value * dens
            used += list(unus.sources)
            method.append("N3 dry+unusable(TCDS)")
            dry_add = DRY_SIGMA_ADD
        elif tot is not None:
            val += UNUSABLE_FUEL_FRAC * tot.value * dens
            used += list(tot.sources)
            method.append("N3 dry+3%total")
            dry_add = DRY_SIGMA_ADD
        else:
            r.warn("N3: dry empty weight, no unusable or total fuel: not corrected")
            dry_add = LICENSED_UNCORRECTED_SIGMA
        if method and method[-1].startswith("N3 dry"):
            logs.append(LogEntry(r.id, "N3", "empty_weight", ew.value, val, method[-1]))
    r.sigma_parts["def"] = math.sqrt(sig * sig + dry_add * dry_add)
    if method:
        r.fields["empty_weight"] = NV(val, "kg", tuple(dict.fromkeys(used)), ew.confidence, ew.note,
                                      ("empty_weight",) + tuple(f for f in ("oil_capacity", "fuel_unusable",
                                                                            "fuel_capacity_total") if f in r.fields),
                                      "+".join(method))
    r.ew_corrected = True
    return r, logs


# --- N4 fuel and oil quantities -------------------------------------------------
def n4_fuel_oil(rec: NormRecord, ctx: NormContext) -> tuple[NormRecord, list[LogEntry]]:
    r = _copy(rec)
    logs: list[LogEntry] = []
    if r.powertrain == Powertrain.ELECTRIC:
        return r, logs
    tot, usa = r.get("fuel_capacity_total"), r.get("fuel_capacity_usable")
    if tot is None and usa is not None:
        logs.append(LogEntry(r.id, "N4", "fuel_capacity_total", None, None, "missing; not inferred"))
    if usa is None and tot is not None:
        logs.append(LogEntry(r.id, "N4", "fuel_capacity_usable", None, None, "missing; not inferred"))
    if tot is not None and usa is not None and usa > tot:
        r.warn("N4: usable fuel exceeds total fuel")
    if r.get("oil_capacity") is None and r.powertrain != Powertrain.TURBOPROP:
        logs.append(LogEntry(r.id, "N4", "oil_capacity", None, None, "missing; take from POH/engine manual"))
    return r, logs


# --- N5 MTOW censoring and design weight ---------------------------------------
def detect_cap(mtow_kg: float) -> tuple[bool, str]:
    cap = min(REGULATORY_CAPS, key=lambda c: abs(mtow_kg - c))
    if abs(mtow_kg - cap) <= CAP_TOL:
        return True, REGULATORY_CAPS[cap]
    return False, ""


def n5_censoring(rec: NormRecord, ctx: NormContext) -> tuple[NormRecord, list[LogEntry]]:
    r = _copy(rec)
    logs: list[LogEntry] = []
    mtow = r.get("mtow")
    r.mtow_censored, r.cap_label, r.design_weight_kg, r.design_weight_latent = False, "", None, False
    if mtow is None:
        return r, logs
    censored, label = detect_cap(mtow)
    if r.cert in CATEGORY_TAGS and not censored:
        r.warn("category aircraft with MTOW off a known cap: check variant/market")
    r.mtow_censored, r.cap_label = censored, label
    if not censored:
        r.design_weight_kg = mtow
    else:
        sdw = r.get("structural_design_weight")
        if sdw is not None:
            r.design_weight_kg = sdw
            logs.append(LogEntry(r.id, "N5", "design_weight_kg", mtow, sdw, "censored: TCDS structural W_dg"))
        else:
            r.design_weight_latent = True
            r.warn(f"MTOW at regulatory cap ({label}): treated as lower bound; "
                   "load-dependent equations need structural design weight from TCDS/POH if available")
            logs.append(LogEntry(r.id, "N5", "design_weight_kg", mtow, None, "censored: latent W_dg (doc 07 §3.3)"))
    return r, logs


# --- N6 load-factor and certification inference --------------------------------
def _far23_normal_npos(w_kg: float) -> float:
    return cert.FAR23_L1.n_pos(w_kg)


N6_RULES: dict[CertTag, tuple[Callable[[float], float], Callable[[float], float], str]] = {
    CertTag.FAR23_NORMAL: (_far23_normal_npos, lambda n: -0.4 * n,
                           "FAR23 normal: min(3.8, 2.1+24000/(W+10000)); -0.4n"),
    CertTag.FAR23_UTILITY: (lambda w: 4.4, lambda n: -0.4 * n, "FAR23 utility 4.4; -0.4n (verify, doc 12)"),
    CertTag.FAR23_AEROBATIC: (lambda w: 6.0, lambda n: -0.5 * n, "FAR23 aerobatic 6.0; -0.5n"),
    CertTag.CS_VLA: (lambda w: 3.8, lambda n: -1.5, "CS-VLA 3.8 / -1.5 (verify, doc 12)"),
    CertTag.ASTM_F2245: (lambda w: 4.0, lambda n: -2.0, "ASTM F2245 4.0 / -2.0"),
    CertTag.EU_UL: (lambda w: 4.0, lambda n: -2.0, "EU UL 4.0 / -2.0 (national rules vary)"),
}


def n6_loads(rec: NormRecord, ctx: NormContext) -> tuple[NormRecord, list[LogEntry]]:
    r = _copy(rec)
    logs: list[LogEntry] = []
    mtow = r.get("mtow")
    tag = r.cert
    if r.cert_categories and mtow is not None:
        match = [k for k, nv in r.cert_categories.items() if abs(nv.value - mtow) <= CAP_TOL]
        if len(match) == 1:
            tag = CertTag(match[0])
        else:
            r.warn("N6: multi-category record but no unique category matches record MTOW")
    r.cert_used = tag.value
    if tag == CertTag.EU_UL:
        r.warn("N6: EU UL load factors vary by national rule")
    rule = N6_RULES.get(tag)
    if "n_limit_pos" not in r.fields:
        if rule is None:
            logs.append(LogEntry(r.id, "N6", "n_limit_pos", None, None, f"no N6 inference rule for {tag.value}"))
        elif mtow is None:
            logs.append(LogEntry(r.id, "N6", "n_limit_pos", None, None, "MTOW missing"))
        else:
            n = rule[0](mtow)
            r.fields["n_limit_pos"] = NV(n, "-", r.fields["mtow"].sources, 0.9, rule[2], ("cert", "mtow"), "N6")
            logs.append(LogEntry(r.id, "N6", "n_limit_pos", None, n, rule[2]))
    if "n_limit_neg" not in r.fields and rule is not None and "n_limit_pos" in r.fields:
        n2 = rule[1](r.fields["n_limit_pos"].value)
        r.fields["n_limit_neg"] = NV(n2, "-", r.fields["n_limit_pos"].sources, 0.9, rule[2],
                                     ("cert", "n_limit_pos"), "N6")
        logs.append(LogEntry(r.id, "N6", "n_limit_neg", None, n2, rule[2]))
    return r, logs


# --- N7 design speeds and dynamic pressure -------------------------------------
_VC_BASIS = {CertTag.FAR23_NORMAL: cert.FAR23_L1, CertTag.ASTM_F2245: cert.ASTM_F2245}


def n7_speeds(rec: NormRecord, ctx: NormContext) -> tuple[NormRecord, list[LogEntry]]:
    r = _copy(rec)
    logs: list[LogEntry] = []
    if "v_d" not in r.fields and "v_ne" in r.fields:
        vne = r.fields["v_ne"]
        r.fields["v_d"] = NV(vne.value / 0.9, "m/s", vne.sources, vne.confidence, "V_D = V_NE/0.9", ("v_ne",), "N7")
        logs.append(LogEntry(r.id, "N7", "v_d", None, vne.value / 0.9, "V_NE <= 0.9 V_D (legacy 23.1505)"))
    if "v_c" not in r.fields:
        if "v_no" in r.fields:
            vno = r.fields["v_no"]
            r.fields["v_c"] = NV(vno.value, "m/s", vno.sources, vno.confidence, "V_C = V_NO", ("v_no",), "N7")
            logs.append(LogEntry(r.id, "N7", "v_c", None, vno.value, "V_C = V_NO"))
        else:
            basis = _VC_BASIS.get(CertTag(r.cert_used) if r.cert_used else r.cert)
            m, s = r.get("mtow"), r.get("wing_area")
            if basis is not None and m is not None and s is not None:
                vc = basis.vc_min_coeff.value * math.sqrt(m * G / s)
                vh = r.get("v_h")
                if vh is not None:
                    vc = min(vc, 0.9 * vh)
                srcs = r.fields["mtow"].sources + r.fields["wing_area"].sources
                r.fields["v_c"] = NV(vc, "m/s", srcs, 0.5, f"formula minimum ({basis.key})",
                                     ("mtow", "wing_area"), "N7")
                r.warn(f"N7: V_C from {basis.key} formula minimum (no V_C or V_NO published)")
                logs.append(LogEntry(r.id, "N7", "v_c", None, vc, f"formula minimum {basis.key}"))
            else:
                logs.append(LogEntry(r.id, "N7", "v_c", None, None, "no V_C, V_NO or applicable formula"))
    for vname, qname in (("v_c", "q_c"), ("v_d", "q_d")):
        if vname in r.fields and qname not in r.fields:
            v = r.fields[vname]
            r.fields[qname] = NV(0.5 * RHO0 * v.value ** 2, "Pa", v.sources, v.confidence,
                                 "sea level EAS", (vname,), "N7")
    return r, logs


# --- N8 geometry derivation -----------------------------------------------------
def n8_geometry(rec: NormRecord, ctx: NormContext) -> tuple[NormRecord, list[LogEntry]]:
    r = _copy(rec)
    logs: list[LogEntry] = []
    f = r.fields

    def put(name: str, value: float, unit: str, deps: tuple[str, ...], method: str, note: str = "") -> None:
        srcs = tuple(dict.fromkeys(s for d in deps if d in f for s in f[d].sources))
        conf = min((f[d].confidence for d in deps if d in f), default=0.9)
        f[name] = NV(value, unit, srcs, conf, note, deps, method)
        logs.append(LogEntry(r.id, "N8", name, None, value, method))

    if "taper" not in f and r.constant_chord_3view:
        f["taper"] = NV(1.0, "-", (), 0.7, "constant chord identified from 3-view (flagged)", ("constant_chord_3view",),
                        "N8.taper_constant_chord")
        r.warn("N8: taper = 1.0 from 3-view constant-chord identification")
        logs.append(LogEntry(r.id, "N8", "taper", None, 1.0, "constant chord from 3-view"))
    if "span" in f and "wing_area" in f and "aspect_ratio" not in f:
        put("aspect_ratio", aspect_ratio(f["span"].value, f["wing_area"].value), "-", ("span", "wing_area"),
            "N8.aspect_ratio")
    for surf in ("htail", "vtail"):
        if f"{surf}_span" in f and f"{surf}_area" in f and f"{surf}_aspect_ratio" not in f:
            put(f"{surf}_aspect_ratio", aspect_ratio(f[f"{surf}_span"].value, f[f"{surf}_area"].value), "-",
                (f"{surf}_span", f"{surf}_area"), "N8.aspect_ratio")
    if "span" in f and "wing_area" in f and "taper" in f and "mac" not in f:
        put("mac", mac_trapezoidal(f["wing_area"].value, f["span"].value, f["taper"].value), "m",
            ("wing_area", "span", "taper"), "N8.mac_trapezoidal")
    if "fuselage_wetted_area" not in f and all(k in f for k in ("fuselage_length", "fuselage_max_width",
                                                                "fuselage_max_height")):
        try:
            sw = fuselage_wetted_area_torenbeek(f["fuselage_length"].value, f["fuselage_max_width"].value,
                                                f["fuselage_max_height"].value)
            put("fuselage_wetted_area", sw, "m2", ("fuselage_length", "fuselage_max_width", "fuselage_max_height"),
                "N8.swet_torenbeek", "approximate (Torenbeek)")
            r.warn("N8: fuselage wetted area from Torenbeek approximation")
        except ValueError as e:
            r.warn(f"N8: {e}")
    for side, fname in (("root", "root_tc"), ("tip", "tip_tc")):
        des = r.airfoil_root if side == "root" else r.airfoil_tip
        if fname not in f and des:
            tc = tc_from_airfoil(des)
            if tc is not None:
                f[fname] = NV(tc, "-", (), 0.95, f"from airfoil {des}", (f"airfoil_{side}",), "N8.tc_from_airfoil")
                logs.append(LogEntry(r.id, "N8", fname, None, tc, f"airfoil designation {des}"))
    return r, logs


# --- N9 measurement variance ---------------------------------------------------
def n9_variance(rec: NormRecord, ctx: NormContext) -> tuple[NormRecord, list[LogEntry]]:
    r = _copy(rec)
    logs: list[LogEntry] = []
    ew = r.fields.get("empty_weight")
    if ew is None:
        r.sigma_rel = None
        return r, logs
    tier = ctx.tier(ew.sources[0])
    base = TIER_SIGMA[tier]
    if base is None:
        r.sigma_rel = None
        r.warn("tier C empty weight: excluded from calibration, analog use only")
        return r, logs
    r.sigma_parts["tier"] = base
    r.sigma_parts.setdefault("def", DEF_SIGMA[r.ew_def])
    r.sigma_parts["proto"] = PROTOTYPE_SIGMA if r.status == "prototype" else 0.0
    if r.status == "prototype":
        r.warn("prototype: production empty weight typically grows")
    r.sigma_parts.setdefault("spread", 0.0)
    r.sigma_rel = _sigma_total(r.sigma_parts)
    return r, logs


def _sigma_total(p: dict[str, float]) -> float:
    return math.sqrt(sum(p.get(k, 0.0) ** 2 for k in ("tier", "def", "proto", "spread")))


# --- N10 outlier screening -------------------------------------------------------
def seats_class(seats: int) -> str:
    return "1-2" if seats <= 2 else "3-4" if seats <= 4 else "5-6" if seats <= 6 else "7+"


def n10_outliers(records: list[NormRecord], ctx: NormContext) -> tuple[list[NormRecord], list[LogEntry]]:
    out = [_copy(r) for r in records]
    logs: list[LogEntry] = []
    cells: dict[tuple[str, str, str], list[NormRecord]] = {}
    for r in out:
        ew, m = r.get("empty_weight"), r.get("mtow")
        if ew is None or m is None or r.sigma_rel is None:
            continue
        cells.setdefault((seats_class(r.seats), r.material_primary.value, r.powertrain.value), []).append(r)
    for cell, rs in cells.items():
        x = np.log(np.array([r.get("empty_weight") / r.get("mtow") for r in rs]))  # type: ignore[operator]
        med = float(np.median(x))
        mad = float(np.median(np.abs(x - med)))
        if len(rs) < 3 or mad == 0.0:
            for r in rs:
                logs.append(LogEntry(r.id, "N10", "empty_weight", None, None, f"cell {cell} too small/degenerate"))
            continue
        for r, xi in zip(rs, x, strict=False):
            z = 0.6745 * (xi - med) / mad
            r.outlier_z = float(z)
            r.outlier = abs(z) > OUTLIER_Z
            r.outlier_confirmed = r.id in ctx.confirmed_outliers
            if r.outlier:
                r.warn(f"N10: robust z = {z:.2f} in cell {cell}; reviewer confirmation required (never auto-dropped)")
                logs.append(LogEntry(r.id, "N10", "empty_weight", None, float(z), "|z| > 3.5 flagged"))
    return out, logs


# --- N11 electric-specific -------------------------------------------------------
def n11_electric(rec: NormRecord, ctx: NormContext) -> tuple[NormRecord, list[LogEntry]]:
    r = _copy(rec)
    logs: list[LogEntry] = []
    if r.powertrain not in ELECTRIC_LIKE:
        return r, logs
    if r.ew_includes_pack is not True and "empty_weight" in r.fields:
        r.warn("electric: confirm published empty weight includes installed pack")
        if r.sigma_rel is not None:
            r.sigma_parts["def"] = max(r.sigma_parts.get("def", 0.0), ELECTRIC_PACK_UNCLEAR_SIGMA)
            r.sigma_rel = _sigma_total(r.sigma_parts)
    f = r.fields
    if "battery_energy_total" in f and "battery_mass_installed" in f and "pack_wh_per_kg" not in f:
        whkg = f["battery_energy_total"].value / f["battery_mass_installed"].value
        f["pack_wh_per_kg"] = NV(whkg, "Wh/kg", f["battery_energy_total"].sources + f["battery_mass_installed"].sources,
                                 min(f["battery_energy_total"].confidence, f["battery_mass_installed"].confidence),
                                 "pack level", ("battery_energy_total", "battery_mass_installed"), "N11.pack_wh_kg")
        logs.append(LogEntry(r.id, "N11", "pack_wh_per_kg", None, whkg, "E/m_pack"))
    if "mtow" in f:
        mlw = f.get("max_landing_weight")
        if mlw is not None and abs(mlw.value - f["mtow"].value) > 1e-9:
            logs.append(LogEntry(r.id, "N11", "landing_weight", mlw.value, f["mtow"].value,
                                 "electric: landing weight = MTOW (published MLW not used)"))
        f["landing_weight"] = NV(f["mtow"].value, "kg", f["mtow"].sources, f["mtow"].confidence,
                                 "electric: landing weight = MTOW", ("mtow",), "N11.landing_eq_mtow")
    if r.powertrain in (Powertrain.HYBRID_SERIES, Powertrain.HYBRID_PARALLEL):
        if "fuel_capacity_usable" not in f and "fuel_capacity_total" not in f:
            r.warn("N11: hybrid without fuel capacity")
        if "battery_energy_total" not in f:
            r.warn("N11: hybrid without battery energy")
    return r, logs


# --- N12 calibration eligibility ------------------------------------------------
REQUIRED_FIELDS = ("mtow", "empty_weight", "wing_area", "span", "rated_power_total")


def n12_eligibility(rec: NormRecord, ctx: NormContext) -> tuple[NormRecord, list[LogEntry]]:
    r = _copy(rec)
    logs: list[LogEntry] = []
    reasons = []
    for k in REQUIRED_FIELDS:
        if k not in r.fields:
            reasons.append(f"missing {k}")
    vc = r.fields.get("v_c")
    vc_published = vc is not None and not vc.note.startswith("formula minimum")
    if "v_ne" not in r.fields and not vc_published:
        reasons.append("missing v_ne and v_c")
    if not (r.type_name and r.variant):
        reasons.append("missing type/variant")
    for k in ("mtow", "empty_weight"):
        nv = r.fields.get(k)
        if nv is not None and ctx.tier(nv.sources[0]) not in (Tier.A, Tier.B):
            reasons.append(f"{k} source not tier A/B")
    if r.sigma_rel is None and "empty_weight" in r.fields:
        reasons.append("no measurement variance (tier C)")
    if r.outlier and not r.outlier_confirmed:
        reasons.append("unresolved N10 outlier")
    if r.duplicate_of:
        reasons.append(f"duplicate of {r.duplicate_of}")
    r.ineligible_reasons = reasons
    r.calibration_eligible = not reasons
    logs.append(LogEntry(r.id, "N12", "calibration_eligible", rec.calibration_eligible, r.calibration_eligible,
                         "; ".join(reasons) or "eligible"))
    return r, logs


# --- pipeline -------------------------------------------------------------------
def _apply(records: list[NormRecord], fn: Routine, ctx: NormContext, logs: list[LogEntry]) -> list[NormRecord]:
    out = []
    for r in records:
        r2, lg = fn(r, ctx)
        out.append(r2)
        logs.extend(lg)
    return out


def run_pipeline(records: Iterable[NormRecord | AircraftRecord], ctx: NormContext
                 ) -> tuple[list[NormRecord], list[LogEntry]]:
    """N1 → N12 in order. Accepts raw AircraftRecords or already-normalized records (idempotent)."""
    rs = [from_record(r) if isinstance(r, AircraftRecord) else r for r in records]
    rs.sort(key=lambda r: r.id)
    logs: list[LogEntry] = []
    rs = _apply(rs, n1_units, ctx, logs)
    rs = _apply(rs, n2_select, ctx, logs)
    rs, lg = n2_dedup(rs)
    logs += lg
    for fn in (n3_empty_weight, n4_fuel_oil, n5_censoring, n6_loads, n7_speeds, n8_geometry, n9_variance):
        rs = _apply(rs, fn, ctx, logs)
    rs, lg = n10_outliers(rs, ctx)
    logs += lg
    rs = _apply(rs, n11_electric, ctx, logs)
    rs = _apply(rs, n12_eligibility, ctx, logs)
    return rs, logs


# --- seed single-record API (kept for compatibility; superseded by run_pipeline) ----------
@dataclass
class NormalizedRecord:
    id: str
    empty_weight_basic_kg: float | None
    sigma_rel: float | None           # None -> exclude from calibration
    mtow_kg: float | None
    mtow_censored: bool
    cap_label: str
    design_weight_kg: float | None    # weight to use in load-dependent component equations
    warnings: list[str]


def normalize(rec: AircraftRecord, tier_of: dict[str, Tier], oil_litres: float | None = None) -> NormalizedRecord:
    """Seed API: N3 (licensed/oil), N5, N9 on one record whose values are already in kg."""
    warn: list[str] = []
    ew = rec.empty_weight.value if rec.empty_weight else None
    mtow = rec.mtow.value if rec.mtow else None
    def_sigma = DEF_SIGMA[rec.empty_weight_def]

    if ew is not None and rec.empty_weight_def == EmptyWeightDef.LICENSED:
        if oil_litres is not None:
            ew += oil_litres * OIL_DENSITY
        else:
            def_sigma = LICENSED_UNCORRECTED_SIGMA
            warn.append("licensed empty weight, oil quantity unknown: not corrected, variance inflated")

    if rec.powertrain in ELECTRIC_LIKE:
        if rec.battery_mass_installed is None and rec.battery_energy_total is not None:
            warn.append("electric: confirm published empty weight includes installed pack")

    sigma = None
    if rec.empty_weight is not None:
        tier = tier_of.get(rec.empty_weight.source, Tier.C)
        base = TIER_SIGMA[tier]
        if base is None:
            warn.append("tier C empty weight: excluded from calibration, analog use only")
        else:
            sigma = (base**2 + def_sigma ** 2) ** 0.5
            if rec.production_status == "prototype":
                sigma = (sigma**2 + PROTOTYPE_SIGMA**2) ** 0.5
                warn.append("prototype: production empty weight typically grows")

    censored, label = (False, "")
    if mtow is not None:
        censored, label = detect_cap(mtow)
        if rec.cert in CATEGORY_TAGS and not censored:
            warn.append("category aircraft with MTOW off a known cap: check variant/market")
    design_w = None if (mtow is None or censored) else mtow
    if censored:
        warn.append(f"MTOW at regulatory cap ({label}): treated as lower bound; "
                    "load-dependent equations need structural design weight from TCDS/POH if available")

    return NormalizedRecord(rec.id, ew, sigma, mtow, censored, label, design_w, warn)
