"""Import the clustered fleet table (``data/fleet/fleet_clusters.csv``) as AircraftRecords + Sources.

The CSV is the owner-compiled fleet list (one row per aircraft, with a cluster id). This module converts it in
memory, so the CSV stays the single editable copy:

- **Tiers.** Per-field tiers come from ``mtow_tier``, ``oew_tier`` and ``wing_area_tier``; every other field takes
  ``min_tier_core``. ``C+`` (a secondary compilation citing Tier A/B, e.g. Wikipedia citing Jane's) maps to
  ``Tier.C_PLUS``, which is used for imputation and analogs but never calibrated (doc 12 SC-27). Tier ``Z``
  values (analyst recollection) are dropped. ``data/fleet/field_overrides.csv`` sets per-field tiers that the
  table does not encode (e.g. Shark gear type = Z, from cluster_summary.csv).
- **Sources.** One ``Source`` per (record, tier): ``fleetcsv_<id>_<tier>``. Its URLs are taken from
  ``primary_sources`` (TCDS/PDF → A, Wikipedia → C+, others → B; all URLs if none match). The access date is
  the date the CSV was compiled (``accessed``). The CSV holds no per-source access dates (doc 12 SC-30).
- **Missing categorical values.** A missing gear type gets the cluster's most common gear (logged in
  ``ImportReport`` and in the record notes). Numeric gaps are left missing for NI (data/impute.py).
- **Mappings.** cert_expected free text → ``CertTag`` (first-listed basis; any "VLA" → CS-VLA), and
  empty_weight_def text → ``EmptyWeightDef``. Overall length is used as fuselage length (flagged). Overall
  height is not mapped (it is not fuselage depth).
"""
from __future__ import annotations

import csv
import datetime as _dt
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..schema import (
    AircraftRecord,
    CertTag,
    ComponentMass,
    EmptyWeightDef,
    GearType,
    Material,
    Powertrain,
    Source,
    Tier,
    V,
)

TIER_MAP = {"A": Tier.A, "B": Tier.B, "C+": Tier.C_PLUS, "C": Tier.C}
DROPPED_TIERS = {"Z"}
SOURCE_KIND = {Tier.A: "TCDS", Tier.B: "spec_sheet", Tier.C_PLUS: "secondary_compilation", Tier.C: "press"}
POWERTRAIN = {"piston_avgas": Powertrain.PISTON_AVGAS, "piston_rotax": Powertrain.PISTON_MOGAS_ROTAX,
              "piston_diesel": Powertrain.PISTON_DIESEL, "turboprop": Powertrain.TURBOPROP,
              "battery_electric": Powertrain.ELECTRIC}
FUEL_TYPE = {Powertrain.PISTON_AVGAS: "avgas", Powertrain.PISTON_MOGAS_ROTAX: "mogas",
             Powertrain.PISTON_DIESEL: "jet_a", Powertrain.TURBOPROP: "jet_a"}
GEAR = {"fixed_tricycle": GearType.FIXED_TRICYCLE, "fixed_taildragger": GearType.FIXED_TAILWHEEL,
        "retractable_tricycle": GearType.RETRACTABLE, "retractable_main (glider)": GearType.RETRACTABLE}
# CSV column -> (record field, unit, note)
VALUE_COLUMNS: dict[str, tuple[str, str, str]] = {
    "mtow_kg": ("mtow", "kg", ""),
    "empty_weight_kg": ("empty_weight", "kg", ""),
    "wing_area_m2": ("wing_area", "m2", ""),
    "span_m": ("span", "m", ""),
    "length_m": ("fuselage_length", "m", "overall aircraft length used as fuselage length"),
    "vne_kt": ("v_ne", "kt", "kt as published (CAS/IAS not stated)"),
    "vno_kt": ("v_no", "kt", "kt as published (CAS/IAS not stated)"),
    "v_cruise_kt": ("v_cruise", "kt", "cruise speed; NI predictor only"),
    "fuel_capacity_l": ("fuel_capacity_total", "L", "fuel_capacity_l: total vs usable not stated"),
    "battery_energy_kwh": ("battery_energy_total", "kWh", ""),
    "battery_mass_kg": ("battery_mass_installed", "kg", ""),
    "htail_area_m2": ("htail_area", "m2", ""),
    "vtail_area_m2": ("vtail_area", "m2", ""),
    "tail_arm_m": ("tail_arm", "m", ""),
    "fuselage_width_m": ("fuselage_max_width", "m", ""),
    "fuselage_height_m": ("fuselage_max_height", "m", ""),
    "main_gear_strut_m": ("main_gear_length", "m", ""),
    "nose_gear_strut_m": ("nose_gear_length", "m", ""),
    "avionics_mass_kg": ("avionics_uninstalled_mass", "kg", "avionics_mass_kg assumed uninstalled (Raymer W_uav)"),
}
COMPONENT_COLUMNS = {"w_wing_kg": "wing", "w_fuselage_kg": "fuselage", "w_htail_kg": "htail",
                     "w_vtail_kg": "vtail", "w_gear_kg": "gear"}
