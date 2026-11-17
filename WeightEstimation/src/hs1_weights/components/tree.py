"""Evaluate the doc 06 §1 component tree for one context (f_i, before technology factors k_i)."""
from __future__ import annotations

from collections.abc import Mapping

from .base import ALL_COMPONENTS, ELECTRIC_ONLY, ICE_ONLY, REGISTRY, ComponentResult, ModelContext
from .battery_bays import battery_bays, cutout_factor
from .dep import inertia_relief, wing_point_masses


def component_set(ctx: ModelContext) -> tuple[str, ...]:
    skip = ICE_ONLY if ctx.is_electric else ELECTRIC_ONLY
    return tuple(c for c in ALL_COMPONENTS if c not in skip)


def method_for(component_id: str, ctx: ModelContext, methods: Mapping[str, str]) -> str:
    return methods.get(component_id) or REGISTRY.default(component_id, "electric" if ctx.is_electric else "ice")


def evaluate_tree(ctx: ModelContext, methods: Mapping[str, str] | None = None,
                  only: tuple[str, ...] | None = None) -> dict[str, ComponentResult]:
    """Return {component_id: ComponentResult}. Raises MissingInputError naming the first missing field."""
    methods = dict(methods or {})
    out: dict[str, ComponentResult] = {}
    comps = only if only is not None else component_set(ctx)
    for cid in comps:
        if cid == "battery_bays":
            continue
        out[cid] = REGISTRY.get(cid, method_for(cid, ctx, methods))(ctx)
    if "battery_bays" in comps:
        m_pack = out["battery_pack"].mass_kg if "battery_pack" in out else None
        out["battery_bays"] = battery_bays(ctx, m_pack)

    if "wing" in out and ctx.is_electric and ctx.motors:
        r = out["wing"]
        pts = wing_point_masses(ctx)
        factor, relief = inertia_relief(r.mass_kg, ctx.req("n_ult", "dep.inertia_relief"),
                                        ctx.req("W_dg", "dep.inertia_relief"), ctx.req("b", "dep.inertia_relief"), pts)
        r.mass_kg = r.mass_kg * factor
        r.extra.update({"inertia_relief_factor": factor, "inertia_relief_kg": relief})
        r.assumptions.append("DEP inertia relief applied (doc 06 §5.5)")
    if "fuselage" in out and ctx.A_cut:
        r = out["fuselage"]
        f = cutout_factor(ctx.req("k_cut", "battery_bays.cutout"), ctx.A_cut, ctx.req("S_f", "battery_bays.cutout"))
        r.extra.update({"cutout_factor": f, "cutout_kg": r.mass_kg * (f - 1.0)})
        r.mass_kg = r.mass_kg * f
        r.assumptions.append("bay cutout penalty applied (doc 06 §5.4; k_cut placeholder)")
    return out
