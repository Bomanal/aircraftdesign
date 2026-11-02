#!/usr/bin/env python3
"""
risen_eas.py - Weight / Performance / Cost estimator for battery-electric light aircraft
scaled from the Risen 912iS reference airframe (600 kg MTOM).

Implements the method in "Electric Aircraft Weight-Performance-Cost Estimation Method":
  inputs (payload, MTOM or battery mass, pack Wh/kg, mission, utilisation)
    -> geometry & mass closure (fixed-MTOM or solved-MTOM)
    -> parabolic-polar aerodynamics & battery-side power
    -> mission range / endurance (BOL / EOL, reserve)
    -> operating cost (fixed + variable + per-pack battery wear), ICE comparator.

Fidelity: aero L0 (fixed parabolic polar), mass L1 (scaled fraction), mission M0
(prescribed sortie), constant pack specific energy. SI units internally.

Run:  python3 risen_eas.py            -> report + verification against reference metrics
      python3 risen_eas.py --verify   -> verification table only (exit code 1 on failure)
"""
from __future__ import annotations
import math, sys
from dataclasses import dataclass, field, replace

G = 9.80665

# --------------------------------------------------------------------------------------
# 1. INPUTS & ASSUMPTIONS
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Reference:                  # Risen 912iS reference airframe (datasheet + estimates)
    mtom_kg: float = 600.0
    wing_area_m2: float = 9.70
    span_m: float = 8.99
    cd0: float = 0.012            # electric: ICE-calibrated CD0 (0.0128, see polar_check) less ~6 % engine cooling/exhaust drag
    oswald_e: float = 0.80
    cl_max_clean: float = 1.6
    m_struct_kg: float = 190.0    # structure (wing, fuselage, empennage, gear)
    m_fixed_kg: float = 45.0      # avionics, seats, BRS, interior
    m_prop_kg: float = 15.0
    p_shaft_W: float = 75e3       # installed shaft power (electric replacement of 912iS)
    m_pt_kg: float = 30.0         # e-powertrain: motor, inverter, cooling, HV harness (2.5 kW/kg)
    empty_kg_ice: float = 325.0   # datasheet empty mass (ICE)
    fuel_L: float = 100.7

@dataclass(frozen=True)
class Tech:
    e_pack_Wh_kg: float = 300.0   # PACK level
    dod: float = 0.90
    eol: float = 0.80
    eta_prop_cruise: float = 0.80
    eta_prop_climb: float = 0.75
    eta_motor: float = 0.95
    eta_inv: float = 0.98
    eta_wire: float = 0.99
    eta_batt: float = 0.96
    eta_charge_cell: float = 0.96
    eta_charger: float = 0.92
    @property
    def eta_elec(self):   # battery -> shaft
        return self.eta_motor * self.eta_inv * self.eta_wire * self.eta_batt
    @property
    def eta_chain(self):  # battery -> thrust power (cruise)
        return self.eta_prop_cruise * self.eta_elec

@dataclass(frozen=True)
class Mission:
    h_cruise_m: float = 1500.0
    v_cruise_ms: float = 60.0     # 216 km/h
    reserve_min: float = 30.0     # flown at V_md (long-range power)
    taxi_kW_ref: float = 3.0      # scaled with MTOM/600
    taxi_min: float = 5.0
    takeoff_min: float = 1.0      # at full installed power
    climb_power_frac: float = 65 / 75
    v_climb_ms: float = 40.0
    v_descent_ms: float = 55.0
    descent_sys_kW: float = 2.0

