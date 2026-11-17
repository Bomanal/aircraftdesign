# 03 — Certification Basis and Design Loads

Implemented in `src/hs1_weights/certification.py` (seed, tested).

## 1. Bases

| Key | Basis | Regulatory path | Means of compliance |
|---|---|---|---|
| `ASTM_F2245` | US light-sport airplane under MOSAIC (14 CFR Part 22) | FAA LSA certification | ASTM F2245-26 (current revision, Jan 2026) |
| `FAR23_L1` | 14 CFR Part 23 Amdt 23-64/65, normal category, **Level 1 low-speed** | DGCA type certification under CAR 21. DGCA accepts FAR/CS Part 23 as the certification requirement. **Decision (2026-11-03): DGCA's Part 23 = FAR 23.** | ASTM F3264 umbrella as accepted by FAA (MOC notice effective 20 May 2025): F3116 (design loads and conditions), F3083 (emergency conditions, occupant safety), F3114 (structures), F3115 (structural durability), F3179 (performance) |

Level 1 applies because HS1 has two seats (pilot + one passenger). Low-speed applies because V_C/V_MO ≤ 250 KCAS.

Indian precedent: Hansa-3, a two-seat composite trainer, was certified by DGCA on a Part 23 basis using JAR-VLA at 750 kg AUW.

**Cross-market note.** Under MOSAIC, sport-pilot eligibility is set independently of the airworthiness basis, at V_S1 ≤ 59 KCAS (clean, 14 CFR 61.316). A FAR 23-certified HS1 meeting that limit is sport-pilot eligible in the US. LSA certification separately requires V_S0 ≤ 61 KCAS (14 CFR 22.100).

## 2. Criteria table

`verify` = must be checked against the purchased standard text before release. **Treat ✔ as provisional until doc 12 §1 is closed.**

| Quantity | ASTM_F2245 | FAR23_L1 | Verify |
|---|---|---|---|
| Ultimate factor of safety | 1.5 | 1.5 | ✔ |
| Positive limit manoeuvre n₁ | ≥ 4.0 | min(3.8, 2.1 + 24000/(W_lb + 10000)) | ✔ |
| Negative limit manoeuvre n₂ | ≤ −2.0 | −0.4·n₁ | ✔ |
| Flaps-extended limit | +2.0 | +2.0 | ✔ |
| V_C minimum | 2.4·√(W/S [Pa]) m/s, need not exceed 0.9 V_H | 33·√(W/S [psf]) kt (≡ 2.45·√Pa m/s), need not exceed 0.9 V_H | ✔ |
| V_D minimum | max(1.25 V_C, 1.40 V_C,min) | max(1.25 V_C, 1.40 V_C,min) | ✔ |
| Gust U_de at V_C / V_D | 15.24 / 7.62 m/s EAS | 50 / 25 ft/s (identical) | ✔ |
| Gust alleviation | K_g = 0.88μ/(5.3+μ), μ = 2(W/S)/(ρ c̄ a g) | same | ✔ |
| Landing sink speed | 4.4·(W/S psf)^0.25 ft/s, clamped 7–10 ft/s (applied to both; F2245 text to confirm) | same | ✔ |
| Emergency landing, occupants (up / fwd / side) | 3.0 / 9.0 / 1.5 g | 3.0 / 9.0 / 1.5 g | ✔ |
| **Powerplant and ESS mounts** (battery packs, motors) | not modelled (confirm F2245-26) | **18.0 g forward** (FAA MOC change to F3083 §4.1.6); up 3.0 / side 4.5 g | fwd: no; up/side: ✔ |
| Dynamic seat tests | none | 19 g / 31 ft/s (test 1); 26 g / 42 ft/s (test 2) | ✔ |
| Stall limit | V_S0 ≤ 61 KCAS (22.100) | V_S0 ≤ 61 KCAS single-engine (legacy 23.49(c); higher only with added crashworthiness) | 23 only |
| Sport-pilot eligibility | V_S1 ≤ 59 KCAS | V_S1 ≤ 59 KCAS | — |

## 3. Envelope rule

For every weight-driving quantity, take the worst case across the selected bases and record the governing basis:

- n_ult⁺ = max over bases of 1.5·max(n₁, n_gust,VC⁺, n_gust,VD⁺)
- n_ult⁻ = min over bases of 1.5·min(n₂, n_gust,VC⁻, n_gust,VD⁻)
- q_D, V_D, landing sink speed: max over bases
- emergency and mount factors: component-wise max
- dynamic seat: required if any basis requires it

The envelope output is the **only** source of loads for L1 components. Mapping:

| L1 consumer | Envelope inputs |
|---|---|
| Wing, tails, fuselage, flight controls | n_ult = max(\|n_ult⁺\|, \|n_ult⁻\|·k_neg), where k_neg = 1 by default (negative case governs only if \|n⁻\| > n⁺); q (cruise) and q_D |
| Landing gear | landing sink speed; ultimate landing load factor N_l (doc 06 §3.4) |
| Battery bays, motor mounts | ESS/powerplant mount factors (18 g fwd) |
| Seats, seat rails, cabin floor | dynamic seat flag; emergency static factors |

## 4. Reference results (illustrative inputs, pinned in tests)

Inputs: 700 kg, S = 11.0 m², c̄ = 1.10 m, a = 5.0 /rad, C_L,max 1.5 / 2.0, V_C = 105 kt, V_H = 115 kt.

| Output | ASTM_F2245 | FAR23_L1 |
|---|---|---|
| W/S | 624 Pa | 624 Pa |
| V_S1 / V_S0 | 50.7 / 43.9 kt | same |
| n_limit ± | +4.00 / −2.00 | +3.80 / −1.64 (gust governs negative) |
| n_ult ± | +6.00 / −3.00 | +5.70 / −2.47 |
| Gust at V_C | +3.64 / −1.64 | same |
| Envelope | n_ult⁺ 6.0 (F2245); mount 18 g fwd (FAR 23); dynamic seats required | |

At 600 kg, gust governs n⁺ under both bases: n_ult⁺ = 6.19.

Minimum wing area at 700 kg: 8.11 m² for V_S1 ≤ 59 kt (C_L,max 1.5); 5.69 m² for V_S0 ≤ 61 kt (C_L,max,land 2.0).

## 5. Rules the implementation must respect

1. **Power-idle stall.** C_L,max inputs exclude DEP blowing. If a user passes a blown C_L,max, raise a warning and use the unblown value for stall-limit checks.
2. **V_C below V_C,min** is a hard check failure reported in `envelope.all_checks`. Do not auto-raise V_C.
3. **Gust μ at sea-level density.** Extending to altitude requires EAS/TAS handling; this is P2.
4. **Fleet aircraft loads.** When calibrating, each fleet aircraft's L1 inputs use **its own** certification basis (inferred per doc 05 N6), not HS1's envelope.
