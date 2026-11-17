"""Component-model protocol, method registry and model context (doc 06 §1–2, P2-1).

All context quantities are SI. Any quantity may be a numpy array (Monte Carlo samples); models broadcast.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

import numpy as np
import numpy.typing as npt

from ..errors import MissingInputError

Arr = float | npt.NDArray[np.float64]

# Calibration group of each calibrated component (doc 06 §1: shared k_tail, shared k_gear)
K_GROUP = {
    "wing": "wing", "htail": "tail", "vtail": "tail", "fuselage": "fuselage", "main_gear": "gear",
    "nose_gear": "gear", "engine_installed": "engine", "fuel_system": "fuel_system",
    "flight_controls": "flight_controls", "electrical_lv": "electrical", "avionics_installed": "avionics",
    "furnishings": "furnishings", "ecs_anti_ice": "ecs",
}
STRUCTURAL_K = ("wing", "tail", "fuselage", "gear")

COMPONENT_GROUPS = {
    "Structure": ("wing", "htail", "vtail", "fuselage", "main_gear", "nose_gear", "nacelles_mounts"),
    "Propulsion": ("engine_installed", "fuel_system", "motors", "inverters", "battery_pack", "battery_bays",
                   "thermal_mgmt", "hv_harness", "propellers"),
    "Systems": ("flight_controls", "electrical_lv", "avionics_installed", "furnishings", "ecs_anti_ice"),
    "Fixed": ("fixed_items",),
}
ALL_COMPONENTS = tuple(c for g in COMPONENT_GROUPS.values() for c in g)
ICE_ONLY = ("engine_installed", "fuel_system")
ELECTRIC_ONLY = ("motors", "inverters", "battery_pack", "battery_bays", "thermal_mgmt", "hv_harness",
                 "nacelles_mounts", "propellers", "fixed_items")


@dataclass
class ComponentResult:
    component_id: str
    mass_kg: Arr
    method: str
    inputs_used: dict[str, Any] = field(default_factory=dict)
    governing_load_case: str = ""
    assumptions: list[str] = field(default_factory=list)
    calibrated: bool = False                 # True -> mass_i = k_i(group) * f_i (doc 06 §6)
    transcription_verified: bool = True      # False -> exponents not yet checked against the source text
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def k_group(self) -> str | None:
        return K_GROUP.get(self.component_id) if self.calibrated else None


@dataclass
class MotorCtx:
    id: str
    p_cont_w: float
    sp_motor_w_per_kg: Arr
    sp_inv_w_per_kg: Arr
    eta_chain: Arr
    wing_mounted: bool
    y_m: float
    inverter_in_nacelle: bool
    m_prop_kg: Arr
    m_fairing_kg: Arr


@dataclass
class HarnessRunCtx:
    motor: str
    length_m: float
    power_w: Arr
    v_bus: float
    rho_kg_m3: float
    j_allow_a_per_mm2: Arr
    f_ins: Arr


@dataclass
class ItemCtx:
    name: str
    category: str
    mass_kg: Arr
    estimate: bool = False


@dataclass
class ModelContext:
    """Inputs to L1 models (SI). ``None`` means missing; ``req`` raises MissingInputError naming the field."""
    # design weights and loads
    W_dg: Arr | None = None
    W_l: Arr | None = None
    n_ult: Arr | None = None
    n_ult_case: str = ""
    N_l: Arr | None = None                     # ultimate landing load factor
    N_l_case: str = ""
    q: Arr | None = None                       # cruise dynamic pressure, Pa (sea level, EAS at V_C)
    q_D: Arr | None = None
    V_C: Arr | None = None                     # m/s EAS
    V_D: Arr | None = None
    V_H: Arr | None = None
    mach: Arr | None = None
    # wing
    S_w: float | None = None
    b: float | None = None
    sweep_qc: float | None = None              # rad
    taper: float | None = None
    tc_root: float | None = None
    W_fw: Arr | None = None                    # wing fuel mass, kg
    # tails
    S_ht: float | None = None
    A_ht: float | None = None
    sweep_ht: float | None = None
    taper_ht: float | None = None
    tc_ht: float | None = None
    S_vt: float | None = None
    A_vt: float | None = None
    sweep_vt: float | None = None
    taper_vt: float | None = None
    tc_vt: float | None = None
    t_tail: bool = False
    L_t: float | None = None                   # tail arm, m
    # fuselage
    fus_L: float | None = None                 # fuselage length, m
    fus_L_struct: float | None = None          # structural length, m (Raymer L)
    fus_W: float | None = None
    fus_H: float | None = None                 # structural depth (Raymer D)
    S_f: float | None = None                   # wetted area, m^2
    gear_on_fuselage: bool = False             # Torenbeek K_f
    high_wing: bool = False
    # gear
    gear_type: str = "fixed_tricycle"
    L_m: float | None = None                   # main strut length, m
    L_n: float | None = None
    # ICE fleet propulsion and systems
    is_electric: bool = False
    W_en: float | None = None                  # engine dry mass each, kg
    N_en: int = 1
    V_t: float | None = None                   # total fuel volume, L
    V_i: float | None = None                   # integral tank volume, L
    N_t: int | None = None
    W_uav: Arr | None = None                   # uninstalled avionics, kg
    N_p: int | None = None
    has_ecs: bool = False
    # electric (bottom-up)
    E_nom_Wh: Arr | None = None
    e_spec_wh_per_kg: Arr | None = None
    energy_level: str | None = None            # "cell" | "pack" (D3)
    eta_c2p: Arr | None = None
    bay_energy_fractions: tuple[float, ...] = ()
    motors: list[MotorCtx] = field(default_factory=list)
    c_mount_kg_per_n: Arr | None = None
    n_mount: Arr | None = None                 # envelope powerplant/ESS mount factor (forward governs)
    n_mount_case: str = ""
    N_bays: int | None = None
    m_bay_hw_kg: Arr | None = None
    c_ret_kg_per_n: Arr | None = None
    A_cut: float | None = None
    k_cut: Arr | None = None
    k_th_kg_per_w: Arr | None = None
    Q_pack_w: Arr | None = None
    cooling: str = ""
    ambient_isa_delta_c: float | None = None
    harness: list[HarnessRunCtx] = field(default_factory=list)
    lv_system_kg: Arr | None = None
    equipment: list[ItemCtx] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def req(self, name: str, where: str) -> Any:
        val = getattr(self, name)
        if val is None:
            raise MissingInputError(name, where)
        return val


@runtime_checkable
class ComponentModel(Protocol):
    @property
    def component_id(self) -> str: ...

    @property
    def method_id(self) -> str: ...

    def __call__(self, ctx: ModelContext) -> ComponentResult: ...


@dataclass(frozen=True)
class FunctionModel:
    component_id: str
    method_id: str
    fn: Callable[[ModelContext], ComponentResult]
    reference: str = ""
    transcription_verified: bool = True

    def __call__(self, ctx: ModelContext) -> ComponentResult:
        return self.fn(ctx)


class MethodRegistry:
    """component_id -> {method_id: model}; defaults per powertrain kind ("ice" / "electric")."""

    def __init__(self) -> None:
        self._m: dict[str, dict[str, ComponentModel]] = {}
        self._default: dict[tuple[str, str], str] = {}

    def register(self, model: ComponentModel, default_for: tuple[str, ...] = ()) -> None:
        self._m.setdefault(model.component_id, {})[model.method_id] = model
        for kind in default_for:
            self._default[(model.component_id, kind)] = model.method_id

    def get(self, component_id: str, method_id: str) -> ComponentModel:
        try:
            return self._m[component_id][method_id]
        except KeyError as e:
            raise KeyError(f"no method {method_id!r} for component {component_id!r}; "
                           f"available: {sorted(self._m.get(component_id, {}))}") from e

    def default(self, component_id: str, kind: str) -> str:
        return self._default[(component_id, kind)]

    def methods(self, component_id: str) -> list[str]:
        return sorted(self._m.get(component_id, {}))

    def components(self) -> list[str]:
        return sorted(self._m)

    def describe(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for c, ms in sorted(self._m.items()):
            out[c] = {mid: {"reference": getattr(m, "reference", ""),
                            "transcription_verified": getattr(m, "transcription_verified", True)}
                      for mid, m in sorted(ms.items())}
        return out


REGISTRY = MethodRegistry()


F = TypeVar("F", bound=Callable[..., ComponentResult])


def component(component_id: str, method_id: str, *, default_for: tuple[str, ...] = (), reference: str = "",
              verified: bool = True) -> Callable[[F], F]:
    """Decorator: register ``fn`` as ``method_id`` for ``component_id``."""
    def deco(fn: F) -> F:
        REGISTRY.register(FunctionModel(component_id, method_id, fn, reference, verified), default_for)
        return fn
    return deco


def as_array(x: Arr) -> npt.NDArray[np.float64]:
    return np.asarray(x, dtype=float)
