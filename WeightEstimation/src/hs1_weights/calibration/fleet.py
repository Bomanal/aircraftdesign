"""Apply L1 to each fleet aircraft with its own geometry and loads, and build calibration design arrays.

Doc 03 §5.4: fleet aircraft use **their own** certification basis (N6 limits), not HS1's envelope.
Doc 07 §3.3: censored records have latent W_dg = MTOW·(1+u); f is pre-tabulated on a grid of u (U_GRID) and
interpolated linearly inside the likelihood.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from ..components import K_GROUP, ModelContext, evaluate_tree
from ..components.tree import component_set
from ..errors import MissingInputError

U_GRID = np.linspace(0.0, 0.20, 11)
GEAR_LIMIT_DEFAULT = 3.0           # doc 06 §3.5: default gear limit load factor (ultimate = 1.5 x)
SPEED_OF_SOUND_SL = 340.294        # m/s, ISA sea level
FUEL_DENSITY = {"avgas": 0.72, "jet_a": 0.80, "mogas": 0.74}

MATERIALS = ("composite_carbon", "composite_glass", "composite_mixed", "metal_aluminium", "steel_tube_fabric", "wood")
COMPOSITES = ("composite_carbon", "composite_glass", "composite_mixed")
CERT_LEVEL = {
    "far23_normal": "far23_normal", "far23_commuter": "far23_normal", "part23_amdt64": "far23_normal",
    "far23_utility": "far23_utility", "far23_aerobatic": "far23_aerobatic", "cs_vla": "cs_vla",
    "cs_lsa": "astm_f2245", "astm_f2245_lsa": "astm_f2245", "eu_ultralight": "eu_ul", "experimental": "experimental",
}
CERT_LEVELS = ("far23_normal", "far23_utility", "far23_aerobatic", "cs_vla", "astm_f2245", "eu_ul", "experimental")
ERAS = ("pre1980", "1980_2005", "post2005")
POWERTRAIN_GROUP = {"piston_avgas": "piston", "piston_rotax": "piston", "piston_jet_a": "diesel",
                    "turboprop": "turboprop", "battery_electric": "electric", "hybrid_series": "electric",
                    "hybrid_parallel": "electric"}
POWERTRAIN_LEVELS = ("piston", "diesel", "turboprop", "electric")
ELECTRIC_LIKE = ("battery_electric", "hybrid_series", "hybrid_parallel")

# Published breakdown names -> tree components summed for the component-level likelihood (doc 07 §3.2)
BREAKDOWN_MAP: dict[str, tuple[str, ...]] = {
    "wing": ("wing",), "fuselage": ("fuselage",), "htail": ("htail",), "vtail": ("vtail",),
    "tail": ("htail", "vtail"), "empennage": ("htail", "vtail"), "gear": ("main_gear", "nose_gear"),
    "main_gear": ("main_gear",), "nose_gear": ("nose_gear",), "engine_installed": ("engine_installed",),
    "fuel_system": ("fuel_system",), "flight_controls": ("flight_controls",),
    "avionics_installed": ("avionics_installed",), "furnishings": ("furnishings",),
}
MATERIAL_KEY = {"wing": "wing", "htail": "htail", "vtail": "vtail", "fuselage": "fuselage", "main_gear": "gear",
                "nose_gear": "gear"}


def era_of(year: Any) -> str | None:
    if year is None or (isinstance(year, float) and math.isnan(year)):
        return None
    y = int(year)
    return "pre1980" if y < 1980 else "1980_2005" if y <= 2005 else "post2005"


def _g(row: Mapping[str, Any], k: str) -> Any:
    v = row.get(k)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v


def component_material(row: Mapping[str, Any], comp: str) -> str:
    mbc = json.loads(row.get("material_by_component") or "{}")
    key = MATERIAL_KEY.get(comp, comp)
    for k in (key, comp, "tail" if comp in ("htail", "vtail") else ""):
        if k and k in mbc:
            return str(mbc[k])
    return str(row["material_primary"])


def fleet_context(row: Mapping[str, Any], W_dg: float) -> tuple[ModelContext, list[str]]:
    """ModelContext for one normalized fleet row at design weight W_dg (kg)."""
    notes: list[str] = []
    electric = row["powertrain"] in ELECTRIC_LIKE
    n_pos, n_neg = _g(row, "n_limit_pos"), _g(row, "n_limit_neg")
    if n_pos is None:
        raise MissingInputError("n_limit_pos", f"fleet {row['id']}")
    n_lim = max(n_pos, abs(n_neg) if n_neg is not None else 0.0)
    W_l = _g(row, "landing_weight")
    if W_l is None:
        W_l = W_dg
        notes.append("W_l = W_dg (no landing design weight)")
    elif _g(row, "design_weight_kg") is None:
        W_l = W_dg          # censored: landing design weight follows latent W_dg
    fus_L_struct = _g(row, "fuselage_structural_length")
    if fus_L_struct is None and _g(row, "fuselage_length") is not None:
        fus_L_struct = _g(row, "fuselage_length")
        notes.append("Raymer L = fuselage length (structural length not published)")
    fuel = _g(row, "fuel_capacity_usable")
    dens = FUEL_DENSITY.get(str(row.get("fuel_type") or ""))
    W_fw = fuel * dens if (fuel is not None and dens is not None) else None
    if not electric:
        notes.append("W_fw = usable fuel mass (all fuel assumed in wing; W_fw^0.0035 => <2% effect)")
    vc = _g(row, "v_c")
    n_en = int(row.get("n_engines") or 1)
    w_en = _g(row, "engine_dry_mass")
    ctx = ModelContext(
        W_dg=W_dg, W_l=W_l, n_ult=1.5 * n_lim, n_ult_case=f"fleet own basis: 1.5 x {n_lim:.2f}",
        N_l=1.5 * GEAR_LIMIT_DEFAULT, N_l_case="gear limit 3.0 (doc 06 §3.5 default)",
        q=_g(row, "q_c"), q_D=_g(row, "q_d"), V_C=vc, V_D=_g(row, "v_d"), V_H=_g(row, "v_h"),
        mach=None if vc is None else vc / SPEED_OF_SOUND_SL,
        S_w=_g(row, "wing_area"), b=_g(row, "span"), sweep_qc=_g(row, "sweep_qc"), taper=_g(row, "taper"),
        tc_root=_g(row, "root_tc"), W_fw=W_fw,
        S_ht=_g(row, "htail_area"), A_ht=_g(row, "htail_aspect_ratio"), sweep_ht=_g(row, "htail_sweep_qc"),
        taper_ht=_g(row, "htail_taper"), tc_ht=_g(row, "htail_tc"), S_vt=_g(row, "vtail_area"),
        A_vt=_g(row, "vtail_aspect_ratio"), sweep_vt=_g(row, "vtail_sweep_qc"), taper_vt=_g(row, "vtail_taper"),
        tc_vt=_g(row, "vtail_tc"), t_tail=bool(row.get("t_tail")), L_t=_g(row, "tail_arm"),
        fus_L=_g(row, "fuselage_length"), fus_L_struct=fus_L_struct, fus_W=_g(row, "fuselage_max_width"),
        fus_H=_g(row, "fuselage_max_height"), S_f=_g(row, "fuselage_wetted_area"),
        high_wing=bool(row.get("high_wing")), gear_type=str(row.get("gear") or "fixed_tricycle"),
        L_m=_g(row, "main_gear_length"), L_n=_g(row, "nose_gear_length"), is_electric=False,
        W_en=None if w_en is None else w_en / n_en, N_en=n_en, V_t=_g(row, "fuel_capacity_total"),
        V_i=_g(row, "fuel_integral_volume"), N_t=_g(row, "fuel_tanks"), W_uav=_g(row, "avionics_uninstalled_mass"),
        N_p=int(row.get("seats") or 0), has_ecs=bool(row.get("pressurized")),
    )
    if w_en is not None:
        notes.append("engine_dry_mass is the total over engines; W_en = total / n_engines")
    return ctx, notes


def fleet_components(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Calibrated components evaluated for a fleet record. Electric fleet: ICE propulsion terms are dropped."""
    electric = row["powertrain"] in ELECTRIC_LIKE
    probe = ModelContext(is_electric=False)
    comps = [c for c in component_set(probe) if c in K_GROUP]
    if electric:
        comps = [c for c in comps if c not in ("engine_installed", "fuel_system", "electrical_lv")]
    return tuple(comps)


