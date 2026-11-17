"""Electric bottom-up models: pack, motors, inverters, thermal, HV harness, propellers, LV system (doc 06 §5).

All numeric defaults are placeholder priors supplied by the configuration and flagged ``assumption=True``
there. Nothing here is calibrated (doc 06 §6).
"""
from __future__ import annotations

import numpy as np

from .base import Arr, ComponentResult, ModelContext, component

RHO_CONDUCTOR = {"Cu": 8960.0, "Al": 2700.0}   # kg/m^3 (doc 06 §5.7)


def pack_mass_kg(E_Wh: Arr, e_wh_per_kg: Arr, eta_c2p: Arr) -> Arr:
    """m_pack = E_nom / (e_cell · η_c2p). Doc 08 §3: 60 kWh, 300 Wh/kg, 0.625 -> 320 kg."""
    return E_Wh / (e_wh_per_kg * eta_c2p)


@component("battery_pack", "bottom_up", default_for=("electric",), reference="doc 06 §5.1")
def battery_pack(ctx: ModelContext) -> ComponentResult:
    w = "electric.battery_pack"
    level = ctx.req("energy_level", w)
    E, e = ctx.req("E_nom_Wh", w), ctx.req("e_spec_wh_per_kg", w)
    notes = []
    if level == "pack":
        eta: Arr = 1.0
        notes.append("specific energy declared pack-level: η_c2p = 1 (doc 06 §5.1)")
    elif level == "cell":
        eta = ctx.req("eta_c2p", w)
    else:
        raise ValueError(f"energy_level must be 'cell' or 'pack', got {level!r}")
    m = pack_mass_kg(E, e, eta)
    fr = ctx.bay_energy_fractions
    extra = {"pack_wh_per_kg": E / m, "per_bay_kg": [m * f for f in fr], "energy_level": level}
    return ComponentResult("battery_pack", m, "bottom_up", {"E_nom_Wh": E, "e": e, "eta_c2p": eta}, "", notes,
                           extra=extra)


def motor_masses(ctx: ModelContext) -> dict[str, Arr]:
    return {m.id: m.p_cont_w / m.sp_motor_w_per_kg for m in ctx.motors}


def inverter_masses(ctx: ModelContext) -> dict[str, Arr]:
    return {m.id: m.p_cont_w / m.sp_inv_w_per_kg for m in ctx.motors}


def _sum(d: dict[str, Arr]) -> Arr:
    return sum(d.values(), start=np.float64(0.0))


@component("motors", "bottom_up", default_for=("electric",), reference="doc 06 §5.2")
def motors(ctx: ModelContext) -> ComponentResult:
    if not ctx.motors:
        from ..errors import MissingInputError
        raise MissingInputError("motors", "electric.motors")
    each = motor_masses(ctx)
    return ComponentResult("motors", _sum(each), "bottom_up", {"n": len(each)}, "", [], extra={"each_kg": each})


@component("inverters", "bottom_up", default_for=("electric",), reference="doc 06 §5.2")
def inverters(ctx: ModelContext) -> ComponentResult:
    each = inverter_masses(ctx)
    return ComponentResult("inverters", _sum(each), "bottom_up", {"n": len(each)}, "", [], extra={"each_kg": each})


def heat_rejected_w(ctx: ModelContext) -> Arr:
    """Q_rej = Σ P_cont,j · (1 − η_chain,j) + Q_pack."""
    q = sum((m.p_cont_w * (1.0 - m.eta_chain) for m in ctx.motors), start=np.float64(0.0))
    return q + ctx.req("Q_pack_w", "electric.thermal_mgmt")  # type: ignore[no-any-return]


@component("thermal_mgmt", "bottom_up", default_for=("electric",), reference="doc 06 §5.6")
def thermal_mgmt(ctx: ModelContext) -> ComponentResult:
    w = "electric.thermal_mgmt"
    Q = heat_rejected_w(ctx)
    k = ctx.req("k_th_kg_per_w", w)
    dT = ctx.req("ambient_isa_delta_c", w)
    return ComponentResult("thermal_mgmt", k * Q, "bottom_up", {"Q_rej_W": Q, "cooling": ctx.cooling},
                           f"hot day ISA{dT:+g} °C", [f"k_th specified for the {ctx.cooling}-cooled, ISA{dT:+g} case"],
                           extra={"Q_rej_W": Q})


def harness_run_mass_kg(I_a: Arr, j_allow: Arr, length_m: float, rho: float, f_ins: Arr) -> Arr:
    """A_r = I/J (mm²); m_r = L·A·ρ·f_ins. Doc 08 §3: 100 A, 4 A/mm², 2 m, Cu, 1.0 -> 0.448 kg."""
    area_m2 = (I_a / j_allow) * 1e-6
    return length_m * area_m2 * rho * f_ins


@component("hv_harness", "bottom_up", default_for=("electric",), reference="doc 06 §5.7")
def hv_harness(ctx: ModelContext) -> ComponentResult:
    if not ctx.harness:
        from ..errors import MissingInputError
        raise MissingInputError("powertrain.harness", "electric.hv_harness")
    each = {}
    for r in ctx.harness:
        I_a = r.power_w / r.v_bus
        each[r.motor] = harness_run_mass_kg(I_a, r.j_allow_a_per_mm2, r.length_m, r.rho_kg_m3, r.f_ins)
    return ComponentResult("hv_harness", _sum(each), "bottom_up", {"runs": len(each)}, "",
                           ["L_r is total conductor length from layout stations (include return conductor)",
                            "contactors, fuses and pyro-disconnect are fixed items"], extra={"each_kg": each})


@component("propellers", "vendor", default_for=("electric",), reference="doc 06 §1 (vendor mass)")
def propellers(ctx: ModelContext) -> ComponentResult:
    each = {m.id: m.m_prop_kg for m in ctx.motors}
    return ComponentResult("propellers", _sum(each), "vendor", {"n": len(each)}, "", [], extra={"each_kg": each})


@component("electrical_lv", "bottom_up", default_for=("electric",), reference="doc 06 §1 (electric: bottom-up)")
def electrical_lv_bottom_up(ctx: ModelContext) -> ComponentResult:
    m = ctx.req("lv_system_kg", "electric.electrical_lv")
    return ComponentResult("electrical_lv", m, "bottom_up", {}, "",
                           ["LV system (DC-DC, LV wiring, breakers); LV battery is a fixed item"])
