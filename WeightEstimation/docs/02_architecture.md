# 02 — Architecture

## 1. Layers

| Layer | Package | Function | Character |
|---|---|---|---|
| L0 Data | `hs1_weights.data` | Raw records, sources, normalization, versioned datasets | Data engineering |
| L1 Component models | `hs1_weights.components` | Semi-empirical and bottom-up masses per component | Deterministic |
| L2 Calibration | `hs1_weights.calibration` | Hierarchical technology factors; GP residuals | Statistical |
| L3 Reasoning | `hs1_weights.reasoning` | Analogs, consistency rules, sensitivity, explanation | Rules + retrieval |
| L4 Integration | `hs1_weights.api`, `hs1_weights.mc` | `predict()`, Monte Carlo, CG, coupling to `hs1_design/` | Orchestration |

`hs1_weights.certification` is shared. It supplies design loads to L1.

## 2. Target module layout

```
src/hs1_weights/
├── __init__.py
├── units.py                 # SI <-> imperial helpers (lb, ft, ft2, psf, kt, US gal)
├── certification.py         # [seed] dual-basis criteria, envelope
├── schema.py                # [seed] fleet record schema
├── data/
│   ├── io.py                # load/save JSONL records, sources CSV
│   ├── normalize.py         # [seed partial] routines N1–N12
│   ├── derive.py            # geometry derivations (wetted areas, tail arms)
│   └── dataset.py           # build versioned calibration dataset (parquet)
├── components/
│   ├── base.py              # ComponentModel protocol, MethodRegistry
│   ├── raymer_ga.py         # Raymer GA eqs (imperial core + SI wrapper)
│   ├── torenbeek.py
│   ├── roskam_cessna.py     # Cessna and USAF class II methods
│   ├── gudmundsson.py
│   ├── electric.py          # pack, motor, inverter, thermal, harness
│   ├── dep.py               # wing nacelles, inertia relief
│   ├── battery_bays.py      # quick-swap bay hardware and retention structure
│   └── fixed_items.py       # equipment list
├── calibration/
│   ├── model.py             # numpyro model
│   ├── fit.py               # MCMC/SVI, artefact writing
│   ├── ridge_fallback.py    # fast MAP with priors (no MCMC dependency)
│   └── gp_residual.py
├── reasoning/
│   ├── analogs.py
│   ├── rules.py
│   └── sensitivity.py       # Sobol (Saltelli)
├── mc.py                    # Monte Carlo propagation
├── cg.py
└── api.py                   # predict(), WeightReport
```

## 3. Data flow

```
raw sources (TCDS, POH, Jane's, spec sheets)
   └─(extraction, doc 04 §6)─> records.jsonl + sources.csv
        └─(normalize N1–N12)─> normalized.parquet (+ warnings log)
             └─(L1 applied to each fleet aircraft with its own geometry and loads)─> f_i per component
                  └─(L2 fit)─> posterior over k (by group), σ_model, GP hyperparameters  [artefact]

HS1 config ─> certification envelope ─> L1 f_i(HS1) ─> × posterior k (HS1 groups) ─> MC samples
         ─> WeightReport (components, W_E, MTOW closure via hs1_design, CG, intervals, analogs, rules, Sobol)
```

## 4. Key design decisions

| Decision | Rationale |
|---|---|
| Calibrate multipliers on physics, not raw regression | Separates known physics from technology level; enables extrapolation |
| Hierarchical groups (material, cert basis, era, manufacturer) | Partial pooling with n ≈ 150–250 |
| Swappable method registry | Benchmark Raymer vs Torenbeek vs Cessna; use an ensemble if justified |
| Electric components bottom-up, not calibrated | Too few fleet points; physics and vendor data dominate |
| Envelope of two certification bases | HS1 targets both the US (MOSAIC) and India (DGCA, FAR 23) |
| Numpyro (JAX) for Bayes | Speed and NUTS; ridge-MAP fallback keeps the core free of heavy deps |

## 5. Public API (P0)

```python
class HS1Config(BaseModel):
    mass_guess_kg: float                  # W_dg initial guess; closure loop updates it
    wing: WingGeom                        # S, b, t/c root/tip, taper, sweep_qc, cbar, a_per_rad
    htail: SurfaceGeom; vtail: SurfaceGeom; tail_arm_m: float
    fuselage: FuselageGeom                # L, W, H, wetted area (optional, derived if absent)
    cl_max_clean: float; cl_max_land: float
    vc_kt: float; vh_kt: float
    gear: GearSpec                        # type, strut lengths
    powertrain: ElectricPowertrain        # motors[], inverters[], pack spec, harness runs, thermal
    battery_bays: BayLayout               # n_bays, bay masses, stations, swap hardware
    equipment: list[FixedItem]            # avionics, seats, BRS, interior, ...
    material_by_component: dict[str, Material]
    cert_bases: list[str] = ["ASTM_F2245", "FAR23_L1"]
    methods: dict[str, str] = {}          # override default method per component

class WeightReport(BaseModel):
    components: dict[str, Dist]           # P10/P50/P90, mean, sd, governing load case, method, k used
    empty_weight: Dist; mtow: Dist | None
    cg: CGReport | None
    envelope: dict                        # from certification.envelope()
    stall_area_min_m2: dict
    analogs: list[AnalogComparison]
    rule_flags: list[RuleFlag]
    sobol: dict[str, float] | None
    assumptions: list[str]                # every default applied
    unverified_criteria: list[str]        # from certification.unverified_criteria()
    versions: dict                        # code, dataset, calibration artefact hashes

def predict(cfg: HS1Config, n_samples: int = 2000, seed: int = 0) -> WeightReport: ...
```