@dataclass(frozen=True)
class CostIn:
    eur_inr: float = 110.0
    landed: float = 1.20
    airframe_ex_engine_eur: float = 229_500.0   # Risen 2025 price EUR 249.5k minus engine credit EUR 20k
    airframe_mass_exp: float = 0.75
    ept_eur_ref: float = 40_000.0               # e-powertrain at 75 kW, scales with power
    four_seat_factor: float = 1.05
    pack_inr_kWh: float = 70_000.0
    cycle_life: float = 1500.0                  # cycles to 80 % capacity
    calendar_life_yr: float = 8.0
    tariff_inr_kWh: float = 12.0
    residual: float = 0.30
    coc: float = 0.09
    insurance: float = 0.025
    hangar_inr: float = 3.6e5
    annual_insp_inr: float = 2.0e5
    charger_inr_ref: float = 20e5               # DC charger, scales with MTOM; fitted when U >= charger_min_U
    charger_life_yr: float = 8.0
    handling_kit_inr: float = 5e5               # modular-pack trolley + HV quick-disconnect
    mx_motor_ref: float = 350.0                 # INR/h, scales with MTOM
    mx_prop_ref: float = 300.0                  # INR/h, scales with (MTOM)^0.4
    mx_airframe_ref: float = 800.0              # INR/h, x (1 + 0.6*(s-1))
    # ICE comparator
    fuel_inr_L: float = 120.0
    ice_fuel_Lph_ref: float = 8.57              # 7.57 L/h long-range + 1 L/h taxi/climb allowance
    ice_v_kmh: float = 220.0
    ice_mx_airframe_ref: float = 1500.0
    ice_engine: dict = field(default_factory=lambda: {600: (20_000, 1375), 750: (45_000, 2000),
                                                      1000: (60_000, 2800), 1178: (75_000, 3300)})

def isa_rho(h):
    T = 288.15 - 0.0065 * h
    p = 101325.0 * (T / 288.15) ** 5.25588
    return p / (287.053 * T)

# --------------------------------------------------------------------------------------
# 2. GEOMETRY & MASS CLOSURE
# --------------------------------------------------------------------------------------
@dataclass
class Aircraft:
    name: str
    mtom: float
    s: float          # scale factor MTOM/600
    S: float
    b: float
    AR: float
    k: float
    p_shaft: float
    m_struct: float
    m_fixed: float
    m_prop: float
    m_pt: float
    seats: int
    packs: dict       # name -> mass kg
    @property
    def oew(self): return self.m_struct + self.m_fixed + self.m_prop + self.m_pt

def scale_airframe(name, mtom, seats=2, ref=Reference(), alpha=0.5, wing_exp=1.2):
    s = mtom / ref.mtom_kg
    S = ref.wing_area_m2 * s                     # constant wing loading
    AR = ref.span_m ** 2 / ref.wing_area_m2      # constant aspect ratio
    b = math.sqrt(AR * S)
    m_struct = ref.m_struct_kg * (alpha * s ** wing_exp + (1 - alpha) * s)   # EAS mass L1
    m_fixed = ref.m_fixed_kg + (3 if mtom > 600 else 0) + (3 if mtom > 800 else 0) + (10 if seats == 4 else 0)
    m_prop = ref.m_prop_kg * s ** 0.7
    m_pt = ref.m_pt_kg * s
    k = 1 / (math.pi * AR * ref.oswald_e)
    return Aircraft(name, mtom, s, S, b, AR, k, ref.p_shaft_W * s, m_struct, m_fixed, m_prop, m_pt, seats, {})

def close_fixed_mtom(name, mtom, payload, seats=2):
    """Fixed-MTOM mode: battery takes whatever mass remains."""
    ac = scale_airframe(name, mtom, seats)
    ac.packs = {"main": mtom - ac.oew - payload}
    return ac

def close_solved_mtom(name, packs: dict, loadings: list, seats=4, lo=600.0, hi=2000.0):
    """Solved-MTOM mode: find the smallest MTOM such that every loading case
    (payload_kg, packs_fitted) satisfies OEW(MTOM) + payload + sum(packs fitted) <= MTOM.
    The heaviest loading case is the binding one."""
    worst = max(pl + sum(packs[p] for p in fitted) for pl, fitted in loadings)
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if scale_airframe(name, mid, seats).oew + worst > mid: lo = mid
        else: hi = mid
    ac = scale_airframe(name, hi, seats)
    ac.packs = dict(packs)
    return ac

