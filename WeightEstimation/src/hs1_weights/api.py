"""Public API: ``predict(cfg) -> WeightReport`` (doc 02 §5, R7, P5-1).

The numeric path is deterministic given ``seed``. No LLM output enters it (doc 09 §4).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from . import __version__
from . import certification as cert
from .calibration.fit import CalibrationResult, load_artefact
from .calibration.posterior import FloatArr, Posterior
from .components import REGISTRY
from .config import Bay, HS1Config, Station
from .mc import Evaluator, Loads, calibrated_sum, loads_at, stall_cl
from .reasoning import rules as R


class Dist(BaseModel):
    mean: float
    sd: float
    p10: float
    p50: float
    p90: float

    @classmethod
    def of(cls, a: Any) -> Dist:
        a = np.asarray(a, dtype=float)
        return cls(mean=float(a.mean()), sd=float(a.std()), p10=float(np.percentile(a, 10)),
                   p50=float(np.percentile(a, 50)), p90=float(np.percentile(a, 90)))


class ComponentReport(BaseModel):
    mass_kg: Dist
    method: str
    governing_load_case: str
    calibrated: bool
    k: Dist | None = None
    transcription_verified: bool = True
    assumptions: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class AnalogComparison(BaseModel):
    id: str
    type_name: str
    distance: float
    tier_c: bool
    empty_weight_kg: float | None
    mtow_kg: float | None
    delta_by_component: dict[str, float]
    delta_by_driver: dict[str, float]
    explanation: str


class RuleFlag(BaseModel):
    id: str
    severity: str
    message: str
    values: dict[str, Any] = Field(default_factory=dict)


class WeightReport(BaseModel):
    components: dict[str, ComponentReport]
    empty_weight: Dist
    mtow: Dist | None
    cg: dict[str, Any] | None
    envelope: dict[str, Any]
    stall_area_min_m2: dict[str, Any]
    analogs: list[AnalogComparison]
    rule_flags: list[RuleFlag]
    sobol: dict[str, float] | None
    sobol_detail: dict[str, Any] | None = None
    n_bays_sensitivity: dict[str, Any] | None = None
    cert_group_sensitivity: dict[str, Any] | None = None
    calibration: dict[str, Any]
    assumptions: list[str]
    unverified_criteria: list[str]
    versions: dict[str, Any]


# --- helpers -----------------------------------------------------------------------------------
def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return Dist.of(x).model_dump() if x.size > 1 else float(x.reshape(-1)[0])
    if isinstance(x, (np.floating, np.integer)):
        return float(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,
                              cwd=Path(__file__).parent).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _registry_hash() -> str:
    return hashlib.sha256(json.dumps(REGISTRY.describe(), sort_keys=True).encode()).hexdigest()[:12]


def _resolve_calibration(cal: CalibrationResult | str | Path | None) -> CalibrationResult | None:
    if cal is None or isinstance(cal, CalibrationResult):
        return cal
    return load_artefact(cal)


def _dedup(xs: list[str]) -> list[str]:
    return list(dict.fromkeys(xs))


# --- predict -----------------------------------------------------------------------------------
def predict(cfg: HS1Config, n_samples: int = 2000, seed: int = 0,
            calibration: CalibrationResult | str | Path | None = None,
            fleet: tuple[pd.DataFrame, pd.DataFrame | None] | None = None, explain: bool = True,
            n_sobol: int = 256) -> WeightReport:
    """Predict component masses, W_E, MTOW (= W_E + payload at the W_dg guess), CG and intervals.

    calibration: a CalibrationResult or artefact path. Without one, technology factors are drawn from their
    priors (doc 07 §3.4) and the report says so. fleet: (normalized rows, component breakdowns) for analogs and
    rules C1/C2/C7.
    """
    if n_samples < 2000:
        n_note = [f"n_samples = {n_samples} < 2000 (R8 minimum) - exploratory run"]
    else:
        n_note = []
    cal = _resolve_calibration(calibration)
    post = cal.posterior if cal else Posterior.from_priors()
    assumptions = [f"input provenance: {p}" for p in cfg.provenance] + n_note
    if cal is None:
        assumptions.append("no calibration artefact: technology factors k drawn from their priors (doc 07 §3.4); "
                           "intervals are prior-predictive")
    rng = np.random.default_rng(seed)
    ev = Evaluator.create(cfg, post)
    assumptions += ev.notes
    w_dg = cfg.mass_guess_kg
    loads = loads_at(cfg, w_dg)
    assumptions += loads.notes
    v = ev.sample(n_samples, rng)
    masses, w_e, tree, notes = ev.evaluate(v, loads, w_dg)
    assumptions += notes
    for r in tree.values():
        assumptions += [f"{r.component_id}: {a}" for a in r.assumptions]

    gp_note = _apply_gp(cal, cfg, w_e, calibrated_sum(masses, tree) * np.exp(v["model_error"]), loads, rng)
    if gp_note:
        assumptions.append(gp_note)

    comps = {}
    for c, r in tree.items():
        k = Dist.of(v[f"k:{c}"]) if r.calibrated else None
        comps[c] = ComponentReport(mass_kg=Dist.of(masses[c]), method=r.method,
                                   governing_load_case=r.governing_load_case, calibrated=r.calibrated, k=k,
                                   transcription_verified=r.transcription_verified, assumptions=r.assumptions,
                                   extra=_jsonable(r.extra))
    mtow = None if cfg.payload_kg is None else w_e + cfg.payload_kg
    if mtow is not None:
        assumptions.append("MTOW = W_E + payload at the W_dg guess (battery inside W_E, no fuel); "
                           "not closed - use hs1_weights.closure for the closure loop")
    cl_c, cl_l, _ = stall_cl(cfg)
    m_ref = np.asarray(mtow if mtow is not None else np.full(n_samples, w_dg))
    stall = {"basis_mass": "predicted MTOW" if mtow is not None else "mass_guess_kg",
             "VS1_59kt_clean": {q: cert.min_area_for_stall(float(np.percentile(m_ref, p)), 59.0, cl_c)
                                for q, p in (("p50", 50), ("p90", 90))},
             "VS0_61kt_landing": {q: cert.min_area_for_stall(float(np.percentile(m_ref, p)), 61.0, cl_l)
                                  for q, p in (("p50", 50), ("p90", 90))},
             "cl_max_clean_unblown": cl_c, "cl_max_land_unblown": cl_l, "current_area_m2": cfg.wing.area_m2}

    cg = None
    if cfg.stations is not None:
        from .cg import compute_cg
        cg = compute_cg(cfg, masses, tree)
    else:
        assumptions.append("CG not computed: no component stations given")

    sobol_top = sobol_detail = nbays = cgs = None
    analogs: list[AnalogComparison] = []
    flags: list[R.Flag] = []
    lev_info: dict[str, Any] = {}
    if explain:
        sobol_top, sobol_detail = _sobol(ev, loads, w_dg, cfg, n_sobol, seed)
        nbays = _n_bays_sweep(cfg, post, v, loads, w_dg)
        cgs = _cert_group_sensitivity(cfg, post, v, loads, w_dg, Dist.of(w_e).p50)
        if fleet is not None:
            analogs, lev_info = _analogs(cfg, post, loads, masses, fleet[0])
    flags = _rules(cfg, loads, w_e, mtow, masses, tree, fleet, sobol_detail, ev, lev_info)

    versions = {"code": __version__, "git": _git_commit(), "method_registry": _registry_hash(),
                "dataset": cal.metadata.get("dataset_hash") if cal else None,
                "calibration_artefact": cal.artefact_hash if cal else None,
                "calibration_path": str(cal.path) if cal and cal.path else None, "seed": seed,
                "n_samples": n_samples}
    calinfo = ({"engine": cal.posterior.engine, "collapse_struct": cal.metadata.get("collapse_struct"),
                "identifiability": cal.metadata.get("identifiability"), "n_records": cal.metadata.get("n_records")}
               if cal else {"engine": "prior", "note": "no fleet calibration yet (Phase 1 data pending)"})
    return WeightReport(
        components=comps, empty_weight=Dist.of(w_e), mtow=None if mtow is None else Dist.of(mtow), cg=cg,
        envelope=_jsonable({"design_weight_kg": w_dg, "n_ult_design": loads.n_ult, "n_ult_case": loads.n_ult_case,
                            "n_mount": loads.n_mount, "n_mount_case": loads.n_mount_case, "q_cruise_Pa": loads.q,
                            **loads.envelope, "per_basis": loads.per_basis}),
        stall_area_min_m2=stall, analogs=analogs, rule_flags=[RuleFlag(**f.__dict__) for f in flags],
        sobol=sobol_top, sobol_detail=sobol_detail, n_bays_sensitivity=nbays, cert_group_sensitivity=cgs,
        calibration=calinfo, assumptions=_dedup(assumptions), unverified_criteria=cert.unverified_criteria(),
        versions=versions)


def _apply_gp(cal: CalibrationResult | None, cfg: HS1Config, w_e: FloatArr, cal_part: FloatArr, loads: Loads,
              rng: np.random.Generator) -> str:
    """GP mean/variance on the log of the calibrated part (the quantity it was trained on). Modifies w_e."""
    if cal is None or not cal.gp:
        return ""
    if not (cfg.gp_out_of_class or cal.gp.get("guard_passed")):
        return "GP residual not applied out of class (doc 07 §6 guard; set gp_out_of_class to override)"
    from .calibration.gp_residual import feature_vector, gp_predict
    mat = cfg.material_by_component.get("wing")
    x = feature_vector(cfg.mass_guess_kg, cfg.wing.area_m2, cfg.wing.span_m ** 2 / cfg.wing.area_m2,
                       float(loads.q_D), float(loads.n_ult), cfg.seats, "electric", mat.value if mat else "",
                       cfg.year)
    m, s = gp_predict(cal.gp, x[None, :])
    w_e += cal_part * (np.exp(m[0] + s[0] * rng.normal(0.0, 1.0, len(w_e))) - 1.0)
    return f"GP residual applied: mean {m[0]:+.3f}, sd {s[0]:.3f} on log W_E"


def _sobol(ev: Evaluator, loads: Loads, w_dg: float, cfg: HS1Config, n: int, seed: int,
           ) -> tuple[dict[str, float], dict[str, Any]]:
    from .reasoning.sensitivity import sobol, top
    rng = np.random.default_rng(seed + 1)
    ins = ev.inputs(4000, rng)
    payload = cfg.payload_kg or 0.0

    def f(vals: Any) -> dict[str, FloatArr]:
        _, w, _, _ = ev.evaluate(vals, loads, w_dg)
        return {"W_E": w, "MTOW": w + payload}
    res = sobol(f, ins, ev.fixed_values(1), n, seed + 2, ["W_E", "MTOW"])
    always = ["powertrain.pack.eta_c2p", "powertrain.pack.specific_energy_wh_per_kg"]
    detail: dict[str, Any] = {o: {"S1": res[o]["S1"], "ST": res[o]["ST"], "var": res[o]["var"],
                  "top_ST": top(res[o]["ST"], 10, always)} for o in res}
    detail["assumption_inputs"] = sorted(i.name for i in ins if i.assumption)
    detail["n_base"] = n
    detail["note"] = "independent marginals; posterior k correlations dropped (doc 12 SC-19)"
    return top(res["W_E"]["ST"], 10, always), detail


def _n_bays_sweep(cfg: HS1Config, post: Posterior, v: dict[str, FloatArr], loads: Loads, w_dg: float,
                  ) -> dict[str, Any]:
    """Doc 06 §5.3: W_E and bay mass vs N_bays at constant energy (equal split at the mean bay station)."""
    bays = cfg.battery_bays.bays
    xs = float(np.mean([b.station.x_m for b in bays]))
    zs = float(np.mean([b.station.z_m for b in bays]))
    out = {}
    for nb in range(1, max(4, len(bays) + 2) + 1):
        new = [Bay(station=Station(x_m=xs, z_m=zs), energy_fraction=1.0 / nb) for _ in range(nb)]
        c2 = cfg.model_copy(update={"battery_bays": cfg.battery_bays.model_copy(update={"bays": new})})
        ev2 = Evaluator.create(c2, post)
        m, w, _, _ = ev2.evaluate(v, loads, w_dg)
        out[str(nb)] = {"W_E_p50": float(np.median(w)), "battery_bays_p50": float(np.median(m["battery_bays"]))}
    return {"by_n_bays": out, "current": len(bays), "note": "constant energy; same Monte Carlo samples"}


def _cert_group_sensitivity(cfg: HS1Config, post: Posterior, v: dict[str, FloatArr], loads: Loads, w_dg: float,
                            base_p50: float) -> dict[str, Any]:
    """D6: report W_E under the alternative structural cert-group level."""
    other = "FAR23_L1" if cfg.cert_group_level == "ASTM_F2245" else "ASTM_F2245"
    ev2 = Evaluator.create(cfg, post, other)
    rng = np.random.default_rng(12345)
    k, sm = ev2.k_samples(len(v["model_error"]), rng)
    v2 = dict(v, **{f"k:{c}": kc for c, kc in k.items()})
    _, w, _, _ = ev2.evaluate(v2, loads, w_dg)
    return {"used": cfg.cert_group_level, "W_E_p50": base_p50, "alternative": other,
            "alternative_W_E_p50": float(np.median(w))}


def _analogs(cfg: HS1Config, post: Posterior, loads: Loads, masses: dict[str, FloatArr], rows: pd.DataFrame,
             ) -> tuple[list[AnalogComparison], dict[str, Any]]:
    from .reasoning import analogs as A
    mat = cfg.material_by_component.get("wing")
    x = A.features(cfg.mass_guess_kg, cfg.wing.area_m2, cfg.wing.span_m ** 2 / cfg.wing.area_m2, float(loads.q_D),
                   float(loads.n_ult), cfg.seats, "electric", mat.value if mat else "", cfg.gear.type.value, cfg.year)
    found, info = A.retrieve(x, rows)
    hs1 = {c: float(np.median(m)) for c, m in masses.items()}
    hs1_inputs = {"n_ult": float(loads.n_ult), "W_dg": cfg.mass_guess_kg, "S_w": cfg.wing.area_m2,
                  "A": cfg.wing.span_m ** 2 / cfg.wing.area_m2, "q": loads.q}
    out = []
    for row, d, tier_c in found:
        try:
            am = A.analog_masses(row, post)
            delta, msg = A.explain(hs1, am, str(row["type_name"]))
            drv = A.driver_attribution(row, hs1_inputs)
        except Exception as e:     # noqa: BLE001 - explanation only; never blocks the numeric path
            delta, msg, drv = {}, f"{row['type_name']}: L1 not evaluable at analog inputs ({e})", {}
        out.append(AnalogComparison(id=str(row["id"]), type_name=str(row["type_name"]), distance=d, tier_c=tier_c,
                                    empty_weight_kg=_f(row.get("empty_weight")), mtow_kg=_f(row.get("mtow")),
                                    delta_by_component=delta, delta_by_driver=drv,
                                    explanation=("[tier C, analog only] " if tier_c else "") + msg))
    return out, info


def _f(x: Any) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


def _rules(cfg: HS1Config, loads: Loads, w_e: FloatArr, mtow: FloatArr | None, masses: dict[str, FloatArr],
           tree: dict[str, Any], fleet: tuple[pd.DataFrame, pd.DataFrame | None] | None,
           sobol_detail: dict[str, Any] | None, ev: Evaluator, lev: dict[str, Any]) -> list[R.Flag]:
    rows = fleet[0] if fleet else None
    comps = fleet[1] if fleet else None
    we50 = float(np.median(w_e))
    m50 = float(np.median(mtow)) if mtow is not None else cfg.mass_guess_kg
    cl_c, cl_l, _ = stall_cl(cfg)
    mat = cfg.material_by_component.get("fuselage") or cfg.material_by_component.get("wing")
    fr = {c: float(np.median(masses[c])) / we50 for c, r in tree.items() if r.calibrated}
    env = loads.envelope
    governing = _governing_criteria(loads)
    pack = tree["battery_pack"]
    flags = [
        R.c1_analog_cell(we50 / m50, rows, cfg.seats, mat.value if mat else ""),
        R.c2_component_fractions(fr, comps, rows),
        R.c3_stall(m50, cfg.wing.area_m2, cl_c, cl_l),
        R.c4_vc_min(env["all_checks"]),
        R.c5_pack(float(np.median(np.asarray(pack.extra["pack_wh_per_kg"]))),
                  ev.params["powertrain.pack.specific_energy_wh_per_kg"].point(), cfg.powertrain.pack.level),
        R.c6_assumption_variance(sobol_detail["W_E"]["ST"] if sobol_detail else None,
                                 set(sobol_detail["assumption_inputs"]) if sobol_detail else set()),
        R.c7_leverage(lev),
        R.c8_unverified(governing),
        R.c9_bays(float(np.median(masses["battery_bays"])), ev.params["powertrain.pack.energy_kwh"].point(),
                  cfg.battery_bays.mass_per_kwh_band),
    ]
    if cfg.cl_max_includes_blowing:
        flags.append(R.Flag("STALL-BLOWN", "warn", "C_L,max input includes DEP blowing; unblown values used for "
                            "certification stall limits (doc 03 §5.1)"))
    return flags


def _governing_criteria(loads: Loads) -> dict[str, list[str]]:
    """Criterion names (as in certification.unverified_criteria) behind each governing load quantity."""
    env = loads.envelope
    by_basis = {r["basis"]: r for r in loads.per_basis}
    out: dict[str, list[str]] = {}
    b = env["n_ult_pos"]["governing"]
    gust = by_basis[b]["pos_governed_by"] == "gust"
    out["n_ult_pos"] = [f"{b}.safety_factor"] + ([f"{b}.ude_vc", f"{b}.ude_vd"] if gust else [f"{b}.n_pos_fixed"])
    b = env["n_ult_neg"]["governing"]
    r = by_basis[b]
    neg_gust = min(r["n_gust_VC"][1], r["n_gust_VD"][1]) < r["n_man_neg"]
    out["n_ult_neg"] = [f"{b}.safety_factor"] + ([f"{b}.ude_vc", f"{b}.ude_vd"] if neg_gust
                                                  else [f"{b}.n_neg_fixed", f"{b}.n_neg_ratio"])
    fwd = env["items_of_mass_aft"].get("forward")
    if fwd:
        out["mount_forward"] = [f"{fwd['governing']}.items_of_mass_aft.forward"]
    out["landing_sink"] = []          # legacy formula, not a Criterion (doc 03 §2: F2245 text to confirm)
    return out


def export_schemas(out_dir: str | Path) -> list[Path]:
    """P5-1: write JSON schemas of the I/O models."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, model in (("HS1Config", HS1Config), ("WeightReport", WeightReport)):
        p = out / f"{name}.schema.json"
        p.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
        paths.append(p)
    return paths
