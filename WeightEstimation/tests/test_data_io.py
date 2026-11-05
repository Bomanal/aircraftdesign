import json

import pytest
from conftest import ROOT, synthetic_record, v

from hs1_weights.data.io import load_record, load_sources, provenance_errors, record_from_dict, save_record
from hs1_weights.data.review import commit, cross_field_errors, validate_candidate
from hs1_weights.errors import RecordValidationError


def test_schema_errors_report_field_paths():
    bad = synthetic_record(mtow={"value": "heavy", "unit": "kg", "source": "sA"}, gear="skids")
    with pytest.raises(RecordValidationError) as ei:
        record_from_dict(bad)
    paths = [p for p, _ in ei.value.errors]
    assert "mtow.value" in paths and "gear" in paths


def test_nested_component_error_path():
    bad = synthetic_record(components=[{"name": "wing", "mass": {"value": 60.0, "unit": "kg"}}])
    with pytest.raises(RecordValidationError) as ei:
        record_from_dict(bad)
    assert any(p.startswith("components.0.mass") for p, _ in ei.value.errors)


def test_every_value_needs_registered_source(sources):
    rec = record_from_dict(synthetic_record(span=v(10.0, "m", "nope")))
    errs = provenance_errors(rec, sources)
    assert errs == [("span.source", "source 'nope' not in sources.csv")]


def test_round_trip(tmp_path, sources):
    rec = record_from_dict(synthetic_record())
    p = tmp_path / "r.json"
    save_record(rec, p)
    assert load_record(p, sources) == rec


def test_example_record_parses_but_is_not_provenanced():
    d = json.loads((ROOT / "data" / "record_example.json").read_text())
    rec = record_from_dict(d)
    assert rec.id == "pip_velis_electro"
    errs = provenance_errors(rec, {})
    assert errs, "example sources are not registered: must not be admissible"


def test_sources_template_loads():
    s = load_sources(ROOT / "data" / "sources_template.csv")
    assert set(s) == {"src_example_tcds", "src_example_poh"}


def test_cross_field_checks():
    rec = record_from_dict(synthetic_record(empty_weight=v(900.0), span=v(30.0, "m"), v_c=v(160.0, "KCAS")))
    fields = {p for p, _ in cross_field_errors(rec)}
    assert fields == {"empty_weight", "span", "v_ne"}


def test_review_commit_requires_decisions_and_blocks_tier_c(tmp_path, sources):
    rec = synthetic_record()
    cand = {"record": rec, "citations": {}}
    assert any("citation" in m for _, m in validate_candidate(cand, sources))
    from hs1_weights.data.io import iter_values
    paths = [p for p, _ in iter_values(record_from_dict(rec))]
    decisions = {p: {"decision": "accept", "confidence": 0.9} for p in paths}
    decisions["empty_weight"] = {"decision": "edit", "value": 512.0, "confidence": 0.95}
    out = commit(cand, decisions, sources, tmp_path, tmp_path / "log.jsonl")
    assert out.empty_weight.value == 512.0 and (tmp_path / f"{out.id}.json").exists()
    with pytest.raises(RecordValidationError):
        commit(cand, {paths[0]: decisions[paths[0]]}, sources, tmp_path, tmp_path / "log.jsonl")
    cand_c = {"record": synthetic_record(span=v(10.0, "m", "sC"))}
    with pytest.raises(RecordValidationError, match="tier C"):
        commit(cand_c, decisions, sources, tmp_path, tmp_path / "log.jsonl")
