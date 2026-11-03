"""Normalization of fleet records before calibration."""
from __future__ import annotations

from dataclasses import dataclass

from .schema import AircraftRecord, EmptyWeightDef, Powertrain, Tier, CertTag

OIL_DENSITY = 0.88       # kg/L
UNUSABLE_FUEL_FRAC = 0.03  # of total fuel when unusable is not published

# Category ceilings that produce MTOW censoring (kg). Tolerance +/-1.5 kg.
REGULATORY_CAPS = {
    300.0: "EU single-seat UL",
    450.0: "EU UL (legacy, 2-seat landplane)",
    472.5: "EU UL with ballistic rescue (legacy)",
    495.0: "EU UL amphibian (legacy)",
    525.0: "DE/AT UL variant",
    560.0: "EU UL (national 2-seat)",
    598.7: "US LSA 1,320 lb (pre-MOSAIC)",
    600.0: "EU UL 600 kg / CS-LSA",
    648.6: "US LSA seaplane 1,430 lb (pre-MOSAIC)",
    650.0: "CS-LSA seaplane",
    750.0: "CS-VLA / JAR-VLA",
}
CAP_TOL = 1.5

# Relative (1-sigma) measurement uncertainty on empty weight
TIER_SIGMA = {Tier.A: 0.02, Tier.B: 0.04, Tier.C: None}
DEF_SIGMA = {
    EmptyWeightDef.BASIC_GAMA: 0.0,
    EmptyWeightDef.LICENSED: 0.01,
    EmptyWeightDef.STANDARD_NO_OPTIONS: 0.03,
    EmptyWeightDef.MARKETING: 0.06,
    EmptyWeightDef.UNKNOWN: 0.03,
}
PROTOTYPE_SIGMA = 0.04


@dataclass
class NormalizedRecord:
    id: str
    empty_weight_basic_kg: float | None
    sigma_rel: float | None           # None -> exclude from calibration
    mtow_kg: float | None
    mtow_censored: bool
    cap_label: str
    design_weight_kg: float | None    # weight to use in load-dependent component equations
    warnings: list[str]


def detect_cap(mtow_kg: float) -> tuple[bool, str]:
    cap = min(REGULATORY_CAPS, key=lambda c: abs(mtow_kg - c))
    if abs(mtow_kg - cap) <= CAP_TOL:
        return True, REGULATORY_CAPS[cap]
    return False, ""


def normalize(rec: AircraftRecord, tier_of: dict[str, Tier], oil_litres: float | None = None) -> NormalizedRecord:
    warn: list[str] = []
    ew = rec.empty_weight.value if rec.empty_weight else None
    mtow = rec.mtow.value if rec.mtow else None

    # 1. Empty-weight definition -> basic empty (GAMA)
    if ew is not None and rec.empty_weight_def == EmptyWeightDef.LICENSED:
        if oil_litres is not None:
            ew += oil_litres * OIL_DENSITY
        else:
            warn.append("licensed empty weight, oil quantity unknown: not corrected, variance inflated")

    if rec.powertrain in (Powertrain.ELECTRIC, Powertrain.HYBRID_SERIES, Powertrain.HYBRID_PARALLEL):
        if rec.battery_mass_installed is None and rec.battery_energy_total is not None:
            warn.append("electric: confirm published empty weight includes installed pack")

    # 2. Variance from source tier and definition
    sigma = None
    if rec.empty_weight is not None:
        tier = tier_of.get(rec.empty_weight.source, Tier.C)
        base = TIER_SIGMA[tier]
        if base is None:
            warn.append("tier C empty weight: excluded from calibration, analog use only")
        else:
            sigma = (base**2 + DEF_SIGMA[rec.empty_weight_def] ** 2) ** 0.5
            if rec.production_status == "prototype":
                sigma = (sigma**2 + PROTOTYPE_SIGMA**2) ** 0.5
                warn.append("prototype: production empty weight typically grows")

    # 3. MTOW censoring
    censored, label = (False, "")
    if mtow is not None:
        censored, label = detect_cap(mtow)
        if rec.cert in (CertTag.ASTM_F2245, CertTag.EU_UL, CertTag.CS_VLA, CertTag.CS_LSA) and not censored:
            warn.append("category aircraft with MTOW off a known cap: check variant/market")
    design_w = None if (mtow is None or censored) else mtow
    if censored:
        warn.append(f"MTOW at regulatory cap ({label}): treated as lower bound; "
                    "load-dependent equations need structural design weight from TCDS/POH if available")

    return NormalizedRecord(rec.id, ew, sigma, mtow, censored, label, design_w, warn)
