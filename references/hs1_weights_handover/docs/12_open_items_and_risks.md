# 12 — Open Items, Decisions and Risks

## 1. Standards to purchase and values to verify

`certification.unverified_criteria()` lists 30 criteria at seed.

| Standard | Verify |
|---|---|
| ASTM F2245-26 | n limits, safety factor, V_C/V_D rules, gust, landing sink, emergency loads; **whether items-of-mass / ESS mount loads exist and their values** |
| ASTM F3264-24 set: F3116 (loads), F3083 (emergency/occupant), F3114, F3115, F3179 | Every FAR23_L1 value; dynamic seat pulses; up/side mount factors |
| FAA Part 23 MOC table (Federal Register, effective 20 May 2025) | Full text of the F3083 §4.1.6 replacement (18 g forward is confirmed; side/up pending) |
| Electric-propulsion special conditions (FAA precedents for electric Part 23 aircraft; what DGCA would adopt) | Battery containment, ESS crashworthiness, HV requirements → feed η_c2p and bay priors |
| DGCA | Written confirmation that FAR 23 Amdt 64 with the FAA-accepted ASTM MOC is acceptable under CAR 21 for an Indian-origin type certificate |
| Raymer (project copy) | All exponents in doc 06 §3 |

## 2. Decisions

| # | Decision | Status |
|---|---|---|
| D1 | Certification bases: ASTM F2245 (MOSAIC) + FAR 23 via DGCA | **Decided 2026-11-03** |
| D2 | DGCA Part 23 = FAR 23 (not CS-23) | **Decided 2026-11-03** |
| D3 | Is the 300 Wh/kg target cell-level or pack-level? | **Open. Highest MTOW leverage.** |
| D4 | Hot-day design ambient for thermal sizing (default ISA+30) | Open |
| D5 | Number of battery bays (trade enabled by §5.3) | Open: drive it from module output |
| D6 | Structural group level used for HS1 (ASTM vs. FAR23 cert-group effect) | Default ASTM; report both |

## 3. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Too few component breakdowns → k_i unidentifiable | Component-level outputs meaningless | Prioritise breakdown sources in P1-5; collapse to k_struct with disclosure |
| Electric fleet data mostly Tier B/C prototypes | Wide electric intervals | Accept honest width; bottom-up dominates electric terms |
| Raymer exponents transcribed incorrectly | Systematic bias | R4 textbook-example acceptance test |
| MTOW censoring mishandled | Biased wing/gear k for the LSA class | Latent W_dg (doc 07 §3.3); sensitivity run with censored records excluded |
| Placeholder priors on bay/retention mass dominate variance | Misleading MTOW | Rule C6; replace with sized load path before design freeze |
| MOSAIC LSA standard acceptance timing | Basis drift | Track FAA notices of availability for F2245-26 |

## 4. Spec conflicts

(The coding assistant appends here; none at handover.)

## 5. Changelog

- 0.1.0 (2026-11-03): Initial handover. Part 23 references corrected to F3116 (design loads and conditions) and F3083 (emergency conditions); F3173 was cited in error in an earlier draft. ESS/powerplant mount 18 g forward confirmed from the FAA MOC table.
