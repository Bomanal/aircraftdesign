# 01 — Requirements

## 1. Problem statement

Aircraft-level regressions on fleet data (e.g., log W_E vs log W_TO) give ±10–15% on empty weight for conventional GA aircraft. They also fail to extrapolate to electric, DEP and swappable-battery configurations.

HS1's design closure is weight-limited. A ~700 kg MTOW with a ~60 kWh pack is the current design point. An empty-weight error of 5% moves usable battery mass by roughly 35 kg, and therefore range by roughly 15–20%.

A calibrated, explainable, uncertainty-aware weight model is a prerequisite for credible sizing.

## 2. Goals

| ID | Goal | Measure |
|---|---|---|
| G1 | Predict component masses and empty weight for a parametric configuration | Component tree of doc 06 fully populated |
| G2 | Calibrated accuracy on held-out conventional aircraft | Empty-weight MAPE ≤ 7% (in-class LOO) |
| G3 | Honest extrapolation to electric aircraft | LOCO on electric class: MAPE ≤ 15%, and 90% predictive interval coverage ≥ 80% |
| G4 | Certification-consistent loads | All load-dependent equations take inputs from the dual-basis envelope (doc 03) |
| G5 | Integration with `hs1_design/` | Drop-in weights module in the MTOW closure loop; Monte Carlo MTOW and range distributions |
| G6 | Explainability | Every prediction ships with analog comparison, governing load case per component, and variance attribution |

## 3. Non-goals

- **Detailed structural sizing (FEM).** This is a conceptual/preliminary module. FEM-derived masses can be injected as overrides.
- **Certification compliance demonstration.** Criteria are used to set design loads only.
- **Cost estimation.** Out of scope; the interface leaves room for it.
- **Rotorcraft, eVTOL, jets.** The fleet scope is fixed-wing 2–10 seats. Turbojet/turbofan types (e.g., SF50) are excluded from calibration.

## 4. Requirements

### P0 — Must-have

| ID | Requirement | Acceptance criterion |
|---|---|---|
| R1 | Fleet database with field-level provenance (doc 04) | ≥ 60 Tier A/B records in Phase 1; schema validation passes; 100% of numeric fields carry a source id |
| R2 | Normalization routines N1–N12 (doc 05) | Unit tests per routine; normalized dataset reproducible from raw + code |
| R3 | Dual-basis certification envelope (doc 03) | Seed tests pass; governing basis reported per load quantity |
| R4 | Component library: Raymer GA, Torenbeek, Cessna/USAF (Roskam Pt V), Gudmundsson as swappable methods | Each method reproduces its textbook worked example to ≤ 1% |
| R5 | Electric bottom-up models: battery pack, motors, inverters, thermal, HV harness, quick-swap bays, DEP nacelles | Each parametrised; HS1 defaults documented; property tests pass |
| R6 | Hierarchical Bayesian calibration (doc 07) | Posterior predictive checks pass; R-hat < 1.01; LOO/LOCO metrics reported |
| R7 | Public API `predict(config) -> WeightReport` (doc 02 §5) | Pydantic-validated I/O; JSON-serialisable report |
| R8 | Uncertainty propagation | Monte Carlo ≥ 2,000 samples; report P10/P50/P90 for each component, W_E and MTOW |
| R9 | Stall-limit coupling | Report minimum wing area for V_S1 ≤ 59 KCAS and V_S0 ≤ 61 KCAS at predicted MTOW |

### P1 — Should-have

| ID | Requirement |
|---|---|
| R10 | GP residual model on log W_E (doc 07 §6) |
| R11 | Analog retrieval and component-wise difference explanation (doc 09) |
| R12 | Sobol variance attribution of W_E and MTOW to inputs |
| R13 | CG computation from component masses and stations |
| R14 | LLM-assisted extraction pipeline with human-review queue (doc 04 §6) |

### P2 — Future

FEM override import; cost model hooks; CS-23 (EASA) basis as a third option; hybrid-electric powertrain models.

## 5. Accuracy targets (empty weight)

| Population | Target MAPE | Interval requirement |
|---|---|---|
| Conventional metal, in-distribution | ≤ 5–7% | 90% PI coverage 85–95% |
| Composite 2-seaters | ≤ 7–10% | same |
| Electric / DEP (LOCO) | ≤ 10–15% | 90% PI coverage ≥ 80%. **Do not narrow intervals to hit MAPE.** |

## 6. Users

- **Design engineer:** runs trade studies through `hs1_design/`.
- **Analyst:** curates fleet data and runs calibration.
- **Reviewer:** audits a prediction via its report.
