"""Monte Carlo propagation (doc 01 R8, doc 10): one vectorised evaluator shared by MC, Sobol and closure.

Inputs are named: config Param paths (e.g. ``powertrain.pack.eta_c2p``), technology factors ``k:<component>``
and the aircraft-level model error ``model_error`` (log multiplier on W_E).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from pydantic import BaseModel

from . import certification as cert
from .calibration.fleet import CERT_LEVEL, ELECTRIC_LIKE
from .calibration.posterior import FloatArr, Posterior, k_draws
from .components import K_GROUP, ComponentResult, HarnessRunCtx, ItemCtx, ModelContext, MotorCtx, evaluate_tree
from .components.electric import RHO_CONDUCTOR
from .components.tree import component_set, method_for
from .config import ETA_C2P_DEFAULT, HS1Config, Param
from .data.derive import fuselage_wetted_area_torenbeek, mac_trapezoidal
from .errors import MissingInputError
from .units import KT, RHO0

GEAR_LIMIT_DEFAULT = 3.0
SPEED_OF_SOUND_SL = 340.294
CERT_GROUP = {"ASTM_F2245": CERT_LEVEL["astm_f2245_lsa"], "FAR23_L1": CERT_LEVEL["far23_normal"]}
DECISION_RELEVANT = ("powertrain.pack.eta_c2p", "powertrain.pack.specific_energy_wh_per_kg")


# --- params --------------------------------------------------------------------------------
def iter_params(model: BaseModel, prefix: str = "") -> Iterator[tuple[str, Param]]:
    """Yield (path, Param) for every Param in a config. List items are keyed by id/name/motor, else index."""
    for name in type(model).model_fields:
        val = getattr(model, name)
        path = f"{prefix}{name}"
        if isinstance(val, Param):
            yield path, val
        elif isinstance(val, BaseModel):
            yield from iter_params(val, path + ".")
        elif isinstance(val, list):
            for i, item in enumerate(val):
                if isinstance(item, BaseModel):
                    key = (getattr(item, "id", None) or getattr(item, "name", None) or getattr(item, "motor", None)
                           or str(i))
                    yield from iter_params(item, f"{path}.{key}.")


def config_params(cfg: HS1Config) -> tuple[dict[str, Param], list[str]]:
    """All Params plus spec-listed defaults that apply, with the assumption lines they generate."""
    params = dict(iter_params(cfg))
    notes = []
    pack = cfg.powertrain.pack
    if pack.level == "cell" and pack.eta_c2p is None:
        params["powertrain.pack.eta_c2p"] = ETA_C2P_DEFAULT
        notes.append("default η_c2p ~ U(0.55, 0.70) (doc 06 §5.1 placeholder prior)")
    for path, p in params.items():
        if p.assumption:
            notes.append(f"placeholder prior: {path} ({p.dist}{': ' + p.note if p.note else ''})")
    return params, notes


# --- loads ---------------------------------------------------------------------------------
@dataclass
class Loads:
    n_ult: Any
    n_ult_case: str
    q: float
    q_D: Any
    V_D: Any
    n_mount: Any | None
    n_mount_case: str
    envelope: dict[str, Any]
    per_basis: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)


def stall_cl(cfg: HS1Config) -> tuple[float, float, list[str]]:
    """Unblown C_L,max for stall-limit checks (doc 03 §5 rule 1: power-idle, no DEP credit)."""
    if not cfg.cl_max_includes_blowing:
        return cfg.cl_max_clean, cfg.cl_max_land, []
    if cfg.cl_max_clean_unblown is None:
        raise MissingInputError("cl_max_clean_unblown", "stall-limit check (blown C_L,max supplied)")
    if cfg.cl_max_land_unblown is None:
        raise MissingInputError("cl_max_land_unblown", "stall-limit check (blown C_L,max supplied)")
    return cfg.cl_max_clean_unblown, cfg.cl_max_land_unblown, [
        "WARNING: C_L,max inputs include DEP blowing; unblown values used for stall limits (doc 03 §5.1)"]


def cbar(cfg: HS1Config) -> tuple[float, list[str]]:
    if cfg.wing.cbar_m is not None:
        return cfg.wing.cbar_m, []
    return (mac_trapezoidal(cfg.wing.area_m2, cfg.wing.span_m, cfg.wing.taper),
            ["wing c̄ derived from trapezoidal planform (N8)"])


def loads_at(cfg: HS1Config, w_dg: float) -> Loads:
    """Dual-basis envelope at W_dg (doc 03 §3). The envelope is the only source of L1 loads."""
    cl_c, cl_l, notes = stall_cl(cfg)
    c, n2 = cbar(cfg)
    res = []
    for key in cfg.cert_bases:
        if key not in cert.BASES:
            raise ValueError(f"unknown certification basis {key!r}")
        res.append(cert.evaluate(cert.BASES[key], w_dg, cfg.wing.area_m2, c, cfg.wing.a_per_rad, cl_c, cl_l,
                                 cfg.vc_kt, cfg.vh_kt))
    env = cert.envelope(res)
    pos, neg = env["n_ult_pos"], env["n_ult_neg"]
    gov_pos = next(r["pos_governed_by"] for r in res if r["basis"] == pos["governing"])
    if abs(neg["value"]) * cfg.k_neg > pos["value"]:
        n_ult, case = abs(neg["value"]) * cfg.k_neg, f"n_ult- = {neg['value']:.2f} x k_neg {cfg.k_neg:g} " \
                                                       f"({neg['governing']})"
    else:
        n_ult, case = pos["value"], f"n_ult+ = {pos['value']:.2f} ({pos['governing']}, {gov_pos})"
    fwd = env["items_of_mass_aft"].get("forward")
    n_mount = None if fwd is None else fwd["value"]
    mcase = "" if fwd is None else f"ESS/powerplant mount {fwd['value']:.1f} g forward ({fwd['governing']})"
    return Loads(n_ult, case, 0.5 * RHO0 * (cfg.vc_kt * KT) ** 2, env["q_D_Pa"]["value"], env["VD_kt"]["value"] * KT,
                 n_mount, mcase, env, res, notes + n2)


# --- context -------------------------------------------------------------------------------
def build_context(cfg: HS1Config, loads: Loads, v: Mapping[str, Any], w_dg: Any) -> tuple[ModelContext, list[str]]:
    """HS1 ModelContext from config, loads and sampled Param values ``v`` (path -> array)."""
    notes: list[str] = []
    pt, bays, f = cfg.powertrain, cfg.battery_bays, cfg.fuselage
    s_f = f.wetted_area_m2
    if s_f is None:
        s_f = fuselage_wetted_area_torenbeek(f.length_m, f.width_m, f.height_m)
        notes.append("fuselage wetted area from Torenbeek approximation (N8), flagged")
    gl = cfg.gear.limit_load_factor
    if gl is None:
        gl = GEAR_LIMIT_DEFAULT
        notes.append("gear limit load factor 3.0 (doc 06 §3.5 default); derive from sink speed and stroke")
    motors = []
    for m in pt.motors:
        p = f"powertrain.motors.{m.id}."
        motors.append(MotorCtx(m.id, m.p_cont_kw * 1e3, v[p + "sp_motor_kw_per_kg"] * 1e3,
                               v[p + "sp_inv_kw_per_kg"] * 1e3, v[p + "eta_chain"], m.location == "wing", m.y_m,
                               m.inverter_in_nacelle, v[p + "prop_mass_kg"], v[p + "fairing_mass_kg"]))
    mm = {m.id: m for m in motors}
    runs = []
    for r in pt.harness:
        p = f"powertrain.harness.{r.motor}."
        if r.power_kw is not None:
            power = v[p + "power_kw"] * 1e3
        else:
            power = mm[r.motor].p_cont_w / mm[r.motor].eta_chain
            notes.append(f"harness run {r.motor}: power = P_cont / eta_chain")
        runs.append(HarnessRunCtx(r.motor, r.length_m, power, pt.v_bus, RHO_CONDUCTOR[r.conductor],
                                  v[p + "j_allow_a_per_mm2"], v[p + "f_ins"]))
    items = [ItemCtx(e.name, e.category, v[f"equipment.{e.name}.mass_kg"], e.estimate) for e in cfg.equipment]
    w, ht, vt = cfg.wing, cfg.htail, cfg.vtail
    ctx = ModelContext(
        W_dg=w_dg, W_l=w_dg, n_ult=loads.n_ult, n_ult_case=loads.n_ult_case, N_l=1.5 * gl,
        N_l_case=f"N_l = 1.5 x {gl:g}", q=loads.q, q_D=loads.q_D, V_C=cfg.vc_kt * KT, V_D=loads.V_D,
        V_H=cfg.vh_kt * KT, mach=cfg.vc_kt * KT / SPEED_OF_SOUND_SL, S_w=w.area_m2, b=w.span_m,
        sweep_qc=math.radians(w.sweep_qc_deg), taper=w.taper, tc_root=w.tc_root, W_fw=w.wing_fuel_kg,
        S_ht=ht.area_m2, A_ht=ht.aspect_ratio, sweep_ht=math.radians(ht.sweep_qc_deg), taper_ht=ht.taper,
        tc_ht=ht.tc, S_vt=vt.area_m2, A_vt=vt.aspect_ratio, sweep_vt=math.radians(vt.sweep_qc_deg), taper_vt=vt.taper,
        tc_vt=vt.tc, t_tail=cfg.t_tail, L_t=cfg.tail_arm_m, fus_L=f.length_m, fus_L_struct=f.structural_length_m,
        fus_W=f.width_m, fus_H=f.height_m, S_f=s_f, gear_on_fuselage=f.gear_on_fuselage,
        gear_type=cfg.gear.type.value, L_m=cfg.gear.main_strut_length_m, L_n=cfg.gear.nose_strut_length_m,
        is_electric=True, N_p=cfg.seats, has_ecs=cfg.has_ecs,
        W_uav=(sum((i.mass_kg for i in items if i.category == "avionics"), start=np.float64(0.0))
               if any(i.category == "avionics" for i in items) else None),
        E_nom_Wh=v["powertrain.pack.energy_kwh"] * 1e3, e_spec_wh_per_kg=v["powertrain.pack.specific_energy_wh_per_kg"],
        energy_level=pt.pack.level, eta_c2p=v.get("powertrain.pack.eta_c2p"),
        bay_energy_fractions=tuple(b.energy_fraction for b in bays.bays), motors=motors,
        c_mount_kg_per_n=v["powertrain.c_mount_kg_per_kn"] / 1e3, n_mount=loads.n_mount,
        n_mount_case=loads.n_mount_case, N_bays=len(bays.bays), m_bay_hw_kg=v["battery_bays.hw_mass_per_bay_kg"],
        c_ret_kg_per_n=v["battery_bays.c_ret_kg_per_kn"] / 1e3, A_cut=bays.cutout_area_m2,
        k_cut=v["battery_bays.k_cut"],
        k_th_kg_per_w=v["powertrain.thermal.k_th_kg_per_kw"] / 1e3, Q_pack_w=v["powertrain.pack.heat_kw"] * 1e3,
        cooling=pt.thermal.cooling, ambient_isa_delta_c=pt.thermal.ambient_isa_delta_c, harness=runs,
        lv_system_kg=v["powertrain.lv_system_kg"], equipment=items,
    )
    if pt.thermal.ambient_isa_delta_c == 30.0:
        notes.append("thermal sizing ambient ISA+30 °C (D4 default)")
    notes.append("electric: W_l = MTOW (no fuel burn); no wing-fuel bending relief")
    return ctx, notes


# --- evaluator -----------------------------------------------------------------------------
@dataclass
class UncertainInput:
    name: str
    ppf: Callable[[FloatArr], FloatArr]
    assumption: bool = False
    decision_relevant: bool = False


def _empirical_ppf(samples: FloatArr) -> Callable[[FloatArr], FloatArr]:
    s = np.sort(np.asarray(samples, dtype=float))
    return lambda u: np.asarray(np.quantile(s, np.clip(u, 0.0, 1.0)), dtype=float)


@dataclass
class Evaluator:
    cfg: HS1Config
    posterior: Posterior
    params: dict[str, Param]
    calibrated: list[str]
    cert_level: str
    notes: list[str]

    @classmethod
    def create(cls, cfg: HS1Config, posterior: Posterior, cert_level: str | None = None) -> Evaluator:
        params, notes = config_params(cfg)
        probe = ModelContext(is_electric=True)
        calibrated = [c for c in component_set(probe) if c in K_GROUP
                      and REG_CALIBRATED(c, method_for(c, probe, cfg.methods))]
        return cls(cfg, posterior, params, calibrated, CERT_GROUP[cert_level or cfg.cert_group_level], notes)

    def material(self) -> dict[str, str]:
        mbc = {k: v.value for k, v in self.cfg.material_by_component.items()}
        out = {}
        for c in self.calibrated:
            key = {"main_gear": "gear", "nose_gear": "gear"}.get(c, c)
            for k in (c, key, "tail" if c in ("htail", "vtail") else ""):
                if k and k in mbc:
                    out[c] = mbc[k]
                    break
            else:
                if c in ("wing", "htail", "vtail", "fuselage", "main_gear", "nose_gear"):
                    raise MissingInputError(f"material_by_component.{key}", "calibration group g1")
        return out

    def k_samples(self, n: int, rng: np.random.Generator) -> tuple[dict[str, FloatArr], FloatArr]:
        k, sm, _ = k_draws(self.posterior, self.calibrated, self.material(), self.cert_level, "post2005", None,
                           "electric", n, rng)
        return k, sm

    def sample(self, n: int, rng: np.random.Generator) -> dict[str, FloatArr]:
        """Joint MC sample of every input (k draws keep their posterior correlation)."""
        u = rng.random((n, len(self.params)))
        v = {p: prm.ppf(u[:, i]) for i, (p, prm) in enumerate(self.params.items())}
        k, sm = self.k_samples(n, rng)
        v.update({f"k:{c}": kc for c, kc in k.items()})
        v["model_error"] = rng.normal(0.0, 1.0, n) * sm
        return v

    def inputs(self, n_ref: int, rng: np.random.Generator) -> list[UncertainInput]:
        """Independent marginals for Sobol (posterior k marginals; correlation dropped, doc 12 SC-19)."""
        ins = [UncertainInput(p, prm.ppf, prm.assumption, p in DECISION_RELEVANT)
               for p, prm in self.params.items() if prm.uncertain]
        ref = self.sample(n_ref, rng)
        ins += [UncertainInput(f"k:{c}", _empirical_ppf(ref[f"k:{c}"])) for c in self.calibrated]
        ins.append(UncertainInput("model_error", _empirical_ppf(ref["model_error"])))
        return ins

    def fixed_values(self, n: int) -> dict[str, FloatArr]:
        return {p: np.full(n, prm.point()) for p, prm in self.params.items()}

    def evaluate(self, v: Mapping[str, FloatArr], loads: Loads, w_dg: Any,
                 ) -> tuple[dict[str, FloatArr], FloatArr, dict[str, ComponentResult], list[str]]:
        """Return (component masses, W_E, raw component results, notes). Calibrated: mass = k · f."""
        ctx, notes = build_context(self.cfg, loads, v, w_dg)
        tree = evaluate_tree(ctx, self.cfg.methods)
        n = len(next(iter(v.values())))
        masses = {}
        for c, r in tree.items():
            m = np.broadcast_to(np.asarray(r.mass_kg, dtype=float), (n,)).copy()
            if r.calibrated:
                m = m * v[f"k:{c}"]
            masses[c] = m
        cal = sum((masses[c] for c, r in tree.items() if r.calibrated), start=np.zeros(n))
        uncal = sum((masses[c] for c, r in tree.items() if not r.calibrated), start=np.zeros(n))
        # sigma_model is fitted on the calibrated (k·f) part; bottom-up terms carry only parameter
        # uncertainty (doc 06 §6). See doc 12 SC-19.
        w_e = uncal + cal * np.exp(v["model_error"])
        return masses, w_e, tree, notes


def calibrated_sum(masses: Mapping[str, FloatArr], tree: Mapping[str, ComponentResult]) -> FloatArr:
    return np.asarray(sum(masses[c] for c, r in tree.items() if r.calibrated), dtype=float)


def REG_CALIBRATED(component: str, method: str) -> bool:
    """Whether a method's output is multiplied by a technology factor (physics methods are; lists are not)."""
    return method not in ("equipment_list", "bottom_up", "vendor")


def is_electric_powertrain(p: str) -> bool:
    return p in ELECTRIC_LIKE
