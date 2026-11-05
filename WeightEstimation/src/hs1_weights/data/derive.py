"""Geometry derivations (doc 05 N8). Pure functions on SI floats; callers record provenance."""
from __future__ import annotations

import math
import re


def aspect_ratio(span_m: float, area_m2: float) -> float:
    return span_m * span_m / area_m2


def root_chord(area_m2: float, span_m: float, taper: float) -> float:
    return 2.0 * area_m2 / (span_m * (1.0 + taper))


def mac_trapezoidal(area_m2: float, span_m: float, taper: float) -> float:
    """c̄ = (2/3)·c_r·(1 + λ + λ²)/(1 + λ), with c_r = 2S/(b(1+λ))."""
    cr = root_chord(area_m2, span_m, taper)
    return (2.0 / 3.0) * cr * (1.0 + taper + taper * taper) / (1.0 + taper)


def fuselage_wetted_area_torenbeek(length_m: float, width_m: float, height_m: float) -> float:
    """Torenbeek approximation: S_wet ≈ π·D·L·(1 − 2/λ_f)^(2/3)·(1 + 1/λ_f²), D = √(W·H), λ_f = L/D.

    Approximate; flag it and compare against 3-view-measured values where available (N8).
    """
    d = math.sqrt(width_m * height_m)
    lam = length_m / d
    if lam <= 2.0:
        raise ValueError(f"fuselage fineness L/D = {lam:.3f} <= 2: Torenbeek approximation invalid")
    return float(math.pi * d * length_m * (1.0 - 2.0 / lam) ** (2.0 / 3.0) * (1.0 + 1.0 / (lam * lam)))


_NACA = re.compile(r"^\s*NACA\s*[-\s]?(?P<code>[0-9A-Z\-]+)\s*$", re.IGNORECASE)


def tc_from_airfoil(designation: str) -> float | None:
    """t/c from a NACA designation (4-digit, 5-digit, 6-series). Unknown families return None (N8: missing)."""
    m = _NACA.match(designation or "")
    if not m:
        return None
    code = m.group("code").upper().replace("-", "")
    digits = re.sub(r"[^0-9]", "", code)
    if len(code) == 4 and code.isdigit():           # NACA 2412
        return int(code[2:]) / 100.0
    if len(code) == 5 and code.isdigit():           # NACA 23015
        return int(code[3:]) / 100.0
    if code.startswith("6") and len(digits) >= 5:    # NACA 63-415, 64A215, 65(2)-415
        return int(digits[-2:]) / 100.0
    return None
