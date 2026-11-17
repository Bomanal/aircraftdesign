# eacsize: electric aircraft conceptual sizing

This package sizes battery-electric light aircraft and estimates their performance from MTOM, payload or seats, and pack Wh/kg. It follows the "Electric Aircraft Sizing Algorithm" (Raymer and Gudmundsson design chain, with the battery-electric method of Hepperle and Riboldi). It is calibrated to the Risen 912iS and uses `risen_eas.py` as the regression reference. It needs only the Python standard library.

## Run

From `WPPestimation/`:

```
python3 -m eacsize                 # web UI at http://127.0.0.1:8765 (opens a browser)
python3 -m eacsize run --preset e750_new_design --set pack_wh_kg=250 assumptions.aspect_ratio=12
python3 -m eacsize presets
python3 -m eacsize verify          # regression vs risen_eas + calibration + cross-checks
```

Every run, from the UI or the CLI, is stored in `runs/`:

- `runs.json` holds the full record of each run: inputs, assumptions, results and chart data.
- `runs.csv` has one column per run, with flattened inputs, assumptions and scalar results as rows.

Use `--data-dir` to keep separate studies apart.

### HS1 weights button (WeightEstimation pipeline)

Every stored run has an **HS1 weights** button: in its row of the *Previous runs* table, and in the header of the
run being shown. Pressing it runs the [WeightEstimation](../../WeightEstimation/README.md) (`hs1_weights`)
pipeline for that run on demand, and shows the result as a separate card on the dashboard. The pipeline:

- builds an HS1 configuration from the run plus an HS1 overlay;
- predicts the component masses with P10/P50/P90, W_E, CG and rule checks;
- compares each component with eacsize's masses;
- re-closes MTOM in the selected **coupling** mode;
- with *Monte Carlo* ticked, repeats the closure per sample.

The **Partial coupling / Full coupling** switch in the *Previous runs* header selects how the closure couples to
eacsize. Your choice is remembered in the browser.

| Mode | What follows the mass in the closure | Card shows |
|---|---|---|
| Partial | Only the mission energy. Wing, power and geometry stay at the eacsize run. *Closure* can be switched off. | Components at the eacsize MTOM; closed MTOW and pack energy |
| Full | eacsize re-sizes the whole aircraft at every iteration: constraint diagram, wing area and span, power, geometry, mission energy. Always closes. | Components at the closed MTOW; a *Closed design* table (eacsize run vs closed: MTOM, wing, power, pack, range, stall, take-off, climb); eacsize's checks at the closed design; convergence history |

In full coupling, eacsize runs once more at the closed MTOW with HS1's empty weight as its OEW override, so its
pack, range and checks describe the closed aircraft. What is re-sized follows the run's own settings: a
`constraint` run re-sizes wing and power, a `fixed` geometry run keeps its wing. Both modes size the pack for the
run's **mission target** fields (range or endurance, end of life, reserve), even when eacsize itself ran at fixed
MTOM. The card says which target it used.

#### Aircraft Structural Config card

eacsize does not model the HS1 detail behind the weight model: DEP layout and power split, motors and inverters,
harness and bus, thermal, quick-swap battery bays, equipment, materials, tail shape and CG stations. In
conceptual design that detail is iterated, so the selected run has an **Aircraft Structural Config** card for
setting it. The card holds 67 parameters in 12 groups, defined in
[WeightEstimation docs/14](../../WeightEstimation/docs/14_aircraft_structure_config.md):

- **Choices:** pick one of the options, e.g. layout `2 wing tractors + aft pusher`, `Single nose tractor`, T-tail,
  Cu/Al conductor, liquid/air cooling, 1–4 bays, and the material per component.
- **Ranges:** set a value inside the allowed range shown beside it.
- **Low–high bands:** set an interval for an uncertain bottom-up quantity, e.g. motor specific power. A band is
  carried as a uniform distribution in the Monte Carlo.
- **Default tags:**
  - `E`: centred on this eacsize run (tail arm, lift slope from its aspect ratio);
  - `S`: an HS1 reference placeholder scaled to this aircraft;
  - `P`: an HS1 reference placeholder.
