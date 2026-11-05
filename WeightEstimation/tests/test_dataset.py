from conftest import synthetic_record, v, write_dataset_inputs

from hs1_weights.data.dataset import build_dataset, load_dataset


def _recs():
    rs = [synthetic_record(i) for i in range(6)]
    rs[0]["components"] = [{"name": "wing", "mass": v(70.0)}, {"name": "fuselage", "mass": v(80.0)}]
    return rs


def test_rebuild_is_byte_identical(tmp_path, sources):
    rd, sc = write_dataset_inputs(tmp_path, _recs(), sources)
    b1 = build_dataset(rd, sc, tmp_path / "b1")
    b2 = build_dataset(rd, sc, tmp_path / "b2")
    for f in ("normalized.parquet", "components.parquet", "normalization_log.jsonl", "dataset_manifest.json"):
        assert (tmp_path / "b1" / f).read_bytes() == (tmp_path / "b2" / f).read_bytes(), f
    assert b1.content_hash == b2.content_hash


def test_dataset_contents(tmp_path, sources):
    rd, sc = write_dataset_inputs(tmp_path, _recs(), sources)
    b = build_dataset(rd, sc, tmp_path / "b")
    df, comps, man = load_dataset(tmp_path / "b")
    assert len(df) == 6 and man["n_eligible"] == 6 and man["n_with_components"] == 1
    assert set(comps["component"]) == {"wing", "fuselage"}
    assert df.loc[0, "mtow"] == 750.0 and df["calibration_eligible"].all()
    assert man["content_hash"] == b.content_hash


def test_hash_changes_with_data(tmp_path, sources):
    rd, sc = write_dataset_inputs(tmp_path, _recs(), sources)
    h1 = build_dataset(rd, sc, tmp_path / "b").content_hash
    r = synthetic_record(0, empty_weight=v(501.0))
    (rd / "synthetic_000.json").write_text(__import__("json").dumps(r))
    assert build_dataset(rd, sc, tmp_path / "b").content_hash != h1
