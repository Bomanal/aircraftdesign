"""Fleet database schema. Every numeric field carries its own provenance."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
import json


class Tier(str, Enum):
    A = "A"   # TCDS, POH W&B, Roskam/Torenbeek component tables, NASA/NACA, Jane's -> calibration
    B = "B"   # manufacturer spec sheet, LAA/EAA data, peer-reviewed study -> calibration, inflated variance
    C = "C"   # brochure, press, prototype claim -> analog reasoning only


class EmptyWeightDef(str, Enum):
    BASIC_GAMA = "basic_empty_gama"        # unusable fuel + full oil + operating fluids (post-1975 GAMA)
    LICENSED = "licensed_empty"            # unusable fuel, no oil (older US definition)
    STANDARD_NO_OPTIONS = "standard_empty" # excludes optional equipment
    MARKETING = "marketing_from"           # "empty weight from ..." claims
    UNKNOWN = "unknown"


class Material(str, Enum):
    CARBON = "composite_carbon"
    GLASS = "composite_glass"
    COMPOSITE_MIXED = "composite_mixed"
    ALUMINIUM = "metal_aluminium"
    STEEL_TUBE_FABRIC = "steel_tube_fabric"
    WOOD = "wood"


class Powertrain(str, Enum):
    PISTON_AVGAS = "piston_avgas"
    PISTON_MOGAS_ROTAX = "piston_rotax"
    PISTON_DIESEL = "piston_jet_a"
    TURBOPROP = "turboprop"
    ELECTRIC = "battery_electric"
    HYBRID_SERIES = "hybrid_series"
    HYBRID_PARALLEL = "hybrid_parallel"


class CertTag(str, Enum):
    FAR23_NORMAL = "far23_normal"
    FAR23_UTILITY = "far23_utility"
    FAR23_AEROBATIC = "far23_aerobatic"
    FAR23_COMMUTER = "far23_commuter"
    PART23_AMDT64 = "part23_amdt64"       # performance-based, level/speed in notes
    CS_VLA = "cs_vla"
    CS_LSA = "cs_lsa"                      # EASA CS-LSA (incl. TC with special conditions)
    ASTM_F2245 = "astm_f2245_lsa"
    EU_UL = "eu_ultralight"
    EXPERIMENTAL = "experimental"


class GearType(str, Enum):
    FIXED_TRICYCLE = "fixed_tricycle"
    FIXED_TAILWHEEL = "fixed_tailwheel"
    RETRACTABLE = "retractable"
    AMPHIBIAN = "amphibian"


@dataclass
class Source:
    id: str
    title: str
    kind: str                 # TCDS | POH | Janes | spec_sheet | report | textbook | press
    tier: Tier
    url: str = ""
    accessed: str = ""


@dataclass
class V:
    """A sourced value."""
    value: float
    unit: str
    source: str               # Source.id
    confidence: float = 0.9   # analyst confidence that the value is correct for this variant
    note: str = ""


@dataclass
class ComponentMass:
    name: str                 # wing | fuselage | htail | vtail | gear | nacelle | engine_installed | ...
    mass: V
    material: Material | None = None


@dataclass
class AircraftRecord:
    id: str
    type_name: str
    variant: str
    year_first_flight: int | None
    cert: CertTag
    cert_note: str = ""
    seats: int = 2
    production_status: str = "production"     # production | out_of_production | prototype | certified_not_delivered

    # weights
    mtow: V | None = None
    empty_weight: V | None = None
    empty_weight_def: EmptyWeightDef = EmptyWeightDef.UNKNOWN
    max_landing_weight: V | None = None
    fuel_capacity_usable: V | None = None     # L
    battery_energy_total: V | None = None     # kWh
    battery_mass_installed: V | None = None   # kg, if separately published

    # geometry
    wing_area: V | None = None
    span: V | None = None
    root_tc: V | None = None
    tip_tc: V | None = None
    taper: V | None = None
    sweep_qc: V | None = None
    fuselage_length: V | None = None
    fuselage_max_width: V | None = None
    fuselage_max_height: V | None = None
    htail_area: V | None = None
    vtail_area: V | None = None

    # loads and speeds
    n_limit_pos: V | None = None
    n_limit_neg: V | None = None
    v_ne: V | None = None
    v_c: V | None = None
    v_h: V | None = None
    v_s0: V | None = None
    v_s1: V | None = None

    # powertrain
    powertrain: Powertrain = Powertrain.PISTON_AVGAS
    n_motors_or_engines: int = 1
    rated_power_total: V | None = None        # kW
    engine_dry_mass: V | None = None
    prop_type: str = ""                       # fixed | ground_adjustable | constant_speed

    # configuration and structure
    material_primary: Material = Material.ALUMINIUM
    material_by_component: dict[str, Material] = field(default_factory=dict)
    gear: GearType = GearType.FIXED_TRICYCLE
    high_wing: bool = False
    pressurized: bool = False
    ballistic_parachute: bool = False
    components: list[ComponentMass] = field(default_factory=list)

    notes: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=lambda o: o.value if isinstance(o, Enum) else str(o), indent=2)