# --------------------------------------------------------------------------------------
# 3. AERODYNAMICS & PERFORMANCE
# --------------------------------------------------------------------------------------
@dataclass
class FlightState:
    ac: Aircraft
    mass: float
    packs_fitted: tuple   # names of packs carried
    ref: Reference = Reference()
    tech: Tech = Tech()
    msn: Mission = Mission()
    @property
    def W(self): return self.mass * G
    @property
    def m_bat(self): return sum(self.ac.packs[p] for p in self.packs_fitted)
    @property
    def E_nom_Wh(self): return self.m_bat * self.tech.e_pack_Wh_kg

    def drag(self, V, rho):
        q = 0.5 * rho * V * V
        CL = self.W / (q * self.ac.S)
        return q * self.ac.S * (self.ref.cd0 + self.ac.k * CL * CL)
    def p_batt(self, V, rho):
        return self.drag(V, rho) * V / self.tech.eta_chain
    def v_md(self, rho):
        return math.sqrt(2 * self.W / (rho * self.ac.S)) * (self.ac.k / self.ref.cd0) ** 0.25
    def v_mp(self, rho):
        return self.v_md(rho) / 3 ** 0.25
    def ld_max(self):
        return 1 / (2 * math.sqrt(self.ref.cd0 * self.ac.k))

    def phases(self):
        """Sortie overhead: taxi, take-off, climb to cruise altitude, glide descent."""
        t, m = self.tech, self.msn
        rm = isa_rho(m.h_cruise_m / 2)
        E_taxi = m.taxi_kW_ref * 1e3 * self.ac.s * m.taxi_min / 60
        E_to = self.ac.p_shaft / t.eta_elec * m.takeoff_min / 60
        P_cl = self.ac.p_shaft * m.climb_power_frac
        roc = (t.eta_prop_climb * P_cl - self.drag(m.v_climb_ms, rm) * m.v_climb_ms) / self.W
        t_cl = m.h_cruise_m / roc
        E_cl = P_cl / t.eta_elec * t_cl / 3600
        d_cl = m.v_climb_ms * t_cl / 1000
        ld_d = self.W / self.drag(m.v_descent_ms, rm)
        d_ds = m.h_cruise_m * ld_d / 1000
        t_ds = d_ds * 1000 / m.v_descent_ms
        E_ds = m.descent_sys_kW * 1e3 * t_ds / 3600
        return dict(E_ovh=E_taxi + E_to + E_cl + E_ds, roc=roc, t_cl=t_cl, d_cl=d_cl, t_ds=t_ds, d_ds=d_ds)

    def reserve_Wh(self, minutes=None):
        rc = isa_rho(self.msn.h_cruise_m)
        mins = self.msn.reserve_min if minutes is None else minutes
        return self.p_batt(self.v_md(rc), rc) * mins / 60

    def mission(self, V, eol=False, reserve_min=None):
        """Range [km] and airborne time [h] for cruise at V with reserve held back."""
        rc = isa_rho(self.msn.h_cruise_m)
        E_use = self.E_nom_Wh * self.tech.dod * (self.tech.eol if eol else 1.0)
        ph = self.phases()
        E_cr = E_use - ph["E_ovh"] - self.reserve_Wh(reserve_min)
        Pc = self.p_batt(V, rc)
        t_cr = max(E_cr, 0) / Pc
        R = ph["d_cl"] + t_cr * V * 3.6 + ph["d_ds"]
        T = (ph["t_cl"] + ph["t_ds"]) / 3600 + t_cr
        return R, T

    def ideal(self):
        rc = isa_rho(self.msn.h_cruise_m)
        E_use = self.E_nom_Wh * self.tech.dod
        R = E_use * 3600 * self.tech.eta_chain * self.ld_max() / self.W / 1000
        CLmp = math.sqrt(3 * self.ref.cd0 / self.ac.k)
        f = CLmp ** 1.5 / (4 * self.ref.cd0)
        P_min = self.W * math.sqrt(2 * self.W / (rc * self.ac.S)) / f
        return R, E_use * self.tech.eta_chain / P_min

    def checks(self):
        P_to = self.ac.p_shaft / self.tech.eta_elec
        return dict(c_to=P_to / self.E_nom_Wh, c_cl=P_to * self.msn.climb_power_frac / self.E_nom_Wh,
                    w_per_kg=P_to / self.m_bat)

    def sortie(self, t_air_h, V=None):
        """Energy [kWh] and distance [km] of a fixed-duration sortie."""
        V = V or self.msn.v_cruise_ms
        rc = isa_rho(self.msn.h_cruise_m)
        ph = self.phases()
        t_cr = t_air_h - (ph["t_cl"] + ph["t_ds"]) / 3600
        E = (ph["E_ovh"] + self.p_batt(V, rc) * t_cr) / 1e3
        d = ph["d_cl"] + ph["d_ds"] + t_cr * V * 3.6
        feasible_eol = E * 1e3 <= self.E_nom_Wh * self.tech.dod * self.tech.eol - self.reserve_Wh()
        return E, d, feasible_eol

