"""
Steps 7 and 9 - mission energy, range and endurance (generalised from risen_eas.FlightState).

Sortie: taxi -> take-off (full power) -> climb to cruise altitude -> cruise / loiter -> glide
descent, with a reserve flown at V_md held back. Battery mass is constant in flight, so the
electric range equation is linear in energy (no Breguet logarithm).
"""
from __future__ import annotations
import math
from .physics import G, rho_at, Polar


class Flight:
    def __init__(self, mass, polar: Polar, P_shaft, E_nom_Wh, A, msn):
        self.m, self.W = mass, mass * G
        self.polar, self.P, self.E_nom = polar, P_shaft, E_nom_Wh
        self.A, self.msn = A, msn
        self.rc = rho_at(msn.h_cruise_m)
        self.rm = rho_at(msn.h_cruise_m / 2)

    # --- power -----------------------------------------------------------------------------
    def p_batt(self, V, rho=None):
        rho = self.rc if rho is None else rho
        return self.polar.drag(self.W, V, rho) * V / self.A.eta_chain_cruise

    def v_md(self, rho=None):
        return self.polar.v_md(self.W, self.rc if rho is None else rho)

    def v_mp(self, rho=None):
        return self.polar.v_mp(self.W, self.rc if rho is None else rho)

    # --- sortie overhead ---------------------------------------------------------------------
    def phases(self):
        A, m = self.A, self.msn
        E_taxi = m.taxi_kw_per_t * self.m * m.taxi_min / 60            # kW/t * kg = W
        E_to = self.P / A.eta_elec * m.takeoff_min / 60
        P_cl = self.P * m.climb_power_frac
        v_cl = m.v_climb_kmh / 3.6 if m.v_climb_kmh > 0 else 1.1 * self.v_mp(self.rm)
        roc = (A.eta_prop_climb * P_cl - self.polar.drag(self.W, v_cl, self.rm) * v_cl) / self.W
        if roc <= 0.05:
            return dict(ok=False, roc=roc, E_ovh=math.inf, t_cl=math.inf, d_cl=0, t_ds=0, d_ds=0, v_cl=v_cl)
        t_cl = m.h_cruise_m / roc
        E_cl = P_cl / A.eta_elec * t_cl / 3600
        v_ds = m.v_descent_kmh / 3.6 if m.v_descent_kmh > 0 else 1.15 * self.v_md(self.rm)
        ld = self.W / self.polar.drag(self.W, v_ds, self.rm)
        d_ds = m.h_cruise_m * ld / 1000
        t_ds = d_ds * 1000 / v_ds
        E_ds = m.descent_sys_kw * 1e3 * t_ds / 3600
        return dict(ok=True, E_ovh=E_taxi + E_to + E_cl + E_ds, E_taxi=E_taxi, E_to=E_to, E_cl=E_cl, E_ds=E_ds,
                    roc=roc, t_cl=t_cl, d_cl=v_cl * t_cl / 1000, t_ds=t_ds, d_ds=d_ds, v_cl=v_cl, v_ds=v_ds)

    def reserve_Wh(self, minutes=None):
        mins = self.msn.reserve_min if minutes is None else minutes
        return self.p_batt(self.v_md()) * mins / 60

    def usable_Wh(self, eol=False):
        return self.E_nom * self.A.dod * (self.A.soh_eol if eol else 1.0)

    # --- range / endurance --------------------------------------------------------------------
    def fly(self, V, eol=False, reserve_min=None):
        """Range [km] and airborne time [h] cruising at V with the reserve held back."""
        ph = self.phases()
        if not ph["ok"]:
            return 0.0, 0.0
        E_cr = self.usable_Wh(eol) - ph["E_ovh"] - self.reserve_Wh(reserve_min)
        if E_cr <= 0:
            return 0.0, 0.0
        t_cr = E_cr / self.p_batt(V)
        return ph["d_cl"] + t_cr * V * 3.6 + ph["d_ds"], (ph["t_cl"] + ph["t_ds"]) / 3600 + t_cr

    def ideal(self):
        E = self.usable_Wh()
        eta = self.A.eta_chain_cruise
        R = E * 3600 * eta * self.polar.ld_max / self.W / 1000
        p = self.polar
        CLmp = math.sqrt(3 * p.cd0 / p.k)
        f = CLmp ** 1.5 / (4 * p.cd0)
        P_min = self.W * math.sqrt(2 * self.W / (self.rc * p.S)) / f
        return R, E * eta / P_min

    # --- energy needed (solve-MTOM mode) -------------------------------------------------------
    def energy_needed_Wh(self, target, value, V):
        """Usable battery energy for a target range [km] at V, or endurance [h] at V_mp."""
        ph = self.phases()
        if not ph["ok"]:
            return math.inf
        E = ph["E_ovh"] + self.reserve_Wh()
        if target == "range":
            t_cr = max(value - ph["d_cl"] - ph["d_ds"], 0.0) / (V * 3.6)
            return E + self.p_batt(V) * t_cr
        vmp = self.v_mp()
        t_cr = max(value - (ph["t_cl"] + ph["t_ds"]) / 3600, 0.0)
        return E + self.p_batt(vmp) * t_cr
