"""
Step 1 - inputs, mission, requirements and the assumption register.

Every field carries metadata (label, unit, range, source, group) so the UI and the reports can
show it next to the results. Fields with assumption=True are the variable assumptions that
drive the design; they are flagged ASSUMPTION everywhere they are shown.
"""
from __future__ import annotations
from dataclasses import dataclass, field, fields, asdict, replace


def p(default, label, unit="", lo=None, hi=None, source="", group="", options=None,
      assumption=False, sens="", help=""):
    return field(default=default, metadata=dict(
        label=label, unit=unit, lo=lo, hi=hi, source=source, group=group, options=options,
        assumption=assumption, sens=sens, help=help))


def A(default, label, unit="", lo=None, hi=None, source="", group="", options=None, sens="", help=""):
    return p(default, label, unit, lo, hi, source, group, options, True, sens, help)


# ------------------------------------------------------------------------------------------
# Design inputs
# ------------------------------------------------------------------------------------------
@dataclass
class DesignInputs:
    mode: str = p("fixed_mtom", "Sizing mode", options=["fixed_mtom", "solve_mtom"], group="Design",
                  help="fixed_mtom: MTOM given, battery takes the remaining mass. "
                       "solve_mtom: smallest MTOM that meets the target range/endurance.")
    mtom_kg: float = p(750.0, "MTOM (fixed-MTOM mode)", "kg", 300, 3000, group="Design")
    target: str = p("range", "Target (solve-MTOM mode)", options=["range", "endurance"], group="Design")
    target_range_km: float = p(250.0, "Target range incl. reserve held back", "km", 20, 2000, group="Design")
    target_endurance_h: float = p(1.5, "Target endurance incl. reserve held back", "h", 0.2, 10, group="Design")
    size_at_eol: bool = p(True, "Meet the target at end of pack life", group="Design",
                          help="Solve-MTOM mode: size the pack so the target is met at end-of-life capacity.")
    seats: int = p(2, "Seats (occupants)", "", 1, 4, group="Payload")
    seat_layout: str = p("side_by_side", "Seat layout", options=["side_by_side", "tandem"], group="Payload")
    occupant_kg: float = p(85.0, "Mass per occupant", "kg", 60, 110, group="Payload",
                           source="risen_eas (2 x 85 + 10 kg)")
    baggage_per_seat_kg: float = p(5.0, "Baggage per seat", "kg", 0, 30, group="Payload")
    payload_override_kg: float = p(0.0, "Payload override (0 = from seats)", "kg", 0, 1000, group="Payload")
    gear: str = p("retractable", "Landing gear", options=["retractable", "fixed"], group="Configuration")
    geometry_mode: str = p("constraint", "Geometry", options=["constraint", "fixed", "scaled_reference"],
                           group="Configuration",
                           help="constraint: wing area and power from the W/S-P/W constraint diagram. "
                                "fixed: user wing area, span and power (conversion of an existing type). "
                                "scaled_reference: Risen scaled at constant W/S, AR and P/W (risen_eas law).")
    wing_loading_kgm2: float = p(0.0, "Wing loading (0 = highest allowed by stall/landing)", "kg/m2", 0, 200,
                                 group="Configuration")
    installed_power_kw: float = p(0.0, "Installed shaft power (0 = from constraints)", "kW", 0, 500,
                                  group="Configuration")
    wing_area_m2: float = p(9.70, "Wing area (fixed geometry)", "m2", 4, 40, group="Configuration")
    span_m: float = p(8.99, "Wing span (fixed geometry)", "m", 5, 25, group="Configuration")
    oew_override_kg: float = p(0.0, "OEW excl. battery override (0 = computed)", "kg", 0, 3000,
                               group="Configuration",
                               help="For conversions where the empty mass of the airframe is known.")
    category: str = p("CS-VLA", "Certification category", options=["EU-UL600", "CS-VLA", "MOSAIC-LSA", "CS-23-L1", "none"],
                      group="Configuration")


