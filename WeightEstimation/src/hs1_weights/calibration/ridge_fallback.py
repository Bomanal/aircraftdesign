"""MAP with priors via scipy L-BFGS, plus a Laplace approximation for uncertainty (doc 07 §5 fallback).

No JAX dependency; used in CI and wherever numpyro is unavailable. Deterministic: the same structure gives
the same MAP and covariance exactly; Laplace draws depend only on ``seed``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy.optimize import minimize

from .structure import U_MAX, Structure, log_posterior

FloatArr = npt.NDArray[np.float64]


@dataclass
class MapResult:
    x: FloatArr
    cov: FloatArr
    names: list[str]
    log_post: float
    success: bool
    message: str
    n_iter: int
    hessian_min_eig: float


def _x0(S: Structure) -> FloatArr:
    return np.concatenate([S.mu, [np.log(0.05)], np.full(len(S.comp_groups), np.log(0.10)),
                           np.zeros(len(S.latent_idx))])


def neg_log_post(S: Structure) -> Any:
    def f(x: FloatArr) -> float:
        return -float(log_posterior(np, S, x))
    return f


def fd_hessian(f: Any, x: FloatArr, h: float = 1e-4) -> FloatArr:
    """Central-difference Hessian."""
    n = len(x)
    H = np.zeros((n, n))
    f0 = f(x)
    E = np.eye(n) * h
    fp = np.array([f(x + E[i]) for i in range(n)])
    fm = np.array([f(x - E[i]) for i in range(n)])
    for i in range(n):
        H[i, i] = (fp[i] - 2 * f0 + fm[i]) / h ** 2
        for j in range(i + 1, n):
            v = (f(x + E[i] + E[j]) - fp[i] - fp[j] + 2 * f0 - fm[i] - fm[j] + f(x - E[i] - E[j])) / (2 * h ** 2)
            H[i, j] = H[j, i] = v
    return H


def _jax_derivatives(S: Structure) -> tuple[Any, Any] | None:
    try:
        import jax
        import jax.numpy as jnp
    except ImportError:
        return None
    jax.config.update("jax_enable_x64", True)

    def nlp(x: Any) -> Any:
        return -log_posterior(jnp, S, x)
    g = jax.jit(jax.grad(nlp))
    h = jax.jit(jax.hessian(nlp))
    return (lambda x: np.asarray(g(x), dtype=float)), (lambda x: np.asarray(h(x), dtype=float))


def fit_map(S: Structure, use_jax: bool | None = None) -> MapResult:
    """L-BFGS MAP. Gradients/Hessian come from JAX when installed (use_jax=None), else finite differences."""
    f = neg_log_post(S)
    derivs = _jax_derivatives(S) if use_jax in (None, True) else None
    if use_jax is True and derivs is None:
        raise ImportError("use_jax=True but jax is not installed")
    jac = derivs[0] if derivs else None
    res = minimize(f, _x0(S), jac=jac, method="L-BFGS-B",
                   options={"maxiter": 20000, "maxfun": 200000, "ftol": 1e-13, "gtol": 1e-9})
    H = derivs[1](res.x) if derivs else fd_hessian(f, res.x)
    H = 0.5 * (H + H.T)
    w, V = np.linalg.eigh(H)
    min_eig = float(w.min())
    w = np.maximum(w, 1e-8)
    cov = (V / w) @ V.T
    return MapResult(res.x, cov, S.param_names(), -float(res.fun), bool(res.success), str(res.message),
                     int(res.nit), min_eig)


def laplace_draws(S: Structure, r: MapResult, n: int, seed: int) -> dict[str, FloatArr]:
    """Draws in natural space, keyed by parameter name (effects, sigma_model, sigma_comp_<g>, u_<id>)."""
    rng = np.random.default_rng(seed)
    z = rng.multivariate_normal(r.x, r.cov, size=n, method="eigh")
    E, G = len(S.effects), len(S.comp_groups)
    out = {name: z[:, i] for i, name in enumerate(S.effects)}
    out["sigma_model"] = np.exp(z[:, E])
    for k, g in enumerate(S.comp_groups):
        out[f"sigma_comp_{g}"] = np.exp(z[:, E + 1 + k])
    for k, i in enumerate(S.latent_idx):
        out[f"u_{S.fm.ids[i]}"] = U_MAX / (1.0 + np.exp(-z[:, E + 1 + G + k]))
    return out


def map_point(S: Structure, r: MapResult) -> dict[str, float]:
    d = laplace_draws(S, MapResult(r.x, np.zeros_like(r.cov), r.names, r.log_post, r.success, r.message,
                                   r.n_iter, r.hessian_min_eig), 1, 0)
    return {k: float(v[0]) for k, v in d.items()}
