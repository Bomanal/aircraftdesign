# WeightEstimation — HS1 weight-prediction module (`hs1_weights`)

**Version:** 0.2.0 · **Date:** 2026-11-03 · **Owner:** Dr. Mallesh Bommanahal · built from the handover package in
`references/hs1_weights_handover/`

This module predicts component masses, empty weight, MTOW and CG for **HS1**, with calibrated uncertainty. HS1 is
a two-seat, low-wing, battery-electric aircraft with DEP (two wing tractors and an aft pusher) and quick-swap
battery bays. The module combines two things:

1. **Physics-based / semi-empirical component models** (L1): Raymer GA, plus Torenbeek and Cessna/USAF
   (Roskam Pt V) for benchmarking, and bottom-up electric, DEP and quick-swap models.
2. **Technology factors k_i** (L2), learned by hierarchical Bayesian calibration on a provenance-tracked GA fleet
   database. Fleet data calibrates the physics; it does not replace it.

Loads come only from the dual-basis certification envelope (ASTM F2245 / MOSAIC and FAR 23 Level 1 via DGCA).

> **Status:** code for all five build phases exists and is tested. **There is no fleet data yet.** P1-5 needs
> sourced, human-reviewed TCDS/POH records. Until a calibration artefact exists, `predict()` draws k from the
> doc 07 priors and says so in the report. Phase-by-phase acceptance: [`docs/13_build_status.md`](docs/13_build_status.md).
> Spec conflicts and the provisional resolutions adopted: [`docs/12_open_items_and_risks.md` §4](docs/12_open_items_and_risks.md).

## Quick start

```bash
cd WeightEstimation
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,gp]"      # add ,bayes for numpyro NUTS + .nc artefacts
.venv/bin/pytest -q                                                 # 168 passed, 4 skipped (textbook goldens)
.venv/bin/pytest -q -m slow                                         # NUTS vs MAP agreement, LOO harness
.venv/bin/python -m hs1_weights predict examples/hs1_reference.json # illustrative HS1 run
.venv/bin/python -m hs1_weights close examples/hs1_reference.json --payload 172 --energy-wh 60000 --mc
.venv/bin/python -m hs1_weights.run_cert_envelope                   # seed demo: dual-basis load envelope
```

```python
from hs1_weights.config import load_config
from hs1_weights.api import predict
from hs1_weights.closure import close_point

cfg = load_config("examples/hs1_reference.json")
rep = predict(cfg, n_samples=2000, seed=0)            # WeightReport (Pydantic, JSON-serialisable)
rep.empty_weight.p50, rep.components["battery_bays"].governing_load_case, rep.rule_flags

# Closure with hs1_design: energy_required(W_dg_kg) -> Wh, from its energy-based mission model
res = close_point(cfg, energy_required=lambda w: 60e3, payload_kg=172.0)
```

## Running a configuration sized in WPPestimation (eacsize)

eacsize gives the first-order sizing: MTOM, wing, fuselage, tails, installed power, pack energy and pack Wh/kg.
hs1_weights adds HS1-specific detail on top: DEP split, quick-swap bays, equipment, materials, CG stations, tail
shape, and the bottom-up priors. It then predicts masses with uncertainty, and can re-close MTOM using
**eacsize's own mission model** as the energy function.

1. **Size and save the run in eacsize** (UI "save run", or `python3 -m eacsize run ...`). It lands in
   `WPPestimation/runs/runs.json` with an id such as `R0003`.
2. **Write an HS1 overlay** for what eacsize does not model. Copy `examples/eacsize_overlay.json` and edit it:
   - `powertrain.motors[]`: one entry per motor, with `power_share` (fractions summing to 1, applied to eacsize's
     installed power), location and `y_m`, specific powers, prop and fairing masses, and station;
   - `powertrain.harness`, `thermal`, `lv_system_kg`, `c_mount_kg_per_kn`, `pack.heat_kw`;
   - `battery_bays`, `equipment`, `material_by_component`;
   - tail aspect ratio, taper, sweep and t/c; wing sweep, lift slope and `x_lemac_m`; fuselage
     `structural_length_m`;
   - `stations` (absolute x/z in m: check them against the eacsize fuselage length), `seat_station`,
     `light_pilot_kg`.

   Any field you set that eacsize also supplies **overrides** it, and the override is logged. Set `vc_kt` here if
   your structural design cruise speed differs from the eacsize mission cruise speed. eacsize's cruise speed is
   used by default, and rule C4 flags it if it falls below V_C,min.
