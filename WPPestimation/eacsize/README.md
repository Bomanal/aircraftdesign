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

## Modes and options

| Setting | Options |
|---|---|
| Sizing mode | `fixed_mtom`: the battery takes the mass that is left. `solve_mtom`: the smallest MTOM that meets a range or endurance target (bisection; reports a weight snowball if it fails to converge). |
| Geometry | `constraint`: wing area and power from the W/S–P/W diagram. `fixed`: the user gives wing area, span and power (conversions). `scaled_reference`: the Risen scaled as in risen_eas. |
| Weights | `raymer_calibrated`: Raymer GA class II × composite factors × k_cal fitted to the Risen. `raymer_raw`. `risen_L1`. An OEW override is available for known airframes. |
| Aerodynamics | C_D0 component build-up calibrated to the Risen's 0.0120, or a C_D0 or L/D override. |

## Files

| File | Algorithm step |
|---|---|
| `model.py` | 1: inputs, mission, requirements and the assumption register (with metadata) |
| `physics.py` | 4–6: ISA, geometry, C_D0 build-up, Oswald e, Raymer GA weights, L1 law |
| `performance.py` | 3 and 9: constraint analysis, take-off and landing, ROC, V_max, ceiling |
| `mission.py` | 7 and 9: sortie energy, range and endurance at beginning and end of life |
| `sizing.py` | Calibration, airframe build, mass closure, checks (step 8), result record |
| `presets.py`, `store.py`, `ui.py`, `static/`, `verify.py` | Presets, run storage, UI, verification |

## Known limitations

- Fidelity is first order: a fixed parabolic polar, constant propeller efficiency and no SOC-dependent voltage sag.
- There is no power lapse with altitude, so the service ceiling usually hits the 12 km cap.
- The take-off estimate carries an empirical factor of 1.5. A Raymer-type estimate underpredicts the C172 POH by about 1.6×.
- CG, structural sizing and cost are not modelled. Cost is still available in `risen_eas.py`.
- The Velis Electro cross-check overpredicts its endurance by about 26%. Check absolute range and endurance figures against flight data before relying on them.
