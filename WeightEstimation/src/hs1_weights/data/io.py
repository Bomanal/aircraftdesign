"""Load/save fleet records (one JSON per variant) and the sources CSV, with field-path validation (P1-2)."""
from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from dataclasses import asdict, fields
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from ..errors import RecordValidationError
from ..schema import PRODUCTION_STATUSES, AircraftRecord, ComponentMass, Source, Tier, V

_RECORD_ADAPTER: TypeAdapter[AircraftRecord] = TypeAdapter(AircraftRecord)
SOURCE_COLUMNS = ["id", "title", "kind", "tier", "url", "accessed", "sha256", "notes"]


# --- sources -------------------------------------------------------------------
def load_sources(path: str | Path) -> dict[str, Source]:
    out: dict[str, Source] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            sid = (row.get("id") or "").strip()
            if not sid:
                raise RecordValidationError(str(path), [(f"row {i}.id", "empty source id")])
            if sid in out:
                raise RecordValidationError(str(path), [(f"row {i}.id", f"duplicate source id {sid!r}")])
            try:
                tier = Tier(row.get("tier", "").strip())
            except ValueError as e:
                raise RecordValidationError(str(path), [(f"row {i}.tier", f"invalid tier {row.get('tier')!r}")]) from e
            out[sid] = Source(id=sid, title=row.get("title", ""), kind=row.get("kind", ""), tier=tier,
                              url=row.get("url", ""), accessed=row.get("accessed", ""),
                              sha256=row.get("sha256", ""), notes=row.get("notes", ""))
    return out


def save_sources(sources: dict[str, Source], path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SOURCE_COLUMNS, lineterminator="\n")
        w.writeheader()
        for sid in sorted(sources):
            s = sources[sid]
            w.writerow({"id": s.id, "title": s.title, "kind": s.kind, "tier": s.tier.value, "url": s.url,
                        "accessed": s.accessed, "sha256": s.sha256, "notes": s.notes})


# --- records -------------------------------------------------------------------
def _loc(loc: tuple[int | str, ...]) -> str:
    return ".".join(str(p) for p in loc) or "<root>"


def record_from_dict(d: dict[str, Any]) -> AircraftRecord:
    """Validate and build an AircraftRecord. Keys starting with '_' are comments and are dropped."""
    clean = {k: v for k, v in d.items() if not k.startswith("_")}
    rid = str(clean.get("id", "<no id>"))
    try:
        rec = _RECORD_ADAPTER.validate_python(clean, strict=False)
    except ValidationError as e:
        raise RecordValidationError(rid, [(_loc(err["loc"]), err["msg"]) for err in e.errors()]) from e
    errs = []
    if rec.production_status not in PRODUCTION_STATUSES:
        errs.append(("production_status", f"must be one of {PRODUCTION_STATUSES}"))
    if errs:
        raise RecordValidationError(rid, errs)
    return rec


def iter_values(rec: AircraftRecord) -> Iterator[tuple[str, V]]:
    """Yield (field path, V) for every sourced value in a record."""
    for f in fields(rec):
        val = getattr(rec, f.name)
        if isinstance(val, V):
            yield f.name, val
    for i, c in enumerate(rec.components):
        yield f"components.{i}.mass", c.mass
    for name, vs in rec.alternates.items():
        for i, v in enumerate(vs):
            yield f"alternates.{name}.{i}", v
    for cat, v in rec.cert_categories.items():
        yield f"cert_categories.{cat}", v


def provenance_errors(rec: AircraftRecord, sources: dict[str, Source]) -> list[tuple[str, str]]:
    """R1: every numeric field carries a source id that resolves to a registered Source."""
    errs: list[tuple[str, str]] = []
    for path, v in iter_values(rec):
        if not v.source:
            errs.append((f"{path}.source", "no source id"))
        elif v.source not in sources:
            errs.append((f"{path}.source", f"source {v.source!r} not in sources.csv"))
        if not 0.0 <= v.confidence <= 1.0:
            errs.append((f"{path}.confidence", "must be within [0, 1]"))
    for name in rec.alternates:
        if name not in {f.name for f in fields(rec)}:
            errs.append((f"alternates.{name}", "not a record field"))
    return errs


def source_errors(sources: dict[str, Source]) -> list[tuple[str, str]]:
    """Every Source needs a tier, a URL or citation, and an access date (CLAUDE.md rule 2)."""
    errs = []
    for sid, s in sources.items():
        if not (s.url or s.title):
            errs.append((f"sources.{sid}", "needs a URL or citation"))
        if not s.accessed:
            errs.append((f"sources.{sid}.accessed", "access date missing"))
    return errs


def load_record(path: str | Path, sources: dict[str, Source] | None = None) -> AircraftRecord:
    with open(path, encoding="utf-8") as f:
        rec = record_from_dict(json.load(f))
    if sources is not None:
        errs = provenance_errors(rec, sources)
        if errs:
            raise RecordValidationError(rec.id, errs)
    return rec


def load_records(directory: str | Path, sources: dict[str, Source] | None = None) -> list[AircraftRecord]:
    paths = sorted(Path(directory).glob("*.json"))
    recs = [load_record(p, sources) for p in paths]
    ids = [r.id for r in recs]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise RecordValidationError(str(directory), [("id", f"duplicate record ids {dup}")])
    return recs


def record_to_dict(rec: AircraftRecord) -> dict[str, Any]:
    def enc(o: Any) -> Any:
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, dict):
            return {k: enc(v) for k, v in o.items()}
        if isinstance(o, list):
            return [enc(v) for v in o]
        return o
    return enc(asdict(rec))  # type: ignore[no-any-return]


def save_record(rec: AircraftRecord, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record_to_dict(rec), f, indent=2, sort_keys=False)
        f.write("\n")


__all__ = ["load_sources", "save_sources", "record_from_dict", "load_record", "load_records", "save_record",
           "record_to_dict", "iter_values", "provenance_errors", "source_errors", "ComponentMass"]