FIELD_TIER_COLUMN = {"mtow": "mtow_tier", "empty_weight": "oew_tier", "wing_area": "wing_area_tier"}


@dataclass
class ImportReport:
    n_rows: int = 0
    dropped: list[str] = field(default_factory=list)          # "<id>.<column>: tier Z"
    imputed_categorical: list[str] = field(default_factory=list)
    mappings: list[str] = field(default_factory=list)          # free-text mappings applied
    clusters: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"n_rows": self.n_rows, "dropped": self.dropped, "imputed_categorical": self.imputed_categorical,
                "mappings": self.mappings, "clusters": self.clusters}


def map_cert(text: str) -> CertTag:
    """First-listed certification basis in a free-text cell (doc 12 SC-30)."""
    t = (text or "").lower()
    if "vla" in t:
        return CertTag.CS_VLA
    pats = [(r"cs-lsa", CertTag.CS_LSA), (r"astm", CertTag.ASTM_F2245), (r"experimental", CertTag.EXPERIMENTAL),
            (r"\blsa\b", CertTag.ASTM_F2245), (r"\bul\b", CertTag.EU_UL),
            (r"far ?23|cs-23|part ?23|jar ?23|car ?3|car ?4a|bcar", CertTag.FAR23_NORMAL)]
    hits = sorted((m.start(), tag) for p, tag in pats for m in [re.search(p, t)] if m)
    if not hits:
        raise ValueError(f"cannot map certification basis {text!r}")
    tag = hits[0][1]
    if tag == CertTag.FAR23_NORMAL:
        sub = sorted((m.start(), s) for s, m in ((s, re.search(s, t)) for s in
                                                 ("normal", "utility", "aerobatic", "commuter")) if m)
        tag = {"normal": CertTag.FAR23_NORMAL, "utility": CertTag.FAR23_UTILITY,
               "aerobatic": CertTag.FAR23_AEROBATIC, "commuter": CertTag.FAR23_COMMUTER}[sub[0][1]] \
            if sub else CertTag.FAR23_NORMAL
    return tag


def map_ew_def(text: str) -> tuple[EmptyWeightDef, bool | None]:
    """(definition, empty weight includes pack?) from the empty_weight_def cell."""
    t = (text or "").strip().lower()
    pack = True if "batter" in t else None
    if t.startswith("basic empty weight"):
        return EmptyWeightDef.BASIC_GAMA, pack
    if "standard" in t or t == "without options":
        return EmptyWeightDef.STANDARD_NO_OPTIONS, pack
    return EmptyWeightDef.UNKNOWN, pack


def _seats(text: str) -> tuple[int, str]:
    parts = [int(p) for p in re.findall(r"\d+", text or "")]
    if not parts:
        raise ValueError(f"seats {text!r}")
    return max(parts), ("" if len(parts) == 1 else f"seats {text!r}: maximum used")


def _urls_for(tier: Tier, urls: list[str]) -> list[str]:
    def kind(u: str) -> Tier:
        lu = u.lower()
        if "wikipedia" in lu:
            return Tier.C_PLUS
        if lu.endswith(".pdf") or "easa" in lu or "faa.gov" in lu:
            return Tier.A
        return Tier.B
    sel = [u for u in urls if kind(u) == tier]
    return sel or urls


def _source(sources: dict[str, Source], row: dict[str, str], urls: list[str], tier_s: str, accessed: str) -> str:
    """Register (once) and return the Source id for one record's values of one tier."""
    tier = TIER_MAP[tier_s]
    sid = f"fleetcsv_{row['id'].strip()}_{tier_s.replace('+', 'plus')}"
    if sid not in sources:
        sources[sid] = Source(
            id=sid, title=f"{row['type_name']} {row.get('variant_hint', '')}".strip()
            + f": tier {tier_s} values in data/fleet/fleet_clusters.csv", kind=SOURCE_KIND[tier],
            tier=tier, url=" ; ".join(_urls_for(tier, urls)), accessed=accessed,
            notes=f"imported from fleet_clusters.csv; review_status={row.get('review_status', '')}")
    return sid


def load_overrides(path: str | Path | None) -> dict[tuple[str, str], tuple[str, str]]:
    if path is None or not Path(path).exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {(r["id"], r["column"]): (r["tier"].strip(), r.get("note", "")) for r in csv.DictReader(f)}


def load_cluster_summary(path: str | Path | None) -> dict[str, dict[str, str]]:
    if path is None or not Path(path).exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["cluster_id"]: dict(r) for r in csv.DictReader(f)}


