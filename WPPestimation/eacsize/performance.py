"""
Step 3 (constraint analysis) and step 9 point performance.

Constraint relations follow Raymer Ch.5 / Gudmundsson Ch.3 for a propeller aircraft, written as
shaft power-to-weight P/W [W/N] versus wing loading W/S [N/m2]. Electric motors keep their power
at altitude, so only density changes the curves. Take-off and landing distances use Raymer Ch.17
(integrated ground roll, transition and climb to 15 m; approach, flare, free roll and braking).
"""
from __future__ import annotations
import math
from .physics import G, KT, isa, rho_at, RHO0, Polar

H_OBS = 15.0          # obstacle height [m]
T_ROT = 1.0           # rotation time [s]
T_FREE_ROLL = 2.0     # landing free roll [s]
GAMMA_APP = math.radians(3.0)


# ------------------------------------------------------------------------------------------
# simple helpers
# ------------------------------------------------------------------------------------------
def v_stall(ws, cl_max, rho=RHO0):
    return math.sqrt(2 * ws / (rho * cl_max))


def ws_for_stall(vs, cl_max, rho=RHO0):
    return 0.5 * rho * vs * vs * cl_max


def _p_req_per_w(ws, V, rho, cd0, k):
    """Drag power per unit weight D*V/W at wing loading ws."""
    q = 0.5 * rho * V * V
    return V * (q * cd0 / ws + k * ws / q)


def _climb_speed(ws, rho, cd0, k, cl_clean):
    v_mp = math.sqrt(2 * ws / rho) * (k / (3 * cd0)) ** 0.25
    return max(v_mp, 1.2 * v_stall(ws, cl_clean, rho))


def static_thrust(P, A, rho):
    disk = math.pi * A.prop_diameter_m ** 2 / 4
    return (A.prop_static_fm * P) ** (2 / 3) * (2 * rho * disk) ** (1 / 3)


def thrust(P, V, A, rho):
    return min(static_thrust(P, A, rho), A.eta_prop_climb * P / max(V, 0.1))


# ------------------------------------------------------------------------------------------
# take-off and landing (Raymer Ch.17)
# ------------------------------------------------------------------------------------------
def takeoff(W, S, P, cd0, k, A, h=0.0):
    rho = rho_at(h)
    m = W / G
    vs = v_stall(W / S, A.cl_max_to, rho)
    v_lof = 1.1 * vs
    cdg = cd0 + A.cd0_flap_to + k * A.cl_ground ** 2
    # ground roll  s = integral of v dv / a(v), midpoint rule in velocity
    n, s = 60, 0.0
    dv = v_lof / n
    for i in range(n):
        v = (i + 0.5) * dv
        q = 0.5 * rho * v * v
        acc = (thrust(P, v, A, rho) - q * S * cdg - A.mu_roll * (W - q * S * A.cl_ground)) / m
        if acc <= 1e-4:
            return dict(ground=math.inf, total=math.inf)
        s += v * dv / acc
    s_g = s + T_ROT * v_lof
    v_tr = 1.15 * vs
    q = 0.5 * rho * v_tr ** 2
    cl = W / (q * S)
    sin_g = (thrust(P, v_tr, A, rho) - q * S * (cd0 + A.cd0_flap_to + k * cl * cl)) / W
    if sin_g <= 0:
        return dict(ground=s_g * A.takeoff_factor, total=math.inf)
    gam = math.asin(min(sin_g, 0.5))
    R = v_tr ** 2 / (0.2 * G)
    h_tr = R * (1 - math.cos(gam))
    if h_tr >= H_OBS:
        s_air = math.sqrt(R * R - (R - H_OBS) ** 2)
    else:
        s_air = R * math.sin(gam) + (H_OBS - h_tr) / math.tan(gam)
    f = A.takeoff_factor
    return dict(ground=s_g * f, total=(s_g + s_air) * f)


def landing(ws, A, h=0.0):
    rho = rho_at(h)
    vs = v_stall(ws, A.cl_max_land, rho)
    v_f, v_td = 1.23 * vs, 1.15 * vs
    R = v_f ** 2 / (0.2 * G)
    h_f = R * (1 - math.cos(GAMMA_APP))
    s_a = (H_OBS - h_f) / math.tan(GAMMA_APP)
    s_f = R * math.sin(GAMMA_APP)
    s_g = T_FREE_ROLL * v_td + v_td ** 2 / (2 * G * A.mu_brake)
    return dict(ground=s_g, total=s_a + s_f + s_g)