- **Highlighting:** changed values are highlighted with their default alongside. Parameters that don't apply
  (e.g. wing-motor settings for a nose tractor) are dimmed.
- **Fractions of the fuselage length:** stations and lengths are stored this way, so in full coupling they
  follow eacsize as it re-sizes the aircraft.

**Submit & run HS1 iterations** validates the selection and saves it as `runs/hs1_weights/<run id>.structure.json`.
It then runs the HS1 pipeline in the current Partial/Full coupling mode. *Save* stores the selection without
running. *Reset to defaults* restores the defaults in the card; they are stored the next time you save.
Invalid values are listed by parameter and nothing is saved. Every HS1 run for that run id uses the saved
selection, and the HS1 card says which input was used: the saved selection (with its hash), or the default
overlay when no selection is saved.

Press the button again (*Re-run HS1*) to refresh a card. *Hide* removes it from the page. Partial and full results
are kept side by side, one card each, and reappear when you select their run.

- A run takes about 3 s (partial, closure) to about 6 s (either mode with Monte Carlo).
- Results are stored as `runs/hs1_weights/<run id>.json` (partial) and `<run id>.full.json` (full). The
  structural config is stored as `<run id>.structure.json`. All three are git-ignored, and `runs.json` and
  `runs.csv` are not changed. Deleting a run also deletes its HS1 results and its structural config.
- The pipeline runs in a subprocess with `WeightEstimation/.venv/bin/python`, so eacsize itself still needs only
  the standard library. Override the defaults with the environment variables `HS1_DIR` (WeightEstimation
  directory), `HS1_OVERLAY` (overlay JSON, default `WeightEstimation/examples/eacsize_overlay.json`, which holds
  illustrative placeholders) and `HS1_PYTHON` (interpreter).
- **Fleet calibration.** Every HS1 run uses the newest calibration artefact in `WeightEstimation/artefacts`
  (`calibration_<date>_<hash>.nc`, written by `hs1-weights calibrate`). The newest is chosen by the date in the
  file name, then by file time, and is looked up again at each run, so a new calibration applies without
  restarting the server. The history card shows which artefact is in use. A result card names the artefact it
  was computed with, and flags it when a newer one exists. `HS1_CALIBRATION` overrides the default: a directory
  (its newest artefact), one artefact file (runs are refused if that file is missing), or `none` for the
  uncalibrated priors. To refresh the calibration after editing the fleet data:

  ```bash
  cd WeightEstimation
  .venv/bin/python -m hs1_weights build-dataset --fleet-csv data/fleet/fleet_clusters.csv --out build
  .venv/bin/python -m hs1_weights calibrate --dataset build --engine nuts --out artefacts
  ```
- API:
  - `POST /api/runs/<id>/hs1` with body `{"coupling": "full", "close": true, "mc": true}`;
  - `GET` and `DELETE` on the same path, with `?coupling=partial|full`;
  - `GET /api/hs1` for status;
  - `GET`, `POST` (`{"selection": {...}}`) and `DELETE` on `/api/runs/<id>/structure-config` for the structural
    config. The catalogue module is standard-library only and is loaded directly, so eacsize keeps no
    third-party dependency.

## Modes and options

