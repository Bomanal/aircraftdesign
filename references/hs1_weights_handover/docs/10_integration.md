# 10 — Integration with hs1_design

## 1. Closure loop

```
W_dg ← initial guess
repeat:
    env      = certification.envelope(evaluate(b, W_dg, geometry, ...) for b in bases)
    E_pack   = hs1_design.energy_required(mission, aero, W_dg)   # energy-based phase model incl. 30-min reserve
    W_E      = weights.predict(cfg(W_dg, E_pack, env)).empty_weight.p50
    W_dg_new = W_E + payload            # battery inside W_E; no fuel term
until |W_dg_new − W_dg| < 0.1 kg (max 50 iterations; damping 0.5 if oscillating)
```

- **Mission model.** Retain `hs1_design`'s energy-based mission model and the 30-minute reserve penalty. Do not use Breguet.
- **Geometry coupling.** If wing loading is fixed, S scales with W_dg. If S is fixed, check C3 each iteration.
- **Uncertainty.** After point closure on P50, run Monte Carlo closure: each sample closes its own loop with sampled parameters, giving distributions of MTOW and range at fixed payload. Use ≥ 2,000 samples; parallelise with joblib.

## 2. Outputs to `results.json`

Add a `weights` block containing:

- component P10/P50/P90
- W_E and MTOW distributions
- governing basis per load quantity
- minimum stall-limited wing areas
- analogs, rule flags, top Sobol indices
- the unverified-criteria list
- dataset, artefact and code hashes

## 3. CG

Each component carries a station (x, z), from layout input or a default fraction of the fuselage/wing reference, flagged.

Compute CG for these loading cases: empty; MTOW with 2 × 86 kg occupants; MTOW single pilot light; each battery-bay-removed state (quick-swap ground handling and tip-over check).

Report the CG range in %MAC.

## 4. Backward compatibility

The existing Raymer implementation in `hs1_design/` is retained as the `raymer_ga_uncalibrated` method, so earlier results are reproducible. The new module becomes the default only after doc 08 statistical acceptance passes.
