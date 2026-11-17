# 04 — Fleet Database Specification

Schema implemented in `src/hs1_weights/schema.py` (seed).

## 1. Storage

| Artefact | Format | Content |
|---|---|---|
| `data/sources.csv` | CSV | One row per source document (`Source`) |
| `data/records/*.json` | One JSON per aircraft variant | `AircraftRecord` with `V` values |
| `data/component_breakdowns/*.json` | JSON | `ComponentMass` lists for aircraft with published breakdowns |
| `build/normalized.parquet` | Parquet | Output of N1–N12; one row per variant |
| `build/normalization_log.jsonl` | JSONL | Every warning and derivation per record |

Records are append-only under version control. Corrections create a new `V` with a `note: "supersedes <source>"`.

## 2. Record rules

1. **One record per variant**, using the TCDS model designation (e.g., `C172S`, not `Cessna 172`).
2. Every numeric field is a `V(value, unit, source, confidence, note)`. Units are stored as published; conversion happens in N1.
3. `empty_weight_def` is mandatory. Use `UNKNOWN` if the source does not say.
4. Fields `material_by_component` and `components` are populated only from sources that state them.
5. `production_status` ∈ {production, out_of_production, prototype, certified_not_delivered}.

## 3. Source tiers

| Tier | Sources | Calibration use | Base σ_rel (empty weight) |
|---|---|---|---|
| A | FAA/EASA TCDS; POH weight-and-balance section; Roskam Pt V and Torenbeek component tables; NASA/NACA GA weight reports; Jane's All the World's Aircraft | Yes | 2% |
| B | Manufacturer spec sheet; LAA/EAA type data; peer-reviewed design study; regulator press release with figures | Yes, inflated variance | 4% |
| C | Brochure, press release, prototype announcement, forum or blog | **No.** Analog reasoning only | — |

TCDS give MTOW, the certification basis, load-factor category and often V_NE. They rarely give empty weight; the POH W&B section is the primary empty-weight source.

## 4. Coverage matrix and targets

The matrix is crossed by seats × powertrain × primary structure. The calibration needs **independent variation** along each axis.

The full candidate list is in `data/aircraft_candidates.csv` (81 entries, 22 at priority 1).

| Class | Composite | Mixed | Metal | Wood / tube-and-fabric |
|---|---|---|---|---|
| 2-seat electric | Velis Electro / Alpha Electro, Bye eFlyer 2, Airbus E-Fan, Elektra Trainer, Taurus Electro | — | Sonex Electric | — |
| 2-seat piston | Virus SW, Sinus, Alpha Trainer, DA20, CTLS, Remos GX, Aquila AT01, Grob G115, T67, Elixir, Shark, Blackwing, Icon A5 | P2008, Extra 300 | C150/152, C162, RV-12, RV-7, PA-38, Beech Skipper, Bristell, SportStar, CH650, Sling 2 | J-3/Super Cub, Aeronca Champ, DR400, HR200, Jodel D11x |
| 4-seat piston/diesel | SR20, SR22, DA40/NG, Cessna 350/400, Glasair III, Lancair | P2010 | C172, C182, PA-28 family, Mooney M20, AA-5, TB10/20, RV-10, Sling 4, Commander 114 | — |
| 4–6 twin | DA42, DA62 | P2006T | Baron, Seneca, C310, P68 | — |
| 6–10 single | Epic E1000 | — | C206, C210, C208, PA-32, PA-46, TBM, PC-12, Kodiak, GA8 | — |
| 6–10 electric/hybrid | Eviation Alice | — | magniX eCaravan, Harbour Air ePlane, Ampaire EEL, VoltAero Cassio | — |
| 9–11 twin | — | — | BN-2 Islander, P2012, C402 | — |

**Phase 1 target:** ≥ 60 Tier A/B records, including **all aircraft with published component breakdowns**. These are the highest priority because they make individual k_i identifiable (doc 07 §4).

**Phase 4 target:** 150–250 records.

**Flags to carry:** `pressurized`, turboprop, and twin are group-level effects in calibration. They anchor the upper range but must not dominate the 2-seat posterior.

## 5. Mandatory fields for calibration eligibility

| Group | Required | Desirable |
|---|---|---|
| Identity | id, type, variant, cert, seats, status | year |
| Weights | mtow, empty_weight (+def) | max landing weight, fuel or kWh |
| Geometry | wing_area, span | t/c, taper, fuselage L/W/H, tail areas, tail arm |
| Speeds/loads | v_ne **or** v_c | n limits (else inferred, N6), V_S0, V_H |
| Powertrain | type, rated power | engine dry mass, n engines |
| Structure | material_primary, gear | material_by_component |

A record missing a required field is stored but marked `calibration_eligible=False`.

## 6. Extraction pipeline (P1, R14)

1. **Acquire.** Download the TCDS/POH PDF and register a `Source` row (sha256 of the file is stored).
2. **Extract.** An LLM or small language model proposes field values **with page and snippet citations**. Output is a candidate JSON, never written directly to `records/`.
3. **Validate.** Schema check, unit check, cross-field checks (e.g., EW < MTOW; span² / S within 4–25; V_NE > V_C).
4. **Review.** A human accepts or edits each field in a review queue. Accepted fields get `confidence` set by the reviewer.
5. **Commit.** The record is written; the review decision is logged.

Tier C sources are never promoted by the pipeline.

## 7. Example

See `data/record_example.json`. Its values are **placeholders marked TO-VERIFY**. It illustrates structure only and must not be used in calibration.
