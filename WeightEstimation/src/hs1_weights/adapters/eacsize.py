"""Build an HS1Config from a WPPestimation/eacsize run, and use eacsize's mission model in the MTOW closure.

eacsize sizes the aircraft (MTOM, wing, fuselage, tails, power, pack energy) at first order. hs1_weights needs
more than eacsize knows: DEP motor split, quick-swap bays, equipment list, materials, CG stations, tail shape,
sweep, lift-curve slope and the bottom-up placeholder priors. Those come from an **overlay** JSON (partial
HS1Config). Every value taken from eacsize is listed in ``HS1Config.provenance`` and therefore in the report's
``assumptions``; an overlay value for an eacsize-owned field wins and is logged as an override.

The overlay is normally built from an **Aircraft structure config** selection (``structure_config.build_overlay``,
docs/14). Its stations and lengths are relative (``{"x_frac", "z_m"}``, ``{"frac", "of"}``) and are resolved here
against the fuselage length and span of the configuration being built, so they follow eacsize re-sizing in full
coupling. Hand-written overlays with absolute values still work.

Mapping (eacsize run -> HS1Config):

=============================================  ==========================================================
eacsize                                        HS1Config
=============================================  ==========================================================
results.summary.mtom_kg                        mass_guess_kg (design weight for loads)
results.summary.wing_area_m2 / span_m          wing.area_m2 / wing.span_m
design.geometry.taper / t_c / mac              wing.taper / wing.tc_root, tc_tip / wing.cbar_m
design.geometry.S_ht / S_vt / tail_arm         htail.area_m2 / vtail.area_m2 / tail_arm_m
design.geometry.fus_len / fus_width /          fuselage.length_m / width_m / height_m / wetted_area_m2
  fus_height / S_wet_fus
assumptions.cl_max_clean / cl_max_land         cl_max_clean / cl_max_land (eacsize: unblown)
mission.v_cruise_kmh                           vc_kt (V_C taken as the design cruise speed; rule C4 checks
                                               it against V_C,min)
performance.vh_kcas                            vh_kt
assumptions.main_gear_len_m / nose_gear_len_m  gear strut lengths; inputs.gear -> gear.type
summary.battery_kwh, assumptions.pack_wh_kg    pack.energy_kwh; pack.specific_energy (level = "pack")
summary.installed_power_kw                     motors[].p_cont_kw = power_share x installed power
assumptions.eta_motor*eta_inverter*eta_wiring  motors[].eta_chain when the overlay gives none
inputs.seats / occupant_kg, summary.payload_kg seats / occupant_kg / payload_kg
=============================================  ==========================================================
"""
from __future__ import annotations

import copy
import json
import math
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from ..config import HS1Config, _strip_comments
from ..structure_config import resolve_relative

# hs1_weights component -> eacsize mass_kg keys it corresponds to (for the side-by-side comparison)
COMPARE = {
    "wing": ("wing",), "htail": ("htail",), "vtail": ("vtail",), "fuselage": ("fuselage",),
    "main_gear": ("main_gear",), "nose_gear": ("nose_gear",), "flight_controls": ("flight_controls",),
    "avionics_installed": ("avionics",), "electrical_lv": ("electrical_lv",), "furnishings": ("furnishings_seats",),
    "motors+inverters": ("e_powertrain",), "propellers": ("propeller",), "battery_pack": ("battery_pack",),
}
GEAR = {"fixed": "fixed_tricycle", "retractable": "retractable"}
GRID_POINTS = 64        # energy_fn: above this many distinct masses, interpolate eacsize on a mass grid