# --------------------------------------------------------------------------------------
# 4. OPERATING COST
# --------------------------------------------------------------------------------------
@dataclass
class Use:            # one operating mode in the mission mix
    state: FlightState
    hours_frac: float
    sortie_h: float
    occupants: int

def electric_cost(ac: Aircraft, uses: list, U: float, c: CostIn = CostIn(), tech: Tech = Tech()):
    s = ac.s
    hf = 1 + 0.6 * (s - 1)                    # hangar/annual/airframe-maintenance growth (+15 % per +25 % MTOM)
    air = (c.airframe_ex_engine_eur * s ** c.airframe_mass_exp + c.ept_eur_ref * s) * c.eur_inr * c.landed
    if ac.seats == 4: air *= c.four_seat_factor
    pack_kWh = {p: m * tech.e_pack_Wh_kg / 1e3 for p, m in ac.packs.items()}
    pack_cost = {p: e * c.pack_inr_kWh for p, e in pack_kWh.items()}
    B = sum(pack_cost.values())
    life = 10 if U >= 1000 else 15
    dep = air * (1 - c.residual) / life
    coc = (air * (1 + c.residual) / 2 + B / 2) * c.coc
    ins = c.insurance * (air + B)
    hang = (c.hangar_inr + c.annual_insp_inr) * hf
    infra_capex = (c.charger_inr_ref * s if U >= 600 else 0) + (c.handling_kit_inr if len(ac.packs) > 1 else 0)
    infra = infra_capex / c.charger_life_yr + infra_capex / 2 * c.coc
    fixed = dep + coc + ins + hang + infra
    # sorties, energy throughput per pack (parallel packs share by capacity)
    thr = {p: 0.0 for p in ac.packs}
    E_tot = km = seat_km = 0.0
    for u in uses:
        n = U * u.hours_frac / u.sortie_h
        E, d, _ = u.state.sortie(u.sortie_h)
        cap = sum(pack_kWh[p] for p in u.state.packs_fitted)
        for p in u.state.packs_fitted: thr[p] += n * E * pack_kWh[p] / cap
        E_tot += n * E; km += n * d; seat_km += n * d * u.occupants
    batt_yr, lives = 0.0, {}
    for p in ac.packs:
        efc = thr[p] / (pack_kWh[p] * 0.9)
        L = min(c.cycle_life / efc, c.calendar_life_yr) if efc > 0 else c.calendar_life_yr
        lives[p] = L; batt_yr += pack_cost[p] / L
    elec = E_tot / (tech.eta_charge_cell * tech.eta_charger) * c.tariff_inr_kWh
    mx = (c.mx_motor_ref * s + c.mx_prop_ref * s ** 0.4 + c.mx_airframe_ref * hf) * U
    total = fixed + elec + batt_yr + mx
    return dict(capex_air_cr=air / 1e7, packs_L=B / 1e5, per_h=total / U, fixed_h=fixed / U,
                dep_h=dep / U, coc_h=coc / U, ins_h=ins / U, hang_h=hang / U, infra_h=infra / U,
                elec_h=elec / U, batt_h=batt_yr / U, mx_h=mx / U, per_km=total / km,
                per_seat_km=total / seat_km, pack_life=lives, annual_L=total / 1e5, km_yr=km)

def ice_cost(mtom_key, U, seats_avg=2.0, c: CostIn = CostIn(), four_seat=False, fuel_inr_L=None):
    s = mtom_key / 600
    hf = 1 + 0.6 * (s - 1)
    eng_eur, eng_res = c.ice_engine[mtom_key]
    air = (c.airframe_ex_engine_eur * s ** c.airframe_mass_exp + eng_eur) * c.eur_inr * c.landed
    if four_seat: air *= c.four_seat_factor
    life = 10 if U >= 1000 else 15
    fixed = air * (1 - c.residual) / life + air * (1 + c.residual) / 2 * c.coc + c.insurance * air \
        + (c.hangar_inr + c.annual_insp_inr) * hf
    fuel = c.ice_fuel_Lph_ref * s * (fuel_inr_L or c.fuel_inr_L)
    var = fuel + eng_res + c.mx_prop_ref * s ** 0.4 + c.ice_mx_airframe_ref * hf
    total = fixed + var * U
    return dict(capex_air_cr=air / 1e7, per_h=total / U, fixed_h=fixed / U, fuel_h=fuel, var_h=var,
                per_km=total / (U * c.ice_v_kmh), per_seat_km=total / (U * c.ice_v_kmh) / seats_avg)