def fleet_uncalibrated(row: Mapping[str, Any]) -> tuple[float, list[str]]:
    """U_j: uncalibrated terms. Electric fleet: published pack mass + motor mass (others absorbed, flagged)."""
    if row["powertrain"] not in ELECTRIC_LIKE:
        return 0.0, []
    pack, motor = _g(row, "battery_mass_installed"), _g(row, "engine_dry_mass")
    if pack is None:
        raise MissingInputError("battery_mass_installed", f"fleet {row['id']} (electric U_j)")
    if motor is None:
        raise MissingInputError("engine_dry_mass", f"fleet {row['id']} (electric U_j: motor mass)")
    return float(pack + motor), ["electric fleet U_j = pack + motor; inverter, thermal, harness, LV absorbed by "
                                 "the powertrain group effect and sigma_model"]


@dataclass
class Breakdown:
    rec: int
    comps: tuple[int, ...]           # column indices into FleetMatrix.comps
    log_obs: float
    sigma_meas: float
    k_group: str
    name: str


@dataclass
class FleetMatrix:
    ids: list[str]
    comps: tuple[str, ...]
    F: npt.NDArray[np.float64]            # [n, C, n_u] kg (0 where absent)
    present: npt.NDArray[np.bool_]        # [n, C]
    U: npt.NDArray[np.float64]            # [n]
    log_obs: npt.NDArray[np.float64]      # [n]
    sigma_meas: npt.NDArray[np.float64]   # [n]
    aircraft_ok: npt.NDArray[np.bool_]    # [n] evaluable for the aircraft-level likelihood
    latent: npt.NDArray[np.bool_]         # [n] censored, latent W_dg
    mtow: npt.NDArray[np.float64]
    material: list[dict[str, str]]        # per record: comp -> material
    cert_level: list[str]
    era: list[str | None]
    manufacturer: list[str]
    powertrain: list[str]
    breakdowns: list[Breakdown]
    raw: pd.DataFrame
    excluded: dict[str, str] = field(default_factory=dict)
    notes: dict[str, list[str]] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.ids)

    def subset(self, keep: npt.NDArray[np.bool_]) -> FleetMatrix:
        idx = np.flatnonzero(keep)
        remap = {int(o): i for i, o in enumerate(idx)}
        return FleetMatrix(
            [self.ids[i] for i in idx], self.comps, self.F[idx], self.present[idx], self.U[idx], self.log_obs[idx],
            self.sigma_meas[idx], self.aircraft_ok[idx], self.latent[idx], self.mtow[idx],
            [self.material[i] for i in idx], [self.cert_level[i] for i in idx], [self.era[i] for i in idx],
            [self.manufacturer[i] for i in idx], [self.powertrain[i] for i in idx],
            [Breakdown(remap[b.rec], b.comps, b.log_obs, b.sigma_meas, b.k_group, b.name)
             for b in self.breakdowns if b.rec in remap],
            self.raw.iloc[idx].reset_index(drop=True), self.excluded, self.notes)


