"""Compatibility shim: normalization moved to ``hs1_weights.data.normalize`` (P1-3)."""
from .data.normalize import (  # noqa: F401
    CAP_TOL,
    DEF_SIGMA,
    OIL_DENSITY,
    PROTOTYPE_SIGMA,
    REGULATORY_CAPS,
    TIER_SIGMA,
    UNUSABLE_FUEL_FRAC,
    NormalizedRecord,
    detect_cap,
    normalize,
)
