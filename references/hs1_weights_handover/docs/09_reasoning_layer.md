# 09 — Reasoning Layer (L3)

The reasoning layer explains and checks predictions. **It never modifies a number in the numeric path.**

## 1. Analog retrieval

- **Feature space:** standardised [log W_dg, log S, A, log q_D, n_ult, seats, powertrain, primary material, gear type, year].
- **Distance:** Mahalanobis, using the fleet covariance. Retrieve the k = 5 nearest calibration-eligible records, plus up to 2 Tier C electric analogs, labelled as such.
- **Component-wise difference explanation.** For each analog, evaluate L1 at the analog's inputs and at HS1's inputs with the same k posterior means. Decompose ΔW_E by component and by driver (n_ult, S, q, bays, DEP, fixed items) using one-at-a-time attribution in a fixed, documented order.
- **Output example:** "HS1 vs. Velis Electro: +38 kg = wing +6 (n_ult), fuselage +9 (bay cutouts), bays +22, DEP nacelles +11, harness +4, fixed −14."

## 2. Consistency rules

Each rule returns `RuleFlag(id, severity, message, values)`.

| ID | Rule | Severity |
|---|---|---|
| C1 | W_E/MTOW outside the [P5, P95] band of the analog cell | warn |
| C2 | Any calibrated component fraction > 2σ from its posterior-predictive class distribution | warn |
| C3 | V_S1 > 59 KCAS or V_S0 > 61 KCAS at predicted MTOW | error |
| C4 | V_C < V_C,min for any basis | error |
| C5 | Pack-level Wh/kg implied > configured cell Wh/kg | error |
| C6 | Any input or default flagged `assumption=True` contributes > 10% of W_E variance (from Sobol) | warn |
| C7 | HS1 feature vector outside the fleet convex hull (leverage > 3× mean) | info: extrapolating |
| C8 | Any criterion with `verify=True` used in a governing load case | info |
| C9 | Battery-bay mass per kWh outside the configured plausibility band | warn |

## 3. Sensitivity

- **Method:** Saltelli sampling with first-order and total Sobol indices for W_E and MTOW, computed over all uncertain inputs (posterior k, bottom-up parameters, geometry tolerances).
- **Report:** top 10 contributors, plus the decision-relevant ones regardless of rank (η_c2p, e_cell, N_bays, n_ult basis).

## 4. LLM boundary

| Permitted | Not permitted |
|---|---|
| Data extraction proposals (doc 04 §6), with mandatory citations and human review | Supplying any value that enters L1/L2 without review |
| Narrative summary of a `WeightReport`, generated from the report JSON only | Altering criteria, priors or rules |
| Drafting rule messages | Deciding pass/fail on checks |
