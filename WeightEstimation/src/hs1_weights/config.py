"""Pydantic v2 I/O models for ``predict()`` (doc 02 §5).

Uncertain inputs are ``Param``: a fixed value or a distribution, with an ``assumption`` flag for placeholder
priors (doc 06 §5). A bare number is accepted wherever a Param is expected. The only numeric defaults here are
those the spec lists (each is logged in the report's ``assumptions`` when used):

* η_c2p ~ U(0.55, 0.70) for cell-level specific energy (doc 06 §5.1)
* hot-day ambient ISA+30 °C (doc 06 §5.6, D4)
* gear limit load factor 3.0 (doc 06 §3.5)
* occupants 2 × 86 kg (doc 10 §3); k_neg = 1 (doc 03 §3); W_fw = 0 (battery-electric)
* certification bases ASTM_F2245 + FAR23_L1; HS1 cert group level ASTM_F2245 (D6)
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator
from scipy import stats

from .schema import GearType, Material

FloatArr = npt.NDArray[np.float64]


class Param(BaseModel):
    """A scalar input: fixed, or a distribution sampled in Monte Carlo."""
    model_config = ConfigDict(extra="forbid")
    value: float | None = None
    dist: Literal["fixed", "uniform", "normal", "lognormal", "triangular"] = "fixed"
    low: float | None = None
    high: float | None = None
    mode: float | None = None
    mean: float | None = None          # normal mean; lognormal median
    sd: float | None = None            # normal sd; lognormal sigma of log
    assumption: bool = False           # placeholder prior: must be replaced by vendor data or analysis
    note: str = ""

    @model_validator(mode="after")
    def _check(self) -> Param:
        need = {"fixed": ("value",), "uniform": ("low", "high"), "normal": ("mean", "sd"),
                "lognormal": ("mean", "sd"), "triangular": ("low", "mode", "high")}[self.dist]
        missing = [k for k in need if getattr(self, k) is None]
        if missing:
            raise ValueError(f"{self.dist} Param needs {missing}")
        if self.dist in ("uniform", "triangular") and not self.low < self.high:  # type: ignore[operator]
            raise ValueError("low must be < high")
        return self

    @property
    def uncertain(self) -> bool:
        return self.dist != "fixed"

    def ppf(self, u: FloatArr) -> FloatArr:
        u = np.asarray(u, dtype=np.float64)
        f = _num
        if self.dist == "fixed":
            return np.full_like(u, f(self.value))
        if self.dist == "uniform":
            return f(self.low) + (f(self.high) - f(self.low)) * u
        if self.dist == "normal":
            return np.asarray(stats.norm.ppf(u, f(self.mean), f(self.sd)), dtype=np.float64)
        if self.dist == "lognormal":
            return np.asarray(f(self.mean) * np.exp(f(self.sd) * stats.norm.ppf(u)), dtype=np.float64)
        lo, hi = f(self.low), f(self.high)
        return np.asarray(stats.triang.ppf(u, (f(self.mode) - lo) / (hi - lo), loc=lo, scale=hi - lo),
                          dtype=np.float64)

    def point(self) -> float:
        return float(self.ppf(np.array([0.5]))[0])


def _num(x: float | None) -> float:
    return float("nan") if x is None else float(x)      # fields are validated in Param._check


def _to_param(v: Any) -> Any:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return {"value": float(v)}
    return v


P = Annotated[Param, BeforeValidator(_to_param)]
ETA_C2P_DEFAULT = Param(dist="uniform", low=0.55, high=0.70, assumption=True,
                        note="doc 06 §5.1 placeholder prior: structure, BMS, containment, enclosure, busbars")


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Station(_M):
    x_m: float
    z_m: float = 0.0


class WingGeom(_M):
    area_m2: float = Field(gt=0)
    span_m: float = Field(gt=0)
    tc_root: float = Field(gt=0, lt=0.4)
    tc_tip: float = Field(gt=0, lt=0.4)
    taper: float = Field(gt=0, le=1.0)
    sweep_qc_deg: float
    a_per_rad: float = Field(gt=0)
    cbar_m: float | None = None        # derived (N8 trapezoidal MAC) if absent
    x_lemac_m: float | None = None     # leading edge of MAC, for CG in %MAC
    wing_fuel_kg: float = 0.0          # battery-electric: no wing fuel (doc 06 §3.1 override applies)


class SurfaceGeom(_M):
    area_m2: float = Field(gt=0)
    aspect_ratio: float = Field(gt=0)
    taper: float = Field(gt=0, le=1.0)
    sweep_qc_deg: float
    tc: float = Field(gt=0, lt=0.4)


class FuselageGeom(_M):
    length_m: float = Field(gt=0)
    structural_length_m: float = Field(gt=0)
    width_m: float = Field(gt=0)
    height_m: float = Field(gt=0)
    wetted_area_m2: float | None = None   # derived (N8 Torenbeek) if absent, flagged
    gear_on_fuselage: bool = False


class GearSpec(_M):
    type: GearType
    main_strut_length_m: float = Field(gt=0)
    nose_strut_length_m: float | None = None
    limit_load_factor: float | None = None   # None -> doc 06 §3.5 default 3.0 (logged)
    x_main_m: float | None = None            # for tip-over check in CG cases
    x_nose_m: float | None = None


class Motor(_M):
    id: str
    p_cont_kw: float = Field(gt=0)
    sp_motor_kw_per_kg: P
    sp_inv_kw_per_kg: P
    eta_chain: P
    location: Literal["wing", "fuselage"]
    y_m: float = 0.0                       # spanwise station (wing-mounted)
    inverter_in_nacelle: bool = False
    prop_mass_kg: P                        # vendor mass (doc 06 §1)
    fairing_mass_kg: P
    station: Station | None = None


class PackSpec(_M):
    energy_kwh: P                          # E_nom from hs1_design
    specific_energy_wh_per_kg: P
    level: Literal["cell", "pack"]         # D3: must be declared
    eta_c2p: P | None = None               # cell-level only; None -> doc 06 §5.1 default prior
    heat_kw: P                             # Q_pack at design C-rate, hot day


class HarnessRun(_M):
    motor: str
    length_m: float = Field(gt=0)          # from layout stations (doc 06 §5.7), total conductor length
    power_kw: P | None = None              # None -> motor P_cont / eta_chain (electrical input)
    conductor: Literal["Cu", "Al"] = "Cu"
    j_allow_a_per_mm2: P
    f_ins: P


class ThermalSpec(_M):
    cooling: Literal["air", "liquid"]
    k_th_kg_per_kw: P
    ambient_isa_delta_c: float = 30.0      # D4 default ISA+30


class ElectricPowertrain(_M):
    motors: list[Motor]
    pack: PackSpec
    v_bus: float = Field(gt=0)
    harness: list[HarnessRun]
    thermal: ThermalSpec
    lv_system_kg: P
    c_mount_kg_per_kn: P                   # mount structure mass per kN of design load (placeholder)
    stations: dict[str, Station] = Field(default_factory=dict)   # thermal, harness, lv, nacelles...


class Bay(_M):
    station: Station
    energy_fraction: float = Field(gt=0, le=1)


class BayLayout(_M):
    bays: list[Bay]
    hw_mass_per_bay_kg: P
    c_ret_kg_per_kn: P
    cutout_area_m2: float = Field(ge=0)
    k_cut: P
    mass_per_kwh_band: tuple[float, float] | None = None    # rule C9 plausibility band (kg/kWh)

    @model_validator(mode="after")
    def _fractions(self) -> BayLayout:
        s = sum(b.energy_fraction for b in self.bays)
        if not self.bays or abs(s - 1.0) > 1e-6:
            raise ValueError(f"bay energy fractions must sum to 1 (got {s:.6f})")
        return self


class FixedItem(_M):
    name: str
    category: Literal["avionics", "seats", "interior", "brs", "lv_battery", "lighting", "hv_protection", "other"]
    mass_kg: P
    station: Station | None = None
    estimate: bool = False                 # no vendor mass yet


class HS1Config(_M):
    name: str = "HS1"
    mass_guess_kg: float = Field(gt=0)
    wing: WingGeom
    htail: SurfaceGeom
    vtail: SurfaceGeom
    t_tail: bool = False
    tail_arm_m: float = Field(gt=0)
    fuselage: FuselageGeom
    cl_max_clean: float = Field(gt=0)
    cl_max_land: float = Field(gt=0)
    cl_max_includes_blowing: bool = False
    cl_max_clean_unblown: float | None = None
    cl_max_land_unblown: float | None = None
    vc_kt: float = Field(gt=0)
    vh_kt: float = Field(gt=0)
    gear: GearSpec
    powertrain: ElectricPowertrain
    battery_bays: BayLayout
    equipment: list[FixedItem]
    brs_fitted: bool = True
    has_ecs: bool = False
    material_by_component: dict[str, Material]
    cert_bases: list[str] = ["ASTM_F2245", "FAR23_L1"]
    cert_group_level: Literal["ASTM_F2245", "FAR23_L1"] = "ASTM_F2245"   # D6
    k_neg: float = 1.0
    methods: dict[str, str] = Field(default_factory=dict)
    year: int = 2026
    seats: int = 2
    payload_kg: float | None = None
    occupant_kg: float = 86.0
    n_occupants: int = 2
    light_pilot_kg: float | None = None
    seat_station: Station | None = None
    stations: dict[str, Station] | None = None      # component -> station (CG); None -> CG not computed
    gp_out_of_class: bool = False                    # doc 07 §6 guard override
    provenance: list[str] = Field(default_factory=list)   # where inputs came from (e.g. an eacsize run)

    @model_validator(mode="after")
    def _cross(self) -> HS1Config:
        ids = [m.id for m in self.powertrain.motors]
        if len(set(ids)) != len(ids):
            raise ValueError("motor ids must be unique")
        for r in self.powertrain.harness:
            if r.motor not in ids:
                raise ValueError(f"harness run references unknown motor {r.motor!r}")
        if self.powertrain.pack.level == "pack" and self.powertrain.pack.eta_c2p is not None:
            raise ValueError("pack-level specific energy: eta_c2p must be omitted (η = 1, doc 06 §5.1)")
        if self.gear.type != GearType.FIXED_TAILWHEEL and self.gear.nose_strut_length_m is None:
            raise ValueError("gear.nose_strut_length_m required for tricycle gear")
        return self


def _strip_comments(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: _strip_comments(v) for k, v in d.items() if not str(k).startswith("_")}
    if isinstance(d, list):
        return [_strip_comments(v) for v in d]
    return d


def load_config(path: str) -> HS1Config:
    """Load an HS1Config from JSON; keys starting with '_' are comments."""
    import json
    with open(path, encoding="utf-8") as f:
        return HS1Config.model_validate(_strip_comments(json.load(f)))
