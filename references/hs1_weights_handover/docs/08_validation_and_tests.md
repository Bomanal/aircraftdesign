# 08 — Validation and Test Plan

## 1. Test pyramid

| Level | Scope | Tooling |
|---|---|---|
| Unit | Each equation, conversion and routine | pytest |
| Property | Monotonicity, units, bounds, invariances | hypothesis |
| Golden | Textbook worked examples; pinned reference cases | pytest with stored JSON |
| Statistical | LOO, LOCO, interval coverage, benchmark vs. raw methods | pytest (slow mark) + report notebook |
| Integration | `predict()` inside the `hs1_design` closure loop | pytest (slow) |

**CI:** unit + property + golden on every commit (< 60 s). Statistical and integration tests run nightly or on demand.

## 2. Seed tests (23, passing)

- `tests/test_certification.py`: n-formula, V_C coefficient equivalence, gust formula, stall/area inverse, sink clamp, envelope governing bases, gust-governs-at-600-kg, ultimate = 1.5 × limit.
- `tests/test_normalize.py`: nearest-cap detection, licensed→basic conversion, tier exclusion, censoring, prototype σ, electric warning.

## 3. Hand-calculated checks to add (examples)

| Check | Expected |
|---|---|
| FAR 23 n₁ at 700 kg | formula 4.179 → capped **3.80** |
| FAR 23 n₁ at 5,000 kg | 2.1 + 24000/(11023.1 + 10000) = **3.242** |
| V_C,min at W/S = 13.0 psf | 33·√13 = **118.98 kt** |
| Torenbeek S_wet,f with L = 6.5 m, W = 1.2 m, H = 1.3 m | compute by hand in the test docstring; tolerance 1e-9 |
| Raymer wing, imperial core | reproduce Raymer's GA worked example (from the project's copy); tolerance 1% |
| Electric W_fw override | W_fw = 0 → finite wing mass equal to the W_fw = 1 lb result |
| Inertia relief | Zero DEP masses → factor exactly 1.0 |
| Battery pack | E = 60 kWh, e_cell = 300 Wh/kg, η = 0.625 → **320 kg** |
| HV harness | I = 100 A, J = 4 A/mm², L = 2 m, Cu, f_ins = 1 → A = 25 mm², m = **0.448 kg** |

## 4. Property tests (hypothesis)

- Every structural component mass is non-decreasing in n_ult, W_dg, S (for the relevant component), and q.
- m_bays is strictly increasing in N_bays at constant E.
- Unit round-trip: SI → imperial → SI is identity to 1e-12.
- Envelope n_ult⁺ ≥ each basis's n_ult⁺.
- Normalization is idempotent: N(N(x)) = N(x).

## 5. Statistical validation

| Protocol | Definition | Pass criterion |
|---|---|---|
| In-class LOO | Remove one record, refit (MAP fallback acceptable for speed), predict | Metal MAPE ≤ 7%; composite ≤ 10% |
| **LOCO electric** | Remove all electric records, fit, predict them | MAPE ≤ 15%; 90% PI coverage ≥ 80% |
| LOCO composite 2-seat | Remove the class, fit, predict | Report; informs priors |
| Benchmark | Raw Raymer GA, raw Cessna (Roskam), raw Gudmundsson vs. calibrated, same records | Calibrated beats best raw method on in-class MAPE by ≥ 30% relative |
| Calibration of intervals | PIT histogram; coverage at 50/80/90% | Coverage within ±7 points of nominal (in-class) |
| Posterior predictive check | Simulated vs. observed W_E/MTOW distributions by cell | No systematic cell bias > 1σ |

**Velis Electro anchor.** The calibrated model must predict the Velis Electro empty weight within its 90% PI when Velis is held out. This complements the existing `hs1_design` endurance validation, which reproduces the ~50 min endurance from the 24.8 kWh pack.

## 6. Reproducibility

Given the dataset hash, code version and seed, calibration results reproduce posterior means to within MCMC error, and the MAP fallback reproduces exactly.
