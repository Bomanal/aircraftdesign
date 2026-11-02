"""
Sizing orchestration: calibration, airframe build (steps 3-6), mass closure in mode A
(fixed MTOM) or mode B (solved MTOM, steps 2 and 7), feasibility checks (step 8),
performance and mission (step 9) and the result record (step 10).
"""
from __future__ import annotations
import math, time
from dataclasses import dataclass

from .model import DesignInputs, Mission, Requirements, Assumptions, CATEGORIES, to_dict
from .physics import (G, KT, rho_at, RHO0, make_geometry, cd0_buildup, oswald, Polar, raymer_ga,
                      risen_l1, powertrain_mass, STRUCT_GROUPS, SYSTEM_GROUPS)
from .performance import (pw_constraints, ws_limits, constraint_diagram, takeoff, landing, roc_max, v_max,
                          service_ceiling, v_stall)
from .mission import Flight

# Risen 912iS reference airframe (datasheet + risen_eas Reference). Calibration point.
RISEN = dict(mtom_kg=600.0, S_m2=9.70, span_m=8.99, P_kw=75.0, seats=2, layout="side_by_side",
             gear="retractable", cd0_elec=0.0120, v_cal_ms=60.0, h_cal_m=1500.0)


# ------------------------------------------------------------------------------------------
# calibration
# ------------------------------------------------------------------------------------------
def calibration(A: Assumptions):
    r = RISEN
    g = make_geometry(r["mtom_kg"], r["S_m2"], r["span_m"], r["seats"], r["layout"], A)
    q = 0.5 * rho_at(r["h_cal_m"]) * r["v_cal_ms"] ** 2
    kg = raymer_ga(g, r["mtom_kg"], r["seats"], r["gear"], q, A)
    struct = sum(kg[k] for k in STRUCT_GROUPS)
    systems = sum(kg[k] for k in SYSTEM_GROUPS)
    cd0_raw, _ = cd0_buildup(g, r["v_cal_ms"], r["h_cal_m"], A)
    return dict(raymer_struct_ref_kg=struct, raymer_systems_ref_kg=systems, cd0_raw_ref=cd0_raw,
                k_struct=A.ref_struct_kg / struct, k_sys=A.ref_systems_kg / systems,
                k_cd0=r["cd0_elec"] / cd0_raw)


# ------------------------------------------------------------------------------------------
# airframe build: steps 3-6
# ------------------------------------------------------------------------------------------
@dataclass
class Airframe:
    M: float
    S: float
    b: float
    geom: object
    polar: Polar
    cd0_source: str
    cd0_parts: dict
    e: float
    P: float
    P_driver: str
    ws_driver: str
    pw_req: dict
    ws_lim: dict
    groups: dict
    oew: float
    vs0_limit_kt: float


def vs0_limit(inp, req):
    vals = [v for v in (req.vs0_max_kt, CATEGORIES[inp.category]["vs0_kt"]) if v]
    return min(vals) if vals else None


