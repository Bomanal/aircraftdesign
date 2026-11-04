"""Hierarchical calibration model structure (doc 07 §2–4), shared by the NUTS and MAP paths.

k_{i,j} = exp(θ_i + α_{i,g1(j)} + β_{g2(j)} + γ_{g3(j)} + δ_{g4(j)} [+ π_{powertrain(j)} for systems])

The log posterior is written against an array module ``xp`` (numpy or jax.numpy) so both inference engines
evaluate exactly the same density.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from ..components.base import STRUCTURAL_K
from .fleet import CERT_LEVELS, COMPOSITES, ERAS, POWERTRAIN_LEVELS, U_GRID, FleetMatrix

SYSTEM_GROUPS = ("flight_controls", "electrical", "avionics", "furnishings", "ecs")
ALL_GROUPS = ("wing", "tail", "fuselage", "gear", "engine", "fuel_system") + SYSTEM_GROUPS
COMPOSITE_PRIOR = {"wing": 0.85, "tail": 0.83, "fuselage": 0.90, "gear": 0.95}   # doc 07 §3.4 (Raymer factors)
U_SD, U_MAX = 0.10, 0.20                 # doc 07 §3.3: u ~ HalfNormal(0.10) truncated at 0.20
TAU_M_SCALE = 0.05
MIN_TYPES_FOR_MANUFACTURER = 3


@dataclass(frozen=True)
class Prior:
    kind: str           # "normal" | "halfnormal"
    loc: float
    scale: float
    basis: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "loc": self.loc, "scale": self.scale, "basis": self.basis}


def theta_name(group: str, collapse: bool) -> str:
    return "theta_struct" if collapse and group in STRUCTURAL_K else f"theta_{group}"


def prior_table(collapse_struct: bool = False) -> dict[str, Prior]:
    """Every parameter the model can use, with its prior. Doc 07 §3.4; gaps recorded as doc 12 SC-7."""
    p: dict[str, Prior] = {}
    for g in ALL_GROUPS:
        p[theta_name(g, collapse_struct)] = Prior("normal", 0.0, 0.15,
                                                  "doc 07 §3.4 (theta_wing); other theta_i: SC-7")
    for g in STRUCTURAL_K:
        for mat in COMPOSITES:
            p[f"alpha_{g}_{mat}"] = Prior("normal", math.log(COMPOSITE_PRIOR[g]), 0.10, "doc 07 §3.4 composite")
        for mat in ("steel_tube_fabric", "wood"):
            p[f"alpha_{g}_{mat}"] = Prior("normal", 0.0, 0.20, "doc 07 §3.4 weakly informative")
    for lvl in CERT_LEVELS:
        p[f"beta_{lvl}"] = Prior("normal", 0.0, 0.10, "doc 07 §3.4")
    for e in ERAS:
        p[f"gamma_{e}"] = Prior("normal", 0.0, 0.10, "doc 07 §3.4")
    for pt in POWERTRAIN_LEVELS:
        p[f"pi_{pt}"] = Prior("normal", 0.0, 0.10, "powertrain group effect on systems: SC-7")
    p["tau_m"] = Prior("halfnormal", 0.0, TAU_M_SCALE, "doc 07 §3.4")
    p["sigma_model"] = Prior("halfnormal", 0.0, 0.08, "doc 07 §3.4")
    for g in ALL_GROUPS:
        p[f"sigma_comp_{g}"] = Prior("halfnormal", 0.0, 0.15, "doc 07 §3.4")
    return p


def effect_names_for(group: str, material: str, cert: str, era: str | None, manufacturer: str | None,
                     powertrain: str, collapse: bool) -> list[str]:
    names = [theta_name(group, collapse)]
    if group in STRUCTURAL_K and material != "metal_aluminium":
        names.append(f"alpha_{group}_{material}")
    names.append(f"beta_{cert}")
    if era is not None:
        names.append(f"gamma_{era}")
    if manufacturer:
        names.append(f"delta_{manufacturer}")
    if group in SYSTEM_GROUPS:
        names.append(f"pi_{powertrain}")
    return names


@dataclass
class Structure:
    fm: FleetMatrix
    collapse_struct: bool
    effects: list[str]                      # additive effects; deltas last
    n_delta: int
    D: npt.NDArray[np.float64]              # [n*C, E]
    mu: npt.NDArray[np.float64]             # [E] prior means (deltas 0)
    sd: npt.NDArray[np.float64]             # [E] prior sds (deltas: TAU_M_SCALE in MAP)
    comp_groups: list[str]                  # sigma_comp groups used by breakdowns
    B: npt.NDArray[np.float64]              # [nb, n*C] breakdown selector
    bd_log_obs: npt.NDArray[np.float64]
    bd_sigma: npt.NDArray[np.float64]
    bd_group_idx: npt.NDArray[np.int64]
    latent_idx: npt.NDArray[np.int64]
    Lmat: npt.NDArray[np.float64]           # [n, L] maps latent u onto records
    air_mask: npt.NDArray[np.float64]       # [n] 1.0 where aircraft-level likelihood applies
    manufacturers: list[str] = field(default_factory=list)

    @property
    def n_params(self) -> int:
        return len(self.effects) + 1 + len(self.comp_groups) + len(self.latent_idx)

    def param_names(self) -> list[str]:
        return (self.effects + ["log_sigma_model"] + [f"log_sigma_comp_{g}" for g in self.comp_groups]
                + [f"logit_u_{self.fm.ids[i]}" for i in self.latent_idx])


def identifiability(fm: FleetMatrix) -> dict[str, Any]:
    """Doc 07 §4: >= 20 breakdown records spanning >= 2 material classes."""
    recs = sorted({b.rec for b in fm.breakdowns})
    mats = {fm.material[r].get("wing", "") for r in recs}
    ok = len(recs) >= 20 and len(mats - {""}) >= 2
    return {"n_breakdown_records": len(recs), "breakdown_materials": sorted(mats - {""}), "component_level_ok": ok}


def build_structure(fm: FleetMatrix, collapse_struct: bool | None = None) -> Structure:
    if collapse_struct is None:
        collapse_struct = not identifiability(fm)["component_level_ok"]
    types = list(fm.raw["type_name"]) if "type_name" in fm.raw else fm.ids
    types_per_manu = Counter(m for m, _ in {(m, t) for m, t in zip(fm.manufacturer, types, strict=False)} if m)
    manus = sorted(m for m, c in types_per_manu.items() if c >= MIN_TYPES_FOR_MANUFACTURER)
    from ..components.base import K_GROUP
    C = len(fm.comps)
    rows_effects: list[list[str]] = []
    for j in range(fm.n):
        for c in fm.comps:
            g = K_GROUP[c]
            manu = fm.manufacturer[j] if fm.manufacturer[j] in manus else None
            rows_effects.append(effect_names_for(g, fm.material[j].get(c, "metal_aluminium"), fm.cert_level[j],
                                                 fm.era[j], manu, fm.powertrain[j], collapse_struct))
    used = sorted({e for es in rows_effects for e in es if not e.startswith("delta_")})
    deltas = [f"delta_{m}" for m in manus]
    effects = used + deltas
    eidx = {e: i for i, e in enumerate(effects)}
    D = np.zeros((fm.n * C, len(effects)))
    for r, es in enumerate(rows_effects):
        for e in es:
            D[r, eidx[e]] = 1.0
    pt = prior_table(collapse_struct)
    mu = np.array([0.0 if e.startswith("delta_") else pt[e].loc for e in effects])
    sd = np.array([TAU_M_SCALE if e.startswith("delta_") else pt[e].scale for e in effects])
    comp_groups = sorted({b.k_group for b in fm.breakdowns})
    gidx = {g: i for i, g in enumerate(comp_groups)}
    B = np.zeros((len(fm.breakdowns), fm.n * C))
    for bi, b in enumerate(fm.breakdowns):
        for ci in b.comps:
            B[bi, b.rec * C + ci] = 1.0
    latent_idx = np.flatnonzero(fm.latent)
    Lmat = np.zeros((fm.n, len(latent_idx)))
    for li, rec in enumerate(latent_idx):
        Lmat[rec, li] = 1.0
    return Structure(fm, collapse_struct, effects, len(deltas), D, mu, sd, comp_groups, B,
                     np.array([b.log_obs for b in fm.breakdowns]), np.array([b.sigma_meas for b in fm.breakdowns]),
                     np.array([gidx[b.k_group] for b in fm.breakdowns], dtype=np.int64), latent_idx, Lmat,
                     fm.aircraft_ok.astype(float), manus)


# --- shared density -------------------------------------------------------------------------
def _normal_lp(xp: Any, x: Any, mu: Any, sd: Any) -> Any:
    return -0.5 * ((x - mu) / sd) ** 2 - xp.log(sd) - 0.5 * math.log(2 * math.pi)


def interp_F(xp: Any, F: Any, u: Any) -> Any:
    """Piecewise-linear interpolation of F [n, C, n_u] at u [n] on U_GRID (hat-function weights)."""
    h = float(U_GRID[1] - U_GRID[0])
    W = xp.maximum(0.0, 1.0 - xp.abs(u[:, None] - xp.asarray(U_GRID)[None, :]) / h)
    return xp.einsum("rcu,ru->rc", F, W)


def predict_parts(xp: Any, S: Structure, eff: Any, u_lat: Any) -> tuple[Any, Any]:
    """Return (k·F [n, C], predicted W_E [n]) for effects ``eff`` and latent u."""
    fm = S.fm
    u = xp.asarray(S.Lmat) @ u_lat if len(S.latent_idx) else xp.zeros(fm.n)
    Fu = interp_F(xp, xp.asarray(fm.F), u) * xp.asarray(fm.present)
    k = xp.exp(xp.asarray(S.D) @ eff).reshape(fm.n, len(fm.comps))
    kF = k * Fu
    return kF, kF.sum(axis=1) + xp.asarray(fm.U)


def log_likelihood(xp: Any, S: Structure, eff: Any, sigma_model: Any, sigma_comp: Any, u_lat: Any) -> Any:
    fm = S.fm
    kF, pred = predict_parts(xp, S, eff, u_lat)
    mask = xp.asarray(S.air_mask)
    safe = xp.where(mask > 0, pred, 1.0)
    s = xp.sqrt(xp.asarray(fm.sigma_meas) ** 2 + sigma_model ** 2)
    ll = xp.sum(mask * _normal_lp(xp, xp.asarray(fm.log_obs), xp.log(safe), s))
    if len(S.bd_log_obs):
        mb = xp.asarray(S.B) @ kF.reshape(-1)
        sb = xp.sqrt(xp.asarray(S.bd_sigma) ** 2 + sigma_comp[xp.asarray(S.bd_group_idx)] ** 2)
        ll = ll + xp.sum(_normal_lp(xp, xp.asarray(S.bd_log_obs), xp.log(mb), sb))
    return ll


def unpack(xp: Any, S: Structure, x: Any) -> tuple[Any, Any, Any, Any, Any]:
    E, G = len(S.effects), len(S.comp_groups)
    eff = x[:E]
    log_sm = x[E]
    log_sc = x[E + 1:E + 1 + G]
    v = x[E + 1 + G:]
    return eff, log_sm, log_sc, v, U_MAX / (1.0 + xp.exp(-v))


def log_posterior(xp: Any, S: Structure, x: Any) -> Any:
    """Unconstrained-space log posterior for MAP (manufacturer τ fixed at its prior scale: doc 12 SC-16)."""
    eff, log_sm, log_sc, v, u = unpack(xp, S, x)
    sm, sc = xp.exp(log_sm), xp.exp(log_sc)
    lp = xp.sum(_normal_lp(xp, eff, xp.asarray(S.mu), xp.asarray(S.sd)))
    lp = lp + (-0.5 * (sm / 0.08) ** 2) + log_sm                       # HalfNormal(0.08) + log-Jacobian
    lp = lp + xp.sum(-0.5 * (sc / 0.15) ** 2 + log_sc)                 # HalfNormal(0.15) + log-Jacobian
    s = 1.0 / (1.0 + xp.exp(-v))
    lp = lp + xp.sum(-0.5 * (u / U_SD) ** 2 + xp.log(U_MAX * s * (1.0 - s)))
    return lp + log_likelihood(xp, S, eff, sm, sc, u)
