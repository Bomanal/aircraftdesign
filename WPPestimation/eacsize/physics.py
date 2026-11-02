"""
Steps 4-6 - atmosphere, geometry, aerodynamics and class-II weights.

Geometry   : Raymer Ch.6-7 (tail volume coefficients, statistical fuselage length), Torenbeek
             fuselage wetted area, seat-layout cabin model.
Aerodynamics: Raymer Ch.12 component build-up  C_D0 = sum(Cf * FF * Q * S_wet) / S, mixed
             laminar/turbulent skin friction, fixed-gear drag area; Raymer straight-wing Oswald fit.
Weights    : Raymer GA component equations (Ch.15, imperial units) x composite factors x k_cal,
             or the risen_eas scaled-fraction (L1) law. E-powertrain from specific power.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, asdict

G = 9.80665
KG_LB = 2.20462262
M_FT = 3.2808399
M2_FT2 = 10.7639104
PA_PSF = 0.0208854342
KT = 0.514444


# ------------------------------------------------------------------------------------------
# ISA
# ------------------------------------------------------------------------------------------
def isa(h):
    """ISA troposphere (+ isothermal above 11 km). Returns rho, T, a, mu."""
    if h <= 11000:
        T = 288.15 - 0.0065 * h
        p = 101325.0 * (T / 288.15) ** 5.25588
    else:
        T = 216.65
        p = 22632.06 * math.exp(-G * (h - 11000) / (287.053 * T))
    rho = p / (287.053 * T)
    a = math.sqrt(1.4 * 287.053 * T)
    mu = 1.458e-6 * T ** 1.5 / (T + 110.4)
    return rho, T, a, mu


def rho_at(h):
    return isa(h)[0]


RHO0 = rho_at(0.0)


# ------------------------------------------------------------------------------------------
# Step 4 - geometry
# ------------------------------------------------------------------------------------------
TAIL_AR_H, TAIL_AR_V, TAIL_TC, TAIL_TAPER = 4.5, 1.5, 0.10, 0.6   # fixed layout constants


@dataclass
class Geometry:
    S: float
    b: float
    AR: float
    taper: float
    t_c: float
    c_root: float
    mac: float
    S_exp: float
    S_wet_wing: float
    fus_len: float
    fus_width: float
    fus_height: float
    fus_fineness: float
    S_wet_fus: float
    tail_arm: float
    S_ht: float
    S_vt: float
    S_wet_ht: float
    S_wet_vt: float
    abreast: int
    rows: int
    fus_len_driver: str

    def as_dict(self):
        return asdict(self)


def make_geometry(mtom, S, b, seats, layout, A) -> Geometry:
    AR = b * b / S
    lam = A.taper
    c_root = 2 * S / (b * (1 + lam))
    mac = 2 / 3 * c_root * (1 + lam + lam * lam) / (1 + lam)
    # fuselage from seat layout, or the statistical length if longer (Raymer Table 6.3 form)
    abreast = 2 if (layout == "side_by_side" and seats >= 2) else 1
    rows = math.ceil(seats / abreast)
    width = abreast * A.seat_width_m + 0.12
    height = A.fus_height_m
    L_cabin = A.nose_len_m + rows * A.seat_pitch_m + A.min_tailcone_m
    L_stat = A.fus_len_coeff * mtom ** 0.23
    L = max(L_cabin, L_stat)
    D = math.sqrt(width * height)
    fin = L / D
    S_wet_fus = math.pi * D * L * (1 - 2 / fin) ** (2 / 3) * (1 + 1 / fin ** 2)   # Torenbeek
    S_exp = max(S - width * c_root, 0.8 * S)
    S_wet_wing = S_exp * (1.977 + 0.52 * A.t_c)                                     # Raymer eq. 7.10
    Lt = A.tail_arm_frac * L
    S_ht = A.c_ht * mac * S / Lt
    S_vt = A.c_vt * b * S / Lt
    k_tail = 1.977 + 0.52 * TAIL_TC
    return Geometry(S, b, AR, lam, A.t_c, c_root, mac, S_exp, S_wet_wing, L, width, height, fin, S_wet_fus,
                    Lt, S_ht, S_vt, S_ht * k_tail, S_vt * k_tail, abreast, rows,
                    "cabin" if L_cabin >= L_stat else "statistical")


# ------------------------------------------------------------------------------------------
# Step 5 - aerodynamics
# ------------------------------------------------------------------------------------------
def _cf(Re, mach, lam):
    cf_t = 0.455 / (math.log10(Re) ** 2.58 * (1 + 0.144 * mach * mach) ** 0.65)
    cf_l = 1.328 / math.sqrt(Re)
    return lam * cf_l + (1 - lam) * cf_t


def cd0_buildup(g: Geometry, V, h, A):
    """Clean C_D0 (no gear increment) by component build-up at the cruise condition."""
    rho, T, a, mu = isa(h)
    M = V / a
    comp = {}
    # wing (x/c of max thickness 0.3)
    Re = rho * V * g.mac / mu
    ff = (1 + 0.6 / 0.3 * g.t_c + 100 * g.t_c ** 4) * (1.34 * M ** 0.18)
    comp["wing"] = _cf(Re, M, A.lam_frac_wing) * ff * 1.0 * g.S_wet_wing
    # tails
    ff_t = (1 + 0.6 / 0.3 * TAIL_TC + 100 * TAIL_TC ** 4) * (1.34 * M ** 0.18)
    c_ht = math.sqrt(g.S_ht / TAIL_AR_H)
    c_vt = math.sqrt(g.S_vt / TAIL_AR_V)
    comp["htail"] = _cf(rho * V * c_ht / mu, M, A.lam_frac_tail) * ff_t * 1.04 * g.S_wet_ht
    comp["vtail"] = _cf(rho * V * c_vt / mu, M, A.lam_frac_tail) * ff_t * 1.04 * g.S_wet_vt
    # fuselage
    f = g.fus_fineness
    ff_f = 1 + 60 / f ** 3 + f / 400
    comp["fuselage"] = _cf(rho * V * g.fus_len / mu, M, A.lam_frac_fus) * ff_f * 1.0 * g.S_wet_fus
    dq = sum(comp.values())
    cd0 = dq * (1 + A.misc_drag_frac) / g.S
    return cd0, {k: v / g.S for k, v in comp.items()}


def oswald(AR, A):
    if A.e_method == "fixed":
        return A.oswald_e
    return 1.78 * (1 - 0.045 * AR ** 0.68) - 0.64


@dataclass
class Polar:
    cd0: float
    k: float
    S: float

    def drag(self, W, V, rho):
        q = 0.5 * rho * V * V
        CL = W / (q * self.S)
        return q * self.S * (self.cd0 + self.k * CL * CL)

    def v_md(self, W, rho):
        return math.sqrt(2 * W / (rho * self.S)) * (self.k / self.cd0) ** 0.25

    def v_mp(self, W, rho):
        return self.v_md(W, rho) / 3 ** 0.25

    @property
    def ld_max(self):
        return 1 / (2 * math.sqrt(self.cd0 * self.k))


# ------------------------------------------------------------------------------------------
# Step 6 - weights
# ------------------------------------------------------------------------------------------
def raymer_ga(g: Geometry, mtom, seats, gear, q_pa, A):
    """Raymer GA component weights [kg], before composite factors and calibration."""
    Wdg = mtom * KG_LB
    NzW = A.n_ult * Wdg
    q = q_pa * PA_PSF
    Sw = g.S * M2_FT2
    lb = {}
    lb["wing"] = (0.036 * Sw ** 0.758 * 1.0 ** 0.0035 * g.AR ** 0.6 * q ** 0.006 * g.taper ** 0.04
                  * (100 * g.t_c) ** -0.3 * NzW ** 0.49)
    lb["htail"] = (0.016 * NzW ** 0.414 * q ** 0.168 * (g.S_ht * M2_FT2) ** 0.896 * (100 * TAIL_TC) ** -0.12
                   * TAIL_AR_H ** 0.043 * TAIL_TAPER ** -0.02)
    lb["vtail"] = (0.073 * NzW ** 0.376 * q ** 0.122 * (g.S_vt * M2_FT2) ** 0.873 * (100 * TAIL_TC) ** -0.49
                   * TAIL_AR_V ** 0.357 * TAIL_TAPER ** 0.039)
    lb["fuselage"] = (0.052 * (g.S_wet_fus * M2_FT2) ** 1.086 * NzW ** 0.177 * (g.tail_arm * M_FT) ** -0.051
                      * g.fus_fineness ** -0.072 * q ** 0.241)
    NlWl = A.n_land_ult * Wdg
    gf = A.retract_gear_factor if gear == "retractable" else 1.0
    lb["main_gear"] = 0.095 * NlWl ** 0.768 * (A.main_gear_len_m * 39.37 / 12) ** 0.409 * gf
    lb["nose_gear"] = 0.125 * NlWl ** 0.566 * (A.nose_gear_len_m * 39.37 / 12) ** 0.845 * gf
    lb["flight_controls"] = 0.053 * (g.fus_len * M_FT) ** 1.536 * (g.b * M_FT) ** 0.371 * (NzW * 1e-4) ** 0.80
    av = 2.117 * (A.avionics_uninst_kg * KG_LB) ** 0.933
    lb["avionics"] = av
    lb["electrical_lv"] = 12.57 * av ** 0.51
    kg = {k: v / KG_LB for k, v in lb.items()}
    kg["furnishings_seats"] = max(0.0, 0.0582 * Wdg - 65) / KG_LB + A.seat_kg * seats
    kg["rescue_system"] = (A.brs_kg + 0.012 * mtom) if A.brs_kg > 0 else 0.0
    # composite factors on the structural groups
    kg["wing"] *= A.kc_wing
    kg["htail"] *= A.kc_tail
    kg["vtail"] *= A.kc_tail
    kg["fuselage"] *= A.kc_fus
    kg["main_gear"] *= A.kc_gear
    kg["nose_gear"] *= A.kc_gear
    return kg


STRUCT_GROUPS = ("wing", "htail", "vtail", "fuselage", "main_gear", "nose_gear")
SYSTEM_GROUPS = ("flight_controls", "avionics", "electrical_lv", "furnishings_seats", "rescue_system")


def powertrain_mass(P_kw, A):
    return dict(e_powertrain=P_kw / A.pt_kw_per_kg, propeller=A.prop_mass_coeff * P_kw ** 0.7)


def risen_l1(mtom, S, seats, ref):
    """risen_eas scaled-fraction law: structure and fixed equipment [kg]."""
    s = mtom / ref["mtom_kg"]
    struct = ref["struct_kg"] * (0.5 * (S / ref["S_m2"]) ** 1.2 + 0.5 * s)
    fixed = ref["systems_kg"] + (3 if mtom > 600 else 0) + (3 if mtom > 800 else 0) + (10 if seats == 4 else 0)
    return dict(structure=struct, fixed_equipment=fixed)
