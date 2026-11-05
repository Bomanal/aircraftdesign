"""Raymer general-aviation weight equations (doc 06 §3).

Source: Raymer, *Aircraft Design: A Conceptual Approach*, Ch. 15, general-aviation weights, as transcribed in
doc 06 §3. **Exponents must be verified against the project's copy of Raymer before the R4 acceptance test**
(doc 12 §1); see tests/golden/raymer_ga_example.json.

Imperial cores ``_raymer_*_imperial`` take lb, ft, ft², psf, in, US gal and return lb. Public wrappers take a
ModelContext (SI) and return kg.
"""
from __future__ import annotations

import numpy as np

from ..units import kg_to_lb, lb_to_kg, litre_to_gal, m2_to_ft2, m_to_ft, m_to_in, pa_to_psf
from .base import Arr, ComponentResult, ModelContext, component

REF = "Raymer, Aircraft Design: A Conceptual Approach, Ch. 15 GA weights (doc 06 §3)"
W_FW_MIN_LB = 1.0   # doc 06 §3.1 electric override: W_fw,eff = max(W_fw, 1 lb)


# --- imperial cores (lb, ft, ft^2, psf, in, gal -> lb) -------------------------------
def _raymer_wing_imperial(S_w: Arr, W_fw: Arr, A: Arr, sweep: Arr, q: Arr, taper: Arr, tc: Arr, N_z: Arr,
                          W_dg: Arr) -> Arr:
    c = np.cos(sweep)
    return (0.036 * S_w ** 0.758 * W_fw ** 0.0035 * (A / c ** 2) ** 0.6 * q ** 0.006 * taper ** 0.04
            * (100.0 * tc / c) ** -0.3 * (N_z * W_dg) ** 0.49)


def _raymer_htail_imperial(N_z: Arr, W_dg: Arr, q: Arr, S_ht: Arr, tc: Arr, sweep: Arr, A_ht: Arr,
                           sweep_ht: Arr, taper_h: Arr) -> Arr:
    return (0.016 * (N_z * W_dg) ** 0.414 * q ** 0.168 * S_ht ** 0.896 * (100.0 * tc / np.cos(sweep)) ** -0.12
            * (A_ht / np.cos(sweep_ht) ** 2) ** 0.043 * taper_h ** -0.02)


def _raymer_vtail_imperial(Ht_Hv: Arr, N_z: Arr, W_dg: Arr, q: Arr, S_vt: Arr, tc: Arr, sweep_vt: Arr,
                           A_vt: Arr, taper_vt: Arr) -> Arr:
    c = np.cos(sweep_vt)
    return (0.073 * (1.0 + 0.2 * Ht_Hv) * (N_z * W_dg) ** 0.376 * q ** 0.122 * S_vt ** 0.873
            * (100.0 * tc / c) ** -0.49 * (A_vt / c ** 2) ** 0.357 * taper_vt ** 0.039)


def _raymer_fuselage_imperial(S_f: Arr, N_z: Arr, W_dg: Arr, L_t: Arr, L_over_D: Arr, q: Arr,
                              W_press: Arr = 0.0) -> Arr:
    return 0.052 * S_f ** 1.086 * (N_z * W_dg) ** 0.177 * L_t ** -0.051 * L_over_D ** -0.072 * q ** 0.241 + W_press


def _raymer_main_gear_imperial(N_l: Arr, W_l: Arr, L_m_in: Arr) -> Arr:
    return 0.095 * (N_l * W_l) ** 0.768 * (L_m_in / 12.0) ** 0.409


def _raymer_nose_gear_imperial(N_l: Arr, W_l: Arr, L_n_in: Arr) -> Arr:
    return 0.125 * (N_l * W_l) ** 0.566 * (L_n_in / 12.0) ** 0.845


def _raymer_engine_installed_imperial(W_en: Arr, N_en: Arr) -> Arr:
    return 2.575 * W_en ** 0.922 * N_en