def load_run(path: str | Path, run: str | None = None) -> dict[str, Any]:
    """Load one run from eacsize ``runs.json`` (a list; select by id or exact label) or a single-run export."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        return data
    if run is None:
        if len(data) != 1:
            raise ValueError(f"{path} holds {len(data)} runs; pass run=<id or label>")
        return dict(data[0])
    hits = [r for r in data if r.get("id") == run or r.get("label") == run]
    if not hits:
        raise KeyError(f"run {run!r} not found in {path}; ids: {[r.get('id') for r in data]}")
    if len(hits) > 1:
        raise ValueError(f"label {run!r} is ambiguous ({[h['id'] for h in hits]}); use the run id")
    return dict(hits[-1])


def _deep_merge(base: dict[str, Any], over: dict[str, Any], path: str, overridden: list[str]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        p = f"{path}{k}"
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("stations", "material_by_component"):
            out[k] = _deep_merge(out[k], v, p + ".", overridden)
        else:
            if k in out and out[k] != v:
                overridden.append(p)
            out[k] = v
    return out


def mapped_fields(run: dict[str, Any]) -> dict[str, Any]:
    """The HS1Config fields eacsize can supply, from a run dict."""
    res, A, inp, msn = run["results"], run["assumptions"], run["inputs"], run["mission"]
    s, geo, perf = res["summary"], res["design"]["geometry"], res["performance"]
    return {
        "name": f"eacsize {run.get('id', '')}: {run.get('label', '')}".strip(),
        "mass_guess_kg": s["mtom_kg"],
        "wing": {"area_m2": s["wing_area_m2"], "span_m": s["span_m"], "taper": geo["taper"], "tc_root": geo["t_c"],
                 "tc_tip": geo["t_c"], "cbar_m": geo["mac"]},
        "htail": {"area_m2": geo["S_ht"]}, "vtail": {"area_m2": geo["S_vt"]}, "tail_arm_m": geo["tail_arm"],
        "fuselage": {"length_m": geo["fus_len"], "width_m": geo["fus_width"], "height_m": geo["fus_height"],
                     "wetted_area_m2": geo["S_wet_fus"]},
        "cl_max_clean": A["cl_max_clean"], "cl_max_land": A["cl_max_land"],
        "vc_kt": msn["v_cruise_kmh"] / 1.852, "vh_kt": perf["vh_kcas"],
        "gear": {"type": GEAR.get(inp["gear"], inp["gear"]), "main_strut_length_m": A["main_gear_len_m"],
                 "nose_strut_length_m": A["nose_gear_len_m"]},
        "powertrain": {"pack": {"energy_kwh": s["battery_kwh"], "specific_energy_wh_per_kg": A["pack_wh_kg"],
                                "level": "pack"}},
        "seats": inp["seats"], "occupant_kg": inp["occupant_kg"], "n_occupants": inp["seats"],
        "payload_kg": s["payload_kg"],
    }


def config_from_eacsize(run: dict[str, Any], overlay: dict[str, Any]) -> HS1Config:
    """HS1Config = eacsize-mapped fields + overlay (overlay wins; overrides are logged in ``provenance``)."""
    status = run.get("results", {}).get("status")
    overlay = _strip_comments(copy.deepcopy(overlay))
    base = mapped_fields(run)
    overridden: list[str] = []
    d = _deep_merge(base, overlay, "", overridden)
    # structure config: stations / lengths given relative to this aircraft (follow eacsize re-sizing)
    L_fus, span = float(d["fuselage"]["length_m"]), float(d["wing"]["span_m"])
    d, n_rel = resolve_relative(d, L_fus, span)
    A, P_kw = run["assumptions"], run["results"]["summary"]["installed_power_kw"]
    eta = A["eta_motor"] * A["eta_inverter"] * A["eta_wiring"]
    shares = [m.get("power_share") for m in d["powertrain"].get("motors", [])]
    if any(s is not None for s in shares):
        if any(s is None for s in shares) or not math.isclose(sum(shares), 1.0, abs_tol=1e-6):
            raise ValueError(f"motor power_share values must all be given and sum to 1 (got {shares})")
    for m in d["powertrain"].get("motors", []):
        share = m.pop("power_share", None)
        if share is not None:
            m["p_cont_kw"] = share * P_kw
        m.setdefault("eta_chain", eta)
    prov = [f"input from WPPestimation/eacsize run {run.get('id', '?')} ({run.get('label', '')}), "
            f"status {status!r}, timestamp {run.get('timestamp', '?')}",
            "eacsize pack_wh_kg is pack-level: HS1 pack level = 'pack' (η_c2p = 1); this fixes D3 for this run",
            "V_C = eacsize design cruise speed (mission.v_cruise_kmh); checked against V_C,min by rule C4",
            f"motor P_cont from eacsize installed power {P_kw:.1f} kW x overlay power_share",
            f"motor eta_chain defaults to eacsize eta_motor·eta_inverter·eta_wiring = {eta:.4f} where not set",
            "geometry (S, b, taper, t/c, MAC, fuselage L/W/H and wetted area, S_ht, S_vt, tail arm), C_L,max, "
            "V_H, gear lengths, MTOM, pack energy and payload taken from eacsize"]
    prov += [f"overlay overrides eacsize value: {p}" for p in overridden]
    if n_rel:
        prov.append(f"{n_rel} relative layout values resolved against fuselage length {L_fus:.3f} m and span "
                    f"{span:.3f} m")
    if status != "ok":
        prov.append(f"WARNING: eacsize run status is {status!r}; check its messages before using the result")
    d["provenance"] = prov + list(d.get("provenance", []))
    return HS1Config.model_validate(d)


def _import_eacsize(wpp_dir: str | Path) -> Any:
    wpp = str(Path(wpp_dir).resolve())
    if wpp not in sys.path:
        sys.path.insert(0, wpp)
    import eacsize.model as model
    import eacsize.sizing as sizing
    return model, sizing


def energy_fn_from_eacsize(run: dict[str, Any], wpp_dir: str | Path) -> Callable[[Any], Any]:
    """``energy_required(W_dg) -> Wh`` from eacsize's mission model, for ``closure.close_point`` / ``close_mc``.

    The airframe is frozen at the run's geometry and installed power (eacsize ``geometry_mode = "fixed"``); only
    the mass changes. Returns the nominal pack energy that meets the run's mission target (with its reserve, DoD,
    end-of-life SoH), specific power and C-rate limits: max of the three pack-mass needs x pack_wh_kg.
    For arrays with more than GRID_POINTS distinct masses (Monte Carlo closure) eacsize is evaluated on a
    GRID_POINTS mass grid and interpolated linearly; masses above the heaviest feasible grid point are
    treated as infeasible (infinite energy).
    """
    model, sizing = _import_eacsize(wpp_dir)
    s = run["results"]["summary"]
    inp_d = dict(run["inputs"], mode="fixed_mtom", geometry_mode="fixed", wing_area_m2=s["wing_area_m2"],
                 span_m=s["span_m"], installed_power_kw=s["installed_power_kw"], oew_override_kg=0.0)
    inp = model.coerce(model.DesignInputs, inp_d)
    msn = model.coerce(model.Mission, run["mission"])
    req = model.coerce(model.Requirements, run["requirements"])
    A = model.coerce(model.Assumptions, run["assumptions"])
    cal = sizing.calibration(A)

    def one(w: float) -> float:
        if not math.isfinite(w) or w <= 0:
            return math.inf
        af = sizing.build_airframe(float(w), inp, msn, req, A, cal)
        kg, _, _ = sizing.battery_required(af, inp, msn, A)
        return float(kg * A.pack_wh_kg)

    def energy(w: Any) -> Any:
        if np.ndim(w) == 0:
            return one(float(w))
        w = np.asarray(w, dtype=float)
        uniq, inv = np.unique(np.round(w, 3), return_inverse=True)
        if len(uniq) <= GRID_POINTS:
            return np.array([one(u) for u in uniq])[inv]
        # Monte Carlo: E(W) is smooth and monotone, so evaluate eacsize on a grid and interpolate
        ok = np.isfinite(uniq) & (uniq > 0)
        grid = np.linspace(uniq[ok].min(), uniq[ok].max(), GRID_POINTS)
        eg = np.array([one(g) for g in grid])
        fin = np.isfinite(eg)
        out = np.full(len(uniq), np.inf)
        if fin.any():
            top = grid[fin].max()
            m = ok & (uniq <= top)
            out[m] = np.interp(uniq[m], grid[fin], eg[fin])
        return out[inv]
    return energy


class EacsizeSizer:
    """Full coupling: re-run eacsize's complete sizing for a stored run at any MTOM.

    The run's own settings are kept (geometry mode, power sizing, mission, requirements, assumptions); only the
    sizing mode is forced to ``fixed_mtom`` at the requested mass, because hs1_weights does the mass closure.
    In ``constraint`` geometry mode eacsize re-sizes wing area and span from the W/S limits and power from the
    P/W constraints at that mass; fuselage and tails are regenerated from its geometry model.
    """

    def __init__(self, run: dict[str, Any], wpp_dir: str | Path) -> None:
        self.model, self.sizing = _import_eacsize(wpp_dir)
        self.run = run
        self.base = {k: copy.deepcopy(run[k]) for k in ("inputs", "mission", "requirements", "assumptions")}
        self.base["inputs"].update(mode="fixed_mtom", oew_override_kg=0.0)
        m = self.model
        self.inp = m.coerce(m.DesignInputs, self.base["inputs"])
        self.msn = m.coerce(m.Mission, self.base["mission"])
        self.req = m.coerce(m.Requirements, self.base["requirements"])
        self.A = m.coerce(m.Assumptions, self.base["assumptions"])
        self.cal = self.sizing.calibration(self.A)
        self._cache: dict[float, dict[str, Any]] = {}

    def sized(self, w: float, oew_excl_battery_kg: float = 0.0) -> dict[str, Any]:
        """Run-like record (inputs, mission, ..., results) of eacsize sized at MTOM ``w``."""
        key = (round(float(w), 3), round(float(oew_excl_battery_kg), 3))
        if oew_excl_battery_kg == 0.0 and key[0] in self._cache:
            return self._cache[key[0]]
        d = copy.deepcopy(self.base)
        d["inputs"].update(mtom_kg=float(w), oew_override_kg=float(oew_excl_battery_kg))
        rec = dict(self.sizing.run_from_dict(d), id=self.run.get("id"), label=self.run.get("label"),
                   timestamp=self.run.get("timestamp"))
        if oew_excl_battery_kg == 0.0:
            self._cache[key[0]] = rec
        return rec

    def energy_wh(self, w: float) -> float:
        """Nominal pack energy for the run's mission at MTOM ``w`` on the airframe eacsize sizes at ``w``."""
        if not math.isfinite(w) or w <= 0:
            return math.inf
        af = self.sizing.build_airframe(float(w), self.inp, self.msn, self.req, self.A, self.cal)
        kg, _, _ = self.sizing.battery_required(af, self.inp, self.msn, self.A)
        return float(kg * self.A.pack_wh_kg)

    @property
    def pack_wh_kg(self) -> float:
        return float(self.A.pack_wh_kg)


