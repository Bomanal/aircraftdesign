"""Cessna and USAF class II structure methods via Roskam, *Airplane Design* Part V (1985), §5 (doc 06 §4).

**TRANSCRIPTION NOT VERIFIED.** Coefficients were entered without the source text at hand. Every model here is
registered with ``transcription_verified=False``. Check each against the project's copy of Roskam Part V and add
the equation numbers before P2-3 acceptance (doc 12 §1, SC-10).

Units of the imperial cores: lb, ft, ft², KEAS.
"""
from __future__ import annotations

import numpy as np

from ..units import kg_to_lb, lb_to_kg, m2_to_ft2, m_to_ft, mps_to_kt
from .base import Arr, ComponentResult, ModelContext, component

REF_C = "Roskam Part V §5, Cessna method (UNVERIFIED transcription)"
REF_U = "Roskam Part V §5, USAF method (UNVERIFIED transcription)"
UNVERIFIED = "Roskam Pt V transcription not verified against source text (doc 12 SC-10)"


# --- Cessna ------------------------------------------------------------------------------
def _cessna_wing_imperial(W_TO: Arr, S: Arr, n_ult: Arr, A: Arr) -> Arr:
    return 0.04674 * W_TO ** 0.397 * S ** 0.360 * n_ult ** 0.397 * A ** 1.712


def _cessna_htail_imperial(W_TO: Arr, S_h: Arr, A_h: Arr, t_rh: Arr) -> Arr:
    return 3.184 * W_TO ** 0.887 * S_h ** 0.101 * A_h ** 0.138 / (174.04 * t_rh ** 0.223)


def _cessna_vtail_imperial(W_TO: Arr, S_v: Arr, A_v: Arr, t_rv: Arr, sweep_v: Arr) -> Arr:
    return 1.68 * W_TO ** 0.567 * S_v ** 1.249 * A_v ** 0.482 / (639.95 * t_rv ** 0.747 * np.cos(sweep_v) ** 0.882)


def _cessna_fuselage_lowwing_imperial(W_TO: Arr, P_max: Arr, l_fs: Arr) -> Arr:
    return 0.04682 * W_TO ** 0.692 * P_max ** 0.374 * l_fs ** 0.590


def _cessna_main_gear_imperial(W_TO: Arr, W_L: Arr, n_l: Arr, l_s: Arr) -> Arr:
    return 0.013 * W_TO + 0.146 * W_L ** 0.417 * n_l ** 0.950 * l_s ** 0.183


def _cessna_nose_gear_imperial(W_TO: Arr, W_L: Arr, n_l: Arr, l_s: Arr) -> Arr:
    return 6.2 + 0.0013 * W_TO + 0.000143 * W_L ** 0.749 * n_l * l_s ** 0.788


# --- USAF --------------------------------------------------------------------------------
def _usaf_wing_imperial(W_TO: Arr, n_ult: Arr, A: Arr, sweep: Arr, S: Arr, taper: Arr, tc: Arr, V_H: Arr) -> Arr:
    inner = ((W_TO * n_ult / 1e5) ** 0.65 * (A / np.cos(sweep) ** 2) ** 0.57 * (S / 100.0) ** 0.61
             * ((1.0 + taper) / (2.0 * tc)) ** 0.36 * (1.0 + V_H / 500.0) ** 0.5)
    return 96.948 * inner ** 0.993


def _usaf_htail_imperial(W_TO: Arr, n_ult: Arr, S_h: Arr, l_h: Arr, b_h: Arr, t_rh: Arr) -> Arr:
    return 127.0 * ((W_TO * n_ult / 1e5) ** 0.87 * (S_h / 100.0) ** 1.2 * (l_h / 10.0) ** 0.483
                    * (b_h / t_rh) ** 0.5) ** 0.458


def _usaf_vtail_imperial(W_TO: Arr, n_ult: Arr, S_v: Arr, b_v: Arr, t_rv: Arr) -> Arr:
    return 98.5 * ((W_TO * n_ult / 1e5) ** 0.87 * (S_v / 100.0) ** 1.2 * (b_v / t_rv) ** 0.5) ** 0.458