@dataclass
class Mission:
    v_cruise_kmh: float = p(216.0, "Cruise speed (TAS)", "km/h", 80, 400, group="Mission")
    h_cruise_m: float = p(1500.0, "Cruise altitude (ISA)", "m", 0, 6000, group="Mission")
    reserve_min: float = A(30.0, "Reserve, flown at V_md", "min", 0, 60, source="risen_eas",
                           group="Mission", sens="high for short missions")
    taxi_min: float = p(5.0, "Taxi time", "min", 0, 30, group="Mission")
    taxi_kw_per_t: float = A(5.0, "Taxi power per tonne MTOM", "kW/t", 1, 15, source="risen_eas (3 kW at 600 kg)",
                             group="Mission", sens="low")
    takeoff_min: float = p(1.0, "Take-off at full power", "min", 0, 5, group="Mission")
    climb_power_frac: float = A(0.867, "Climb power / installed power", "-", 0.5, 1.0,
                                source="risen_eas (65/75 kW)", group="Mission", sens="low")
    v_climb_kmh: float = p(0.0, "Climb speed (0 = 1.1 x V_mp)", "km/h", 0, 300, group="Mission")
    v_descent_kmh: float = p(0.0, "Descent glide speed (0 = 1.15 x V_md)", "km/h", 0, 350, group="Mission")
    descent_sys_kw: float = A(2.0, "Systems power in descent", "kW", 0, 10, source="risen_eas", group="Mission",
                              sens="low")


@dataclass
class Requirements:
    vs0_max_kt: float = p(0.0, "Max stall speed V_S0 (0 = category limit)", "KCAS", 0, 90, group="Requirements")
    s_to50_max_m: float = p(500.0, "Max take-off distance over 15 m", "m", 100, 2000, group="Requirements")
    s_ld50_max_m: float = p(500.0, "Max landing distance over 15 m", "m", 100, 2000, group="Requirements")
    roc_min_ms: float = p(3.0, "Min sea-level rate of climb (continuous power)", "m/s", 0.5, 10, group="Requirements",
                          help="3.0 m/s = 590 fpm. Check the applicable code; this is a design requirement.")
    ceiling_min_m: float = p(3000.0, "Min service ceiling (0.5 m/s, continuous power)", "m", 0, 8000,
                             group="Requirements")
    v_max_min_kmh: float = p(0.0, "Min max level speed at full power, SL (0 = off)", "km/h", 0, 450,
                             group="Requirements")
    field_elevation_m: float = p(0.0, "Field elevation for take-off / landing (ISA)", "m", 0, 3000,
                                 group="Requirements")


# Category presets: VS0 limit [KCAS], MTOM limit [kg] (None = no limit), VH limit [KCAS], max seats
CATEGORIES = {
    "EU-UL600": dict(vs0_kt=45.0, mtom_kg=600.0, vh_kt=None, seats=2),
    "CS-VLA": dict(vs0_kt=45.0, mtom_kg=750.0, vh_kt=None, seats=2),
    "MOSAIC-LSA": dict(vs0_kt=61.0, mtom_kg=None, vh_kt=250.0, seats=4),
    "CS-23-L1": dict(vs0_kt=61.0, mtom_kg=None, vh_kt=None, seats=4),
    "none": dict(vs0_kt=None, mtom_kg=None, vh_kt=None, seats=None),
}


