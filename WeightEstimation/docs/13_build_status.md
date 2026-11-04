# 13 — Build Status (0.2.0, 2026-11-03)

Status of every doc 11 ticket against its acceptance criterion. **PASS** means that the criterion is met and a
test demonstrates it. **PASS (synthetic)** means the machinery meets the criterion on synthetic fleets with known
truth, but the criterion itself is defined on real fleet data, so it stays open until P1-5 closes. **OPEN** means
the criterion is not met yet; the reason is given.

Why code exists beyond Phase 1 although P1-5 is open: see doc 12 SC-1.

## Phase 1 — Data foundation

| Ticket | Status | Evidence / reason |
|---|---|---|
| P1-1 `units.py` | PASS | `tests/test_units.py`: constants (doc 05 N1), hypothesis round-trips SI ↔ imperial to 1e-12 |
| P1-2 `data/io.py` | PASS | `tests/test_data_io.py`: schema errors reported with field paths (`mtow.value`, `components.0.mass...`); provenance check (every value → registered `Source`) |
| P1-3 N1–N12 | PASS | `tests/test_normalize_pipeline.py`: unit tests per routine; hypothesis idempotence `N(N(x)) = N(x)` |
| P1-4 `dataset.py` | PASS | `tests/test_dataset.py`: rebuild byte-identical (parquet, log, manifest); content hash |
| P1-5 ≥ 60 Tier A/B records, ≥ 15 breakdowns, ≥ 5 electric | **OPEN** | `data/records/` is empty. Needs TCDS/POH acquisition and human review (doc 04 §6); the assistant must not supply values (CLAUDE.md rule 2). Review-queue tooling is ready (`data/review.py`). |

## Phase 2 — Component library

| Ticket | Status | Evidence / reason |
|---|---|---|
| P2-1 base, registry, `ModelContext` | PASS | `test_registry_protocol_and_defaults` |
| P2-2 Raymer GA | Partly: equations PASS; textbook example **OPEN** | Every doc 06 §3 equation pinned against an independent evaluation; SI wrappers tested; electric W_fw override test passes. The R4 worked example needs the project's Raymer (`tests/golden/raymer_ga_example.json`, skipped). |
| P2-3 Torenbeek, Cessna/USAF, Gudmundsson | **OPEN** | Torenbeek (wing, tails, fuselage, gear), Cessna (wing, tails, fuselage, gear) and USAF (wing, tails, fuselage, main gear) are registered with `transcription_verified=False` (SC-10). Gudmundsson is not implemented (SC-9). The golden tests skip. |
| P2-4 Electric | PASS | Pack 60 kWh / 300 / 0.625 → 320 kg; harness 100 A / 4 A/mm² / 2 m Cu → 0.448 kg |
| P2-5 Bays, cutout, DEP | PASS | Bays strictly increasing in N_bays (hypothesis); zero-mass relief factor exactly 1.0; relief hand calculation |
| P2-6 Equipment list | PASS | `test_equipment_validation`: categories, masses, mandatory entries, duplicates; stations validated by CG |
| P2-7 Raw-method benchmark | Harness PASS; table **OPEN** | `calibration.validate.raw_method_mape`; the table needs P1-5 data |

## Phase 3 — Calibration

| Ticket | Status | Evidence / reason |
|---|---|---|
| P3-1 numpyro model incl. censored W_dg | PASS (synthetic) | `calibration/model.py`; latent u on the doc 07 §3.3 grid; prior predictive: no negative masses |
| P3-2 MAP/Laplace agrees with NUTS within 2% | PASS (synthetic) | `test_nuts_agrees_with_map_within_2pct` (slow): structural k for metal and composite agree within 2% |
| P3-3 Diagnostics and artefact | PASS | R-hat, ESS, divergences (re-run at 0.95 if any), θ and k_ref correlation flags, identifiability and collapse to k_struct; `artefacts/calibration_<date>_<hash>.nc` round-trip |
| P3-4 LOO / LOCO / benchmark harness | Harness PASS; thresholds **OPEN** | `validation_report()` evaluates all doc 08 §5 thresholds, but they need real data. On a 50-record synthetic fleet, LOCO composite MAPE < 15%. |

## Phase 4 — Expansion and reasoning

| Ticket | Status | Evidence / reason |
|---|---|---|
| P4-1 150–250 records | **OPEN** | Depends on P1-5 |
| P4-2 GP residual with LOCO guard | Implemented; guard **OPEN** | `calibration/gp_residual.py` (ARD Matérn-5/2 + white). Deterministic JSON storage. The out-of-class guard (`guard_passed`, `gp_out_of_class`) is honoured by `predict()`; evaluating it needs the electric LOCO on real data. |
| P4-3 Analogs, C1–C9, Sobol | PASS | Rendered in `WeightReport`; analog Δ explanation in the doc 09 format; rules that cannot be evaluated say so |

## Phase 5 — Integration

| Ticket | Status | Evidence / reason |
|---|---|---|
| P5-1 `predict()` with Pydantic I/O | PASS | `schemas/HS1Config.schema.json`, `schemas/WeightReport.schema.json` (`hs1-weights schema`) |
| P5-2 Closure and Monte Carlo | PASS against a stand-in; hs1_design coupling **OPEN** | Converges for the 700 kg / 60 kWh reference with a constant-energy stand-in (5 iterations; Monte Carlo closure 100% converged). `hs1_design/` is not in this repository: pass its `energy_required(W_dg)` to `closure.close_point` / `close_mc`. |
| P5-3 CG and loading cases | PASS | Empty, 2 × 86 kg, single pilot light, each bay removed; %MAC; tip-over fraction |
| P5-4 Re-run HS1 design point | **OPEN** | Needs hs1_design `results.json` inputs and a fleet calibration artefact |

## Doc 08 §5 statistical acceptance

All **OPEN**: in-class LOO MAPE, LOCO electric MAPE and coverage, benchmark improvement ≥ 30%, PIT/coverage
calibration, and the Velis Electro anchor (the check runs automatically once `pip_velis_electro` is in the
dataset). The R-hat < 1.01 / ESS > 400 diagnostics are computed for every NUTS run.

## Illustrative reference run (not a design result)

`hs1-weights predict examples/hs1_reference.json` (prior-predictive k, illustrative placeholder priors, 300 Wh/kg
read as **cell-level**): W_E P10/P50/P90 ≈ 675 / 740 / 814 kg at a 700 kg design-weight guess. The closed MTOW
with 172 kg payload is P50 ≈ 948 kg. The top variance contributor is η_c2p (rule C6 warns). D3 (cell- vs
pack-level) remains the highest-leverage open decision.

## Test inventory

`pytest -q`: 175 passed (includes the WPPestimation/eacsize adapter), 4 skipped (the textbook golden examples), 2 deselected (`-m slow`: NUTS-vs-MAP and LOO;
both pass, about 70 s). The 23 seed tests are unchanged and pass. `ruff check src tests` is clean, and
`mypy --strict` (on `src/`) reports no issues.
