# 05 — Normalization Routines

Module: `hs1_weights/data/normalize.py`. The seed implements N3 (partial), N5 and N9.

Each routine is a pure function `(record, context) -> (record', log_entries)`. Routines run in the order N1 → N12. Every routine appends to `normalization_log.jsonl` with the routine id, field, before/after values, and rule applied.

| ID | Routine | Status |
|---|---|---|
| N1 | Units and airspeed conventions | to build |
| N2 | Variant resolution and de-duplication | to build |
| N3 | Empty-weight definition → basic empty (GAMA) | partial |
| N4 | Fuel and oil quantities | to build |
| N5 | MTOW censoring and design weight | done |
| N6 | Load-factor and certification inference | to build |
| N7 | Design speeds (V_C, V_D, q) | to build |
| N8 | Geometry derivation | to build |
| N9 | Measurement variance model | done |
| N10 | Outlier screening | to build |
| N11 | Electric-specific normalization | partial |
| N12 | Calibration eligibility and dataset freeze | to build |

## N1 — Units and airspeed conventions

- Convert to SI. Constants are in `units.py`: lb = 0.45359237 kg; US gal = 3.785411784 L; kt = 0.514444 m/s; mph = 0.44704 m/s; ft = 0.3048 m.
- Airspeeds are stored as KCAS where published. If only KIAS is published, store it as-is with `note="IAS"`. Treat the IAS–CAS difference as negligible for calibration, but flag it.
- If km/h vs. mph is ambiguous (common in European brochures), resolve it with a consistency check against stall/cruise plausibility. If it remains ambiguous, set `confidence ≤ 0.5`.

## N2 — Variant resolution and de-duplication

- Key: (manufacturer, TCDS model, MTOW). A different MTOW means a different record, even within the same TCDS model (e.g., C172 S vs. R).
- Duplicate sources for the same field are kept as a list. The selected value is the highest tier, then the latest date, then the highest confidence. Inter-source spread > 3% raises a warning and widens σ (N9) by the observed spread.

## N3 — Empty-weight definition → basic empty weight (GAMA)

Target definition: **basic empty weight per GAMA (post-1975)** = airframe + powerplant + fixed equipment + unusable fuel + full engine oil + full operating fluids. For electric aircraft, the installed battery pack is included.

| Source definition | Conversion | σ add (N9) |
|---|---|---|
| BASIC_GAMA | none | 0 |
| LICENSED (no oil) | + oil_capacity_L × 0.88 kg/L. If oil capacity is unknown, do not correct. | 1% (3% if uncorrected) |
| STANDARD_NO_OPTIONS | none (optional equipment rarely itemised) | 3% |
| MARKETING ("from") | none | 6% |
| UNKNOWN | none | 3% |
| Dry / excludes unusable fuel | + unusable fuel (TCDS value; else 3% of total capacity × fuel density) | 1% |

Fuel densities: avgas 0.72 kg/L, Jet-A 0.80 kg/L, mogas 0.74 kg/L.

## N4 — Fuel and oil quantities

Store total and usable fuel. If only one is given, record the other as missing (do not infer). Oil capacity comes from the POH or the engine manual.

## N5 — MTOW censoring and design weight (implemented)

- Detect the nearest regulatory cap within ±1.5 kg from `REGULATORY_CAPS` (EU UL 300/450/472.5/495/525/560/600; US LSA 598.7 and 648.6 (pre-MOSAIC); CS-LSA 600/650; CS-VLA 750).
- If censored: `mtow_censored=True` and `design_weight_kg=None`. Calibration then uses W_dg from TCDS structural data if available. Otherwise, the record is used **only** in the W_E likelihood with W_dg treated as an uncertain parameter, with a lower bound of MTOW and a weak prior up to +20% (doc 07 §3.3).
- Category aircraft (ASTM F2245, EU UL, CS-VLA) whose MTOW is **not** at a cap raise a warning: check variant and market.