# --------------------------------------------------------------------------------------
# 5. CONFIGURATIONS
# --------------------------------------------------------------------------------------
def build_configs():
    occ2 = 2 * 85 + 10
    occ4 = 4 * 85 + 20
    e600 = close_fixed_mtom("E600 2-seat electric", 600, occ2)
    e750 = close_fixed_mtom("E750 2-seat electric", 750, occ2)
    optA = close_solved_mtom("Option A 4-seat modular", {"main": 274.0, "removable": 140.0},
                            [(occ2, ("main", "removable")), (occ4, ("main",))])
    st = {
        "E600": FlightState(e600, 600, ("main",)),
        "E750": FlightState(e750, 750, ("main",)),
        "A1": FlightState(optA, optA.oew + occ2 + 414, ("main", "removable")),
        "A2": FlightState(optA, optA.oew + occ4 + 274, ("main",)),
    }
    return dict(e600=e600, e750=e750, optA=optA), st

def all_metrics():
    acs, st = build_configs()
    out = {}
    for key, fs in st.items():
        rc = isa_rho(fs.msn.h_cruise_m)
        vmd, vmp = fs.v_md(rc), fs.v_mp(rc)
        Ri, Ti = fs.ideal()
        ph = fs.phases(); ck = fs.checks()
        out[key] = dict(
            mtom=fs.ac.mtom, tom=fs.mass, oew=fs.ac.oew, m_bat=fs.m_bat, E_kWh=fs.E_nom_Wh / 1e3,
            S=fs.ac.S, b=fs.ac.b, P_kW=fs.ac.p_shaft / 1e3, vmd_kmh=vmd * 3.6, vmp_kmh=vmp * 3.6,
            P216_kW=fs.p_batt(60, rc) / 1e3, ovh_kWh=ph["E_ovh"] / 1e3, res_kWh=fs.reserve_Wh() / 1e3,
            roc=ph["roc"], R_ideal=Ri, T_ideal=Ti,
            R_vmd=fs.mission(vmd)[0], R_216=fs.mission(60)[0], R_216_nores=fs.mission(60, reserve_min=0)[0],
            R_216_eol=fs.mission(60, eol=True)[0], R_252=fs.mission(70)[0],
            T_vmp=fs.mission(vmp)[1], T_vmp_nores=fs.mission(vmp, reserve_min=0)[1],
            T_vmp_eol=fs.mission(vmp, eol=True)[1], **ck)
    # costs
    occ = {"E600": 2, "E750": 2}
    for key, ac in (("E600", acs["e600"]), ("E750", acs["e750"])):
        for U in (150, 600, 1200):
            out[key][f"cost{U}"] = electric_cost(ac, [Use(st[key], 1.0, 1.0, occ[key])], U)
    for U in (150, 600, 1200):
        for mix, f2 in (("2", 1.0), ("mix", 0.5), ("4", 0.0)):
            uses = [u for u in (Use(st["A1"], f2, 1.0, 2), Use(st["A2"], 1 - f2, 0.70, 4)) if u.hours_frac > 0]
            out.setdefault("A", {})[f"cost{U}_{mix}"] = electric_cost(acs["optA"], uses, U)
    out["A"]["sortie_A1"] = st["A1"].sortie(1.0)
    out["A"]["sortie_A2"] = st["A2"].sortie(0.70)
    out["ICE"] = {f"cost{U}": ice_cost(600, U) for U in (150, 600, 1200)}
    out["ICE750"] = {f"cost{U}": ice_cost(750, U) for U in (150, 1200)}
    out["ICE_A"] = {f"cost{U}": ice_cost(1178, U, seats_avg=3.0, four_seat=True) for U in (150, 600, 1200)}
    out["ICE_A"]["cost1200_avgas"] = ice_cost(1178, 1200, seats_avg=3.0, four_seat=True, fuel_inr_L=200)
    out["ICE"]["polar_check"] = polar_check()
    return out

