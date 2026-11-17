"""Posterior (or prior) draws of technology factors, and k_i for a given aircraft's groups (doc 07 §7)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from ..components.base import K_GROUP
from .structure import Prior, effect_names_for, prior_table

FloatArr = npt.NDArray[np.float64]


@dataclass
class Posterior:
    """Joint draws by parameter name. Parameters the fit did not touch are drawn from their priors."""
    draws: dict[str, FloatArr]
    priors: dict[str, Prior]
    collapse_struct: bool
    engine: str                              # "prior" | "map_laplace" | "numpyro_nuts"
    manufacturers: list[str] = field(default_factory=list)

    @classmethod
    def from_priors(cls, collapse_struct: bool = False) -> Posterior:
        return cls({}, prior_table(collapse_struct), collapse_struct, "prior")

    @property
    def n_draws(self) -> int:
        return len(next(iter(self.draws.values()))) if self.draws else 0

    def joint(self, names: list[str], n: int, rng: np.random.Generator) -> dict[str, FloatArr]:
        idx = rng.integers(0, self.n_draws, n) if self.n_draws else None
        out: dict[str, FloatArr] = {}
        for name in names:
            if idx is not None and name in self.draws:
                out[name] = self.draws[name][idx]
            else:
                out[name] = self._prior_draw(name, n, rng, out)
        return out

    def _prior_draw(self, name: str, n: int, rng: np.random.Generator, ctx: dict[str, FloatArr]) -> FloatArr:
        if name.startswith("delta_"):
            tau = ctx.get("tau_m")
            if tau is None:
                tau = np.abs(rng.normal(0.0, self.priors["tau_m"].scale, n))
            return rng.normal(0.0, 1.0, n) * tau
        p = self.priors[name]
        if p.kind == "normal":
            return rng.normal(p.loc, p.scale, n)
        return np.abs(rng.normal(0.0, p.scale, n))

    def mean(self, name: str) -> float:
        if name in self.draws:
            return float(np.mean(self.draws[name]))
        p = self.priors[name]
        return p.loc if p.kind == "normal" else p.scale * float(np.sqrt(2.0 / np.pi))


def k_draws(post: Posterior, components: list[str], material: Mapping[str, str], cert_level: str, era: str | None,
            manufacturer: str | None, powertrain: str, n: int, rng: np.random.Generator,
            ) -> tuple[dict[str, FloatArr], FloatArr, dict[str, list[str]]]:
    """k samples for each calibrated component of one aircraft, plus sigma_model samples.

    A manufacturer without a fitted effect is "new": δ is drawn from its hyperprior (tau_m).
    """
    known_manu = manufacturer if manufacturer and manufacturer in post.manufacturers else None
    per_comp: dict[str, list[str]] = {}
    names: list[str] = ["tau_m"]
    for c in components:
        g = K_GROUP[c]
        es = effect_names_for(g, material.get(c, "metal_aluminium"), cert_level, era, known_manu, powertrain,
                              post.collapse_struct)
        if known_manu is None:
            es.append("delta__new_")
        per_comp[c] = es
        names += [e for e in es if e not in names]
    names.append("sigma_model")
    j = post.joint(names, n, rng)
    k = {c: np.exp(sum((j[e] for e in es), start=np.zeros(n))) for c, es in per_comp.items()}
    return k, j["sigma_model"], per_comp


def summarize(post: Posterior) -> dict[str, Any]:
    out = {}
    for k, v in sorted(post.draws.items()):
        out[k] = {"mean": float(np.mean(v)), "sd": float(np.std(v)), "p10": float(np.percentile(v, 10)),
                  "p90": float(np.percentile(v, 90))}
    return out
