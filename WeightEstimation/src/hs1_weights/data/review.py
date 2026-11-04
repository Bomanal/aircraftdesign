"""Extraction review queue (doc 04 §6, R14).

An extractor (human, LLM or SLM) proposes a *candidate* JSON with page/snippet citations. Candidates are never
written to ``records/`` directly. This module validates candidates (step 3) and commits reviewed fields
(steps 4-5). No LLM is called here: the numeric path stays deterministic (doc 09 §4).

Candidate format::

    {"record": {...AircraftRecord fields...},
     "citations": {"<field path>": {"page": 12, "snippet": "Empty weight 428 kg"}}}

Review decision format (one per field path)::

    {"<field path>": {"decision": "accept" | "edit" | "reject", "value": 431.0, "confidence": 0.9, "reviewer": "MB"}}
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from ..errors import RecordValidationError
from ..schema import AircraftRecord, Source, Tier, V
from ..units import to_si
from .io import iter_values, provenance_errors, record_from_dict, save_record


def cross_field_errors(rec: AircraftRecord) -> list[tuple[str, str]]:
    """Doc 04 §6.3 cross-field checks: EW < MTOW; span²/S within 4–25; V_NE > V_C."""
    errs: list[tuple[str, str]] = []

    def si(v: V | None) -> float | None:
        return None if v is None else to_si(v.value, v.unit)[0]

    ew, mtow = si(rec.empty_weight), si(rec.mtow)
    if ew is not None and mtow is not None and not ew < mtow:
        errs.append(("empty_weight", f"EW {ew:.1f} kg not < MTOW {mtow:.1f} kg"))
    b, s = si(rec.span), si(rec.wing_area)
    if b is not None and s is not None and not 4.0 <= b * b / s <= 25.0:
        errs.append(("span", f"aspect ratio {b * b / s:.2f} outside 4–25"))
    vne, vc = si(rec.v_ne), si(rec.v_c)
    if vne is not None and vc is not None and not vne > vc:
        errs.append(("v_ne", "V_NE must exceed V_C"))
    return errs


def validate_candidate(cand: dict[str, Any], sources: dict[str, Source]) -> list[tuple[str, str]]:
    try:
        rec = record_from_dict(cand["record"])
    except RecordValidationError as e:
        return e.errors
    errs = provenance_errors(rec, sources) + cross_field_errors(rec)
    cites = cand.get("citations", {})
    for path, _ in iter_values(rec):
        c = cites.get(path)
        if not c or "page" not in c or not c.get("snippet"):
            errs.append((path, "missing page/snippet citation"))
    return errs


def commit(cand: dict[str, Any], decisions: dict[str, dict[str, Any]], sources: dict[str, Source],
           records_dir: str | Path, review_log: str | Path) -> AircraftRecord:
    """Apply reviewer decisions; every sourced field needs a decision. Tier C values are never promoted."""
    rec_d = json.loads(json.dumps(cand["record"]))
    rec0 = record_from_dict(rec_d)
    missing = [p for p, _ in iter_values(rec0) if p not in decisions]
    if missing:
        raise RecordValidationError(rec0.id, [(p, "no review decision") for p in missing])
    for path, dec in decisions.items():
        node, key = _walk(rec_d, path)
        val = node[key]
        tier = sources[val["source"]].tier if val["source"] in sources else Tier.C
        if tier == Tier.C and dec["decision"] != "reject":
            raise RecordValidationError(rec0.id, [(path, "tier C value cannot be promoted by the pipeline")])
        if dec["decision"] == "reject":
            _delete(rec_d, path)
            continue
        if dec["decision"] == "edit":
            val["value"] = float(dec["value"])
        val["confidence"] = float(dec.get("confidence", val.get("confidence", 0.9)))
    rec = record_from_dict(rec_d)
    errs = provenance_errors(rec, sources) + cross_field_errors(rec)
    if errs:
        raise RecordValidationError(rec.id, errs)
    save_record(rec, Path(records_dir) / f"{rec.id}.json")
    with open(review_log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"record_id": rec.id, "committed": _dt.date.today().isoformat(),
                            "decisions": decisions}, sort_keys=True) + "\n")
    return rec


def _walk(d: dict[str, Any], path: str) -> tuple[Any, str | int]:
    parts = path.split(".")
    node: Any = d
    for p in parts[:-1]:
        node = node[int(p)] if isinstance(node, list) else node[p]
    last: str | int = int(parts[-1]) if isinstance(node, list) else parts[-1]
    return node, last


def _delete(d: dict[str, Any], path: str) -> None:
    node, key = _walk(d, path)
    if path.startswith("components."):
        node[key] = None
        comps = d.get("components", [])
        d["components"] = [c for c in comps if c and c.get("mass") is not None]
    elif isinstance(node, list):
        node.pop(int(key))
    elif isinstance(node, dict) and path.count(".") == 0:
        node[key] = None
    else:
        node.pop(key)
