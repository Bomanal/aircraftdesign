"""Calibration driver and versioned artefacts (doc 07 §5, P3-3).

Artefact: ``artefacts/calibration_<YYYYMMDD>_<hash12>.nc`` (arviz InferenceData, NetCDF) holding the posterior
draws plus metadata: dataset hash, code version, method registry, prior spec, engine, seed, diagnostics,
identifiability report and (optionally) the GP residual model. Without arviz a ``.json`` artefact with the same
content is written instead (doc 12 SC-17).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import __version__
from ..components import REGISTRY
from .fleet import FleetMatrix, build_fleet_matrix
from .posterior import FloatArr, Posterior, summarize
from .ridge_fallback import fit_map, laplace_draws
from .structure import Structure, build_structure, identifiability, prior_table

THETA_CORR_FLAG = 0.8


@dataclass
class CalibrationResult:
    posterior: Posterior
    diagnostics: dict[str, Any]
    metadata: dict[str, Any]
    structure: Structure | None = None
    gp: dict[str, Any] | None = None
    path: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def artefact_hash(self) -> str:
        return str(self.metadata["artefact_hash"])


def _theta_corr_flags(draws: Mapping[str, FloatArr], offset: FloatArr | None = None) -> list[dict[str, Any]]:
    """Doc 07 §4: flag |rho| > 0.8 pairs of theta. With ``offset`` (beta_ref + gamma_ref draws) the test is on
    log k_i at a reference group level, which removes the common-mode correlation shared offsets induce."""
    names = sorted(k for k in draws if k.startswith("theta_"))
    if len(names) < 2:
        return []
    X = np.vstack([draws[k] for k in names])
    if offset is not None:
        X = X + offset[None, :]
    M = np.atleast_2d(np.corrcoef(X))
    return [{"a": names[i], "b": names[j], "rho": float(M[i, j])}
            for i in range(len(names)) for j in range(i + 1, len(names)) if abs(M[i, j]) > THETA_CORR_FLAG]


def calibrate(rows: pd.DataFrame, components: pd.DataFrame | None, dataset_hash: str, engine: str = "auto",
              seed: int = 0, methods: Mapping[str, str] | None = None, collapse_struct: bool | None = None,
              n_draws: int = 4000, fm: FleetMatrix | None = None, **nuts_kw: Any) -> CalibrationResult:
    """Fit technology factors. engine: "nuts", "map" (Laplace fallback) or "auto" (NUTS if numpyro is installed)."""
    from . import model as nuts
    fm = fm if fm is not None else build_fleet_matrix(rows, components, methods)
    if fm.n == 0 or not (fm.aircraft_ok.any() or fm.breakdowns):
        raise ValueError("no evaluable calibration records (see FleetMatrix.excluded)")
    S = build_structure(fm, collapse_struct)
    if engine == "auto":
        engine = "nuts" if nuts.available() else "map"
    diag: dict[str, Any]
    if engine == "nuts":
        draws, diag = nuts.fit_nuts(S, seed=seed, **nuts_kw)
        eng = "numpyro_nuts"
    elif engine == "map":
        r = fit_map(S)
        draws = laplace_draws(S, r, n_draws, seed)
        diag = {"engine": "map_laplace", "success": r.success, "message": r.message, "n_iter": r.n_iter,
                "log_post": r.log_post, "hessian_min_eig": r.hessian_min_eig,
                "map": {n: float(v) for n, v in zip(r.names, r.x, strict=False)}}
        eng = "map_laplace"
    else:
        raise ValueError(f"unknown engine {engine!r}")
    diag["theta_corr_flags"] = _theta_corr_flags(draws)
    from collections import Counter
    ref_cert = Counter(fm.cert_level).most_common(1)[0][0]
    ref_era = Counter(e for e in fm.era if e).most_common(1)[0][0] if any(fm.era) else None
    off = draws.get(f"beta_{ref_cert}", 0.0) + (draws.get(f"gamma_{ref_era}", 0.0) if ref_era else 0.0)
    deltas = [v for k, v in draws.items() if k.startswith("delta_")]
    if deltas:
        off = off + np.mean(deltas, axis=0)
    diag["k_ref_corr_flags"] = _theta_corr_flags(draws, np.broadcast_to(off, draws["sigma_model"].shape))
    diag["k_ref_level"] = {"cert": ref_cert, "era": ref_era}
    diag["theta_corr_note"] = ("raw theta flags include common-mode correlation from shared beta/gamma offsets; "
                               "k_ref flags test log k_i at the reference cert/era level plus mean manufacturer effect")
    ident = identifiability(fm)
    meta = {
        "dataset_hash": dataset_hash, "code_version": __version__, "engine": eng, "seed": seed,
        "collapse_struct": S.collapse_struct, "identifiability": ident,
        "collapse_note": ("component-level k unidentified (doc 07 §4): collapsed to shared k_struct"
                          if S.collapse_struct else ""),
        "methods": dict(methods or {}), "method_registry": REGISTRY.describe(),
        "prior_spec": {k: v.to_dict() for k, v in prior_table(S.collapse_struct).items()},
        "n_records": fm.n, "n_aircraft_level": int(fm.aircraft_ok.sum()), "n_breakdowns": len(fm.breakdowns),
        "excluded": fm.excluded, "manufacturers": S.manufacturers, "effects": S.effects,
        "summary": None,
    }
    post = Posterior(draws, prior_table(S.collapse_struct), S.collapse_struct, eng, S.manufacturers)
    meta["summary"] = summarize(post)
    meta["artefact_hash"] = _hash(meta)
    return CalibrationResult(post, diag, meta, S)


def _hash(meta: Mapping[str, Any]) -> str:
    keys = ("dataset_hash", "code_version", "engine", "seed", "collapse_struct", "methods", "method_registry",
            "prior_spec")
    blob = json.dumps({k: meta[k] for k in keys}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def write_artefact(res: CalibrationResult, out_dir: str | Path = "artefacts", date: _dt.date | None = None) -> Path:
    date = date or _dt.date.today()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"calibration_{date:%Y%m%d}_{res.artefact_hash[:12]}"
    attrs = {"metadata": json.dumps(res.metadata, sort_keys=True, default=str),
             "diagnostics": json.dumps(res.diagnostics, sort_keys=True, default=str),
             "gp": json.dumps(res.gp, sort_keys=True, default=str) if res.gp else ""}
    try:
        import arviz as az
        post = {k: np.asarray(v)[None, :] for k, v in res.posterior.draws.items()}
        try:
            idata = az.from_dict({"posterior": post})                       # arviz >= 1.0 (DataTree)
        except TypeError:
            idata = az.from_dict(posterior=post)                            # arviz < 1.0 (InferenceData)
        idata["posterior"].attrs.update(attrs)
        path = out / f"{stem}.nc"
        idata.to_netcdf(str(path))
    except ImportError:
        path = out / f"{stem}.json"
        path.write_text(json.dumps({"attrs": attrs, "draws": {k: list(map(float, v))
                                                               for k, v in res.posterior.draws.items()}}))
    res.path = path
    return path


def load_artefact(path: str | Path) -> CalibrationResult:
    path = Path(path)
    if path.suffix == ".nc":
        import arviz as az
        idata = az.from_netcdf(str(path))
        posterior = idata["posterior"]
        attrs = dict(posterior.attrs)
        draws = {str(k): np.asarray(posterior[k].values).reshape(-1) for k in posterior.data_vars}
    else:
        d = json.loads(path.read_text())
        attrs, draws = d["attrs"], {k: np.asarray(v) for k, v in d["draws"].items()}
    meta = json.loads(attrs["metadata"])
    diag = json.loads(attrs["diagnostics"])
    gp = json.loads(attrs["gp"]) if attrs.get("gp") else None
    post = Posterior(draws, prior_table(meta["collapse_struct"]), meta["collapse_struct"], meta["engine"],
                     list(meta.get("manufacturers", [])))
    return CalibrationResult(post, diag, meta, None, gp, path)