def load_fleet_csv(path: str | Path, accessed: str | None = None, overrides: str | Path | None = "auto",
                   ) -> tuple[list[AircraftRecord], dict[str, Source], ImportReport]:
    """Convert the fleet CSV. ``accessed`` defaults to the CSV file's modification date (logged)."""
    path = Path(path)
    if accessed is None:
        accessed = _dt.date.fromtimestamp(path.stat().st_mtime).isoformat()
    ov = load_overrides(path.parent / "field_overrides.csv" if overrides == "auto" else overrides)
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rep = ImportReport(n_rows=len(rows))
    rep.mappings.append(f"source access date = {accessed} (CSV compilation date; no per-source dates in the CSV)")

    def tier_of(row: dict[str, str], column: str, fld: str) -> str:
        if (row["id"], column) in ov:
            return ov[(row["id"], column)][0]
        return (row.get(FIELD_TIER_COLUMN.get(fld, ""), "") or row.get("min_tier_core", "")).strip()

    # cluster-mode gear for rows without a usable gear type
    def gear_of(row: dict[str, str]) -> GearType | None:
        g = row.get("gear_type", "").strip()
        if not g or tier_of(row, "gear_type", "gear") in DROPPED_TIERS:
            return None
        return GEAR[g]
    mode: dict[str, GearType] = {}
    for cid in {r["cluster_id"] for r in rows}:
        gs = [g for r in rows if r["cluster_id"] == cid for g in [gear_of(r)] if g is not None]
        if gs:
            mode[cid] = Counter(gs).most_common(1)[0][0]

    records: list[AircraftRecord] = []
    sources: dict[str, Source] = {}
    for row in rows:
        rid = row["id"].strip()
        urls = [u.strip() for u in row.get("primary_sources", "").split(";") if u.strip()]
        notes: list[str] = [f"cluster {row['cluster_id']} ({row['cluster_name']})",
                            f"calibration_class={row.get('calibration_class', '')}",
                            f"review_status={row.get('review_status', '')}"]

        def src(tier_s: str, row: dict[str, str] = row, urls: list[str] = urls) -> str:
            return _source(sources, row, urls, tier_s, accessed)

        vals: dict[str, V] = {}
        for col, (fld, unit, note) in VALUE_COLUMNS.items():
            raw = (row.get(col) or "").strip()
            if not raw:
                continue
            t = tier_of(row, col, fld)
            if t in DROPPED_TIERS:
                rep.dropped.append(f"{rid}.{col}: tier {t}")
                continue
            v = float(raw)
            if col == "fuel_capacity_l" and row["powertrain"] == "battery_electric":
                continue
            vals[fld] = V(v, unit, src(t), 0.9 if TIER_MAP[t] in (Tier.A, Tier.B) else 0.7, note)
        p1 = (row.get("rated_power_kw_per_engine") or "").strip()
        n_en = int(float(row.get("n_engines") or 1))
        if p1:
            t = tier_of(row, "rated_power_kw_per_engine", "rated_power_total")
            if t not in DROPPED_TIERS:
                vals["rated_power_total"] = V(float(p1) * n_en, "kW", src(t),
                                              0.9 if TIER_MAP[t] in (Tier.A, Tier.B) else 0.7,
                                              f"{n_en} x {p1} kW per engine")
        comps = []
        for col, name in COMPONENT_COLUMNS.items():
            raw = (row.get(col) or "").strip()
            if raw:
                t = tier_of(row, col, "components")
                if t not in DROPPED_TIERS:
                    comps.append(ComponentMass(name, V(float(raw), "kg", src(t), 0.9)))
        gear = gear_of(row)
        if gear is None:
            gear = mode.get(row["cluster_id"], GearType.FIXED_TRICYCLE)
            why = "tier Z" if row.get("gear_type", "").strip() else "missing"
            rep.imputed_categorical.append(f"{rid}.gear: {why} -> cluster {row['cluster_id']} mode {gear.value}")
            notes.append(f"gear {why}: cluster mode {gear.value} used (NI categorical)")
        cert = map_cert(row["cert_expected"])
        rep.mappings.append(f"{rid}.cert: {row['cert_expected']!r} -> {cert.value}")
        ew_def, pack = map_ew_def(row.get("empty_weight_def", ""))
        seats, seat_note = _seats(row["seats"])
        if seat_note:
            notes.append(seat_note)
        pt = POWERTRAIN[row["powertrain"]]
        for k in ("engine_or_motor", "cert_basis_retrieved", "height_m", "fields_with_variant_flag"):
            if row.get(k):
                notes.append(f"{k}={row[k]}")
        records.append(AircraftRecord(
            id=rid, type_name=row["type_name"].strip(), variant=row.get("variant_hint", "").strip(),
            year_first_flight=None, cert=cert, cert_note=row.get("cert_basis_retrieved", ""), seats=seats,
            production_status=row["status"].strip(), manufacturer=row["type_name"].split()[0],
            cluster=row["cluster_id"].strip(), empty_weight_def=ew_def,
            empty_weight_includes_pack=pack if pt == Powertrain.ELECTRIC else None,
            fuel_type=FUEL_TYPE.get(pt, ""), powertrain=pt, n_motors_or_engines=n_en,
            material_primary=Material(row["material_primary"].strip()), gear=gear, components=comps,
            notes="; ".join(notes)))
        for fld, sv in vals.items():
            setattr(records[-1], fld, sv)
    for cid in sorted({r["cluster_id"] for r in rows}):
        members = [r for r in rows if r["cluster_id"] == cid]
        rep.clusters[cid] = {"name": members[0]["cluster_name"], "n": len(members),
                             "n_tier_ab": sum(r["mtow_tier"] in ("A", "B") and r["oew_tier"] in ("A", "B")
                                              for r in members)}
    return records, sources, rep
