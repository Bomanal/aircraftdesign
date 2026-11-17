# aircraftdesign
Tools for conceptual design of aircraft.

## Summary

This repository holds conceptual and preliminary design tools for battery-electric light aircraft, benchmarked on the Porto Aviation Risen. You give it MTOM, payload or seats, and battery specific energy. It sizes the aircraft and estimates its performance using the Raymer and Gudmundsson design chain with battery-electric mass closure. The variable assumptions (battery Wh/kg, L/D, structural weight method, efficiencies and so on) are listed with defaults, ranges and sources so they can be varied in experiments. Results are checked against the earlier `risen_eas.py` study.

## Contents

| Path | What it is |
|---|---|
| [`WPPestimation/eacsize/`](WPPestimation/eacsize/) | Electric aircraft sizing module with a local web UI. It covers the constraint diagram, geometry, drag build-up, class-II weights (Raymer or Roskam/Cessna, calibrated to the Risen) or a regression on flown electric aircraft, mission energy, fixed- or solved-MTOM closure and feasibility checks. Runs are saved to `runs.json` and `runs.csv`. See its [README](WPPestimation/eacsize/README.md). |
| [`WPPestimation/risen_eas.py`](WPPestimation/risen_eas.py) | Earlier weight, performance and operating-cost estimator scaled from the Risen 912iS. It is the regression reference for `eacsize`. |
| `WPPestimation/*.pdf` | The method document and the Risen electric configurations study that `risen_eas.py` implements |
| [`WPPestimation/runs/`](WPPestimation/runs/) | Example runs: Risen E600, E750 new design, four-seat solved MTOM, E-Hansa 950 kg, Velis Electro check |

## Quick start

Requires Python 3.9 or later. Only the standard library is used.

```
cd WPPestimation
python3 -m eacsize            # opens the UI at http://127.0.0.1:8765
python3 -m eacsize verify     # regression vs risen_eas and calibration checks
python3 -m eacsize run --preset e750_new_design --set pack_wh_kg=250
python3 risen_eas.py          # original Risen cost and performance report
```

## Fidelity

These are first-order conceptual estimates: a parabolic drag polar, constant propeller efficiency and constant pack specific energy. Treat absolute range and endurance as roughly ±25%; the trends between configurations are more reliable. Known limitations are listed in the [eacsize README](WPPestimation/eacsize/README.md#known-limitations).