def _raymer_fuel_system_imperial(V_t: Arr, V_i: Arr, N_t: Arr, N_en: Arr) -> Arr:
    return 2.49 * V_t ** 0.726 * (1.0 / (1.0 + V_i / V_t)) ** 0.363 * N_t ** 0.242 * N_en ** 0.157


def _raymer_flight_controls_imperial(L: Arr, B_w: Arr, N_z: Arr, W_dg: Arr) -> Arr:
    return 0.053 * L ** 1.536 * B_w ** 0.371 * (N_z * W_dg * 1e-4) ** 0.80


def _raymer_hydraulics_imperial(W_dg: Arr) -> Arr:
    return 0.001 * W_dg


def _raymer_electrical_imperial(W_fs: Arr, W_av: Arr) -> Arr:
    return 12.57 * (W_fs + W_av) ** 0.51


def _raymer_avionics_imperial(W_uav: Arr) -> Arr:
    return 2.117 * W_uav ** 0.933


def _raymer_ac_anti_ice_imperial(W_dg: Arr, N_p: Arr, W_av: Arr, M: Arr) -> Arr:
    return 0.265 * W_dg ** 0.52 * N_p ** 0.68 * W_av ** 0.17 * M ** 0.08


def _raymer_furnishings_imperial(W_dg: Arr) -> Arr:
    return np.maximum(0.0582 * np.asarray(W_dg, dtype=float) - 65.0, 0.0)


# --- SI wrappers ---------------------------------------------------------------------
def _nz_case(ctx: ModelContext) -> str:
    return ctx.n_ult_case or "n_ult"


def raymer_wing_si(ctx: ModelContext) -> tuple[Arr, list[str]]:
    w = "raymer_ga.wing"
    S, b = ctx.req("S_w", w), ctx.req("b", w)
    notes = []
    W_fw_lb = kg_to_lb(np.asarray(ctx.W_fw if ctx.W_fw is not None else 0.0, dtype=float))
    if ctx.is_electric or np.any(W_fw_lb < W_FW_MIN_LB):
        W_fw_lb = np.maximum(W_fw_lb, W_FW_MIN_LB)
        notes.append("doc 06 §3.1 override: W_fw,eff = max(W_fw, 1 lb); no fuel bending relief represented")
    notes.append("Raymer t/c taken at the wing root")
    lb = _raymer_wing_imperial(m2_to_ft2(S), W_fw_lb, b * b / S, ctx.req("sweep_qc", w), pa_to_psf(ctx.req("q", w)),
                               ctx.req("taper", w), ctx.req("tc_root", w), ctx.req("n_ult", w),
                               kg_to_lb(ctx.req("W_dg", w)))
    return lb_to_kg(lb), notes


@component("wing", "raymer_ga", default_for=("ice", "electric"), reference=REF + " §3.1")
def raymer_wing(ctx: ModelContext) -> ComponentResult:
    m, notes = raymer_wing_si(ctx)
    return ComponentResult("wing", m, "raymer_ga", {"S_w": ctx.S_w, "b": ctx.b, "n_ult": ctx.n_ult},
                           _nz_case(ctx), notes, calibrated=True)


@component("htail", "raymer_ga", default_for=("ice", "electric"), reference=REF + " §3.2")
def raymer_htail(ctx: ModelContext) -> ComponentResult:
    w = "raymer_ga.htail"
    sweep = ctx.req("sweep_ht", w)
    lb = _raymer_htail_imperial(ctx.req("n_ult", w), kg_to_lb(ctx.req("W_dg", w)), pa_to_psf(ctx.req("q", w)),
                                m2_to_ft2(ctx.req("S_ht", w)), ctx.req("tc_ht", w), sweep, ctx.req("A_ht", w),
                                sweep, ctx.req("taper_ht", w))
    return ComponentResult("htail", lb_to_kg(lb), "raymer_ga", {"S_ht": ctx.S_ht}, _nz_case(ctx),
                           ["htail t/c, sweep and A are the tail's own values (doc 12 §4 SC-3)"], calibrated=True)


