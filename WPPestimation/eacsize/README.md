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

## Known limitations

- Fidelity is first order: a fixed parabolic polar, constant propeller efficiency and no SOC-dependent voltage sag.
- There is no power lapse with altitude, so the service ceiling usually hits the 12 km cap.
- The take-off estimate carries an empirical factor of 1.5. A Raymer-type estimate underpredicts the C172 POH by about 1.6×.
- CG, structural sizing and cost are not modelled. Cost is still available in `risen_eas.py`.
- The Velis Electro cross-check overpredicts its endurance by about 26%. Check absolute range and endurance figures against flight data before relying on them.