def build_airframe(M, inp: DesignInputs, msn: Mission, req: Requirements, A: Assumptions, cal) -> Airframe:
    W = M * G
    vlim = vs0_limit(inp, req)
    ws_lim = ws_limits(A, req, vlim)
    # --- step 3a: wing loading -> wing area
    if inp.geometry_mode == "constraint":
        if inp.wing_loading_kgm2 > 0:
            ws, ws_driver = inp.wing_loading_kgm2 * G, "user"
        else:
            ws_driver = min(ws_lim, key=ws_lim.get)
            ws = ws_lim[ws_driver]
        S = W / ws
        b = math.sqrt(A.aspect_ratio * S)
    elif inp.geometry_mode == "fixed":
        S, b, ws_driver = inp.wing_area_m2, inp.span_m, "fixed geometry"
    else:  # scaled_reference
        S = RISEN["S_m2"] * M / RISEN["mtom_kg"]
        b = math.sqrt(RISEN["span_m"] ** 2 / RISEN["S_m2"] * S)
        ws_driver = "Risen W/S"
    # --- step 4: geometry
    geom = make_geometry(M, S, b, inp.seats, inp.seat_layout, A)
    # --- step 5: aerodynamics
    V_cr = msn.v_cruise_kmh / 3.6
    cd0_raw, parts = cd0_buildup(geom, V_cr, msn.h_cruise_m, A)
    kc = cal["k_cd0"] if A.calibrate_cd0 else 1.0
    cd0_gear = A.gear_dq_m2 / S if inp.gear == "fixed" else 0.0
    parts = {k: v * (1 + A.misc_drag_frac) * kc for k, v in parts.items()}
    parts["gear"] = cd0_gear
    cd0 = cd0_raw * kc + cd0_gear
    e = oswald(geom.AR, A)
    k = 1 / (math.pi * geom.AR * e)
    src = "build-up" + (" (Risen-calibrated)" if A.calibrate_cd0 else "")
    if A.cd0_override > 0:
        cd0, src = A.cd0_override, "override"
    if A.ld_override > 0:
        cd0, src = 1 / (4 * k * A.ld_override ** 2), "from L/D override"
    polar = Polar(cd0, k, S)
    # --- step 3b: power from constraints
    pw = pw_constraints(W / S, W, cd0, k, A, msn, req)
    finite = {n: v for n, v in pw.items() if math.isfinite(v)}
    if inp.installed_power_kw > 0:
        P, P_driver = inp.installed_power_kw * 1e3, "user"
    elif inp.geometry_mode == "scaled_reference":
        P, P_driver = RISEN["P_kw"] * 1e3 * M / RISEN["mtom_kg"], "Risen P/W"
    else:
        P_driver = max(finite, key=finite.get) if finite else "none"
        P = (finite[P_driver] if finite else 0.0) * W * (1 + A.power_margin)
    # --- step 6: weights
    q_cr = 0.5 * rho_at(msn.h_cruise_m) * V_cr ** 2
    if A.weight_method == "risen_L1":
        groups = risen_l1(M, S, inp.seats, dict(mtom_kg=RISEN["mtom_kg"], S_m2=RISEN["S_m2"],
                                                  struct_kg=A.ref_struct_kg, systems_kg=A.ref_systems_kg))
    else:
        groups = raymer_ga(geom, M, inp.seats, inp.gear, q_cr, A)
        if A.weight_method == "raymer_calibrated":
            for g_ in STRUCT_GROUPS:
                groups[g_] *= cal["k_struct"]
            for g_ in SYSTEM_GROUPS:
                groups[g_] *= cal["k_sys"]
    groups.update(powertrain_mass(P / 1e3, A))
    oew = sum(groups.values())
    if inp.oew_override_kg > 0:
        oew = inp.oew_override_kg
    return Airframe(M, S, b, geom, polar, src, parts, e, P, P_driver, ws_driver, pw, ws_lim, groups, oew, vlim)


def payload_of(inp: DesignInputs):
    if inp.payload_override_kg > 0:
        return inp.payload_override_kg
    return inp.seats * (inp.occupant_kg + inp.baggage_per_seat_kg)


# ------------------------------------------------------------------------------------------
# mass closure
# ------------------------------------------------------------------------------------------
def battery_required(af: Airframe, inp, msn, A):
    """Mode B: pack mass [kg] needed by energy, power and C-rate, and the governing one."""
    fl = Flight(af.M, af.polar, af.P, 1.0, A, msn)
    value = inp.target_range_km if inp.target == "range" else inp.target_endurance_h
    E_use = fl.energy_needed_Wh(inp.target, value, msn.v_cruise_kmh / 3.6)
    soh = A.soh_eol if inp.size_at_eol else 1.0
    P_peak = af.P / A.eta_elec
    need = dict(energy=E_use / (A.dod * soh) / A.pack_wh_kg,
                specific_power=P_peak / A.pack_w_kg_max,
                c_rate=P_peak / (A.c_rate_max * A.pack_wh_kg))
    driver = max(need, key=need.get)
    return need[driver], driver, need


