"""Build the versioned calibration dataset (P1-4, doc 05 N12 freeze).

Outputs in ``out_dir``:
  normalized.parquet         one row per variant (SI), sorted by id
  components.parquet         published component breakdowns (doc 07 §3.2)
  normalization_log.jsonl    every routine's log entries, in pipeline order
  dataset_manifest.json      content hash and summary (eligible counts, N10 flags)

The content hash is sha256 over a canonical JSON of the rows, so it does not depend on the parquet writer.
Rebuilding from the same records and code gives byte-identical files.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .. import __version__
from ..schema import AircraftRecord, Source
from .io import load_records, load_sources, provenance_errors, source_errors
from .normalize import LogEntry, NormContext, NormRecord, run_pipeline

# Columns of normalized.parquet, in order. Field values are SI.
VALUE_FIELDS = (
    "mtow", "empty_weight", "landing_weight", "structural_design_weight", "wing_area", "span", "aspect_ratio",
    "taper", "sweep_qc", "root_tc", "tip_tc", "mac", "fuselage_length", "fuselage_max_width", "fuselage_max_height",
    "fuselage_wetted_area", "fuselage_structural_length", "htail_area", "vtail_area", "htail_aspect_ratio",
    "htail_taper", "htail_tc", "htail_sweep_qc", "vtail_aspect_ratio", "vtail_taper", "vtail_tc", "vtail_sweep_qc",
    "tail_arm", "main_gear_length", "nose_gear_length",
    "n_limit_pos", "n_limit_neg", "v_ne", "v_no", "v_c", "v_d", "v_h", "v_s0", "v_s1", "q_c", "q_d",
    "rated_power_total",
    "engine_dry_mass", "fuel_capacity_total", "fuel_capacity_usable", "fuel_integral_volume", "oil_capacity",
    "battery_energy_total", "battery_mass_installed", "pack_wh_per_kg", "avionics_uninstalled_mass", "v_cruise",
)


@dataclass(frozen=True)
class DatasetBuild:
    rows: pd.DataFrame
    components: pd.DataFrame
    content_hash: str
    manifest: dict[str, Any]
    logs: list[LogEntry]


def _row(r: NormRecord) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": r.id, "manufacturer": r.manufacturer, "type_name": r.type_name, "variant": r.variant,
        "tcds_model": r.tcds_model, "cluster": r.cluster,
        "year": r.year, "cert": r.cert.value, "cert_used": r.cert_used,
        "seats": r.seats, "status": r.status, "powertrain": r.powertrain.value, "n_engines": r.n_engines,
        "material_primary": r.material_primary.value,
        "material_by_component": json.dumps({k: v.value for k, v in sorted(r.material_by_component.items())}),
        "gear": r.gear.value, "high_wing": r.high_wing, "pressurized": r.pressurized, "t_tail": r.t_tail,
        "ballistic_parachute": r.ballistic_parachute, "ew_def": r.ew_def.value, "fuel_tanks": r.fuel_tanks,
        "sigma_rel": r.sigma_rel, "mtow_censored": r.mtow_censored, "cap_label": r.cap_label,
        "design_weight_kg": r.design_weight_kg, "design_weight_latent": r.design_weight_latent,
        "outlier": r.outlier, "outlier_z": r.outlier_z, "outlier_confirmed": r.outlier_confirmed,
        "duplicate_of": r.duplicate_of, "calibration_eligible": r.calibration_eligible,
        "ineligible_reasons": "; ".join(r.ineligible_reasons), "warnings": json.dumps(r.warnings),
        "imputed_fields": json.dumps(r.imputed),
    }
    prov = {}
    for k in VALUE_FIELDS:
        nv = r.fields.get(k)
        row[k] = None if nv is None else float(nv.value)
        if nv is not None:
            prov[k] = {"sources": list(nv.sources), "confidence": nv.confidence, "derived_from": list(nv.derived_from),
                       "method": nv.method, "sd": nv.sd, "sd_scale": nv.sd_scale}
    row["provenance"] = json.dumps(prov, sort_keys=True)
    return row


def _component_rows(r: NormRecord, ctx: NormContext) -> list[dict[str, Any]]:
    out = []
    for name, nv, mat in r.components:
        out.append({"id": r.id, "component": name, "mass_kg": float(nv.value),
                    "source": nv.sources[0], "tier": ctx.tier(nv.sources[0]).value,
                    "material": mat.value if mat is not None else r.material_by_component.get(
                        name, r.material_primary).value,
                    "calibration_eligible": r.calibration_eligible})
    return out


def canonical_hash(rows: list[dict[str, Any]], comps: list[dict[str, Any]]) -> str:
    blob = json.dumps({"rows": rows, "components": comps}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _imputation_summary(normed: list[NormRecord]) -> dict[str, Any]:
    by_method: dict[str, int] = {}
    for r in normed:
        for f in r.imputed:
            m = "NI.design_prior" if f == "fuel_tanks" else r.fields[f].method
            by_method[m] = by_method.get(m, 0) + 1
    elig = [r for r in normed if r.calibration_eligible]
    return {"n_records_imputed": sum(bool(r.imputed) for r in normed), "by_method": dict(sorted(by_method.items())),
            "eligible_imputed": {r.id: list(r.imputed) for r in elig if r.imputed}}


def build_from_records(records: list[AircraftRecord], sources: dict[str, Source],
                       confirmed_outliers: frozenset[str] = frozenset(), impute: bool = False,
                       design_priors: bool = False, extra_manifest: dict[str, Any] | None = None) -> DatasetBuild:
    errs = source_errors(sources)
    for rec in records:
        errs += [(f"{rec.id}.{p}", m) for p, m in provenance_errors(rec, sources)]
    if errs:
        from ..errors import RecordValidationError
        raise RecordValidationError("dataset", errs)
    ctx = NormContext(sources, confirmed_outliers, impute=impute, design_priors=design_priors and impute)
    normed, logs = run_pipeline(records, ctx)
    rows = [_row(r) for r in normed]
    comps = [c for r in normed for c in _component_rows(r, ctx)]
    h = canonical_hash(rows, comps)
    df = pd.DataFrame(rows, columns=list(rows[0]) if rows else ["id"])
    cdf = pd.DataFrame(comps, columns=["id", "component", "mass_kg", "source", "tier", "material",
                                       "calibration_eligible"])
    manifest = {
        "content_hash": h, "code_version": __version__, "n_records": len(rows),
        "n_eligible": int(sum(r["calibration_eligible"] for r in rows)),
        "n_with_components": len({c["id"] for c in comps}),
        "n_electric": int(sum(r["powertrain"] == "battery_electric" for r in rows)),
        "n10_flagged": sorted(r["id"] for r in rows if r["outlier"]),
        "ineligible": {r["id"]: r["ineligible_reasons"] for r in rows if not r["calibration_eligible"]},
        "imputation": {"enabled": impute, "design_priors": bool(design_priors and impute),
                       **(_imputation_summary(normed) if impute else {})},
        "eligible_ids": sorted(r["id"] for r in rows if r["calibration_eligible"]),
        **(extra_manifest or {}),
    }
    return DatasetBuild(df, cdf, h, manifest, logs)


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    table = pa.Table.from_pandas(df, preserve_index=False)
    table = table.replace_schema_metadata(None)    # drop pandas metadata for byte stability
    pq.write_table(table, path, compression="snappy", write_statistics=False)


def build_dataset(records_dir: str | Path, sources_csv: str | Path, out_dir: str | Path,
                  confirmed_outliers: frozenset[str] = frozenset(), fleet_csv: str | Path | None = None,
                  fleet_accessed: str | None = None, impute: bool | None = None,
                  design_priors: bool = True) -> DatasetBuild:
    """Records from ``records_dir`` plus (optionally) the clustered fleet CSV. NI imputation defaults to on when a
    fleet CSV is given (it is what the clusters are for) and off otherwise."""
    sources = load_sources(sources_csv)
    records = load_records(records_dir, sources)
    extra: dict[str, Any] = {}
    if fleet_csv is not None:
        from .fleet_csv import load_cluster_summary, load_fleet_csv
        frecs, fsrc, rep = load_fleet_csv(fleet_csv, fleet_accessed)
        clash = sorted(set(sources) & set(fsrc)) + sorted({r.id for r in records} & {r.id for r in frecs})
        if clash:
            from ..errors import RecordValidationError
            raise RecordValidationError(str(fleet_csv), [("id", f"ids already in records/sources: {clash}")])
        records, sources = records + frecs, {**sources, **fsrc}
        summary = load_cluster_summary(Path(fleet_csv).parent / "cluster_summary.csv")
        for cid, c in rep.clusters.items():
            if cid in summary:
                c["deficiencies"] = summary[cid].get("deficiencies", "")
        extra["fleet_csv"] = {"path": str(fleet_csv), **rep.to_dict()}
    if impute is None:
        impute = fleet_csv is not None
    b = build_from_records(records, sources, confirmed_outliers, impute, design_priors, extra)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_parquet(b.rows, out / "normalized.parquet")
    _write_parquet(b.components, out / "components.parquet")
    with open(out / "normalization_log.jsonl", "w", encoding="utf-8") as f:
        for e in b.logs:
            f.write(json.dumps(e.to_dict(), sort_keys=True, default=str) + "\n")
    with open(out / "dataset_manifest.json", "w", encoding="utf-8") as f:
        json.dump(b.manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    return b


def load_dataset(out_dir: str | Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    out = Path(out_dir)
    with open(out / "dataset_manifest.json", encoding="utf-8") as f:
        manifest = json.load(f)
    return pd.read_parquet(out / "normalized.parquet"), pd.read_parquet(out / "components.parquet"), manifest


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Build normalized fleet dataset (doc 05 N12)")
    ap.add_argument("--records", default="data/records")
    ap.add_argument("--sources", default="data/sources.csv")
    ap.add_argument("--out", default="build")
    ap.add_argument("--fleet-csv", default=None)
    a = ap.parse_args()
    b = build_dataset(a.records, a.sources, a.out, fleet_csv=a.fleet_csv)
    print(json.dumps({k: v for k, v in b.manifest.items() if k != "ineligible"}, indent=2))


if __name__ == "__main__":
    main()
