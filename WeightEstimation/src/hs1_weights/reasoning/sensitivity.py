"""Sobol variance attribution by Saltelli sampling (doc 09 §3, R12).

First-order: Saltelli (2010) estimator S_i = E[f_B (f_ABi − f_A)] / V, with f centred on its mean.
Total:       Jansen (1999) estimator  ST_i = E[(f_A − f_ABi)²] / (2V).
Inputs are independent marginals (posterior k correlations dropped: doc 12 SC-19).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

from ..calibration.posterior import FloatArr
from ..mc import UncertainInput


def sobol(f: Callable[[Mapping[str, FloatArr]], Mapping[str, FloatArr]], inputs: Sequence[UncertainInput],
          base: Mapping[str, FloatArr], n: int, seed: int, outputs: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Return {output: {"S1": {name: v}, "ST": {name: v}, "var": V}}.

    ``f`` maps a dict of input arrays (length m) to output arrays; ``base`` supplies fixed values for every
    non-varied input (arrays of length >= 1, broadcast).
    """
    rng = np.random.default_rng(seed)
    d = len(inputs)
    A, B = rng.random((n, d)), rng.random((n, d))
    blocks = [A, B] + [np.where(np.arange(d)[None, :] == i, B, A) for i in range(d)]
    U = np.vstack(blocks)                                    # (n(d+2), d)
    m = U.shape[0]
    vals = {k: np.broadcast_to(np.asarray(v, dtype=float)[:1], (m,)).copy() for k, v in base.items()}
    for i, inp in enumerate(inputs):
        vals[inp.name] = inp.ppf(U[:, i])
    out = f(vals)
    res: dict[str, dict[str, Any]] = {}
    for o in outputs:
        y = np.asarray(out[o], dtype=float)
        y = y - float(np.mean(y[:2 * n]))      # centring: same expectation, lower estimator variance
        fA, fB = y[:n], y[n:2 * n]
        V = float(np.var(np.concatenate([fA, fB])))
        S1, ST = {}, {}
        for i, inp in enumerate(inputs):
            fAB = y[(2 + i) * n:(3 + i) * n]
            S1[inp.name] = float(np.mean(fB * (fAB - fA)) / V) if V > 0 else 0.0
            ST[inp.name] = float(0.5 * np.mean((fA - fAB) ** 2) / V) if V > 0 else 0.0
        res[o] = {"S1": S1, "ST": ST, "var": V}
    return res


def top(indices: Mapping[str, float], k: int = 10, always: Sequence[str] = ()) -> dict[str, float]:
    """Top-k contributors plus the decision-relevant ones regardless of rank."""
    ranked = sorted(indices.items(), key=lambda kv: -kv[1])
    keep = dict(ranked[:k])
    for a in always:
        if a in indices:
            keep[a] = indices[a]
    return keep