def _bisect(f, lo, hi, n=60):
    """Smallest x in [lo, hi] with f(x) True (f monotone False->True). None if never True."""
    if not f(hi):
        return None
    if f(lo):
        return lo
    for _ in range(n):
        mid = 0.5 * (lo + hi)
        if f(mid):
            hi = mid
        else:
            lo = mid
    return hi


# ------------------------------------------------------------------------------------------
# Step 3 - constraint analysis
# ------------------------------------------------------------------------------------------
def pw_constraints(ws, W, cd0, k, A, msn, req):
    """Required installed shaft P/W [W/N] for each constraint at wing loading ws [N/m2]."""
    h_f = req.field_elevation_m
    rho_f = rho_at(h_f)
    out = {}
    # sea-level / field rate of climb at max continuous power
    V = _climb_speed(ws, rho_f, cd0, k, A.cl_max_clean)
    out["climb"] = (req.roc_min_ms + _p_req_per_w(ws, V, rho_f, cd0, k)) / (A.eta_prop_climb * A.cont_power_frac)
    # cruise speed at altitude, continuous power
    rc = rho_at(msn.h_cruise_m)
    Vc = msn.v_cruise_kmh / 3.6
    out["cruise"] = _p_req_per_w(ws, Vc, rc, cd0, k) / (A.eta_prop_cruise * A.cont_power_frac)
    # service ceiling (0.5 m/s), continuous power
    if req.ceiling_min_m > 0:
        rh = rho_at(req.ceiling_min_m)
        Vh = _climb_speed(ws, rh, cd0, k, A.cl_max_clean)
        out["ceiling"] = (0.508 + _p_req_per_w(ws, Vh, rh, cd0, k)) / (A.eta_prop_climb * A.cont_power_frac)
    # max level speed at full power, SL
    if req.v_max_min_kmh > 0:
        out["v_max"] = _p_req_per_w(ws, req.v_max_min_kmh / 3.6, RHO0, cd0, k) / A.eta_prop_cruise
    # take-off distance over 15 m
    S = W / ws
    pw = _bisect(lambda x: takeoff(W, S, x * W, cd0, k, A, h_f)["total"] <= req.s_to50_max_m,
                 0.5 / G, 1500 / G, 40)
    out["takeoff"] = pw if pw is not None else math.inf
    return out


def ws_limits(A, req, vs0_limit_kt):
    """Upper bounds on wing loading [N/m2] from stall speed and landing distance."""
    lim = {}
    if vs0_limit_kt:
        lim["stall"] = ws_for_stall(vs0_limit_kt * KT, A.cl_max_land)
    h = req.field_elevation_m
    wl = _bisect(lambda ws: landing(ws, A, h)["total"] > req.s_ld50_max_m, 50.0, 5000.0, 50)
    lim["landing"] = wl if wl is not None else 5000.0
    return lim


def constraint_diagram(ws_design, W, cd0, k, A, msn, req, vs0_limit_kt, n=36):
    """Curves for plotting, in kg/m2 and W/kg."""
    lo, hi = 0.4 * ws_design, 1.7 * ws_design
    xs = [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    curves = {}
    for ws in xs:
        for name, v in pw_constraints(ws, W, cd0, k, A, msn, req).items():
            curves.setdefault(name, []).append(None if not math.isfinite(v) else v * G)
    lim = ws_limits(A, req, vs0_limit_kt)
    return dict(ws_kgm2=[x / G for x in xs], curves=curves, limits_kgm2={k_: v / G for k_, v in lim.items()})


# ------------------------------------------------------------------------------------------
# Step 9 - point performance
# ------------------------------------------------------------------------------------------
def roc_max(W, polar: Polar, P, eta, rho, cl_clean):
    vs1 = v_stall(W / polar.S, cl_clean, rho)
    best, v_best = -math.inf, None
    for i in range(121):
        V = 1.1 * vs1 + i * 0.5
        r = (eta * P - polar.drag(W, V, rho) * V) / W
        if r > best:
            best, v_best = r, V
    return best, v_best


def v_max(W, polar: Polar, P, eta, rho):
    lo = polar.v_mp(W, rho)
    if eta * P < polar.drag(W, lo, rho) * lo:
        return None
    hi = 200.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if eta * P >= polar.drag(W, mid, rho) * mid:
            lo = mid
        else:
            hi = mid
    return lo


def service_ceiling(W, polar: Polar, P, A, roc=0.508, h_max=12000.0):
    def ok(h):
        return roc_max(W, polar, P, A.eta_prop_climb, rho_at(h), A.cl_max_clean)[0] >= roc
    if not ok(0.0):
        return 0.0
    if ok(h_max):
        return h_max
    lo, hi = 0.0, h_max
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo
