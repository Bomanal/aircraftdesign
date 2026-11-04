"""Shared fixtures. All fleet-like records here are SYNTHETIC test data, not real aircraft (CLAUDE.md rule 2)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hs1_weights.schema import Source, Tier

ROOT = Path(__file__).resolve().parents[1]


def synthetic_sources() -> dict[str, Source]:
    return {
        "sA": Source("sA", "Synthetic tier-A source", "TCDS", Tier.A, "test://a", "2026-01-01"),
        "sA2": Source("sA2", "Synthetic tier-A source, later", "POH", Tier.A, "test://a2", "2026-06-01"),
        "sB": Source("sB", "Synthetic tier-B source", "spec_sheet", Tier.B, "test://b", "2026-01-01"),
        "sC": Source("sC", "Synthetic tier-C source", "press", Tier.C, "test://c", "2026-01-01"),
    }


def v(value: float, unit: str = "kg", source: str = "sA", **kw: Any) -> dict[str, Any]:
    return {"value": value, "unit": unit, "source": source, **kw}


def synthetic_record(i: int = 0, **over: Any) -> dict[str, Any]:
    """A complete, calibration-eligible synthetic 2-seat metal piston record (values invented for tests)."""
    d: dict[str, Any] = {
        "id": f"synthetic_{i:03d}", "type_name": f"Synthetic {i}", "variant": "T", "year_first_flight": 1990,
        "cert": "far23_normal", "seats": 2, "production_status": "production", "manufacturer": "SynthCo",
        "tcds_model": f"S{i}",
        "mtow": v(750.0 + 10 * i), "empty_weight": v(500.0 + 6 * i), "empty_weight_def": "basic_empty_gama",
        "wing_area": v(11.0, "m2"), "span": v(10.0, "m"), "taper": v(0.8, "-"), "sweep_qc": v(0.0, "deg"),
        "root_tc": v(0.15, "-"), "fuselage_length": v(7.0, "m"), "fuselage_max_width": v(1.1, "m"),
        "fuselage_max_height": v(1.3, "m"), "htail_area": v(2.0, "m2"), "vtail_area": v(1.1, "m2"),
        "tail_arm": v(4.2, "m"), "main_gear_length": v(0.5, "m"), "nose_gear_length": v(0.45, "m"),
        "v_ne": v(150.0, "KCAS"), "v_c": v(110.0, "KCAS"), "v_h": v(120.0, "KCAS"),
        "rated_power_total": v(75.0, "kW"), "engine_dry_mass": v(60.0), "fuel_capacity_total": v(100.0, "L"),
        "fuel_capacity_usable": v(95.0, "L"), "fuel_type": "avgas", "fuel_tanks": 2, "oil_capacity": v(4.0, "L"),
        "avionics_uninstalled_mass": v(8.0), "powertrain": "piston_avgas", "material_primary": "metal_aluminium",
        "gear": "fixed_tricycle",
    }
    d.update(over)
    return d


@pytest.fixture
def sources() -> dict[str, Source]:
    return synthetic_sources()


def write_dataset_inputs(tmp: Path, recs: list[dict[str, Any]], sources: dict[str, Source]) -> tuple[Path, Path]:
    from hs1_weights.data.io import save_sources
    rd = tmp / "records"
    rd.mkdir()
    for r in recs:
        (rd / f"{r['id']}.json").write_text(json.dumps(r))
    sc = tmp / "sources.csv"
    save_sources(sources, sc)
    return rd, sc
