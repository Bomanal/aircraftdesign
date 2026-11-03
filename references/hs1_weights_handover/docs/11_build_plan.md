# 11 — Build Plan

Each phase closes only when all of its acceptance criteria pass.

## Phase 1 — Data foundation

| Ticket | Task | Acceptance |
|---|---|---|
| P1-1 | `units.py` with constants and tests | Round-trip property tests pass |
| P1-2 | `data/io.py`: JSON records, sources CSV, validation | Schema errors are reported with field paths |
| P1-3 | N1, N2, N4, N6, N7, N8, N10, N11, N12 (N3/N5/N9 seeded) | Unit tests per routine; idempotence test |
| P1-4 | `dataset.py`: build `normalized.parquet` + log + hash | Rebuild is byte-identical |
| P1-5 | Extract ≥ 60 Tier A/B records, prioritising aircraft with component breakdowns (`aircraft_candidates.csv` priority 1) | ≥ 60 eligible; ≥ 15 with component breakdowns; electric ≥ 5 (any tier) |

## Phase 2 — Component library

| Ticket | Task | Acceptance |
|---|---|---|
| P2-1 | `components/base.py`, registry, `ModelContext` | Protocol checks pass |
| P2-2 | Raymer GA (all of doc 06 §3) | Textbook example within 1%; electric W_fw override test |
| P2-3 | Torenbeek, Cessna/USAF (Roskam), Gudmundsson structure methods | Each reproduces its source example within 1% |
| P2-4 | Electric: pack, motors, inverters, thermal, harness | Hand-calc tests (doc 08 §3) |
| P2-5 | Battery bays, cutout penalty, DEP nacelles and inertia relief | Property tests; zero-mass relief = 1.0 |
| P2-6 | Fixed-items equipment list loader | Validation of stations and masses |
| P2-7 | Raw-method benchmark on the Phase 1 dataset | Benchmark table produced |

## Phase 3 — Calibration

| Ticket | Task | Acceptance |
|---|---|---|
| P3-1 | numpyro model (doc 07 §3) incl. censored W_dg | Prior predictive check sane (no negative masses; W_E/MTOW within 0.3–0.9) |
| P3-2 | MAP / Laplace fallback | Agrees with NUTS posterior means within 2% on Phase 1 data |
| P3-3 | Diagnostics and artefact writer | R-hat, ESS and divergences reported |
| P3-4 | LOO / LOCO / benchmark harness | Doc 08 §5 table produced; thresholds evaluated |

## Phase 4 — Expansion and reasoning

| Ticket | Task | Acceptance |
|---|---|---|
| P4-1 | Expand to 150–250 records | Coverage matrix cells non-empty except documented gaps |
| P4-2 | GP residual model with LOCO guard | Improves in-class MAPE without degrading electric LOCO coverage |
| P4-3 | Analogs, rules C1–C9, Sobol | Reports render in `WeightReport` |

## Phase 5 — Integration

| Ticket | Task | Acceptance |
|---|---|---|
| P5-1 | `api.predict()` with Pydantic I/O | JSON schema exported |
| P5-2 | Closure loop and Monte Carlo in `hs1_design` | Converges for the 700 kg / 60 kWh reference; MTOW and range P10/P50/P90 reported |
| P5-3 | CG module and loading cases | CG range in %MAC reported; bay-removed cases included |
| P5-4 | Re-run the HS1 design point with uncertainty | Updated `results.json` and design report section |