def solve_mtom(inp, msn, req, A, cal, payload, m_max=8000.0):
    """Smallest MTOM with OEW(M) + payload + battery(M) <= M (bisection after a geometric scan)."""
    evals = 0

    def resid(M):
        nonlocal evals
        evals += 1
        af = build_airframe(M, inp, msn, req, A, cal)
        pack, drv, need = battery_required(af, inp, msn, A)
        r = af.oew + payload + pack * (1 + A.battery_install_frac) - M
        return (r if math.isfinite(r) else math.inf), af, pack, drv, need

    M_prev = max(payload + 100.0, 200.0)
    r_prev = resid(M_prev)[0]
    best = (r_prev, M_prev)
    M = M_prev
    while M < m_max:
        M = min(M * 1.06, m_max)
        r = resid(M)[0]
        best = min(best, (r, M))
        if r <= 0 and r_prev > 0:
            lo, hi = M_prev, M
            for _ in range(50):
                mid = 0.5 * (lo + hi)
                if resid(mid)[0] > 0:
                    lo = mid
                else:
                    hi = mid
                if hi - lo < 0.05:
                    break
            r, af, pack, drv, need = resid(hi)
            return dict(converged=True, M=hi, af=af, pack=pack, driver=drv, need=need, evals=evals)
        M_prev, r_prev = M, r
    r, af, pack, drv, need = resid(best[1])
    return dict(converged=False, M=best[1], af=af, pack=pack, driver=drv, need=need, evals=evals,
                closest_residual_kg=r)


# ------------------------------------------------------------------------------------------
# evaluation: checks, performance, mission, charts
# ------------------------------------------------------------------------------------------
def _fin(x):
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, float):
        return _fin(round(o, 6))
    return o


