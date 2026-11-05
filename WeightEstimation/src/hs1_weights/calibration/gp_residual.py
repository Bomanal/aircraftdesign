"""GP residual model on log W_E (doc 07 §6, P1/R10). Requires scikit-learn (``pip install .[gp]``).

Target r_j = log W_E,obs,j − log(posterior-mean prediction). Kernel: ARD Matérn-5/2 + white noise.
The fitted model is stored as plain JSON (standardisation, training set, kernel hyperparameters) so artefacts stay
deterministic; prediction rebuilds the GP with the stored hyperparameters and no optimiser.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import numpy.typing as npt

from .fleet import MATERIALS, POWERTRAIN_LEVELS

FloatArr = npt.NDArray[np.float64]
FEATURES = (["log_wdg", "log_S", "A", "log_qD", "n_ult", "seats"] + [f"pt_{p}" for p in POWERTRAIN_LEVELS]
            + [f"mat_{m}" for m in MATERIALS] + ["year"])


def feature_vector(w_dg: float, S: float, A: float, q_D: float, n_ult: float, seats: int, powertrain: str,
                   material: str, year: float) -> FloatArr:
    return np.array([math.log(w_dg), math.log(S), A, math.log(q_D), n_ult, float(seats)]
                    + [1.0 if powertrain == p else 0.0 for p in POWERTRAIN_LEVELS]
                    + [1.0 if material == m else 0.0 for m in MATERIALS] + [float(year)])


def _kernel(d: int) -> Any:
    from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
    return ConstantKernel(1e-2, (1e-5, 1.0)) * Matern(np.ones(d), (1e-2, 1e3), nu=2.5) + WhiteKernel(1e-3, (1e-6, 1e-1))


def fit_gp(X: FloatArr, r: FloatArr, seed: int = 0) -> dict[str, Any]:
    from sklearn.gaussian_process import GaussianProcessRegressor
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    Z = (X - mu) / sd
    gp = GaussianProcessRegressor(_kernel(X.shape[1]), normalize_y=False, n_restarts_optimizer=2,
                                  random_state=seed).fit(Z, r)
    return {"features": FEATURES, "x_mean": mu.tolist(), "x_sd": sd.tolist(), "Z": Z.tolist(), "y": r.tolist(),
            "theta": gp.kernel_.theta.tolist(), "log_marginal_likelihood": float(gp.log_marginal_likelihood_value_)}


def gp_predict(model: Mapping[str, Any], X: FloatArr) -> tuple[FloatArr, FloatArr]:
    """Mean and sd of the log-residual at X (rows = feature vectors)."""
    from sklearn.gaussian_process import GaussianProcessRegressor
    Z_train, y = np.asarray(model["Z"]), np.asarray(model["y"])
    k = _kernel(Z_train.shape[1]).clone_with_theta(np.asarray(model["theta"]))
    gp = GaussianProcessRegressor(k, optimizer=None, normalize_y=False).fit(Z_train, y)
    Z = (np.atleast_2d(X) - np.asarray(model["x_mean"])) / np.asarray(model["x_sd"])
    m, s = gp.predict(Z, return_std=True)
    return np.asarray(m, dtype=float), np.asarray(s, dtype=float)