TIER_SIGMA = {"A": 0.02, "B": 0.04}


def build_fleet_matrix(rows: pd.DataFrame, components: pd.DataFrame | None = None,
                       methods: Mapping[str, str] | None = None) -> FleetMatrix:
    """Evaluate L1 for every calibration-eligible row (on the u grid where W_dg is latent)."""
    methods = dict(methods or {})
    comps_all = tuple(c for c in component_set(ModelContext()) if c in K_GROUP)
    cidx = {c: i for i, c in enumerate(comps_all)}
    elig = rows[rows["calibration_eligible"]].sort_values("id").reset_index(drop=True)
    recs: list[dict[str, Any]] = []
    excluded: dict[str, str] = {}
    notes: dict[str, list[str]] = {}
    bd_rows = components if components is not None else pd.DataFrame(columns=["id", "component", "mass_kg", "tier"])
    for _, s in elig.iterrows():
        row = s.to_dict()
        rid = row["id"]
        latent = bool(row.get("design_weight_latent"))
        mtow = float(row["mtow"])
        wdgs = [mtow * (1.0 + u) for u in U_GRID] if latent else [float(row.get("design_weight_kg") or mtow)] * 1
        comps = fleet_components(row)
        F = np.zeros((len(comps_all), len(U_GRID)))
        present = np.zeros(len(comps_all), dtype=bool)
        aircraft_ok, reason = True, ""
        per_comp_ok = dict.fromkeys(comps, True)
        rnotes: list[str] = []
        for ui, wdg in enumerate(wdgs):
            try:
                ctx, rnotes = fleet_context(row, wdg)
            except MissingInputError as e:
                aircraft_ok, reason = False, str(e)
                per_comp_ok = dict.fromkeys(comps, False)
                break
            for c in comps:
                if not per_comp_ok[c]:
                    continue
                try:
                    r = evaluate_tree(ctx, methods, only=(c,))[c]
                except MissingInputError as e:
                    per_comp_ok[c] = False
                    if aircraft_ok:
                        aircraft_ok, reason = False, str(e)
                    continue
                F[cidx[c], ui] = float(np.asarray(r.mass_kg))
                present[cidx[c]] = True
        if not latent:
            F[:, :] = F[:, :1]
        try:
            U, unotes = fleet_uncalibrated(row)
        except MissingInputError as e:
            U, unotes = 0.0, []
            if aircraft_ok:
                aircraft_ok, reason = False, str(e)
        if not aircraft_ok:
            excluded[rid] = f"aircraft-level likelihood: {reason}"
        notes[rid] = rnotes + unotes
        mats = {c: component_material(row, c) for c in comps}
        bds = []
        for _, b in bd_rows[bd_rows["id"] == rid].iterrows():
            tgt = BREAKDOWN_MAP.get(str(b["component"]))
            if tgt is None or not all(per_comp_ok.get(t, False) for t in tgt):
                notes[rid].append(f"breakdown {b['component']!r} not usable (unmapped or inputs missing)")
                continue
            sig = TIER_SIGMA.get(str(b["tier"]))
            if sig is None:
                continue
            bds.append((tuple(cidx[t] for t in tgt), math.log(float(b["mass_kg"])), sig, K_GROUP[tgt[0]],
                        str(b["component"])))
        recs.append(dict(id=rid, F=F, present=present, U=U, log_obs=math.log(float(row["empty_weight"])),
                         sigma=float(row["sigma_rel"]), ok=aircraft_ok, latent=latent, mtow=mtow, mats=mats,
                         cert=CERT_LEVEL[str(row.get("cert_used") or row["cert"])], era=era_of(row.get("year")),
                         manu=str(row.get("manufacturer") or ""),
                         pt=POWERTRAIN_GROUP[str(row["powertrain"])], bds=bds))
    n = len(recs)
    breakdowns = [Breakdown(i, *b) for i, r in enumerate(recs) for b in r["bds"]]
    return FleetMatrix(
        ids=[r["id"] for r in recs], comps=comps_all,
        F=np.array([r["F"] for r in recs]).reshape(n, len(comps_all), len(U_GRID)),
        present=np.array([r["present"] for r in recs], dtype=bool).reshape(n, len(comps_all)),
        U=np.array([r["U"] for r in recs], dtype=float), log_obs=np.array([r["log_obs"] for r in recs], dtype=float),
        sigma_meas=np.array([r["sigma"] for r in recs], dtype=float),
        aircraft_ok=np.array([r["ok"] for r in recs], dtype=bool), latent=np.array([r["latent"] for r in recs],
                                                                                   dtype=bool),
        mtow=np.array([r["mtow"] for r in recs], dtype=float), material=[r["mats"] for r in recs],
        cert_level=[r["cert"] for r in recs], era=[r["era"] for r in recs], manufacturer=[r["manu"] for r in recs],
        powertrain=[r["pt"] for r in recs], breakdowns=breakdowns, raw=elig, excluded=excluded, notes=notes)