# ------------------------------------------------------------------------------------------
# Assumption register
# ------------------------------------------------------------------------------------------
@dataclass
class Assumptions:
    # --- battery ---
    pack_wh_kg: float = A(300.0, "Pack specific energy (pack level)", "Wh/kg", 150, 500, group="Battery",
                          source="risen_eas default. In-service/certified packs ~160-250 Wh/kg; "
                                 "cell-to-pack ~0.65-0.75 (NASA)", sens="high (linear in range)")
    pack_w_kg_max: float = A(600.0, "Pack specific power limit", "W/kg", 300, 2000, group="Battery",
                             source="Method doc: >600 W/kg puts the Wh/kg value at risk", sens="sets battery floor")
    c_rate_max: float = A(2.0, "Max discharge C-rate", "1/h", 0.5, 6, group="Battery",
                          source="Method doc: >2C puts the Wh/kg value at risk", sens="sets battery floor")
    pack_wh_l: float = A(450.0, "Pack energy density (volume)", "Wh/L", 150, 900, group="Battery",
                         source="Estimate for aviation Li-ion packs", sens="volume check only")
    battery_bay_l: float = A(0.0, "Available battery volume (0 = not checked)", "L", 0, 2000, group="Battery")
    battery_install_frac: float = A(0.0, "Battery installation mass / pack mass", "-", 0, 0.3, group="Battery",
                                    source="0 in risen_eas (pack-level Wh/kg includes it)", sens="medium")
    dod: float = A(0.90, "Usable depth of discharge", "-", 0.7, 1.0, group="Battery", source="risen_eas",
                   sens="high")
    soh_eol: float = A(0.80, "End-of-life capacity (state of health)", "-", 0.6, 1.0, group="Battery",
                       source="risen_eas", sens="high")
    # --- propulsion ---
    eta_prop_cruise: float = A(0.80, "Propeller efficiency, cruise", "-", 0.6, 0.9, group="Propulsion",
                               source="risen_eas", sens="medium")
    eta_prop_climb: float = A(0.75, "Propeller efficiency, climb", "-", 0.5, 0.9, group="Propulsion",
                              source="risen_eas", sens="medium")
    eta_motor: float = A(0.95, "Motor efficiency", "-", 0.85, 0.98, group="Propulsion", source="risen_eas")
    eta_inverter: float = A(0.98, "Inverter efficiency", "-", 0.9, 0.995, group="Propulsion", source="risen_eas")
    eta_wiring: float = A(0.99, "Wiring efficiency", "-", 0.95, 1.0, group="Propulsion", source="risen_eas")
    eta_battery: float = A(0.96, "Battery discharge efficiency", "-", 0.9, 0.99, group="Propulsion",
                           source="risen_eas")
    pt_kw_per_kg: float = A(2.5, "E-powertrain system specific power", "kW/kg", 1.0, 8.0, group="Propulsion",
                            source="risen_eas (motor+inverter+cooling+HV harness)", sens="low-medium")
    prop_mass_coeff: float = A(0.7304, "Propeller mass coefficient (m = c * P_kW^0.7)", "kg/kW^0.7", 0.3, 1.5,
                               group="Propulsion", source="risen_eas: 15 kg at 75 kW")
    cont_power_frac: float = A(0.867, "Max continuous / installed power", "-", 0.5, 1.0, group="Propulsion",
                               source="risen_eas climb setting 65/75 kW")
    prop_diameter_m: float = A(1.75, "Propeller diameter (static thrust)", "m", 1.0, 3.0, group="Propulsion")
    prop_static_fm: float = A(0.70, "Propeller static figure of merit", "-", 0.5, 0.85, group="Propulsion",
                              source="Momentum theory, typical fixed/variable pitch")
    power_margin: float = A(0.05, "Margin on constraint-derived power", "-", 0, 0.5, group="Propulsion")
    # --- aerodynamics ---
    aspect_ratio: float = A(8.33, "Wing aspect ratio (constraint mode)", "-", 5, 20, group="Aerodynamics",
                            source="Risen 8.99^2/9.70", sens="medium")
    taper: float = A(0.50, "Wing taper ratio", "-", 0.3, 1.0, group="Aerodynamics")
    t_c: float = A(0.15, "Wing thickness / chord", "-", 0.08, 0.20, group="Aerodynamics")
    cl_max_clean: float = A(1.6, "C_Lmax clean", "-", 1.1, 2.0, group="Aerodynamics", source="risen_eas",
                            sens="sets V_S1")
    cl_max_to: float = A(1.8, "C_Lmax take-off flap", "-", 1.2, 2.4, group="Aerodynamics", source="Estimate")
    cl_max_land: float = A(2.0, "C_Lmax landing flap", "-", 1.3, 2.8, group="Aerodynamics", source="Estimate",
                           sens="sets W/S and wing area")
    e_method: str = A("raymer", "Oswald e method", options=["raymer", "fixed"], group="Aerodynamics",
                      source="Raymer straight-wing fit e = 1.78(1-0.045 AR^0.68)-0.64")
    oswald_e: float = A(0.80, "Oswald e (when fixed)", "-", 0.6, 0.95, group="Aerodynamics", source="risen_eas")
    cd0_override: float = A(0.0, "C_D0 override (0 = build-up)", "-", 0, 0.08, group="Aerodynamics",
                            source="Risen electric 0.0120; Hansa-NG ~0.042 (back-calculated)", sens="high")
    ld_override: float = A(0.0, "L/D_max override (0 = computed)", "-", 0, 40, group="Aerodynamics",
                           source="Risen ~20.9 (electric); Hansa-NG ~11", sens="high (linear in range)")
    calibrate_cd0: bool = A(True, "Calibrate C_D0 build-up to Risen (0.0120)", group="Aerodynamics")
    lam_frac_wing: float = A(0.35, "Laminar fraction, wing", "-", 0, 0.7, group="Aerodynamics")
    lam_frac_tail: float = A(0.35, "Laminar fraction, tails", "-", 0, 0.7, group="Aerodynamics")
    lam_frac_fus: float = A(0.15, "Laminar fraction, fuselage", "-", 0, 0.5, group="Aerodynamics")
    misc_drag_frac: float = A(0.05, "Leakage and protuberance drag / C_D0", "-", 0, 0.3, group="Aerodynamics",
                              source="Raymer Ch.12 (2-5 %)")
    gear_dq_m2: float = A(0.12, "Fixed-gear drag area D/q", "m2", 0.02, 0.4, group="Aerodynamics",
                          source="Estimate: tricycle gear, partly faired", sens="high for fixed gear")
    cd0_flap_to: float = A(0.010, "C_D0 increment, take-off flap", "-", 0, 0.05, group="Aerodynamics")
    # --- geometry layout ---
    c_ht: float = A(0.70, "Horizontal-tail volume coefficient", "-", 0.3, 1.0, group="Geometry",
                    source="Raymer Table 6.4, GA single engine")
    c_vt: float = A(0.04, "Vertical-tail volume coefficient", "-", 0.015, 0.08, group="Geometry",
                    source="Raymer Table 6.4, GA single engine")
    tail_arm_frac: float = A(0.55, "Tail arm / fuselage length", "-", 0.35, 0.7, group="Geometry")
    fus_len_coeff: float = A(1.516, "Fuselage length coefficient (L = c * MTOM^0.23)", "m/kg^0.23", 1.0, 2.5,
                             group="Geometry", source="Raymer Table 6.3 form; c fitted to Risen 6.6 m at 600 kg")
    fus_height_m: float = A(1.15, "Fuselage max height", "m", 0.8, 1.6, group="Geometry")
    seat_width_m: float = A(0.58, "Seat width", "m", 0.45, 0.75, group="Geometry")
    seat_pitch_m: float = A(1.00, "Seat row pitch", "m", 0.8, 1.3, group="Geometry")
    nose_len_m: float = A(1.30, "Nose length (motor + battery bay)", "m", 0.6, 2.5, group="Geometry")
    min_tailcone_m: float = A(3.0, "Minimum tail-cone length", "m", 1.5, 5, group="Geometry")
    main_gear_len_m: float = A(0.60, "Main gear strut length", "m", 0.3, 1.2, group="Geometry")
    nose_gear_len_m: float = A(0.50, "Nose gear strut length", "m", 0.3, 1.2, group="Geometry")
    # --- structure and weights ---
    weight_method: str = A("raymer_calibrated", "Weight method",
                           options=["raymer_calibrated", "raymer_raw", "risen_L1"], group="Weights",
                           source="raymer_calibrated: Raymer GA class II x k_cal fitted to the Risen. "
                                  "risen_L1: scaled-fraction law of risen_eas", sens="highest")
    n_ult: float = A(5.7, "Ultimate load factor N_z", "g", 4.0, 9.0, group="Weights",
                     source="3.8 g limit x 1.5 (normal category)", sens="medium")
    n_land_ult: float = A(4.5, "Ultimate landing load factor N_l", "g", 3.0, 6.0, group="Weights",
                          source="Raymer: 1.5 x gear load factor 3")
    kc_wing: float = A(0.85, "Composite factor, wing", "-", 0.7, 1.0, group="Weights",
                       source="Raymer advanced-composites factors (verify in Ch.15 table)")
    kc_tail: float = A(0.83, "Composite factor, tails", "-", 0.7, 1.0, group="Weights", source="Raymer")
    kc_fus: float = A(0.90, "Composite factor, fuselage", "-", 0.7, 1.0, group="Weights", source="Raymer")
    kc_gear: float = A(0.95, "Composite factor, landing gear", "-", 0.7, 1.0, group="Weights", source="Raymer")
    retract_gear_factor: float = A(1.20, "Retractable / fixed gear mass", "-", 1.0, 1.6, group="Weights")
    avionics_uninst_kg: float = A(12.0, "Uninstalled avionics", "kg", 2, 60, group="Weights")
    seat_kg: float = A(6.0, "Seat mass, each", "kg", 2, 20, group="Weights")
    brs_kg: float = A(6.0, "Rescue system base mass (+1.2 % MTOM)", "kg", 0, 30, group="Weights",
                      help="BRS mass = base + 0.012 x MTOM. Set 0 to drop the rescue system.")
    ref_struct_kg: float = A(190.0, "Calibration target: Risen structure", "kg", 100, 300, group="Weights",
                             source="risen_eas Reference (325 kg empty less ~75 kg Rotax installation)")
    ref_systems_kg: float = A(45.0, "Calibration target: Risen fixed equipment", "kg", 20, 100, group="Weights",
                              source="risen_eas Reference")
    # --- ground performance ---
    mu_roll: float = A(0.04, "Rolling friction coefficient", "-", 0.02, 0.10, group="Ground")
    mu_brake: float = A(0.35, "Braking friction coefficient", "-", 0.2, 0.5, group="Ground")
    cl_ground: float = A(0.30, "C_L during ground roll", "-", 0, 1.0, group="Ground")
    takeoff_factor: float = A(1.5, "Take-off distance correction factor", "-", 1.0, 2.0, group="Ground",
                              source="Raymer Ch.17 estimate underpredicts the C172 POH (497 m over 50 ft) by ~1.6x",
                              sens="sets power when take-off governs")

    # derived
    @property
    def eta_elec(self):
        return self.eta_motor * self.eta_inverter * self.eta_wiring * self.eta_battery

    @property
    def eta_chain_cruise(self):
        return self.eta_prop_cruise * self.eta_elec


# ------------------------------------------------------------------------------------------
# schema helpers
# ------------------------------------------------------------------------------------------
SECTIONS = {"inputs": DesignInputs, "mission": Mission, "requirements": Requirements, "assumptions": Assumptions}


def schema():
    out = {}
    for sec, cls in SECTIONS.items():
        rows = []
        for f in fields(cls):
            m = dict(f.metadata)
            t = "bool" if f.type in ("bool", bool) else "select" if m.get("options") else \
                "int" if f.type in ("int", int) else "number"
            rows.append(dict(key=f.name, default=f.default, type=t, **m))
        out[sec] = rows
    out["categories"] = CATEGORIES
    return out


def coerce(cls, data: dict):
    """Build a dataclass from a (possibly partial, possibly string-valued) dict."""
    obj = cls()
    kw = {}
    for f in fields(cls):
        if f.name not in data or data[f.name] is None or data[f.name] == "":
            continue
        v = data[f.name]
        default = getattr(obj, f.name)
        if isinstance(default, bool):
            v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        elif isinstance(default, int):
            v = int(float(v))
        elif isinstance(default, float):
            v = float(v)
        else:
            v = str(v)
        kw[f.name] = v
    return replace(obj, **kw)


def to_dict(obj):
    return asdict(obj)
