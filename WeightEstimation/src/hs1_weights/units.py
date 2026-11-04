"""SI <-> imperial helpers. SI is used everywhere inside the module (CLAUDE.md rule 4).

Constants follow doc 05 N1. Functions accept floats or numpy arrays.
"""
from __future__ import annotations

import numpy as np
import numpy.typing as npt

G = 9.80665                 # m/s^2
RHO0 = 1.225                # kg/m^3, ISA sea level
LB = 0.45359237             # kg per lb (mass)
LBF = LB * G                # N per lbf
FT = 0.3048                 # m per ft
IN = 0.0254                 # m per in
FT2 = FT * FT               # m^2 per ft^2
KT = 0.514444               # m/s per knot (doc 05 N1; matches certification.KT)
MPH = 0.44704               # m/s per mph
KMH = 1.0 / 3.6             # m/s per km/h
US_GAL = 3.785411784        # L per US gallon
PSF = LBF / FT2             # Pa per lbf/ft^2 (= 47.880259 Pa)
HP = 745.69987158227022     # W per mechanical horsepower

Num = float | npt.NDArray[np.float64]


def kg_to_lb(x: Num) -> Num:
    return x / LB


def lb_to_kg(x: Num) -> Num:
    return x * LB


def m_to_ft(x: Num) -> Num:
    return x / FT


def ft_to_m(x: Num) -> Num:
    return x * FT


def m_to_in(x: Num) -> Num:
    return x / IN


def in_to_m(x: Num) -> Num:
    return x * IN


def m2_to_ft2(x: Num) -> Num:
    return x / FT2


def ft2_to_m2(x: Num) -> Num:
    return x * FT2


def pa_to_psf(x: Num) -> Num:
    return x / PSF


def psf_to_pa(x: Num) -> Num:
    return x * PSF


def mps_to_kt(x: Num) -> Num:
    return x / KT


def kt_to_mps(x: Num) -> Num:
    return x * KT


def litre_to_gal(x: Num) -> Num:
    return x / US_GAL


def gal_to_litre(x: Num) -> Num:
    return x * US_GAL


# Published unit -> (factor to SI, SI unit). Airspeed variants keep their convention in the note (N1).
UNIT_TO_SI: dict[str, tuple[float, str]] = {
    "kg": (1.0, "kg"), "lb": (LB, "kg"), "lbs": (LB, "kg"),
    "m": (1.0, "m"), "ft": (FT, "m"), "in": (IN, "m"), "mm": (1e-3, "m"), "cm": (1e-2, "m"),
    "m2": (1.0, "m2"), "ft2": (FT2, "m2"),
    "m/s": (1.0, "m/s"), "kt": (KT, "m/s"), "KCAS": (KT, "m/s"), "KIAS": (KT, "m/s"),
    "KEAS": (KT, "m/s"), "KTAS": (KT, "m/s"), "mph": (MPH, "m/s"), "km/h": (KMH, "m/s"),
    "Pa": (1.0, "Pa"), "psf": (PSF, "Pa"),
    "W": (1.0, "W"), "kW": (1e3, "W"), "hp": (HP, "W"),
    "Wh": (1.0, "Wh"), "kWh": (1e3, "Wh"),
    "L": (1.0, "L"), "US gal": (US_GAL, "L"), "gal": (US_GAL, "L"),
    "g": (1.0, "-"), "-": (1.0, "-"),
    "rad": (1.0, "rad"), "deg": (np.pi / 180.0, "rad"),
    "Wh/kg": (1.0, "Wh/kg"),
}

AIRSPEED_UNITS = {"kt", "KCAS", "KIAS", "KEAS", "KTAS", "mph", "km/h", "m/s"}


class UnknownUnitError(ValueError):
    pass


def to_si(value: float, unit: str) -> tuple[float, str]:
    """Convert a published value to SI. Raises UnknownUnitError for unlisted units."""
    try:
        f, si = UNIT_TO_SI[unit]
    except KeyError as e:
        raise UnknownUnitError(f"unit {unit!r} not in units.UNIT_TO_SI") from e
    return value * f, si


def from_si(value: float, unit: str) -> float:
    try:
        f, _ = UNIT_TO_SI[unit]
    except KeyError as e:
        raise UnknownUnitError(f"unit {unit!r} not in units.UNIT_TO_SI") from e
    return value / f


def is_si(unit: str) -> bool:
    return unit in {"kg", "m", "m2", "m/s", "Pa", "W", "Wh", "L", "-", "rad", "Wh/kg"}