def polar_check():
    """Calibrate the ICE polar on the Risen 912iS datasheet max-speed point (73.5 kW shaft at
    175 kt TAS, SL, 600 kg, eta_p 0.80) and check it against the independent 75 %-power point
    (159 kt -> 0.75 x 73.5 = 55.1 kW)."""
    ref = Reference(); AR = ref.span_m ** 2 / ref.wing_area_m2; k = 1 / (math.pi * AR * ref.oswald_e)
    W = 600 * G; rho = isa_rho(0); S = ref.wing_area_m2; kt = 0.514444
    V = 175 * kt; q = 0.5 * rho * V * V; CL = W / (q * S)
    cd0_ice = 73.5e3 * 0.80 / V / (q * S) - k * CL * CL
    def p_shaft(V, cd0):
        q = 0.5 * rho * V * V; CL = W / (q * S)
        return q * S * (cd0 + k * CL * CL) * V / 0.80
    return dict(cd0_ice=cd0_ice, cooling_drag_removed=1 - ref.cd0 / cd0_ice,
                P_75pct_159kt_kW=p_shaft(159 * kt, cd0_ice) / 1e3,   # datasheet: 55.1 kW
                LDmax_ice=1 / (2 * math.sqrt(cd0_ice * k)),          # datasheet glide 23:1 (prop feathered)
                LDmax_elec=1 / (2 * math.sqrt(ref.cd0 * k)))

# --------------------------------------------------------------------------------------
# 6. VERIFICATION against the metrics published in the configuration document
# --------------------------------------------------------------------------------------
# (path, expected, rel_tol). Expected values = figures published in the configuration document.
EXPECTED = [
    # --- E600 ---
    ("E600.m_bat", 140.0, 0.005), ("E600.E_kWh", 42.0, 0.005), ("E600.vmd_kmh", 172, 0.01),
    ("E600.vmp_kmh", 131, 0.01), ("E600.P216_kW", 26.4, 0.01), ("E600.ovh_kWh", 6.82, 0.02),
    ("E600.res_kWh", 9.5, 0.02), ("E600.R_ideal", 342, 0.01), ("E600.R_vmd", 233, 0.01),
    ("E600.R_216", 215, 0.01), ("E600.R_216_nores", 293, 0.01), ("E600.R_216_eol", 153, 0.015),
    ("E600.T_vmp", 1.50, 0.015), ("E600.T_ideal", 2.26, 0.01), ("E600.c_to", 2.02, 0.01),
    ("E600.cost1200.per_h", 8720, 0.01), ("E600.cost1200.per_km", 41.8, 0.01),
    ("E600.cost150.per_h", 40250, 0.01),
    # --- E750 ---
    ("E750.m_bat", 224.0, 0.005), ("E750.E_kWh", 67.2, 0.005), ("E750.oew", 346.0, 0.005),
    ("E750.R_ideal", 438, 0.01), ("E750.R_vmd", 329, 0.01), ("E750.R_216", 302, 0.01),
    ("E750.R_216_eol", 223, 0.015), ("E750.T_vmp", 2.14, 0.015), ("E750.T_ideal", 2.90, 0.01),
    ("E750.c_to", 1.58, 0.01), ("E750.cost1200.per_h", 10520, 0.01), ("E750.cost1200.per_km", 50.5, 0.01),
    ("E750.cost150.per_h", 49370, 0.01),
    # --- Option A ---
    ("A1.mtom", 1177.9, 0.002), ("A1.oew", 543.9, 0.003), ("A1.tom", 1137.9, 0.003),
    ("A1.S", 19.04, 0.005), ("A1.R_ideal", 533, 0.01), ("A1.R_vmd", 426, 0.01), ("A1.R_216", 384, 0.01),
    ("A1.R_216_eol", 289, 0.015), ("A1.T_vmp", 2.82, 0.015), ("A1.c_to", 1.34, 0.01),
    ("A2.tom", 1177.9, 0.002), ("A2.R_ideal", 341, 0.01), ("A2.R_vmd", 233, 0.01), ("A2.R_216", 215, 0.01),
    ("A2.R_216_eol", 153, 0.015), ("A2.T_vmp", 1.50, 0.015), ("A2.c_to", 2.02, 0.01),
    ("A.cost1200_mix.per_h", 15870, 0.01), ("A.cost1200_mix.per_km", 76.6, 0.01),
    ("A.cost1200_mix.per_seat_km", 25.6, 0.01), ("A.cost1200_4.per_seat_km", 19.8, 0.015),
    ("A.cost600_mix.per_h", 23550, 0.01), ("A.cost150_mix.per_h", 76060, 0.01),
    # --- ICE references ---
    ("ICE.cost150.per_h", 36500, 0.01), ("ICE.cost1200.per_h", 8880, 0.01), ("ICE.cost1200.per_km", 40.4, 0.01),
    ("ICE_A.cost1200.per_h", 16890, 0.01), ("ICE_A.cost1200_avgas.per_h", 18240, 0.01),
    ("ICE.polar_check.cd0_ice", 0.0128, 0.02), ("ICE.polar_check.P_75pct_159kt_kW", 55.1, 0.05),
]