def _usaf_fuselage_imperial(W_TO: Arr, n_ult: Arr, l_f: Arr, w_f: Arr, h_f: Arr, V_C: Arr) -> Arr:
    return 200.0 * ((W_TO * n_ult / 1e5) ** 0.286 * (l_f / 10.0) ** 0.857 * ((w_f + h_f) / 10.0)
                    * (V_C / 100.0) ** 0.338) ** 1.1


def _usaf_gear_imperial(l_m: Arr, W_L: Arr, n_l: Arr) -> Arr:
    return 0.054 * l_m ** 0.501 * (W_L * n_l) ** 0.684


# --- geometry helpers ----------------------------------------------------------------------
def _surface_root_thickness(S: float, A: float, taper: float, tc: float) -> tuple[float, float]:
    b = float(np.sqrt(A * S))
    c_r = 2.0 * S / (b * (1.0 + taper))
    return b, tc * c_r


def _res(cid: str, kg: Arr, mid: str, case: str = "", inputs: dict[str, object] | None = None,
         notes: list[str] | None = None) -> ComponentResult:
    return ComponentResult(cid, kg, mid, dict(inputs or {}), case, [UNVERIFIED] + (notes or []), calibrated=True,
                           transcription_verified=False)


@component("wing", "cessna", reference=REF_C, verified=False)
def cessna_wing(ctx: ModelContext) -> ComponentResult:
    w = "cessna.wing"
    S, b = ctx.req("S_w", w), ctx.req("b", w)
    lb = _cessna_wing_imperial(kg_to_lb(ctx.req("W_dg", w)), m2_to_ft2(S), ctx.req("n_ult", w), b * b / S)
    return _res("wing", lb_to_kg(lb), "cessna", ctx.n_ult_case, notes=["cantilever wing form"])


@component("htail", "cessna", reference=REF_C, verified=False)
def cessna_htail(ctx: ModelContext) -> ComponentResult:
    w = "cessna.htail"
    S, A = ctx.req("S_ht", w), ctx.req("A_ht", w)
    _, t_r = _surface_root_thickness(S, A, ctx.req("taper_ht", w), ctx.req("tc_ht", w))
    lb = _cessna_htail_imperial(kg_to_lb(ctx.req("W_dg", w)), m2_to_ft2(S), A, m_to_ft(t_r))
    return _res("htail", lb_to_kg(lb), "cessna", inputs={"t_rh": t_r})


@component("vtail", "cessna", reference=REF_C, verified=False)
def cessna_vtail(ctx: ModelContext) -> ComponentResult:
    w = "cessna.vtail"
    S, A = ctx.req("S_vt", w), ctx.req("A_vt", w)
    _, t_r = _surface_root_thickness(S, A, ctx.req("taper_vt", w), ctx.req("tc_vt", w))
    lb = _cessna_vtail_imperial(kg_to_lb(ctx.req("W_dg", w)), m2_to_ft2(S), A, m_to_ft(t_r), ctx.req("sweep_vt", w))
    return _res("vtail", lb_to_kg(lb), "cessna", inputs={"t_rv": t_r})


@component("fuselage", "cessna", reference=REF_C, verified=False)
def cessna_fuselage(ctx: ModelContext) -> ComponentResult:
    w = "cessna.fuselage"
    W, H = ctx.req("fus_W", w), ctx.req("fus_H", w)
    P_max = np.pi * (W + H) / 2.0
    lb = _cessna_fuselage_lowwing_imperial(kg_to_lb(ctx.req("W_dg", w)), m_to_ft(P_max),
                                           m_to_ft(ctx.req("fus_L_struct", w)))
    return _res("fuselage", lb_to_kg(lb), "cessna", inputs={"P_max": P_max},
                notes=["low-wing form", "max perimeter approximated as π(W+H)/2"])


@component("main_gear", "cessna", reference=REF_C, verified=False)
def cessna_main_gear(ctx: ModelContext) -> ComponentResult:
    w = "cessna.main_gear"
    lb = _cessna_main_gear_imperial(kg_to_lb(ctx.req("W_dg", w)), kg_to_lb(ctx.req("W_l", w)), ctx.req("N_l", w),
                                    m_to_ft(ctx.req("L_m", w)))
    return _res("main_gear", lb_to_kg(lb), "cessna", ctx.N_l_case)


