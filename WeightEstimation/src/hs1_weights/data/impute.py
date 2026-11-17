"""NI: cluster imputation of missing L1 inputs (doc 05 NI; owner decision 2026-11-12, doc 12 SC-28/SC-29).

Runs on the whole record list after N5 and before N6, because it needs cluster statistics. For a field that
record j (in cluster c) does not report, the methods are tried in this order:

1. ``cluster_regression``: log y = a + b·log x, fitted on members of cluster c that report both y and the
   predictor x (at least 4 members).
2. ``cluster_offset``: 2–3 members report it. Uses the fleet slope b with the cluster's own intercept
   (partial pooling).
3. ``fleet_regression``: pooled over the fleet, restricted to the same powertrain group where the spec sets
   ``pool="powertrain"`` (at least 4 records).
4. ``cluster_median`` / ``fleet_median``: for fields without a usable predictor.
5. ``design_prior``: no fleet record reports the field. A cited design relation with a wide sd. Used only when
   ``ctx.design_priors``; every constant is flagged *verify against the cited text*.

Only observed values are donors; imputed values never are. MTOW and empty weight (the calibration
observations) are never imputed. Each imputed ``NV`` records ``derived_from``, ``method`` (``NI.<method>``), its
1-sd uncertainty (``sd``, ``sd_scale``) and a confidence of at most 0.5. ``calibration/fleet.py`` propagates
``sd`` through L1 into the record's likelihood sigma, so heavily imputed records weigh less.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..schema import CertTag, GearType, Powertrain
from .derive import mac_trapezoidal
from .normalize import NV, LogEntry, NormContext, NormRecord, _copy

N_REG = 4                 # members needed for a regression
N_OFFSET = 2              # members needed for a cluster intercept with the fleet slope
SD_FLOOR = 0.03           # floor on any data-imputation sd (log scale)
SD_MEDIAN_FLOOR = {"cluster": 0.10, "fleet": 0.15}
DATA_CONFIDENCE, PRIOR_CONFIDENCE = 0.5, 0.3
DEG = math.pi / 180.0
PRIOR_REGISTRY = "hs1_weights.data.impute.SPECS"   # derived_from for constant design priors (rule 6)

PT_POOL = {Powertrain.PISTON_AVGAS: "piston", Powertrain.PISTON_MOGAS_ROTAX: "piston",
           Powertrain.PISTON_DIESEL: "diesel", Powertrain.TURBOPROP: "turboprop",
           Powertrain.ELECTRIC: "electric", Powertrain.HYBRID_SERIES: "electric",
           Powertrain.HYBRID_PARALLEL: "electric"}


def _electric(r: NormRecord) -> bool:
    return PT_POOL[r.powertrain] == "electric"


def _ice(r: NormRecord) -> bool:
    return not _electric(r)


def _always(r: NormRecord) -> bool:
    return True


def _has_nose_gear(r: NormRecord) -> bool:
    return r.gear != GearType.FIXED_TAILWHEEL


# --- design priors (doc 12 SC-29) ----------------------------------------------------------------------
RAYMER_T64 = "Raymer, Aircraft Design: A Conceptual Approach, §6.4 / Table 6.4 (verify edition and values)"
RAYMER_T43 = "Raymer, Aircraft Design, Table 4.3 tail geometry ranges (verify)"
WEAK = "weakly informative; no fleet record reports it (verify / replace with sourced data)"

C_HT = {"ga_single": 0.70, "homebuilt": 0.50, "ga_twin": 0.80, "twin_turboprop": 0.90, "sailplane": 0.50}
C_VT = {"ga_single": 0.04, "homebuilt": 0.04, "ga_twin": 0.07, "twin_turboprop": 0.08, "sailplane": 0.02}
TAIL_ARM_FRAC = {"ga_single": 0.60, "homebuilt": 0.60, "ga_twin": 0.55, "twin_turboprop": 0.55, "sailplane": 0.65}
FUSELAGE_FINENESS = 6.5                       # overall length / equivalent fuselage depth
ENGINE_KG_PER_KW = {Powertrain.PISTON_MOGAS_ROTAX: 0.80, Powertrain.PISTON_AVGAS: 0.90,
                    Powertrain.PISTON_DIESEL: 1.40, Powertrain.TURBOPROP: 0.28}
MOTOR_KW_PER_KG = 5.0                         # centre of the doc 06 §5.2 placeholder U(4, 6) kW/kg
PACK_WH_PER_KG = 140.0                        # pack-level, fleet-era electric aircraft
N_LIMIT_PRIOR = {CertTag.CS_LSA: (4.0, 0.10, "CS-LSA is based on ASTM F2245 (n = 4.0); SC-5 gap"),
                 CertTag.EXPERIMENTAL: (4.4, 0.20, "experimental: no regulatory n; centred on FAR23 utility, "
                                                   "+-20% covers normal 3.8 to aerobatic 6")}


def tail_class(r: NormRecord) -> str:
    if r.n_engines >= 2:
        return "twin_turboprop" if r.powertrain == Powertrain.TURBOPROP else "ga_twin"
    s, b = r.get("wing_area"), r.get("span")
    if s and b and b * b / s >= 15.0:
        return "sailplane"
    return "homebuilt" if r.cert == CertTag.EXPERIMENTAL else "ga_single"


PriorOut = tuple[float, float, str, tuple[str, ...], str]   # value, sd, scale, deps, basis
PriorFn = Callable[[NormRecord], "PriorOut | None"]


def _need(r: NormRecord, *names: str) -> list[float] | None:
    vals = [r.get(n) for n in names]
    return None if any(v is None for v in vals) else [float(v) for v in vals]  # type: ignore[arg-type]


def _p_const(value: float, sd: float, basis: str, scale: str = "log") -> PriorFn:
    return lambda r: (value, sd, scale, (), basis)


def _p_tail_arm(r: NormRecord) -> PriorOut | None:
    v = _need(r, "fuselage_length")
    if v is None:
        return None
    c = tail_class(r)
    return TAIL_ARM_FRAC[c] * v[0], 0.10, "log", ("fuselage_length",), f"L_t = {TAIL_ARM_FRAC[c]} L_fus ({c}); " \
        + RAYMER_T64


def _p_htail_area(r: NormRecord) -> PriorOut | None:
    v = _need(r, "wing_area", "span", "taper", "tail_arm")
    if v is None:
        return None
    s, b, lam, lt = v
    c = tail_class(r)
    return (C_HT[c] * s * mac_trapezoidal(s, b, lam) / lt, 0.25, "log", ("wing_area", "span", "taper", "tail_arm"),
            f"S_ht = c_HT S c̄ / L_t, c_HT = {C_HT[c]} ({c}); {RAYMER_T64}")


def _p_vtail_area(r: NormRecord) -> PriorOut | None:
    v = _need(r, "wing_area", "span", "tail_arm")
    if v is None:
        return None
    s, b, lt = v
    c = tail_class(r)
    return (C_VT[c] * s * b / lt, 0.25, "log", ("wing_area", "span", "tail_arm"),
            f"S_vt = c_VT S b / L_vt (L_vt = L_t), c_VT = {C_VT[c]} ({c}); {RAYMER_T64}")


def _p_fus_height(r: NormRecord) -> PriorOut | None:
    v = _need(r, "fuselage_length")
    if v is None:
        return None
    return (v[0] / FUSELAGE_FINENESS, 0.15, "log", ("fuselage_length",),
            f"fuselage fineness L/D = {FUSELAGE_FINENESS} ({WEAK})")


def _p_fus_width(r: NormRecord) -> PriorOut | None:
    v = _need(r, "fuselage_max_height")
    if v is None:
        return None
    # follows an imputed height exactly (sd 0, re-derived in propagation); +-20% about a published height
    sd = 0.0 if r.fields["fuselage_max_height"].method.startswith("NI.") else 0.20
    return v[0], sd, "log", ("fuselage_max_height",), f"fuselage width = height ({WEAK})"


def _p_gear(anchor_m: float) -> PriorFn:
    def f(r: NormRecord) -> PriorOut | None:
        v = _need(r, "mtow")
        if v is None:
            return None
        return (anchor_m * (v[0] / 1000.0) ** (1.0 / 3.0), 0.35, "log", ("mtow",),
                f"strut length = {anchor_m} m x (MTOW/1000 kg)^(1/3), geometric similarity ({WEAK})")
    return f


def _p_avionics(r: NormRecord) -> PriorOut | None:
    v = _need(r, "mtow")
    return None if v is None else (0.025 * v[0], 0.40, "log", ("mtow",), f"W_uav = 0.025 MTOW ({WEAK})")


def _p_engine(r: NormRecord) -> PriorOut | None:
    v = _need(r, "rated_power_total")
    if v is None:
        return None
    kw = v[0] / 1e3
    if _electric(r):
        return (kw / MOTOR_KW_PER_KG, 0.35, "log", ("rated_power_total",),
                f"motor mass = P / {MOTOR_KW_PER_KG} kW/kg (doc 06 §5.2 placeholder centre; {WEAK})")
    k = ENGINE_KG_PER_KW[r.powertrain]
    return (k * kw, 0.25, "log", ("rated_power_total",),
            f"engine dry mass = {k} kg/kW x P ({r.powertrain.value} family; verify against the engine TCDS)")


def _p_pack(r: NormRecord) -> PriorOut | None:
    v = _need(r, "battery_energy_total")
    return None if v is None else (v[0] / PACK_WH_PER_KG, 0.30, "log", ("battery_energy_total",),
                                   f"pack mass = E / {PACK_WH_PER_KG} Wh/kg pack level ({WEAK})")


def _p_fuel_vi(r: NormRecord) -> PriorOut | None:
    v = _need(r, "fuel_capacity_total")
    return None if v is None else (0.5 * v[0], 0.50, "log", ("fuel_capacity_total",),
                                   f"integral tank volume = 0.5 V_t (Raymer fuel system ∝ (1+V_i/V_t)^-0.363; {WEAK})")


def _p_nlimit(r: NormRecord) -> PriorOut | None:
    p = N_LIMIT_PRIOR.get(r.cert)
    return None if p is None else (p[0], p[1], "log", ("cert",), p[2])


@dataclass(frozen=True)
class Spec:
    field: str
    unit: str
    predictors: tuple[str, ...] = ()
    pool: str = "all"                  # "all" | "powertrain"
    applies: Callable[[NormRecord], bool] = _always
    prior: PriorFn | None = None
    data: bool = True                  # False: design prior only (e.g. load factors: N6 rules come first)


# Field-major order: a field's predictors and prior inputs are imputed before it.
SPECS: tuple[Spec, ...] = (
    Spec("wing_area", "m2", ("mtow",)),
    Spec("span", "m", ("wing_area", "mtow")),
    Spec("fuselage_length", "m", ("mtow",)),
    Spec("rated_power_total", "W", ("mtow",), pool="powertrain"),
    Spec("v_ne", "m/s", ("v_cruise", "mtow")),
    Spec("v_no", "m/s", ("v_ne", "v_cruise"), applies=lambda r: "v_c" not in r.fields),
    Spec("fuel_capacity_total", "L", ("rated_power_total", "mtow"), pool="powertrain", applies=_ice),
    Spec("battery_energy_total", "Wh", ("mtow",), pool="powertrain", applies=_electric),
    Spec("battery_mass_installed", "kg", ("battery_energy_total",), pool="powertrain", applies=_electric,
         prior=_p_pack),
    Spec("engine_dry_mass", "kg", ("rated_power_total",), pool="powertrain", prior=_p_engine),
    Spec("taper", "-", prior=_p_const(0.75, 0.30, "GA wings rectangular to λ≈0.5; Raymer GA wing ∝ λ^0.04 "
                                                   f"(insensitive); {WEAK}")),
    Spec("sweep_qc", "rad", prior=_p_const(0.0, 3.0 * DEG, f"unswept GA wing, sd 3 deg ({WEAK})", "abs")),
    Spec("root_tc", "-", prior=_p_const(0.15, 0.15, f"GA root airfoils 12-18% thick ({WEAK})")),
    Spec("tail_arm", "m", ("fuselage_length",), prior=_p_tail_arm),
    Spec("htail_area", "m2", ("wing_area",), prior=_p_htail_area),
    Spec("vtail_area", "m2", ("wing_area",), prior=_p_vtail_area),
    Spec("htail_aspect_ratio", "-", prior=_p_const(4.0, 0.20, f"horizontal tail A 3-5; {RAYMER_T43}")),
    Spec("htail_taper", "-", prior=_p_const(0.6, 0.35, f"horizontal tail λ 0.3-1.0; {RAYMER_T43}")),
    Spec("htail_tc", "-", prior=_p_const(0.10, 0.20, f"tail t/c ≈ 0.10 ({WEAK})")),
    Spec("htail_sweep_qc", "rad", prior=_p_const(5.0 * DEG, 5.0 * DEG, f"horizontal tail sweep ({WEAK})", "abs")),
    Spec("vtail_aspect_ratio", "-", prior=_p_const(1.5, 0.25, f"vertical tail A 1.3-2.0; {RAYMER_T43}")),
    Spec("vtail_taper", "-", prior=_p_const(0.5, 0.35, f"vertical tail λ 0.3-0.6; {RAYMER_T43}")),
    Spec("vtail_tc", "-", prior=_p_const(0.10, 0.20, f"tail t/c ≈ 0.10 ({WEAK})")),
    Spec("vtail_sweep_qc", "rad", prior=_p_const(25.0 * DEG, 10.0 * DEG, f"GA fin sweep ({WEAK})", "abs")),
    Spec("fuselage_max_height", "m", ("fuselage_length",), prior=_p_fus_height),
    Spec("fuselage_max_width", "m", ("fuselage_length",), prior=_p_fus_width),
    Spec("main_gear_length", "m", ("mtow",), prior=_p_gear(0.6)),
    Spec("nose_gear_length", "m", ("mtow",), applies=_has_nose_gear, prior=_p_gear(0.5)),
    Spec("avionics_uninstalled_mass", "kg", ("mtow",), prior=_p_avionics),
    Spec("fuel_integral_volume", "L", ("fuel_capacity_total",), applies=_ice, prior=_p_fuel_vi),
    Spec("n_limit_pos", "-", data=False, prior=_p_nlimit),
)
IMPUTABLE = tuple(s.field for s in SPECS)
NEVER_IMPUTED = ("mtow", "empty_weight")
assert not set(IMPUTABLE) & set(NEVER_IMPUTED)


# --- data methods ----------------------------------------------------------------------------------
def _observed(r: NormRecord, name: str) -> float | None:
    nv = r.fields.get(name)
    if nv is None or nv.method.startswith("NI.") or nv.value <= 0.0:
        return None
    return nv.value


@dataclass(frozen=True)
class Fit:
    a: float
    b: float
    s: float
    n: int
    xbar: float
    sxx: float
    donors: tuple[str, ...]

    def predict(self, lx: float) -> tuple[float, float]:
        sd = self.s * math.sqrt(1.0 + 1.0 / self.n + (lx - self.xbar) ** 2 / self.sxx)
        return self.a + self.b * lx, max(sd, SD_FLOOR)


def _fit(pairs: list[tuple[str, float, float]]) -> Fit | None:
    if len(pairs) < N_REG:
        return None
    lx = np.log([p[1] for p in pairs])
    ly = np.log([p[2] for p in pairs])
    xbar = float(lx.mean())
    sxx = float(((lx - xbar) ** 2).sum())
    if sxx < 1e-6:
        return None
    b = float(((lx - xbar) * (ly - ly.mean())).sum() / sxx)
    a = float(ly.mean() - b * xbar)
    s = math.sqrt(float(((ly - a - b * lx) ** 2).sum()) / (len(pairs) - 2))
    return Fit(a, b, s, len(pairs), xbar, sxx, tuple(p[0] for p in pairs))


def _pairs(rs: list[NormRecord], y: str, x: str | None) -> list[tuple[str, float, float]]:
    out = []
    for r in rs:
        yv = _observed(r, y)
        xv = 1.0 if x is None else _observed(r, x)
        if yv is not None and xv is not None:
            out.append((r.id, xv, yv))
    return out


def _pool(rs: list[NormRecord], r: NormRecord, spec: Spec) -> list[NormRecord]:
    return [o for o in rs if spec.pool == "all" or PT_POOL[o.powertrain] == PT_POOL[r.powertrain]]


def _data_estimate(rs: list[NormRecord], r: NormRecord, spec: Spec
                   ) -> tuple[float, float, str, tuple[str, ...], str] | None:
    """(value, log-sd, method, derived_from, detail) from fleet data, or None."""
    cluster = [o for o in rs if r.cluster and o.cluster == r.cluster]
    pool = _pool(rs, r, spec)
    tag = f"cluster:{r.cluster}" if r.cluster else "cluster:-"
    for x in spec.predictors:
        xnv = r.fields.get(x)
        if xnv is None or xnv.value <= 0.0:
            continue
        lx = math.log(xnv.value)
        sd_x = xnv.sd if xnv.sd_scale == "log" else 0.0
        fleet = _fit(_pairs(pool, spec.field, x))
        cpairs = _pairs(cluster, spec.field, x)
        cfit = _fit(cpairs)
        if cfit is not None:
            mu, sd = cfit.predict(lx)
            return (math.exp(mu), math.hypot(sd, cfit.b * sd_x), "cluster_regression", (x, tag),
                    f"log y = {cfit.a:.3f} + {cfit.b:.3f} log {x}; n={cfit.n}, s={cfit.s:.3f}; donors "
                    + ",".join(cfit.donors))
        if fleet is not None and len(cpairs) >= N_OFFSET:
            off = float(np.mean([math.log(p[2]) - fleet.b * math.log(p[1]) for p in cpairs]))
            sd = max(fleet.s * math.sqrt(1.0 + 1.0 / len(cpairs)), SD_FLOOR)
            return (math.exp(off + fleet.b * lx), math.hypot(sd, fleet.b * sd_x), "cluster_offset", (x, tag),
                    f"fleet slope {fleet.b:.3f} (n={fleet.n}) with intercept from cluster donors "
                    + ",".join(p[0] for p in cpairs))
        if fleet is not None:
            mu, sd = fleet.predict(lx)
            return (math.exp(mu), math.hypot(sd, fleet.b * sd_x), "fleet_regression",
                    (x, f"fleet:{spec.pool if spec.pool == 'all' else PT_POOL[r.powertrain]}"),
                    f"log y = {fleet.a:.3f} + {fleet.b:.3f} log {x}; n={fleet.n}, s={fleet.s:.3f}; donors "
                    + ",".join(fleet.donors))
    for level, group, nmin in (("cluster", cluster, N_OFFSET), ("fleet", pool, 3)):
        ys = _pairs(group, spec.field, None)
        if len(ys) >= nmin:
            ly = np.log([p[2] for p in ys])
            sd = max(float(ly.std(ddof=1)) if len(ys) > 1 else 0.0, SD_MEDIAN_FLOOR[level])
            return (float(math.exp(np.median(ly))), sd * math.sqrt(1.0 + 1.0 / len(ys)), f"{level}_median",
                    (tag if level == "cluster" else "fleet",),
                    f"{level} median of n={len(ys)}; donors " + ",".join(p[0] for p in ys))
    return None


def ni_impute(records: list[NormRecord], ctx: NormContext) -> tuple[list[NormRecord], list[LogEntry]]:
    """Impute missing L1 inputs, field by field (see module docstring)."""
    rs = [_copy(r) for r in records]
    logs: list[LogEntry] = []
    for spec in SPECS:
        estimates: dict[str, NV] = {}
        for r in rs:
            if spec.field in r.fields or not spec.applies(r):
                continue
            est = _data_estimate(rs, r, spec) if spec.data else None
            if est is not None:
                val, sd, method, deps, detail = est
                srcs = tuple(dict.fromkeys(s for d in deps if d in r.fields for s in r.fields[d].sources))
                estimates[r.id] = NV(val, spec.unit, srcs, DATA_CONFIDENCE, detail, deps, f"NI.{method}", sd, "log")
                logs.append(LogEntry(r.id, "NI", spec.field, None, val, f"{method} (sd {sd:.3f}): {detail}"))
            elif spec.prior is not None and ctx.design_priors:
                p = spec.prior(r)
                if p is None:
                    logs.append(LogEntry(r.id, "NI", spec.field, None, None, "design prior inputs missing"))
                    continue
                val, sd, scale, deps, basis = p
                srcs = tuple(dict.fromkeys(s for d in deps if d in r.fields for s in r.fields[d].sources))
                method = "NI.fus_width_eq_height" if (spec.field == "fuselage_max_width" and sd == 0.0) \
                    else "NI.design_prior"
                estimates[r.id] = NV(val, spec.unit, srcs, PRIOR_CONFIDENCE, f"design prior: {basis}",
                                     deps or (PRIOR_REGISTRY,), method, sd, scale)
                logs.append(LogEntry(r.id, "NI", spec.field, None, val,
                                     f"design prior (sd {sd:.3f} {scale}): {basis}"))
            else:
                logs.append(LogEntry(r.id, "NI", spec.field, None, None,
                                     "no fleet data" + ("" if spec.prior is None else "; design priors off")))
        # assign after the field's pass so imputed values never act as donors for the same field
        for r in rs:
            if r.id in estimates:
                r.fields[spec.field] = estimates[r.id]
                r.imputed.append(spec.field)
    for r in rs:
        if ctx.design_priors and _ice(r) and r.fuel_tanks is None:
            r.fuel_tanks = 2
            r.imputed.append("fuel_tanks")
            logs.append(LogEntry(r.id, "NI", "fuel_tanks", None, 2, f"design prior: two wing tanks ({WEAK}); "
                                 "Raymer fuel system ∝ N_t^0.242"))
        n_prior = sum(1 for f in r.imputed if f == "fuel_tanks" or r.fields[f].method.endswith(
            ("design_prior", "fus_width_eq_height")))
        if r.imputed:
            r.warn(f"NI: {len(r.imputed)} L1 inputs imputed ({len(r.imputed) - n_prior} from fleet data, "
                   f"{n_prior} design priors): {', '.join(r.imputed)}")
    return rs, logs