@component("vtail", "raymer_ga", default_for=("ice", "electric"), reference=REF + " §3.3")
def raymer_vtail(ctx: ModelContext) -> ComponentResult:
    w = "raymer_ga.vtail"
    lb = _raymer_vtail_imperial(1.0 if ctx.t_tail else 0.0, ctx.req("n_ult", w), kg_to_lb(ctx.req("W_dg", w)),
                                pa_to_psf(ctx.req("q", w)), m2_to_ft2(ctx.req("S_vt", w)), ctx.req("tc_vt", w),
                                ctx.req("sweep_vt", w), ctx.req("A_vt", w), ctx.req("taper_vt", w))
    return ComponentResult("vtail", lb_to_kg(lb), "raymer_ga", {"S_vt": ctx.S_vt, "t_tail": ctx.t_tail},
                           _nz_case(ctx), [], calibrated=True)


def raymer_fuselage_si(ctx: ModelContext) -> Arr:
    w = "raymer_ga.fuselage"
    lb = _raymer_fuselage_imperial(m2_to_ft2(ctx.req("S_f", w)), ctx.req("n_ult", w), kg_to_lb(ctx.req("W_dg", w)),
                                   m_to_ft(ctx.req("L_t", w)), ctx.req("fus_L", w) / ctx.req("fus_H", w),
                                   pa_to_psf(ctx.req("q", w)), 0.0)
    return lb_to_kg(lb)


@component("fuselage", "raymer_ga", default_for=("ice", "electric"), reference=REF + " §3.4")
def raymer_fuselage(ctx: ModelContext) -> ComponentResult:
    return ComponentResult("fuselage", raymer_fuselage_si(ctx), "raymer_ga", {"S_f": ctx.S_f, "L_t": ctx.L_t},
                           _nz_case(ctx), ["W_press = 0 (unpressurized)", "L/D uses fuselage height as depth"],
                           calibrated=True)


def _gear_inputs(ctx: ModelContext, w: str) -> tuple[Arr, Arr]:
    return ctx.req("N_l", w), kg_to_lb(ctx.req("W_l", w))


@component("main_gear", "raymer_ga", default_for=("ice", "electric"), reference=REF + " §3.5")
def raymer_main_gear(ctx: ModelContext) -> ComponentResult:
    w = "raymer_ga.main_gear"
    N_l, W_l = _gear_inputs(ctx, w)
    lb = _raymer_main_gear_imperial(N_l, W_l, m_to_in(ctx.req("L_m", w)))
    notes = ["electric: W_l = MTOW"] if ctx.is_electric else []
    return ComponentResult("main_gear", lb_to_kg(lb), "raymer_ga", {"N_l": ctx.N_l, "L_m": ctx.L_m},
                           ctx.N_l_case, notes, calibrated=True)


@component("nose_gear", "raymer_ga", default_for=("ice", "electric"), reference=REF + " §3.5")
def raymer_nose_gear(ctx: ModelContext) -> ComponentResult:
    w = "raymer_ga.nose_gear"
    if ctx.gear_type == "fixed_tailwheel":
        return ComponentResult("nose_gear", 0.0, "raymer_ga", {}, "", [
            "tailwheel: Raymer GA nose-gear equation not applicable; tailwheel mass not modelled (0)"],
            calibrated=True)
    N_l, W_l = _gear_inputs(ctx, w)
    lb = _raymer_nose_gear_imperial(N_l, W_l, m_to_in(ctx.req("L_n", w)))
    return ComponentResult("nose_gear", lb_to_kg(lb), "raymer_ga", {"N_l": ctx.N_l, "L_n": ctx.L_n},
                           ctx.N_l_case, [], calibrated=True)


@component("engine_installed", "raymer_ga", default_for=("ice",), reference=REF + " §3.6")
def raymer_engine_installed(ctx: ModelContext) -> ComponentResult:
    lb = _raymer_engine_installed_imperial(kg_to_lb(ctx.req("W_en", "raymer_ga.engine_installed")), ctx.N_en)
    return ComponentResult("engine_installed", lb_to_kg(lb), "raymer_ga", {"W_en": ctx.W_en, "N_en": ctx.N_en},
                           "", ["installed engine includes propeller, mounts and accessories (Raymer)"],
                           calibrated=True)