| Setting | Options |
|---|---|
| Sizing mode | `fixed_mtom`: the battery takes the mass that is left. `solve_mtom`: the smallest MTOM that meets a range or endurance target (bisection; reports a weight snowball if it fails to converge). |
| Geometry | `constraint`: wing area and power from the W/S–P/W diagram. `fixed`: the user gives wing area, span and power (conversions). `scaled_reference`: the Risen scaled as in risen_eas. |
| Weights | `raymer_calibrated` (default), `raymer_raw`, `roskam_calibrated`, `roskam_raw`, `electric_regression`, `risen_L1`. See [Weight methods](#weight-methods). An OEW override is available for known airframes. |
| Aerodynamics | C_D0 component build-up calibrated to the Risen's 0.0120, or a C_D0 or L/D override. |

## Weight methods

All methods estimate the airframe: OEW excluding the e-powertrain, propeller and battery. The e-powertrain (P / 2.5 kW/kg) and propeller (0.73·P^0.7) are added the same way for every method.

| Method | Source | What it responds to | Notes |
|---|---|---|---|
| `raymer_*` | Raymer, *Aircraft Design*, Ch.15 GA equations | S, AR, t/c, taper, q, N_z, fuselage wetted area and fineness, tail areas, gear length and type | Composite factors on structure. Raymer systems for both class II families. |
| `roskam_*` | Roskam, *Airplane Design* Part V: Cessna class II wing, tails and low-wing fuselage, plus USAF landing gear | W, S, AR, N_z, tail areas and root thickness, fuselage length and perimeter, seats, gear length | Fitted to metal Cessnas (AR ≈ 7, below about 370 km/h). Raw values are about 37% heavier than Raymer for the Risen, mostly from the AR^1.71 wing term. Systems use Raymer's equations; the Cessna systems coefficients were not verified. The horizontal-tail AR exponent is 0.138, per Roskam (one reproduction prints 0.101). |
| `*_calibrated` | Either family × k_struct, k_sys | As above | k_struct and k_sys make the method return 190 kg structure and 45 kg systems for the Risen (280 kg OEW excl. battery). They are recomputed each run from your current assumptions. |
| `electric_regression` | Log-linear fit to flown electric aircraft in [`data/electric_aircraft.json`](data/electric_aircraft.json), with sources | MTOM only (all included aircraft are two-seaters) | Default fit over 6 aircraft (Velis, Alpha Electro, E430, eFusion, Elektra Trainer, eDA40), 470–1,310 kg: m = 0.0873·MTOM^1.233, R² 0.85, rms error 17%. Three battery masses are estimated from kWh at 170 Wh/kg. The exponent depends on the eDA40: without it b = 1.90. Use `regression_fixed_exponent` (b = 1 gives a constant 39% fraction, R² 0.82) when extrapolating. A check warns beyond ±15% of the data range. |
| `risen_L1` | `risen_eas.py` scaled-fraction law | MTOM, S, four-seat step | Used for the regression against risen_eas |

To add a method, return a dict of group name to kg from a function in `physics.py` (or a module like `regression.py`). Then add the name to the `weight_method` options in `model.py`, add a branch in step 6 of `sizing.build_airframe`, and add a check in `verify.py`. The UI, JSON and CSV pick it up automatically.

## Files

| File | Algorithm step |
|---|---|
| `model.py` | 1: inputs, mission, requirements and the assumption register (with metadata) |
| `physics.py` | 4–6: ISA, geometry, C_D0 build-up, Oswald e, Raymer and Roskam/Cessna class II weights, L1 law |
| `regression.py`, `data/electric_aircraft.json` | 6: electric-aircraft mass regression and its sourced dataset |
| `performance.py` | 3 and 9: constraint analysis, take-off and landing, ROC, V_max, ceiling |
| `mission.py` | 7 and 9: sortie energy, range and endurance at beginning and end of life |
| `sizing.py` | Calibration, airframe build, mass closure, checks (step 8), result record |
| `presets.py`, `store.py`, `ui.py`, `static/`, `verify.py` | Presets, run storage, UI, verification |
| `hs1_bridge.py` | On-demand WeightEstimation (HS1 weights) pipeline for a stored run, used by the UI |

## Known limitations

- Fidelity is first order: a fixed parabolic polar, constant propeller efficiency and no SOC-dependent voltage sag.
- There is no power lapse with altitude, so the service ceiling usually hits the 12 km cap.
- The take-off estimate carries an empirical factor of 1.5. A Raymer-type estimate underpredicts the C172 POH by about 1.6×.
- CG, structural sizing and cost are not modelled. Cost is still available in `risen_eas.py`.
- The Velis Electro cross-check overpredicts its endurance by about 26%. Check absolute range and endurance figures against flight data before relying on them.