@component("nose_gear", "cessna", reference=REF_C, verified=False)
def cessna_nose_gear(ctx: ModelContext) -> ComponentResult:
    w = "cessna.nose_gear"
    lb = _cessna_nose_gear_imperial(kg_to_lb(ctx.req("W_dg", w)), kg_to_lb(ctx.req("W_l", w)), ctx.req("N_l", w),
                                    m_to_ft(ctx.req("L_n", w)))
    return _res("nose_gear", lb_to_kg(lb), "cessna", ctx.N_l_case)


@component("wing", "usaf", reference=REF_U, verified=False)
def usaf_wing(ctx: ModelContext) -> ComponentResult:
    w = "usaf.wing"
    S, b = ctx.req("S_w", w), ctx.req("b", w)
    lb = _usaf_wing_imperial(kg_to_lb(ctx.req("W_dg", w)), ctx.req("n_ult", w), b * b / S, ctx.req("sweep_qc", w),
                             m2_to_ft2(S), ctx.req("taper", w), ctx.req("tc_root", w), mps_to_kt(ctx.req("V_H", w)))
    return _res("wing", lb_to_kg(lb), "usaf", ctx.n_ult_case)


@component("htail", "usaf", reference=REF_U, verified=False)
def usaf_htail(ctx: ModelContext) -> ComponentResult:
    w = "usaf.htail"
    S, A = ctx.req("S_ht", w), ctx.req("A_ht", w)
    b_h, t_r = _surface_root_thickness(S, A, ctx.req("taper_ht", w), ctx.req("tc_ht", w))
    lb = _usaf_htail_imperial(kg_to_lb(ctx.req("W_dg", w)), ctx.req("n_ult", w), m2_to_ft2(S),
                              m_to_ft(ctx.req("L_t", w)), m_to_ft(b_h), m_to_ft(t_r))
    return _res("htail", lb_to_kg(lb), "usaf", ctx.n_ult_case)


@component("vtail", "usaf", reference=REF_U, verified=False)
def usaf_vtail(ctx: ModelContext) -> ComponentResult:
    w = "usaf.vtail"
    S, A = ctx.req("S_vt", w), ctx.req("A_vt", w)
    b_v, t_r = _surface_root_thickness(S, A, ctx.req("taper_vt", w), ctx.req("tc_vt", w))
    lb = _usaf_vtail_imperial(kg_to_lb(ctx.req("W_dg", w)), ctx.req("n_ult", w), m2_to_ft2(S), m_to_ft(b_v),
                              m_to_ft(t_r))
    return _res("vtail", lb_to_kg(lb), "usaf", ctx.n_ult_case, notes=["vertical span from A_v = b_v²/S_v"])


@component("fuselage", "usaf", reference=REF_U, verified=False)
def usaf_fuselage(ctx: ModelContext) -> ComponentResult:
    w = "usaf.fuselage"
    lb = _usaf_fuselage_imperial(kg_to_lb(ctx.req("W_dg", w)), ctx.req("n_ult", w), m_to_ft(ctx.req("fus_L", w)),
                                 m_to_ft(ctx.req("fus_W", w)), m_to_ft(ctx.req("fus_H", w)),
                                 mps_to_kt(ctx.req("V_C", w)))
    return _res("fuselage", lb_to_kg(lb), "usaf", ctx.n_ult_case)


@component("main_gear", "usaf", reference=REF_U, verified=False)
def usaf_main_gear(ctx: ModelContext) -> ComponentResult:
    w = "usaf.main_gear"
    lb = _usaf_gear_imperial(m_to_ft(ctx.req("L_m", w)), kg_to_lb(ctx.req("W_l", w)), ctx.req("N_l", w))
    return _res("main_gear", lb_to_kg(lb), "usaf", ctx.N_l_case, notes=["USAF gear eq. covers total gear; "
                                                                         "applied to main gear only"])
