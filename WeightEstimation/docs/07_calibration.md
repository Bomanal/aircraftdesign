# 07 — Calibration (L2)

## 1. Objective

Learn technology factors k_i, by component and group, that map physics/semi-empirical predictions f_i onto observed fleet empty weights. Also learn a model-error term, so that predictive intervals are honest.

## 2. Groups (hierarchy)

| Level | Factor | Levels |
|---|---|---|
| g1 | Material class of the component | carbon, glass, composite-mixed, aluminium, steel-tube-fabric, wood |
| g2 | Certification basis | FAR23 normal/utility/aerobatic, CS-VLA, ASTM F2245, EU UL, experimental |
| g3 | Era | < 1980, 1980–2005, > 2005 |
| g4 | Manufacturer | random effect, only for manufacturers with ≥ 3 types |
| — | Powertrain group | piston, diesel, turboprop, electric. Affects aggregate W_E (fixed-items, systems), **not** structural k_i |

## 3. Model

### 3.1 Aircraft-level likelihood (all eligible records)

For aircraft j with calibrated components C and uncalibrated/fixed terms U_j (fleet: installed engine for the ICE fleet if not calibrated, equipment where known):

- log W_E,obs,j ~ Normal( log( Σ_{i∈C} k_{i,j} · f_{i,j} + U_j ), √(σ_meas,j² + σ_model²) )
- k_{i,j} = exp( θ_i + α_{i,g1(j)} + β_{g2(j)} + γ_{g3(j)} + δ_{g4(j)} )

σ_meas,j comes from N9 (relative σ on the log scale).

### 3.2 Component-level likelihood (records with published breakdowns)

log m_{i,j,obs} ~ Normal( log(k_{i,j} · f_{i,j}), √(σ_meas,i,j² + σ_comp,i²) )

This term identifies individual θ_i. Without it, wing and fuselage factors are confounded.

### 3.3 Censored-MTOW records

For records with `design_weight_kg=None` (N5): W_dg,j is a latent parameter.

W_dg,j = MTOW_j · (1 + u_j), u_j ~ HalfNormal(0.10), truncated at 0.20.

f_{i,j} is evaluated at W_dg,j inside the model. This requires L1 to be JAX-traceable for these components, or alternatively pre-tabulated on a grid of u and interpolated.

### 3.4 Priors

| Parameter | Prior | Basis |
|---|---|---|
| θ_wing (aluminium baseline) | N(0, 0.15) | Raymer GA eqs are calibrated on metal GA aircraft |
| α_wing,composite | N(log 0.85, 0.10) | Raymer composite factor ~0.85 (wing) |
| α_tail,composite | N(log 0.83, 0.10) | ~0.83 (tails) |
| α_fus,composite | N(log 0.90, 0.10) | ~0.90 (fuselage) |
| α_gear,composite | N(log 0.95, 0.10) | ~0.95 (gear) |
| α (wood, steel-tube) | N(0, 0.20) | weakly informative |
| β, γ | N(0, 0.10) | partial pooling |
| δ (manufacturer) | N(0, τ_m), τ_m ~ HalfNormal(0.05) | |
| σ_model | HalfNormal(0.08) | |
| σ_comp,i | HalfNormal(0.15) | |

The Raymer composite factors are derived largely from advanced military composite practice. They are used here as **priors only**; the posterior decides.

## 4. Identifiability

- **Required:** ≥ 20 records with component breakdowns spanning at least two material classes, before reporting component-level k_i as meaningful.
- **Diagnostics:** posterior correlation matrix of θ. Flag |ρ| > 0.8 pairs in the calibration report.
- **If unidentified:** collapse to a shared structural factor k_struct and report that collapse in the artefact metadata.

## 5. Inference

| Path | Engine |
|---|---|
| Primary | numpyro NUTS, 4 chains × 1,000 warm-up × 1,000 samples, `seed` exposed |
| Fallback (`ridge_fallback.py`) | MAP via scipy L-BFGS on the same log-posterior, Laplace approximation for uncertainty. Used in CI and when JAX is unavailable. |

Diagnostics: R-hat < 1.01; ESS > 400 per parameter; no divergences (else raise target_accept to 0.95 and report).

**Artefact:** NetCDF (arviz InferenceData) including the dataset hash, code version, method registry, and prior spec.

## 6. GP residual model (P1)

- **Target:** r_j = log W_E,obs,j − log(posterior-mean prediction).
- **Features (standardised):** log W_dg, log S, A, log q_D, n_ult, seats, powertrain one-hot, material one-hot, year.
- **Kernel:** ARD Matern-5/2 + white noise. Fit with scikit-learn or GPyTorch.
- **Use:** the GP mean adds to the log prediction; the GP variance adds to the predictive variance.
- **Guard:** if LOCO on electric shows the GP worsens MAPE or interval coverage, disable it for out-of-class predictions. A flag in the config controls this.

## 7. Predictive distribution for HS1

1. Determine HS1's groups: carbon/glass composite (per component), basis = envelope (use the ASTM_F2245 group level as the closest structural analog, and report sensitivity to the FAR23 level), era > 2005, manufacturer = new (draw δ from its hyperprior).
2. Draw posterior samples of θ, α, β, γ, δ and σ_model.
3. Combine with Monte Carlo samples of the bottom-up parameters (doc 06 §5).
