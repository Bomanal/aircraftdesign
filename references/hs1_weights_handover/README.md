# HS1 Weight-Prediction Module — Engineering Handover Package

**Version:** 0.1.0 (Phase 1 seed) · **Date:** 2026-11-03 · **Owner:** Dr. Mallesh Bommanahal

## Purpose

Build a weight-prediction module for HS1. HS1 is a two-seat, low-wing, side-by-side electric aircraft with distributed electric propulsion (DEP): two leading-edge tractor props on the wing, one aft-fuselage pusher, and swappable battery bays.

The module predicts component masses, empty weight, CG and MTOW, each with calibrated uncertainty. It does this by combining two sources:

1. **Physics-based / semi-empirical component models** (Raymer GA, Torenbeek, Cessna/USAF methods, and bottom-up electric models).
2. **Technology factors** learned from a normalized database of production general-aviation aircraft (2–10 seats, all powertrain and structure classes).

It is **not** a black-box regression on fleet data. Fleet data calibrates the physics; it does not replace it.

## Reading order

| # | Document | Content |
|---|---|---|
| — | `CLAUDE.md` | Operating rules for the coding assistant. **Read first.** |
| 01 | `docs/01_requirements.md` | Scope, goals, non-goals, accuracy targets, P0/P1/P2 requirements |
| 02 | `docs/02_architecture.md` | Five-layer architecture, module layout, data flow, public API |
| 03 | `docs/03_certification_basis.md` | Dual basis (ASTM F2245 / MOSAIC and 14 CFR Part 23 via DGCA), envelope rules |
| 04 | `docs/04_data_spec.md` | Fleet database schema, source tiers, coverage matrix, extraction pipeline |
| 05 | `docs/05_normalization.md` | Normalization routines N1–N12 |
| 06 | `docs/06_component_models.md` | Component tree, equations, electric/DEP/quick-swap models |
| 07 | `docs/07_calibration.md` | Bayesian hierarchical calibration, priors, identifiability, GP residuals |
| 08 | `docs/08_validation_and_tests.md` | Test pyramid, hand-calculated checks, LOO/LOCO protocol, acceptance |
| 09 | `docs/09_reasoning_layer.md` | Analogs, consistency rules, sensitivity, LLM boundaries |
| 10 | `docs/10_integration.md` | Coupling to `hs1_design/` closure loop, Monte Carlo, CG |
| 11 | `docs/11_build_plan.md` | Phased tickets with acceptance criteria |
| 12 | `docs/12_open_items_and_risks.md` | Values to verify, decisions pending, risks |

## Repository contents

```
hs1_weights_handover/
├── README.md, CLAUDE.md, pyproject.toml
├── docs/                      specifications (this package)
├── src/hs1_weights/
│   ├── certification.py       DONE  dual-basis load criteria and envelope
│   ├── schema.py              DONE  fleet record schema with field-level provenance
│   ├── normalize.py           DONE (partial)  N3, N5, N9 implemented; move to data/ in P1-3
│   └── run_cert_envelope.py   DONE  demo (illustrative inputs)
├── tests/                     23 passing seed tests
└── data/
    ├── aircraft_candidates.csv   81 candidates, 22 priority-1 (no numbers: Phase 1 extraction)
    ├── sources_template.csv
    └── record_example.json       filled-in schema example (values marked TO-VERIFY)
```

## Quick start

```bash
pip install -e ".[dev]"
pytest -q                                   # 23 passed
python -m hs1_weights.run_cert_envelope     # dual-basis load envelope, illustrative HS1 inputs
```

## Non-negotiables

- **SI internally.** Convert at I/O boundaries only.
- **Every number traces to a source.** A fleet value without a `Source` is not admissible.
- **Certification criteria flagged `verify=True` are working values**, not certified ones (see doc 12).
- **The numeric path is deterministic and auditable.** No LLM output enters a calculation.
