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
| Raymer (project copy) | All exponents in doc 06 §3; GA worked example → `tests/golden/raymer_ga_example.json` |
| Torenbeek 1982, Roskam Part V (project copies) | Every coefficient in `components/torenbeek.py` and `components/roskam_cessna.py` (entered without the text: SC-10); worked examples → `tests/golden/*.json` |
| Gudmundsson 2014 Ch. 6 (project copy) | Which equations differ from Raymer/USAF/Cessna (SC-9) |

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

Appended by the coding assistant during the 0.2.0 build (2026-11-03). Each entry gives the conflict or gap, the
resolution **adopted provisionally** in code, and what the owner needs to decide. Nothing here was resolved
silently: each item is also visible in code comments and, where it affects a prediction, in the report's
`assumptions`.

| ID | Conflict / gap | Provisional resolution in 0.2.0 | Owner action |
|---|---|---|---|
| SC-1 | CLAUDE.md rule 10 says "do not start Phase N+1 until Phase N acceptance passes", but P1-5 (≥ 60 Tier A/B records with sources) requires acquiring TCDS/POH documents and **human review** (doc 04 §6.4). Under rule 2 the assistant cannot supply fleet values. Strictly applied, rule 10 blocks Phases 2–5 indefinitely. | Code for all phases was built and tested on **synthetic** fleets (`calibration/simulate.py`, ids `synthetic_*`, never calibration data). Every acceptance criterion that needs real fleet data is marked **OPEN** in `docs/13_build_status.md`. Without a calibration artefact, `predict()` draws k from the doc 07 priors and says so in `assumptions` and `calibration.engine = "prior"`. | Confirm this reading of rule 10, or freeze Phases 2–5 until P1-5 closes. |
| SC-2 | Doc 04 §5 lists tail areas, tail arm, fuselage L/W/H as "desirable", but the doc 07 §3.1 aircraft-level likelihood needs the **full** L1 tree, and Raymer GA also needs tail AR/taper/t/c/sweep, gear strut lengths, W_uav, fuel V_t/V_i/N_t. None of the tail-shape fields existed in the seed schema. | Optional sourced fields added to `schema.py` (htail/vtail span, taper, t/c, sweep; structural length; gear lengths; W_uav; fuel tanks, integral volume; oil; unusable fuel; V_NO, V_D; structural design weight). A record missing any L1 input is **excluded from the aircraft-level likelihood** (reason logged in `FleetMatrix.excluded`) but still used in the component-level likelihood where its breakdown components are computable. | Promote the L1 inputs to "required for aircraft-level calibration" in doc 04 §5, or accept the exclusions. |
| SC-3 | Doc 06 §3.2 (Raymer htail) writes `(100·t/c / cosΛ)` with an unsubscripted Λ, t/c and A, but `Λ_ht` in the A term. | Tail's own t/c, sweep and aspect ratio used throughout (same as §3.3). | Verify against the project's Raymer. |
| SC-4 | Raymer wing t/c: root or mean not stated. | Root t/c (logged). | Verify. |
| SC-5 | N6 has no rule for CS-LSA, FAR23 commuter or Part 23 Amdt 64; doc 07 §2 g2 has no levels for them. | No N6 inference (missing). For the calibration group only: CS-LSA → ASTM F2245 level; commuter / Amdt 64 → FAR23 normal level. | Add N6 rows / g2 levels. |
| SC-6 | N7 "formula minimum per the record's basis": only FAR23 normal (33√W/S) and ASTM/CS-VLA (2.4√Pa) coefficients are in the spec; utility/aerobatic coefficients are not. | Formula minimum only for FAR23 normal and ASTM F2245; others left missing. A formula-minimum V_C does **not** satisfy the doc 04 §5 "v_ne or v_c" eligibility requirement. | Supply coefficients if wanted. |
| SC-7 | Doc 07 §3.4 gives a prior only for θ_wing; none for the other θ_i or for the powertrain group effect; "composite" is not mapped to the g1 classes. | θ_i ~ N(0, 0.15) for all components; π_powertrain ~ N(0, 0.10) on systems groups; composite α priors applied to carbon, glass and composite-mixed. All recorded in the artefact's `prior_spec`. | Confirm or set priors. |
| SC-8 | Doc 06 §5 placeholder priors (sp_motor, sp_inv, m_bay_hw, c_ret, k_cut, k_th, J_allow, f_ins) have no numeric values; rule 8 forbids unlisted defaults. | Library has no defaults for them: they are required `Param` inputs. `examples/hs1_reference.json` carries **illustrative** values flagged `assumption=true`. Only η_c2p ~ U(0.55, 0.70) is defaulted (spec-listed). | Replace with vendor data / sized load paths. |
| SC-9 | Gudmundsson Ch. 6 "where it differs": cannot be determined without the text. | No Gudmundsson methods registered (`components/gudmundsson.py` documents the slot). | Provide the text or drop the requirement. |
| SC-10 | R4 / P2-2 / P2-3 require reproducing textbook worked examples to 1%; the project copies are not available to the assistant. Torenbeek and Roskam (Cessna, USAF) coefficients are not transcribed in the spec. | Raymer implemented exactly as doc 06 §3 and pinned by independent hand-calc tests. Torenbeek/Cessna/USAF entered from the assistant's knowledge and registered with `transcription_verified=False` (shown in every report). Golden tests in `tests/golden/*.json` **skip** until filled from the texts. | Fill the golden files; correct coefficients; set verified. |
| SC-11 | N3 "dry" empty weight when fuel type or capacities are unknown is not covered. | Not corrected; σ_def += 3% (as for uncorrected licensed EW). | Confirm. |
| SC-12 | N10 "seats class" bins are not defined. | ≤ 2, 3–4, 5–6, ≥ 7. | Confirm. |
| SC-13 | Doc 10 §3 allows "a default fraction of the fuselage/wing reference" for stations but gives no fractions. | No defaults: a missing station raises `MissingInputError`. CG is skipped (and logged) if `stations` is absent. | Supply fractions if wanted. |
| SC-14 | CG case "single pilot light": mass not given. "MTOW with 2 × 86 kg": baggage/remaining payload not defined. | `light_pilot_kg` is a required config input for CG; the full case is W_E + 2 × 86 kg at the seat station (no baggage). | Define the cases fully. |
| SC-15 | The 18 g forward mount factor exists only in FAR23_L1 (ASTM "not modelled"). CLAUDE.md applies it to packs and mounts "behind the occupants"; doc 06 §5.3/5.5 apply it to all bays and mounts. | Applied to all bays and all motor mounts (conservative). With only ASTM_F2245 selected, bays/mounts raise `MissingInputError("n_mount")`. | Confirm F2245-26 text; decide on wing-mount factor. |
| SC-16 | MAP of a centred hierarchical manufacturer effect is degenerate (τ → 0). | MAP/Laplace fixes τ_m at its prior scale 0.05; NUTS samples τ_m (non-centred). New manufacturers draw δ from the hyperprior in both. | Confirm. |
| SC-17 | Artefact must be `.nc` (arviz); arviz is an optional dependency. | `.nc` when arviz is installed (both arviz < 1 and ≥ 1 APIs), else `.json` with the same content and naming. | — |
| SC-18 | Doc 06 §5.5 "size mounts to the envelope powerplant-mount factors" gives no mass relation. | m_mount = c_mount · (n_mount · g · m_supported), c_mount a placeholder prior (kg/kN). | Replace with a sized mount. |
| SC-19 | Where σ_model applies to HS1 is not stated; doc 06 §6 says bottom-up terms carry only parameter uncertainty. Sobol needs independent inputs. | σ_model (and the GP residual) scale only the calibrated part Σ k·f; σ_comp is not added to HS1 components. Sobol uses independent marginals (posterior k correlations dropped). | Confirm. |
| SC-20 | Fleet L1 inputs not covered by the schema/spec: gust loads need a lift-curve slope; wing fuel W_fw; Raymer L; W_en per engine; W_l when no MLW; ECS presence; electric fleet U_j. | Fleet n_ult = 1.5 × max(n⁺, \|n⁻\|) from published or N6 limits (no gust); W_fw = usable fuel mass (W_fw^0.0035, < 2% effect); L = fuselage length if structural length missing; W_en = engine_dry_mass / n_engines; W_l = W_dg; ECS = `pressurized`; electric U_j = published pack + motor mass (inverter/thermal/harness absorbed by the powertrain effect and σ_model). All logged per record. | Confirm, or add fields. |
| SC-21 | Records without `year` have no era group. | γ = 0 for that record (logged). | Make year required, or confirm. |
| SC-22 | Doc 07 §4 "flag \|ρ\| > 0.8 pairs of θ": with shared β/γ/δ offsets this fires on every fit. | Raw θ flags reported as specified, plus `k_ref_corr_flags` on log k_i at the reference cert/era level including the mean manufacturer effect (the identified quantity). | Confirm which governs the collapse decision. |
| SC-23 | Gear limit factor "derive from sink speed and stroke" (doc 06 §3.5): no formula given. | Default 3.0 (spec-listed) unless `gear.limit_load_factor` is given. | Supply the derivation. |
| SC-24 | Harness run power P_r and whether L_r includes the return conductor are not stated. | P_r = P_cont / η_chain unless given; L_r is total conductor length (include return). | Confirm. |
| SC-25 | HS1 avionics: doc 06 tree says "Raymer GA from uninstalled avionics mass" while §5.8 lists "avionics (installed)" as a mandatory equipment entry. | Equipment items in category `avionics` are uninstalled masses feeding Raymer W_uav (default, calibrated); `methods={"avionics_installed": "equipment_list"}` treats them as installed instead. | Confirm. |
| SC-26 | CLAUDE.md asks for `mypy --strict` with Python ≥ 3.10; jax ships 3.12-only stub syntax. | mypy runs with `python_version = 3.12`, third-party imports not followed; runtime floor stays 3.10. | — |

## 5. Changelog

- 0.1.0 (2026-11-03): Initial handover. Part 23 references corrected to F3116 (design loads and conditions) and F3083 (emergency conditions); F3173 was cited in error in an earlier draft. ESS/powerplant mount 18 g forward confirmed from the FAA MOC table.
- 0.2.0 (2026-11-03): `WeightEstimation` build of Phases 1–5 code (see `docs/13_build_status.md`). Spec conflicts SC-1 to SC-26 recorded above. `certification.py`: **type annotations only** (plus an `assert` guarding `n_neg_ratio`); no criterion value, `ref` or `verify` flag changed. `normalize.py` moved to `data/normalize.py` (shim kept); the seed `normalize()` now inflates σ_def to 3% for uncorrected licensed EW, per the N3 table. Schema extended with optional sourced fields (SC-2).