def evaluate(af: Airframe, pack_kg, payload, inp, msn, req, A):
    M, W, polar, P = af.M, af.M * G, af.polar, af.P
    S = af.S
    rc, rf = rho_at(msn.h_cruise_m), rho_at(req.field_elevation_m)
    V_cr = msn.v_cruise_kmh / 3.6
    cat = CATEGORIES[inp.category]
    pack_kg = max(pack_kg, 0.0)
    E_nom = pack_kg * A.pack_wh_kg
    fl = Flight(M, polar, P, E_nom, A, msn)
    ph = fl.phases()

    # --- point performance
    vs0 = v_stall(W / S, A.cl_max_land)
    vs1 = v_stall(W / S, A.cl_max_clean)
    vmax_sl = v_max(W, polar, P, A.eta_prop_cruise, RHO0)
    vh = v_max(W, polar, P * A.cont_power_frac, A.eta_prop_cruise, RHO0)
    vmax_cr = v_max(W, polar, P * A.cont_power_frac, A.eta_prop_cruise, rc)
    roc_full = roc_max(W, polar, P, A.eta_prop_climb, rf, A.cl_max_clean)[0]
    roc_cont = roc_max(W, polar, P * A.cont_power_frac, A.eta_prop_climb, rf, A.cl_max_clean)[0]
    ceiling = service_ceiling(W, polar, P * A.cont_power_frac, A)
    to = takeoff(W, S, P, polar.cd0, polar.k, A, req.field_elevation_m)
    ld = landing(W / S, A, req.field_elevation_m)
    p_cr = fl.p_batt(V_cr)
    P_peak = P / A.eta_elec
    perf = dict(
        vs0_kcas=vs0 / KT, vs1_kcas=vs1 / KT, vs0_kmh=vs0 * 3.6, vs1_kmh=vs1 * 3.6,
        v_md_kmh=fl.v_md() * 3.6, v_mp_kmh=fl.v_mp() * 3.6, ld_max=polar.ld_max,
        v_max_sl_full_kmh=vmax_sl and vmax_sl * 3.6, vh_kcas=vh and vh / KT,
        v_max_cruise_alt_cont_kmh=vmax_cr and vmax_cr * 3.6,
        roc_sl_full_ms=roc_full, roc_sl_cont_ms=roc_cont, roc_sl_cont_fpm=roc_cont * 196.85,
        service_ceiling_m=ceiling,
        takeoff_ground_roll_m=to["ground"], takeoff_15m_m=to["total"],
        landing_ground_roll_m=ld["ground"], landing_15m_m=ld["total"],
        p_batt_cruise_kw=p_cr / 1e3, energy_per_km_wh=p_cr / (V_cr * 3.6),
        c_rate_takeoff=P_peak / E_nom if E_nom > 0 else math.inf,
        c_rate_climb=P_peak * msn.climb_power_frac / E_nom if E_nom > 0 else math.inf,
        pack_w_per_kg_takeoff=P_peak / pack_kg if pack_kg > 0 else math.inf,
        battery_volume_l=E_nom / A.pack_wh_l,
    )

    # --- mission
    R_cr, T_cr = fl.fly(V_cr)
    R_cr_eol, _ = fl.fly(V_cr, eol=True)
    R_cr_nores, _ = fl.fly(V_cr, reserve_min=0)
    R_md, _ = fl.fly(fl.v_md())
    _, T_mp = fl.fly(fl.v_mp())
    _, T_mp_eol = fl.fly(fl.v_mp(), eol=True)
    _, T_mp_nores = fl.fly(fl.v_mp(), reserve_min=0)
    R_ideal, T_ideal = fl.ideal() if E_nom > 0 else (0.0, 0.0)
    mission = dict(
        e_nom_kwh=E_nom / 1e3, e_usable_bol_kwh=fl.usable_Wh() / 1e3, e_usable_eol_kwh=fl.usable_Wh(True) / 1e3,
        e_overhead_kwh=ph["E_ovh"] / 1e3, e_reserve_kwh=fl.reserve_Wh() / 1e3,
        climb_roc_ms=ph["roc"], climb_time_min=ph["t_cl"] / 60, climb_dist_km=ph["d_cl"], descent_dist_km=ph["d_ds"],
        range_cruise_km=R_cr, range_cruise_eol_km=R_cr_eol, range_cruise_no_reserve_km=R_cr_nores,
        time_cruise_h=T_cr, range_vmd_km=R_md,
        endurance_vmp_h=T_mp, endurance_vmp_eol_h=T_mp_eol, endurance_vmp_no_reserve_h=T_mp_nores,
        range_ideal_km=R_ideal, endurance_ideal_h=T_ideal,
    )

    # --- step 8: feasibility checks
    checks = []

    def chk(name, value, limit, unit, ok, severity="fail", note=""):
        checks.append(dict(name=name, value=value, limit=limit, unit=unit, ok=bool(ok),
                           severity="pass" if ok else severity, note=note))

    chk("Battery mass positive", pack_kg, 0, "kg", pack_kg > 0)
    if cat["mtom_kg"]:
        chk(f"MTOM within {inp.category}", M, cat["mtom_kg"], "kg", M <= cat["mtom_kg"] + 1e-6)
    if cat["seats"]:
        chk(f"Seats within {inp.category}", inp.seats, cat["seats"], "", inp.seats <= cat["seats"])
    if af.vs0_limit_kt:
        chk("Stall speed V_S0", vs0 / KT, af.vs0_limit_kt, "KCAS", vs0 / KT <= af.vs0_limit_kt + 0.05)
    if cat["vh_kt"] and vh:
        chk(f"V_H within {inp.category}", vh / KT, cat["vh_kt"], "KCAS", vh / KT <= cat["vh_kt"])
    chk("Take-off distance over 15 m", to["total"], req.s_to50_max_m, "m", to["total"] <= req.s_to50_max_m * 1.001)
    chk("Landing distance over 15 m", ld["total"], req.s_ld50_max_m, "m", ld["total"] <= req.s_ld50_max_m * 1.001)
    chk("Rate of climb, continuous power", roc_cont, req.roc_min_ms, "m/s", roc_cont >= req.roc_min_ms * 0.999)
    if req.ceiling_min_m > 0:
        chk("Service ceiling", ceiling, req.ceiling_min_m, "m", ceiling >= req.ceiling_min_m * 0.999)
    chk("Cruise speed reachable at continuous power", vmax_cr and vmax_cr * 3.6, msn.v_cruise_kmh, "km/h",
        bool(vmax_cr) and vmax_cr >= V_cr * 0.999)
    if req.v_max_min_kmh > 0:
        chk("Max level speed, full power, SL", vmax_sl and vmax_sl * 3.6, req.v_max_min_kmh, "km/h",
            bool(vmax_sl) and vmax_sl * 3.6 >= req.v_max_min_kmh * 0.999)
    chk("Climb to cruise altitude", ph["roc"], 0.05, "m/s", ph["ok"])
    chk("Cruise energy left after overhead and reserve", R_cr, 0, "km", R_cr > 0)
    chk("Take-off C-rate", perf["c_rate_takeoff"], A.c_rate_max, "1/h",
        perf["c_rate_takeoff"] <= A.c_rate_max * 1.001, "warn", "above this the Wh/kg value is at risk")
    chk("Pack specific power at take-off", perf["pack_w_per_kg_takeoff"], A.pack_w_kg_max, "W/kg",
        perf["pack_w_per_kg_takeoff"] <= A.pack_w_kg_max * 1.001, "warn", "above this the Wh/kg value is at risk")
    if A.battery_bay_l > 0:
        chk("Battery volume", perf["battery_volume_l"], A.battery_bay_l, "L",
            perf["battery_volume_l"] <= A.battery_bay_l)

    # --- charts
    ws_d = W / S
    cd = constraint_diagram(ws_d, W, polar.cd0, polar.k, A, msn, req, af.vs0_limit_kt)
    cd["design"] = dict(ws_kgm2=M / S, pw_wkg=P / M)
    v_lo = 1.15 * v_stall(W / S, A.cl_max_clean, rc)
    v_hi = vmax_cr or 1.6 * fl.v_md()
    vs = [v_lo + (v_hi - v_lo) * i / 24 for i in range(25)]
    rs = dict(v_kmh=[v * 3.6 for v in vs], range_bol_km=[fl.fly(v)[0] for v in vs],
              range_eol_km=[fl.fly(v, eol=True)[0] for v in vs], p_batt_kw=[fl.p_batt(v) / 1e3 for v in vs],
              v_cruise_kmh=msn.v_cruise_kmh)
    bat_total = pack_kg * (1 + A.battery_install_frac)
    pr_fixed, pr_swap = [], []
    for i in range(11):
        pl = payload * i / 10
        f1 = Flight(af.oew + bat_total + pl, polar, P, E_nom, A, msn)
        pr_fixed.append([pl, f1.fly(V_cr)[0]])
    swap_max = max(M - af.oew, 0.0)
    for i in range(11):
        pl = swap_max * i / 10
        pk = max(M - af.oew - pl, 0.0) / (1 + A.battery_install_frac)
        f2 = Flight(M, polar, P, pk * A.pack_wh_kg, A, msn)
        pr_swap.append([pl, f2.fly(V_cr)[0]])
    charts = dict(constraint=cd, range_speed=rs,
                  payload_range=dict(fixed_pack=pr_fixed, pack_swap_at_mtom=pr_swap, design_payload_kg=payload))
    return perf, mission, checks, charts