def full_coupling(run: dict[str, Any], overlay: dict[str, Any], wpp_dir: str | Path,
                  ) -> tuple[Callable[[float], tuple[HS1Config, float]], EacsizeSizer]:
    """``config_at(W) -> (HS1Config at W with pack energy E, E_Wh)`` for ``closure.close_point_coupled``."""
    sizer = EacsizeSizer(run, wpp_dir)
    from ..closure import _with
    mode = run["inputs"]["geometry_mode"]

    def config_at(w: float) -> tuple[HS1Config, float]:
        e = sizer.energy_wh(w)
        rec = sizer.sized(w)
        cfg = config_from_eacsize(rec, overlay)
        s = rec["results"]["summary"]
        prov = [p for p in cfg.provenance if not p.startswith("WARNING: eacsize run status")]
        prov.append(f"full coupling: eacsize re-sized at MTOM {w:.1f} kg (geometry mode {mode!r}): S "
                    f"{s['wing_area_m2']:.2f} m², b {s['span_m']:.2f} m, P {s['installed_power_kw']:.1f} kW; "
                    f"pack energy from the run's mission target at this mass")
        cfg = cfg.model_copy(update={"provenance": prov})
        return (_with(cfg, w, e) if math.isfinite(e) else cfg), e
    return config_at, sizer


