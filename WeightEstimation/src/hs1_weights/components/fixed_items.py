"""Equipment-list components (doc 06 §5.8, P2-6).

Each equipment item belongs to exactly one category, and each category feeds exactly one component, so nothing
is counted twice:

=================  ==================================================
component          categories
=================  ==================================================
avionics_installed avionics (equipment_list method; the Raymer method
                   reads the same items as *uninstalled* W_uav)
furnishings        seats, interior
fixed_items        brs, lv_battery, lighting, hv_protection, other
=================  ==================================================
"""
from __future__ import annotations

import numpy as np

from .base import ComponentResult, ItemCtx, ModelContext, component

CATEGORY_COMPONENT = {
    "avionics": "avionics_installed", "seats": "furnishings", "interior": "furnishings", "brs": "fixed_items",
    "lv_battery": "fixed_items", "lighting": "fixed_items", "hv_protection": "fixed_items", "other": "fixed_items",
}
CATEGORIES = tuple(CATEGORY_COMPONENT)
MANDATORY = ("avionics", "seats", "interior", "lv_battery", "lighting")   # + "brs" if fitted (doc 06 §5.8)


def validate_equipment(items: list[ItemCtx], brs_fitted: bool) -> list[str]:
    """Return a list of problems: unknown categories, missing mandatory entries, non-positive masses."""
    errs = []
    cats = {i.category for i in items}
    for i in items:
        if i.category not in CATEGORY_COMPONENT:
            errs.append(f"{i.name}: unknown category {i.category!r}")
        if np.any(np.asarray(i.mass_kg) <= 0.0):
            errs.append(f"{i.name}: mass must be > 0")
    for c in MANDATORY + (("brs",) if brs_fitted else ()):
        if c not in cats:
            errs.append(f"mandatory equipment category {c!r} missing")
    names = [i.name for i in items]
    errs += [f"duplicate item name {n!r}" for n in sorted({n for n in names if names.count(n) > 1})]
    return errs


def _items_for(ctx: ModelContext, comp: str) -> list[ItemCtx]:
    return [i for i in ctx.equipment if CATEGORY_COMPONENT.get(i.category) == comp]


def _list_result(ctx: ModelContext, comp: str) -> ComponentResult:
    items = _items_for(ctx, comp)
    total = sum((i.mass_kg for i in items), start=np.float64(0.0))
    est = [i.name for i in items if i.estimate]
    notes = [f"items without vendor mass (estimate=True): {', '.join(est)}"] if est else []
    return ComponentResult(comp, total, "equipment_list", {"items": [i.name for i in items]}, "", notes,
                           extra={"each_kg": {i.name: i.mass_kg for i in items}})


@component("furnishings", "equipment_list", default_for=("electric",), reference="doc 06 §5.8")
def furnishings_list(ctx: ModelContext) -> ComponentResult:
    return _list_result(ctx, "furnishings")


@component("avionics_installed", "equipment_list", reference="doc 06 §5.8")
def avionics_list(ctx: ModelContext) -> ComponentResult:
    return _list_result(ctx, "avionics_installed")


@component("fixed_items", "equipment_list", default_for=("electric",), reference="doc 06 §5.8")
def fixed_items_list(ctx: ModelContext) -> ComponentResult:
    return _list_result(ctx, "fixed_items")