def get(d, path):
    for p in path.split("."): d = d[p]
    return d

def verify(m, show=True):
    fails = 0
    rows = []
    for path, exp, tol in EXPECTED:
        v = get(m, path); err = (v - exp) / exp
        ok = abs(err) <= tol; fails += not ok
        rows.append((path, exp, v, err, tol, ok))
    if show:
        print(f"{'metric':38s} {'expected':>10s} {'code':>10s} {'err':>8s} {'tol':>6s}")
        for path, exp, v, err, tol, ok in rows:
            print(f"{path:38s} {exp:10.2f} {v:10.2f} {err*100:7.2f}% {tol*100:5.1f}% {'OK' if ok else 'FAIL'}")
        print(f"\n{len(rows)-fails}/{len(rows)} metrics within tolerance")
    return fails

def report(m):
    def r(x, n=0): return f"{x:,.{n}f}"
    print("PERFORMANCE (1,500 m ISA, 216 km/h cruise unless noted, 30-min reserve at V_md)")
    hdr = ["", "MTOM", "TOM", "OEW", "bat kg", "kWh", "R_ideal", "R@Vmd", "R@216", "R@216 EOL", "R@252", "T@Vmp", "T EOL", "C_TO"]
    print("  ".join(f"{h:>9s}" for h in hdr))
    for k in ("E600", "E750", "A1", "A2"):
        x = m[k]
        vals = [k, r(x['mtom'],1), r(x['tom'],1), r(x['oew'],1), r(x['m_bat'],1), r(x['E_kWh'],1), r(x['R_ideal']),
                r(x['R_vmd']), r(x['R_216']), r(x['R_216_eol']), r(x['R_252']), r(x['T_vmp'],2), r(x['T_vmp_eol'],2), r(x['c_to'],2)]
        print("  ".join(f"{v:>9s}" for v in vals))
    print("\nCOST (INR per flight hour / per km / per seat-km)")
    for k in ("E600", "E750"):
        for U in (150, 600, 1200):
            c = m[k][f"cost{U}"]
            print(f"  {k:5s} {U:5d} h/yr  {r(c['per_h']):>8s}  {c['per_km']:7.1f}  {c['per_seat_km']:6.1f}  batt {r(c['batt_h'])}/h")
    for U in (150, 600, 1200):
        for mix in ("2", "mix", "4"):
            c = m["A"][f"cost{U}_{mix}"]
            print(f"  A-{mix:3s} {U:5d} h/yr  {r(c['per_h']):>8s}  {c['per_km']:7.1f}  {c['per_seat_km']:6.1f}  "
                  f"pack life {', '.join(f'{p}={L:.2f}y' for p, L in c['pack_life'].items())}")
    for key in ("ICE", "ICE750", "ICE_A"):
        for kk, c in m[key].items():
            if kk.startswith("cost"):
                print(f"  {key:6s} {kk:15s} {r(c['per_h']):>8s}  {c['per_km']:7.1f}  {c['per_seat_km']:6.1f}")
    print("\nPOLAR CHECK vs datasheet:", {k: round(v, 2) for k, v in m["ICE"]["polar_check"].items()})

if __name__ == "__main__":
    m = all_metrics()
    if "--verify" not in sys.argv:
        report(m); print()
    sys.exit(1 if verify(m) else 0)