SUMMARY_KEYS = ("mtom_kg", "wing_area_m2", "span_m", "wing_loading_kgm2", "installed_power_kw", "battery_kwh",
                "range_cruise_km", "range_cruise_eol_km", "endurance_vmp_h", "vs0_kcas", "takeoff_15m_m",
                "roc_sl_cont_ms")


def summary_of(rec: dict[str, Any]) -> dict[str, Any]:
    r = rec["results"]
    return {"status": r["status"], **{k: r["summary"].get(k) for k in SUMMARY_KEYS},
            "fus_len_m": r["design"]["geometry"].get("fus_len"), "messages": r.get("messages", []),
            "checks": [{k: c.get(k) for k in ("name", "value", "limit", "unit", "severity", "note")}
                       for c in r.get("checks", [])]}


def mission_target(run: dict[str, Any]) -> dict[str, Any]:
    """The mission the closure sizes the pack for (eacsize target fields, used even for fixed-MTOM runs)."""
    i = run["inputs"]
    rng = i["target"] == "range"
    note = ("eacsize ran this case at fixed MTOM (pack = remaining mass, target fields greyed out in its UI); the "
            "closure sizes the pack to these target fields instead") if i["mode"] == "fixed_mtom" else ""
    return {"target": i["target"], "value": i["target_range_km"] if rng else i["target_endurance_h"],
            "unit": "km" if rng else "h", "at_end_of_life": bool(i["size_at_eol"]),
            "reserve_min": run["mission"]["reserve_min"], "v_cruise_kmh": run["mission"]["v_cruise_kmh"],
            "eacsize_mode": i["mode"], "note": note}


def comparison(run: dict[str, Any], report: Any) -> list[dict[str, Any]]:
    """Rows of eacsize mass vs hs1_weights P10/P50/P90 for comparable components."""
    em = run["results"]["mass_kg"]
    rows = []
    for comp, keys in COMPARE.items():
        parts = comp.split("+")
        if not all(p in report.components for p in parts):
            continue
        p = {q: sum(getattr(report.components[c].mass_kg, q) for c in parts) for q in ("p10", "p50", "p90")}
        rows.append({"component": comp, "eacsize_kg": sum(em.get(k, 0.0) for k in keys), **p})
    s = run["results"]["summary"]
    rows.append({"component": "EMPTY WEIGHT (incl. pack)", "eacsize_kg": s["oew_excl_battery_kg"] + s["battery_kg"],
                 "p10": report.empty_weight.p10, "p50": report.empty_weight.p50, "p90": report.empty_weight.p90})
    return rows
