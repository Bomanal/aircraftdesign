"""Torenbeek light-aircraft structure methods (doc 06 §4), for benchmarking.

Source: E. Torenbeek, *Synthesis of Subsonic Airplane Design*, Delft Univ. Press / Kluwer, 1982, Ch. 8 and
App. C (light aircraft, W_TO <= 12,500 lb), as also reproduced in Roskam, *Airplane Design* Part V (1985) §5.

**TRANSCRIPTION NOT VERIFIED.** The coefficients below were entered without the source text at hand. Every
model here is registered with ``transcription_verified=False`` and is reported as such. They must be checked
against the project's copy (equation numbers to be added) before P2-3 acceptance (doc 12 §1, SC-10).
"""
from __future__ import annotations

import numpy as np

from ..units import kg_to_lb, kt_to_mps, lb_to_kg, m2_to_ft2, m_to_ft
from .base import Arr, ComponentResult, ModelContext, component

REF = "Torenbeek 1982 Ch. 8, light aircraft (UNVERIFIED transcription)"
UNVERIFIED = "Torenbeek transcription not verified against source text (doc 12 SC-10)"

# Light-aircraft landing gear coefficients (A, B, C, D) for W_uc = K_uc (A + B W^0.75 + C W + D W^1.5), lb
GEAR_COEFFS = {
    ("fixed", "main"): (20.0, 0.10, 0.019, 0.0),
    ("fixed", "nose"): (25.0, 0.0, 0.0024, 0.0),
    ("fixed", "tail"): (9.0, 0.0, 0.0024, 0.0),
    ("retractable", "main"): (33.0, 0.04, 0.021, 0.0),
    ("retractable", "nose"): (12.0, 0.06, 0.0024, 0.0),
}


def _torenbeek_wing_imperial(W_G: Arr, b: Arr, sweep_half: Arr, n_ult: Arr, S: Arr, t_r: Arr) -> Arr:
    c = np.cos(sweep_half)
    return (0.00125 * W_G * (b / c) ** 0.75 * (1.0 + (6.3 * c / b) ** 0.5) * n_ult ** 0.55
            * (b * S / (t_r * W_G * c)) ** 0.30)


def _torenbeek_tail_imperial(n_ult: Arr, S_tail: Arr) -> Arr:
    return 0.04 * (n_ult * S_tail ** 2) ** 0.75


def _torenbeek_fuselage_imperial(K_f: Arr, V_D_keas: Arr, l_t: Arr, b_f: Arr, h_f: Arr, S_G: Arr) -> Arr:
    return 0.021 * K_f * (V_D_keas * l_t / (b_f + h_f)) ** 0.5 * S_G ** 1.2


def _torenbeek_gear_imperial(W_TO: Arr, coeffs: tuple[float, float, float, float], K_uc: float) -> Arr:
    a, b, c, d = coeffs
    return K_uc * (a + b * W_TO ** 0.75 + c * W_TO + d * W_TO ** 1.5)


@component("wing", "torenbeek", reference=REF, verified=False)
def torenbeek_wing(ctx: ModelContext) -> ComponentResult:
    w = "torenbeek.wing"
    S, b, lam = ctx.req("S_w", w), ctx.req("b", w), ctx.req("taper", w)
    c_r = 2.0 * S / (b * (1.0 + lam))
    t_r = ctx.req("tc_root", w) * c_r
    lb = _torenbeek_wing_imperial(kg_to_lb(ctx.req("W_dg", w)), m_to_ft(b), ctx.req("sweep_qc", w),
                                  ctx.req("n_ult", w), m2_to_ft2(S), m_to_ft(t_r))
    return ComponentResult("wing", lb_to_kg(lb), "torenbeek", {"t_r": t_r}, ctx.n_ult_case,
                           [UNVERIFIED, "half-chord sweep approximated by quarter-chord sweep"], calibrated=True,
                           transcription_verified=False)


def _tail_split(ctx: ModelContext, part: str) -> ComponentResult:
    w = f"torenbeek.{part}"
    S_h, S_v = ctx.req("S_ht", w), ctx.req("S_vt", w)
    total = lb_to_kg(_torenbeek_tail_imperial(ctx.req("n_ult", w), m2_to_ft2(S_h + S_v)))
    share = (S_h if part == "htail" else S_v) / (S_h + S_v)
    return ComponentResult(part, total * share, "torenbeek", {"S_ht": S_h, "S_vt": S_v}, ctx.n_ult_case,
                           [UNVERIFIED, "Torenbeek gives total empennage; split by area"], calibrated=True,
                           transcription_verified=False)


@component("htail", "torenbeek", reference=REF, verified=False)
def torenbeek_htail(ctx: ModelContext) -> ComponentResult:
    return _tail_split(ctx, "htail")


@component("vtail", "torenbeek", reference=REF, verified=False)
def torenbeek_vtail(ctx: ModelContext) -> ComponentResult:
    return _tail_split(ctx, "vtail")


@component("fuselage", "torenbeek", reference=REF, verified=False)
def torenbeek_fuselage(ctx: ModelContext) -> ComponentResult:
    w = "torenbeek.fuselage"
    K_f = 1.07 if ctx.gear_on_fuselage else 1.0
    lb = _torenbeek_fuselage_imperial(K_f, ctx.req("V_D", w) / kt_to_mps(1.0), m_to_ft(ctx.req("L_t", w)),
                                      m_to_ft(ctx.req("fus_W", w)), m_to_ft(ctx.req("fus_H", w)),
                                      m2_to_ft2(ctx.req("S_f", w)))
    return ComponentResult("fuselage", lb_to_kg(lb), "torenbeek", {"K_f": K_f}, "V_D", [UNVERIFIED], calibrated=True,
                           transcription_verified=False)


def _gear(ctx: ModelContext, part: str) -> ComponentResult:
    kind = "retractable" if ctx.gear_type == "retractable" else "fixed"
    which = "main" if part == "main_gear" else ("tail" if ctx.gear_type == "fixed_tailwheel" else "nose")
    K_uc = 1.08 if ctx.high_wing else 1.0
    lb = _torenbeek_gear_imperial(kg_to_lb(ctx.req("W_l", f"torenbeek.{part}")), GEAR_COEFFS[(kind, which)], K_uc)
    return ComponentResult(part, lb_to_kg(lb), "torenbeek", {"kind": kind, "which": which, "K_uc": K_uc}, "",
                           [UNVERIFIED], calibrated=True, transcription_verified=False)


@component("main_gear", "torenbeek", reference=REF, verified=False)
def torenbeek_main_gear(ctx: ModelContext) -> ComponentResult:
    return _gear(ctx, "main_gear")


@component("nose_gear", "torenbeek", reference=REF, verified=False)
def torenbeek_nose_gear(ctx: ModelContext) -> ComponentResult:
    return _gear(ctx, "nose_gear")
