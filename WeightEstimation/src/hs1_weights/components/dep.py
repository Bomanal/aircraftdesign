"""DEP nacelles, mounts and wing inertia relief (doc 06 §5.5)."""
from __future__ import annotations

import math

import numpy as np

from ..units import G
from .base import Arr, ComponentResult, ModelContext, MotorCtx, component
from .electric import inverter_masses, motor_masses


def supported_mass_kg(m: MotorCtx, m_motor: Arr, m_inv: Arr) -> Arr:
    """Mass carried by the mount: motor + prop (+ inverter when it sits in the nacelle)."""
    return m_motor + m.m_prop_kg + (m_inv if m.inverter_in_nacelle else 0.0)


def nacelle_masses(ctx: ModelContext) -> dict[str, tuple[Arr, Arr]]:
    """Per motor: (mount mass, fairing mass). m_mount = c_mount · (n_mount · g · m_supported)."""
    w = "dep.nacelles_mounts"
    c, n = ctx.req("c_mount_kg_per_n", w), ctx.req("n_mount", w)
    mm, mi = motor_masses(ctx), inverter_masses(ctx)
    return {m.id: (c * n * G * supported_mass_kg(m, mm[m.id], mi[m.id]), m.m_fairing_kg) for m in ctx.motors}


@component("nacelles_mounts", "bottom_up", default_for=("electric",), reference="doc 06 §5.5")
def nacelles_mounts(ctx: ModelContext) -> ComponentResult:
    each = nacelle_masses(ctx)
    total = sum((a + b for a, b in each.values()), start=np.float64(0.0))
    return ComponentResult("nacelles_mounts", total, "bottom_up", {"n": len(each)}, ctx.n_mount_case,
                           ["mount mass sized to the envelope powerplant/ESS mount factor (placeholder c_mount)"],
                           extra={"mount_kg": {k: v[0] for k, v in each.items()},
                                  "fairing_kg": {k: v[1] for k, v in each.items()}})


def wing_point_masses(ctx: ModelContext) -> list[tuple[Arr, float]]:
    """Wing-mounted DEP masses (motor + inverter if wing-mounted + nacelle + prop) and their span stations."""
    mm, mi = motor_masses(ctx), inverter_masses(ctx)
    nac = nacelle_masses(ctx) if ctx.motors else {}
    out = []
    for m in ctx.motors:
        if not m.wing_mounted:
            continue
        mount, fairing = nac[m.id]
        out.append((supported_mass_kg(m, mm[m.id], mi[m.id]) + mount + fairing, m.y_m))
    return out


def inertia_relief(W_wing: Arr, n_ult: Arr, W_dg: Arr, b: float,
                   point_masses: list[tuple[Arr, float]]) -> tuple[Arr, Arr]:
    """W_wing,DEP = W_wing · (M_root,net / M_lift)^0.49. Returns (factor, relief kg).

    M_lift = (n·W_dg·g/2)·(4b/6π) (elliptic half-span centroid); M_relief = Σ m_j·g·n·y_j.
    Zero DEP masses give factor exactly 1.0 (doc 08 §3).
    """
    if not point_masses:
        return 1.0, 0.0 * np.asarray(W_wing)
    M_lift = (n_ult * W_dg * G / 2.0) * (4.0 * b / (6.0 * math.pi))
    M_rel = sum((m * G * n_ult * y for m, y in point_masses), start=np.float64(0.0))
    ratio = (M_lift - M_rel) / M_lift
    if np.any(np.asarray(ratio) <= 0.0):
        raise ValueError("DEP inertia relief exceeds lift bending moment: check masses and stations")
    factor = ratio ** 0.49
    return factor, W_wing * (1.0 - factor)