def raymer_fuel_system_lb(ctx: ModelContext) -> Arr:
    w = "raymer_ga.fuel_system"
    V_t = litre_to_gal(ctx.req("V_t", w))
    V_i = litre_to_gal(ctx.req("V_i", w))
    return _raymer_fuel_system_imperial(V_t, V_i, ctx.req("N_t", w), ctx.N_en)


@component("fuel_system", "raymer_ga", default_for=("ice",), reference=REF + " §3.6")
def raymer_fuel_system(ctx: ModelContext) -> ComponentResult:
    return ComponentResult("fuel_system", lb_to_kg(raymer_fuel_system_lb(ctx)), "raymer_ga",
                           {"V_t": ctx.V_t, "V_i": ctx.V_i, "N_t": ctx.N_t}, "", [], calibrated=True)


@component("flight_controls", "raymer_ga", default_for=("ice", "electric"), reference=REF + " §3.7")
def raymer_flight_controls(ctx: ModelContext) -> ComponentResult:
    w = "raymer_ga.flight_controls"
    lb = _raymer_flight_controls_imperial(m_to_ft(ctx.req("fus_L_struct", w)), m_to_ft(ctx.req("b", w)),
                                          ctx.req("n_ult", w), kg_to_lb(ctx.req("W_dg", w)))
    return ComponentResult("flight_controls", lb_to_kg(lb), "raymer_ga", {"L": ctx.fus_L_struct, "b": ctx.b},
                           _nz_case(ctx), [], calibrated=True)


def raymer_avionics_lb(ctx: ModelContext) -> Arr:
    return _raymer_avionics_imperial(kg_to_lb(ctx.req("W_uav", "raymer_ga.avionics_installed")))


@component("avionics_installed", "raymer_ga", default_for=("ice", "electric"), reference=REF + " §3.7")
def raymer_avionics(ctx: ModelContext) -> ComponentResult:
    return ComponentResult("avionics_installed", lb_to_kg(raymer_avionics_lb(ctx)), "raymer_ga",
                           {"W_uav": ctx.W_uav}, "", [], calibrated=True)


@component("electrical_lv", "raymer_ga", default_for=("ice",), reference=REF + " §3.7")
def raymer_electrical(ctx: ModelContext) -> ComponentResult:
    lb = _raymer_electrical_imperial(raymer_fuel_system_lb(ctx), raymer_avionics_lb(ctx))
    return ComponentResult("electrical_lv", lb_to_kg(lb), "raymer_ga", {}, "", [], calibrated=True)


@component("ecs_anti_ice", "raymer_ga", default_for=("ice", "electric"), reference=REF + " §3.7")
def raymer_ac_anti_ice(ctx: ModelContext) -> ComponentResult:
    if not ctx.has_ecs:
        return ComponentResult("ecs_anti_ice", 0.0, "raymer_ga", {"has_ecs": False}, "",
                               ["no ECS / anti-ice fitted: 0"], calibrated=True)
    w = "raymer_ga.ecs_anti_ice"
    lb = _raymer_ac_anti_ice_imperial(kg_to_lb(ctx.req("W_dg", w)), ctx.req("N_p", w), raymer_avionics_lb(ctx),
                                      ctx.req("mach", w))
    return ComponentResult("ecs_anti_ice", lb_to_kg(lb), "raymer_ga", {"N_p": ctx.N_p}, "", [], calibrated=True)


@component("furnishings", "raymer_ga", default_for=("ice",), reference=REF + " §3.7")
def raymer_furnishings(ctx: ModelContext) -> ComponentResult:
    lb = _raymer_furnishings_imperial(kg_to_lb(ctx.req("W_dg", "raymer_ga.furnishings")))
    return ComponentResult("furnishings", lb_to_kg(lb), "raymer_ga", {"W_dg": ctx.W_dg}, "",
                           ["Raymer furnishings floored at 0"], calibrated=True)