## N6 — Load-factor and certification inference

If n limits are not published, infer them from the certification tag:

| Tag | n⁺ | n⁻ |
|---|---|---|
| FAR23 normal | min(3.8, 2.1 + 24000/(W_lb + 10000)) | −0.4 n⁺ |
| FAR23 utility | 4.4 | −0.4 n⁺ (i.e., −1.76) |
| FAR23 aerobatic | 6.0 | −0.5 n⁺ |
| CS-VLA | 3.8 | −1.5 |
| ASTM F2245 | 4.0 | −2.0 |
| EU UL | 4.0 | −2.0 (national rules vary; flag) |
| Experimental | no inference. Use the published value or exclude. | |

Aircraft certified in multiple categories (e.g., normal/utility) use the category matching the MTOW of the record.

Every inferred value is logged with `method="N6"`. Verify the utility negative and CS-VLA values against the texts (doc 12).

## N7 — Design speeds and dynamic pressure

- **V_D.** If not published, V_D = V_NE / 0.9 (legacy 23.1505: V_NE ≤ 0.9 V_D).
- **V_C.** If not published, use V_C = V_NO (structural cruising speed, which equals V_C by definition). If V_NO is also missing, use the formula minimum per the record's basis and flag it.
- **q.** Raymer GA equations use cruise q in psf. Use q at V_C at sea level, EAS. Also store q_D for alternative methods.

## N8 — Geometry derivation

Every derived field records `derived_from` and `method`.

- **Mean aerodynamic chord**, trapezoidal: c̄ = (2/3)·c_r·(1 + λ + λ²)/(1 + λ), with c_r = 2S/(b(1+λ)). If λ is unknown, use λ = 1.0 for constant-chord types identified from 3-views and flag it; otherwise exclude the record from methods that need λ.
- **Aspect ratio:** A = b²/S.
- **Fuselage wetted area** (Torenbeek approximation): S_wet,f ≈ π·D_eff·L·(1 − 2/λ_f)^(2/3)·(1 + 1/λ_f²), with D_eff = √(W·H) and λ_f = L/D_eff. Flag it as approximate; compare against 3-view-measured values where available.
- **Tail arm L_t:** from 3-view, quarter-chord MAC wing to quarter-chord MAC tail. If unavailable, set it to missing (no default).
- **t/c:** from the airfoil designation where known (e.g., NACA 23015 → 0.15). Otherwise missing.

## N9 — Measurement variance model (implemented)

σ_rel = √(σ_tier² + σ_def² + σ_proto² + σ_spread²)

| Component | Values |
|---|---|
| σ_tier | A 0.02; B 0.04; C → excluded |
| σ_def | per N3 table |
| σ_proto | prototype status adds 0.04 (production empty weights typically grow) |
| σ_spread | inter-source relative spread from N2 |

This is the measurement-error term in the likelihood (doc 07 §3). It is not the model-error term.

## N10 — Outlier screening

- Within each (seats class, material, powertrain) cell, compute a robust z-score on log(W_E/MTOW) using the median and MAD.
- If |z| > 3.5: flag the record and require reviewer confirmation. **Never auto-drop.**
- Report flagged records in the dataset build summary.

## N11 — Electric-specific normalization

- Confirm that the published empty weight includes the installed pack. If the source is unclear, warn and set σ_def ≥ 0.03.
- Record pack mass and nominal energy separately if published. Compute pack-level Wh/kg as a derived field.
- **Landing weight = MTOW**, regardless of the published max landing weight unless explicitly stated otherwise.
- Hybrid: record fuel and battery separately; the powertrain group effect is mandatory.

## N12 — Calibration eligibility and dataset freeze

- `calibration_eligible` = required fields present (doc 04 §5) AND tier ∈ {A, B} AND not an unresolved N10 outlier.
- Freeze: write `build/normalized.parquet` with a content hash. Calibration artefacts reference this hash.