# ------------------------------------------------------------------------------------------
# entry point
# ------------------------------------------------------------------------------------------
def run_design(inp: DesignInputs, msn: Mission = None, req: Requirements = None, A: Assumptions = None):
    msn, req, A = msn or Mission(), req or Requirements(), A or Assumptions()
    t0 = time.time()
    messages = []
    cal = calibration(A)
    payload = payload_of(inp)
    closure = {}
    if inp.mode == "solve_mtom":
        sol = solve_mtom(inp, msn, req, A, cal, payload)
        af, pack = sol["af"], sol["pack"]
        closure = dict(converged=sol["converged"], evaluations=sol["evals"], battery_driver=sol["driver"],
                       battery_need_kg=sol["need"])
        if not sol["converged"]:
            messages.append(f"Mass closure did not converge below 8,000 kg (weight snowball). Closest point "
                            f"{sol['M']:.0f} kg is short by {sol['closest_residual_kg']:.0f} kg. "
                            f"Raise pack Wh/kg or L/D, or lower the target.")
    else:
        af = build_airframe(inp.mtom_kg, inp, msn, req, A, cal)
        pack = (inp.mtom_kg - af.oew - payload) / (1 + A.battery_install_frac)
        closure = dict(converged=True, evaluations=1, battery_driver="remaining mass")
        if pack <= 0:
            messages.append(f"No mass left for a battery: OEW {af.oew:.0f} kg + payload {payload:.0f} kg "
                            f"exceeds MTOM {inp.mtom_kg:.0f} kg.")
    perf, mission, checks, charts = evaluate(af, pack, payload, inp, msn, req, A)
    fails = [c for c in checks if c["severity"] == "fail"]
    warns = [c for c in checks if c["severity"] == "warn"]
    status = "infeasible" if (fails or not closure["converged"]) else "warning" if warns else "ok"
    bat_total = max(pack, 0) * (1 + A.battery_install_frac)
    mass = dict(af.groups)
    if inp.oew_override_kg > 0:
        mass = {"oew_override": inp.oew_override_kg}
    mass["battery_pack"] = max(pack, 0)
    mass["battery_installation"] = max(pack, 0) * A.battery_install_frac
    mass["payload"] = payload
    summary = dict(
        mtom_kg=af.M, oew_excl_battery_kg=af.oew, payload_kg=payload, battery_kg=bat_total,
        battery_kwh=max(pack, 0) * A.pack_wh_kg / 1e3, battery_mass_frac=bat_total / af.M,
        empty_frac_incl_battery=(af.oew + bat_total) / af.M,
        wing_area_m2=af.S, span_m=af.b, aspect_ratio=af.geom.AR, wing_loading_kgm2=af.M / af.S,
        installed_power_kw=af.P / 1e3, power_loading_wkg=af.P / af.M,
        cd0=af.polar.cd0, oswald_e=af.e, ld_max=af.polar.ld_max,
        range_cruise_km=mission["range_cruise_km"], range_cruise_eol_km=mission["range_cruise_eol_km"],
        endurance_vmp_h=mission["endurance_vmp_h"], vs0_kcas=perf["vs0_kcas"],
        takeoff_15m_m=perf["takeoff_15m_m"], roc_sl_cont_ms=perf["roc_sl_cont_ms"],
    )
    design = dict(
        wing_loading_driver=af.ws_driver, power_driver=af.P_driver, cd0_source=af.cd0_source,
        cd0_parts=af.cd0_parts, pw_required_wkg={k: (v * G if math.isfinite(v) else None) for k, v in af.pw_req.items()},
        ws_limits_kgm2={k: v / G for k, v in af.ws_lim.items()}, vs0_limit_kt=af.vs0_limit_kt,
        geometry=af.geom.as_dict(), eta_chain_cruise=A.eta_chain_cruise, eta_elec=A.eta_elec,
    )
    out = dict(status=status, messages=messages, summary=summary, mass_kg=mass, design=design,
               performance=perf, mission=mission, checks=checks, closure=closure, calibration=cal, charts=charts,
               runtime_s=time.time() - t0)
    return clean(out)


def run_from_dict(d: dict):
    """Run from a JSON-style dict {inputs, mission, requirements, assumptions} (partial dicts allowed)."""
    from .model import coerce
    inp = coerce(DesignInputs, d.get("inputs", {}))
    msn = coerce(Mission, d.get("mission", {}))
    req = coerce(Requirements, d.get("requirements", {}))
    A = coerce(Assumptions, d.get("assumptions", {}))
    res = run_design(inp, msn, req, A)
    return dict(inputs=to_dict(inp), mission=to_dict(msn), requirements=to_dict(req), assumptions=to_dict(A),
                results=res)
