"""Certification-basis load criteria for HS1 weight estimation.

Two bases are carried in parallel and enveloped:
  * ASTM_F2245  - US light-sport (MOSAIC, 14 CFR Part 22), ASTM F2245-26
  * FAR23_L1    - DGCA type certification under CAR 21 against 14 CFR Part 23
                  (Amdt 23-64/65), Level 1 low-speed (<= 1 passenger, VC/VMO <= 250 KCAS).
                  Means of compliance: ASTM F3264 umbrella as accepted by FAA
                  (MOC notice effective 20 May 2025), principally F3116 (design
                  loads and conditions) and F3083 (emergency conditions). Values
                  below are the legacy FAR 23 (Amdt 62) numbers that F3116/F3083
                  carry forward.

Every Criterion flagged verify=True is a working value. It must be checked
against the purchased standard revision (F2245-26; F3116/F3083 and the FAA
MOC table) before any structural sizing is released.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

G = 9.80665
RHO0 = 1.225
KT = 0.514444          # m/s per knot
LB = 0.45359237        # kg per lb
PSF = 47.880259        # Pa per lbf/ft^2
FPS = 0.3048           # m/s per ft/s


@dataclass(frozen=True)
class Criterion:
    value: float
    unit: str
    ref: str
    verify: bool = True


@dataclass(frozen=True)
class CertBasis:
    key: str
    title: str
    safety_factor: Criterion
    n_pos_fixed: Criterion | None      # None -> Part 23 weight formula
    n_neg_fixed: Criterion | None      # None -> ratio of n_pos
    n_neg_ratio: Criterion | None
    flap_n_pos: Criterion
    vc_min_coeff: Criterion            # VC_min = k*sqrt(W/S), W/S in Pa -> m/s
    vd_over_vc: Criterion
    vd_over_vcmin: Criterion
    ude_vc: Criterion                  # derived gust velocity, m/s EAS
    ude_vd: Criterion
    emergency_static: dict[str, Criterion]
    items_of_mass_aft: dict[str, Criterion] | None
    dynamic_seat: dict[str, Criterion] | None
    vs0_max_kcas: Criterion | None     # certification stall limit, landing config

    # --- manoeuvre load factors -------------------------------------------
    def n_pos(self, mass_kg: float) -> float:
        if self.n_pos_fixed is not None:
            return self.n_pos_fixed.value
        w_lb = mass_kg / LB
        return min(3.8, 2.1 + 24000.0 / (w_lb + 10000.0))

    def n_neg(self, mass_kg: float) -> float:
        if self.n_neg_fixed is not None:
            return self.n_neg_fixed.value
        return -self.n_neg_ratio.value * self.n_pos(mass_kg)


SPORT_PILOT_VS1_MAX = Criterion(59.0, "KCAS", "14 CFR 61.316 (MOSAIC), clean, power idle", False)

ASTM_F2245 = CertBasis(
    key="ASTM_F2245",
    title="ASTM F2245-26 light-sport airplane (MOSAIC Part 22)",
    safety_factor=Criterion(1.5, "-", "F2245 structure, ultimate factor"),
    n_pos_fixed=Criterion(4.0, "g", "F2245 Table X1.1 minimum positive limit"),
    n_neg_fixed=Criterion(-2.0, "g", "F2245 Table X1.1 minimum negative limit"),
    n_neg_ratio=None,
    flap_n_pos=Criterion(2.0, "g", "F2245 flaps-extended limit"),
    vc_min_coeff=Criterion(2.4, "m/s per sqrt(Pa)", "F2245 X1 (CS-VLA-equivalent form)"),
    vd_over_vc=Criterion(1.25, "-", "F2245 design dive speed"),
    vd_over_vcmin=Criterion(1.40, "-", "F2245 design dive speed"),
    ude_vc=Criterion(15.24, "m/s", "F2245 X3 gust at VC"),
    ude_vd=Criterion(7.62, "m/s", "F2245 X3 gust at VD"),
    emergency_static={
        "up": Criterion(3.0, "g", "F2245 emergency landing"),
        "forward": Criterion(9.0, "g", "F2245 emergency landing"),
        "side": Criterion(1.5, "g", "F2245 emergency landing"),
    },
    items_of_mass_aft=None,            # confirm F2245-26 text; Part 23 governs in envelope
    dynamic_seat=None,
    vs0_max_kcas=Criterion(61.0, "KCAS", "14 CFR 22.100 (MOSAIC)", False),
)

FAR23_L1 = CertBasis(
    key="FAR23_L1",
    title="14 CFR Part 23 Amdt 64 Level 1 low-speed, normal category (DGCA CAR 21)",
    safety_factor=Criterion(1.5, "-", "F3116 / legacy 23.303"),
    n_pos_fixed=None,
    n_neg_fixed=None,
    n_neg_ratio=Criterion(0.4, "-", "F3116 / legacy 23.337(b)"),
    flap_n_pos=Criterion(2.0, "g", "F3116 / legacy 23.345"),
    vc_min_coeff=Criterion(33.0 * KT / math.sqrt(PSF), "m/s per sqrt(Pa)", "F3116 / legacy 23.335(a): 33*sqrt(W/S)"),
    vd_over_vc=Criterion(1.25, "-", "F3116 / legacy 23.335(b)"),
    vd_over_vcmin=Criterion(1.40, "-", "F3116 / legacy 23.335(b), normal category"),
    ude_vc=Criterion(50.0 * FPS, "m/s", "F3116 / legacy 23.333(c): 50 fps at VC"),
    ude_vd=Criterion(25.0 * FPS, "m/s", "F3116 / legacy 23.333(c): 25 fps at VD"),
    emergency_static={
        "up": Criterion(3.0, "g", "F3083 / legacy 23.561(b)(2)"),
        "forward": Criterion(9.0, "g", "F3083 / legacy 23.561(b)(2)"),
        "side": Criterion(1.5, "g", "F3083 / legacy 23.561(b)(2)"),
    },
    items_of_mass_aft={  # powerplant and ESS (battery) mounts: governs battery bays and pusher motor
        "up": Criterion(3.0, "g", "F3083 / legacy 23.561(b)(3)"),
        "forward": Criterion(18.0, "g", "FAA MOC change to F3083 4.1.6: powerplant and ESS mounts "
                             "and supporting structure 18.0 g forward", False),
        "side": Criterion(4.5, "g", "F3083 / legacy 23.561(b)(3)"),
    },
    dynamic_seat={
        "test1_peak": Criterion(19.0, "g", "F3083 / legacy 23.562 test 1 (combined vertical/longitudinal)"),
        "test1_dv": Criterion(31.0 * FPS, "m/s", "F3083 / legacy 23.562 test 1"),
        "test2_peak": Criterion(26.0, "g", "F3083 / legacy 23.562 test 2 (longitudinal, 10 deg yaw)"),
        "test2_dv": Criterion(42.0 * FPS, "m/s", "F3083 / legacy 23.562 test 2"),
    },
    vs0_max_kcas=Criterion(61.0, "KCAS", "F3179 / legacy 23.49(c) single-engine; amdt 64 permits higher VS0 "
                           "only with added crashworthiness (F3083) - weight penalty"),
)

BASES = {b.key: b for b in (ASTM_F2245, FAR23_L1)}


# --- aerodynamics helpers ----------------------------------------------------
def stall_speed(mass_kg: float, area_m2: float, cl_max: float) -> float:
    """EAS stall speed, m/s. Certification stall is power-idle: no DEP blowing credit."""
    return math.sqrt(2.0 * mass_kg * G / (RHO0 * area_m2 * cl_max))


def min_area_for_stall(mass_kg: float, v_kcas: float, cl_max: float) -> float:
    v = v_kcas * KT
    return 2.0 * mass_kg * G / (RHO0 * v * v * cl_max)


def gust_load_factors(ws_pa: float, cbar: float, a_per_rad: float, ude: float, v_eas: float):
    mu = 2.0 * ws_pa / (RHO0 * cbar * a_per_rad * G)
    kg = 0.88 * mu / (5.3 + mu)
    dn = kg * RHO0 * ude * v_eas * a_per_rad / (2.0 * ws_pa)
    return 1.0 + dn, 1.0 - dn, kg


def landing_descent_velocity(ws_pa: float) -> float:
    """Legacy 23.473(d): V = 4.4 (W/S)^0.25 fps, clamped 7-10 fps. Applied to both bases (verify for F2245)."""
    v_fps = 4.4 * (ws_pa / PSF) ** 0.25
    return min(max(v_fps, 7.0), 10.0) * FPS


# --- per-basis evaluation ----------------------------------------------------
def evaluate(basis: CertBasis, mass_kg: float, area_m2: float, cbar_m: float,
             a_per_rad: float, cl_max_clean: float, cl_max_land: float,
             vc_kt: float, vh_kt: float | None = None) -> dict:
    w = mass_kg * G
    ws = w / area_m2
    vc = vc_kt * KT
    vc_min = basis.vc_min_coeff.value * math.sqrt(ws)
    if vh_kt is not None:
        vc_min = min(vc_min, 0.9 * vh_kt * KT)
    vd = max(basis.vd_over_vc.value * vc, basis.vd_over_vcmin.value * vc_min)

    n1, n2 = basis.n_pos(mass_kg), basis.n_neg(mass_kg)
    vs1 = stall_speed(mass_kg, area_m2, cl_max_clean)
    vs0 = stall_speed(mass_kg, area_m2, cl_max_land)
    va = vs1 * math.sqrt(n1)

    gc_pos, gc_neg, kg = gust_load_factors(ws, cbar_m, a_per_rad, basis.ude_vc.value, vc)
    gd_pos, gd_neg, _ = gust_load_factors(ws, cbar_m, a_per_rad, basis.ude_vd.value, vd)

    n_lim_pos = max(n1, gc_pos, gd_pos)
    n_lim_neg = min(n2, gc_neg, gd_neg)
    sf = basis.safety_factor.value

    checks = {
        "vc_meets_min": vc >= vc_min,
        "sport_pilot_vs1": vs1 / KT <= SPORT_PILOT_VS1_MAX.value,
    }
    if basis.vs0_max_kcas is not None:
        checks["cert_vs0"] = vs0 / KT <= basis.vs0_max_kcas.value

    return {
        "basis": basis.key,
        "wing_loading_Pa": ws,
        "VS1_kt": vs1 / KT, "VS0_kt": vs0 / KT, "VA_kt": va / KT,
        "VC_kt": vc_kt, "VC_min_kt": vc_min / KT, "VD_kt": vd / KT,
        "n_man_pos": n1, "n_man_neg": n2,
        "n_gust_VC": (gc_pos, gc_neg), "n_gust_VD": (gd_pos, gd_neg), "Kg": kg,
        "n_limit_pos": n_lim_pos, "n_limit_neg": n_lim_neg,
        "n_ult_pos": sf * n_lim_pos, "n_ult_neg": sf * n_lim_neg,
        "pos_governed_by": "gust" if max(gc_pos, gd_pos) > n1 else "manoeuvre",
        "q_D_Pa": 0.5 * RHO0 * vd * vd,
        "landing_sink_mps": landing_descent_velocity(ws),
        "emergency_static": {k: c.value for k, c in basis.emergency_static.items()},
        "items_of_mass_aft": None if basis.items_of_mass_aft is None
        else {k: c.value for k, c in basis.items_of_mass_aft.items()},
        "dynamic_seat": basis.dynamic_seat is not None,
        "checks": checks,
    }


def envelope(results: list[dict]) -> dict:
    """Design-to set: worst case across bases for every weight-driving quantity."""
    def worst(key, fn=max):
        vals = [(r[key], r["basis"]) for r in results]
        v, b = fn(vals, key=lambda t: t[0])
        return {"value": v, "governing": b}

    def worst_dict(key):
        out = {}
        for r in results:
            d = r[key] or {}
            for k, v in d.items():
                if k not in out or v > out[k]["value"]:
                    out[k] = {"value": v, "governing": r["basis"]}
        return out

    return {
        "n_ult_pos": worst("n_ult_pos"),
        "n_ult_neg": worst("n_ult_neg", min),
        "q_D_Pa": worst("q_D_Pa"),
        "VD_kt": worst("VD_kt"),
        "landing_sink_mps": worst("landing_sink_mps"),
        "emergency_static": worst_dict("emergency_static"),
        "items_of_mass_aft": worst_dict("items_of_mass_aft"),
        "dynamic_seat_required": any(r["dynamic_seat"] for r in results),
        "all_checks": {r["basis"]: r["checks"] for r in results},
    }


def unverified_criteria() -> list[str]:
    out = []
    for b in BASES.values():
        for name, val in vars(b).items():
            items = val.items() if isinstance(val, dict) else [(name, val)]
            for k, c in items:
                if isinstance(c, Criterion) and c.verify:
                    out.append(f"{b.key}.{name}{'' if k == name else '.' + k}: {c.value:g} {c.unit} [{c.ref}]")
    return out