3. **Run:**

   ```bash
   cd WeightEstimation
   .venv/bin/python -m hs1_weights from-eacsize --run R0003 --overlay my_overlay.json \
       --close --mc --write-config my_hs1_config.json --out my_report.json
   ```

   This prints the `WeightReport` table and an **eacsize vs hs1_weights** component comparison. With `--close` it
   adds the MTOW closure using eacsize's mission energy for the run's mission, reserve, DoD, end-of-life SoH,
   power and C-rate, with geometry and installed power held at the run's values. `--mc` adds the Monte Carlo
   closure, and reports samples that snowball instead of dropping them. `--write-config` saves the merged
   `HS1Config`, so you can edit it and re-run with `hs1-weights predict my_hs1_config.json`. Defaults:
   `--runs ../WPPestimation/runs/runs.json`, `--wpp ../WPPestimation`.

   ```python
   import json
   from hs1_weights.adapters import eacsize as ea
   from hs1_weights.api import predict
   from hs1_weights.closure import close_point
   run = ea.load_run("../WPPestimation/runs/runs.json", "R0003")
   cfg = ea.config_from_eacsize(run, json.load(open("my_overlay.json")))
   rep = predict(cfg)                                                    # masses, CG, rules, Sobol ...
   res = close_point(cfg, ea.energy_fn_from_eacsize(run, "../WPPestimation"), cfg.payload_kg)
   ```

What carries over and how is documented in the `adapters/eacsize.py` docstring, and it is recorded per run in
`report.assumptions` ("input provenance: ..."). eacsize's `pack_wh_kg` is pack-level, so for these runs the
pack is modelled with η_c2p = 1. Example (R0003, illustrative overlay, prior k): W_E P50 647 kg against eacsize's
570 kg at 750 kg. Re-closed with eacsize's mission energy, MTOW is 841 kg (P10–P90 744–998 kg) and the pack is
77.6 kWh.

## Workflow

```
data/sources.csv + data/records/*.json          (sourced, reviewed; data/review.py for the extraction queue)
   └─ hs1-weights build-dataset                 N1–N12 → build/normalized.parquet + log + content hash
        └─ hs1-weights calibrate --engine auto  NUTS (numpyro) or MAP/Laplace → artefacts/calibration_<date>_<hash>.nc
             └─ hs1-weights validate            LOO / LOCO / raw-method benchmark vs doc 08 §5 thresholds
HS1Config ─ hs1-weights predict --calibration <artefact> --fleet build
          ─ closure.close_point / close_mc      MTOW closure inside hs1_design
```

## What a `WeightReport` contains

- P10/P50/P90, mean and sd for every component, W_E and MTOW (≥ 2,000 Monte Carlo samples). Each component also
  reports its method, its governing load case, whether it is calibrated (with its k distribution), and whether
  its equation transcription has been verified against the source text.
- The envelope, with the governing basis for every load quantity. Minimum wing areas for V_S1 ≤ 59 KCAS and
  V_S0 ≤ 61 KCAS at the predicted MTOW, using power-idle (unblown) C_L,max.
- CG for the doc 10 loading cases (empty; 2 × 86 kg; single light pilot; each bay removed), in metres and %MAC,
  with a tip-over fraction.
- Sobol total indices (top 10 plus η_c2p and e_cell), the N_bays sensitivity, and the ASTM-vs-FAR23 cert-group
  sensitivity (D6).
- Analogs with component-wise Δ explanations, and rule flags C1–C9.
- `assumptions` (every default applied, every placeholder prior), `unverified_criteria`, and `versions` (code,
  git, method registry, dataset and artefact hashes, seed).

## Layout

```
src/hs1_weights/
  units.py  errors.py  schema.py  certification.py (seed)  config.py (Pydantic I/O)
  data/        io.py  normalize.py (N1–N12)  derive.py  dataset.py  review.py
  components/  base.py (protocol, registry, ModelContext)  tree.py  raymer_ga.py  torenbeek.py*  roskam_cessna.py*
               gudmundsson.py (slot)  electric.py  dep.py  battery_bays.py  fixed_items.py
  calibration/ fleet.py  structure.py (shared density)  model.py (numpyro)  ridge_fallback.py (MAP/Laplace)
               posterior.py  fit.py (artefacts)  gp_residual.py  validate.py  simulate.py (SYNTHETIC test fleets)
  reasoning/   analogs.py  rules.py (C1–C9)  sensitivity.py (Sobol)
  mc.py  cg.py  api.py  closure.py  cli.py
tests/         one file per layer; golden/ holds the textbook worked examples to be filled from the project texts
examples/      hs1_reference.json (ILLUSTRATIVE; every placeholder flagged assumption=true)
schemas/       JSON schemas of HS1Config and WeightReport
docs/          01–12 specification (handover), 13 build status
```
`*` Registered with `transcription_verified=False` until checked against the project's copies (SC-10).

## Non-negotiables (from CLAUDE.md, enforced in code)

- SI internally; imperial textbook cores sit behind tested wrappers.
- No fleet value without a registered `Source` (tier, citation, access date). Tier C is never calibrated.
- Certification criteria values are unchanged from the seed; `verify=True` items are listed in every report.
- A missing input raises `MissingInputError` naming the field. Only spec-listed defaults are used, and each is logged.
- Battery-electric: W_l = MTOW, no wing-fuel relief (the W_fw ≥ 1 lb override is applied), power-idle stall,
  18 g forward ESS/mount retention, and MTOW censoring for capped LSA/UL fleet records.
- The numeric path is deterministic given `seed`. No LLM output enters a calculation.
