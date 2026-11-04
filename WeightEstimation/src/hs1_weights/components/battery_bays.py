"""Quick-swap battery bays and fuselage cutout penalty (doc 06 §5.3–5.4)."""
from __future__ import annotations

from ..units import G
from .base import Arr, ComponentResult, ModelContext, component


def bays_mass_kg(N_bays: int, m_bay_hw: Arr, c_ret: Arr, n_fwd: Arr, m_pack: Arr) -> Arr:
    """m_bays = N_bays · m_bay_hw + c_ret · (n_fwd · g · m_pack). Strictly increasing in N_bays."""
    return N_bays * m_bay_hw + c_ret * (n_fwd * G * m_pack)


@component("battery_bays", "bottom_up", default_for=("electric",), reference="doc 06 §5.3")
def battery_bays(ctx: ModelContext, m_pack: Arr | None = None) -> ComponentResult:
    w = "battery_bays"
    if m_pack is None:
        from .electric import battery_pack
        m_pack = battery_pack(ctx).mass_kg
    N = ctx.req("N_bays", w)
    hw, c, n = ctx.req("m_bay_hw_kg", w), ctx.req("c_ret_kg_per_n", w), ctx.req("n_mount", w)
    m = bays_mass_kg(N, hw, c, n, m_pack)
    return ComponentResult("battery_bays", m, "bottom_up", {"N_bays": N, "n_fwd": n}, ctx.n_mount_case,
                           ["retention sized at the ESS mount forward factor (18 g, FAA MOC to F3083 §4.1.6)",
                            "c_ret and m_bay_hw are placeholder priors until a sized load path exists"],
                           extra={"hardware_kg": N * hw, "retention_kg": m - N * hw})


def cutout_factor(k_cut: Arr, A_cut: float, S_f: float) -> Arr:
    """W_fus,HS1 = W_fus · (1 + k_cut · A_cut / S_f)."""
    return 1.0 + k_cut * A_cut / S_f
