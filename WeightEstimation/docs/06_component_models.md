# 06 — Component Models

## 1. Component tree (W_E breakdown)

| Group | Component id | Default method | Calibrated (k_i)? |
|---|---|---|---|
| Structure | `wing` | Raymer GA (§3.1) + DEP inertia relief (§5.5) | Yes |
| | `htail`, `vtail` | Raymer GA | Yes (shared k_tail) |
| | `fuselage` | Raymer GA + bay cutout penalty (§5.4) | Yes |
| | `main_gear`, `nose_gear` | Raymer GA with W_l = MTOW for electric | Yes (shared k_gear) |
| | `nacelles_mounts` | Bottom-up (§5.5) | No |
| Propulsion | `engine_installed` (ICE fleet only) | Raymer GA | Yes |
| | `fuel_system` (ICE fleet only) | Raymer GA | Yes |
| | `motors`, `inverters` | Bottom-up (§5.2) | No |
| | `battery_pack` | Bottom-up (§5.1) | No |
| | `battery_bays` | Bottom-up (§5.3) | No |
| | `thermal_mgmt` | Bottom-up (§5.6) | No |
| | `hv_harness` | Bottom-up (§5.7) | No |
| | `propellers` | Vendor mass, or scaling law with flag | No |
| Systems | `flight_controls` | Raymer GA | Yes |
| | `electrical_lv` | Raymer GA (ICE fleet); bottom-up for electric | ICE only |
| | `avionics_installed` | Raymer GA from uninstalled avionics mass | Yes |
| | `furnishings` | Raymer GA, or equipment list | Yes |
| | `ecs_anti_ice` | Raymer GA (fleet); usually 0 for HS1 | Yes |
| Fixed | `fixed_items` | Equipment list (BRS, seats if itemised, etc.) | No |

Every component model returns `ComponentResult(mass_kg, method, inputs_used, governing_load_case, assumptions)`.

## 2. Method registry

```python
@runtime_checkable
class ComponentModel(Protocol):
    component_id: str
    method_id: str
    def __call__(self, ctx: ModelContext) -> ComponentResult: ...
```

`MethodRegistry` maps `component_id -> {method_id: model}`. The defaults are the table above. Alternatives (Torenbeek, Cessna, USAF via Roskam Pt V; Gudmundsson) must be registered for structure components so they can be benchmarked (doc 08 §5).

## 3. Raymer GA equations (imperial core)

Source: Raymer, *Aircraft Design: A Conceptual Approach*, Ch. 15, general-aviation weights.

**Transcribed here for implementation. Each exponent must be verified against the project's copy of Raymer before the R4 acceptance test.**

Units throughout this section: lb, ft, ft², psf, in, US gal.

| Symbol | Meaning |
|---|---|
| N_z | ultimate load factor (= 1.5 × limit) |
| W_dg | design gross weight |
| q | cruise dynamic pressure |
| A | aspect ratio |
| Λ | quarter-chord sweep |
| λ | taper ratio |
| t/c | thickness ratio |
| W_fw | wing fuel weight |
| S_f | fuselage wetted area |
| L_t | tail arm |
| L/D | fuselage length / structural depth (here, not the aerodynamic L/D) |
| N_l | ultimate landing load factor |
| W_l | landing design gross weight |
| L_m, L_n | main/nose gear lengths (in) |
| W_en | engine weight each |
| N_en | number of engines |
| V_t, V_i | total / integral tank volume (gal) |
| N_t | number of tanks |
| B_w | span (ft) |
| L | fuselage structural length (ft) |
| W_uav | uninstalled avionics weight |
| N_p | persons on board |
| M | Mach number |

### 3.1 Wing

W_wing = 0.036 · S_w^0.758 · W_fw^0.0035 · (A/cos²Λ)^0.6 · q^0.006 · λ^0.04 · (100·(t/c)/cosΛ)^−0.3 · (N_z·W_dg)^0.49

**Electric override.** Set W_fw,eff = max(W_fw, 1.0 lb). Then W_fw^0.0035 → 1 for HS1. Without this, W_fw = 0 gives zero wing weight.

The missing fuel bending relief is not represented by this exponent. It is captured by the electric group effect in calibration (doc 07) and by the explicit inertia-relief term (§5.5).

### 3.2 Horizontal tail

W_ht = 0.016 · (N_z·W_dg)^0.414 · q^0.168 · S_ht^0.896 · (100·(t/c)/cosΛ)^−0.12 · (A/cos²Λ_ht)^0.043 · λ_h^−0.02

### 3.3 Vertical tail

W_vt = 0.073 · (1 + 0.2·H_t/H_v) · (N_z·W_dg)^0.376 · q^0.122 · S_vt^0.873 · (100·(t/c)/cosΛ_vt)^−0.49 · (A/cos²Λ_vt)^0.357 · λ_vt^0.039

H_t/H_v = 0 for a conventional tail and 1 for a T-tail.

### 3.4 Fuselage

W_fus = 0.052 · S_f^1.086 · (N_z·W_dg)^0.177 · L_t^−0.051 · (L/D)^−0.072 · q^0.241 + W_press

W_press = 0 for HS1.

### 3.5 Landing gear

- W_mlg = 0.095 · (N_l·W_l)^0.768 · (L_m/12)^0.409
- W_nlg = 0.125 · (N_l·W_l)^0.566 · (L_n/12)^0.845

N_l = 1.5 × gear limit load factor. Default limit 3.0 if not otherwise derived; when the gear design is defined, derive it from sink speed (doc 03) and stroke.

**For electric aircraft, W_l = MTOW.**

### 3.6 Installed engine and fuel system (fleet ICE aircraft only)

- W_eng,inst = 2.575 · W_en^0.922 · N_en
- W_fs = 2.49 · V_t^0.726 · (1/(1 + V_i/V_t))^0.363 · N_t^0.242 · N_en^0.157

### 3.7 Systems

| Component | Equation |
|---|---|
| Flight controls | W_fc = 0.053 · L^1.536 · B_w^0.371 · (N_z·W_dg·10⁻⁴)^0.80 |
| Hydraulics | W_hyd = 0.001 · W_dg |
| Electrical (ICE fleet) | W_el = 12.57 · (W_fs + W_avionics)^0.51 |
| Avionics (installed) | W_av = 2.117 · W_uav^0.933 |
| AC and anti-ice | W_ac = 0.265 · W_dg^0.52 · N_p^0.68 · W_av^0.17 · M^0.08 |
| Furnishings | W_furn = 0.0582 · W_dg − 65 (floor at 0; prefer equipment list for HS1) |

### 3.8 SI wrapper requirement

Each equation is implemented as `_raymer_wing_imperial(**lb_ft_psf) -> lb`, with a public `raymer_wing(ctx) -> kg` that converts. Test both layers.

## 4. Alternative methods

Implement for benchmarking: Torenbeek (wing, empennage, fuselage, gear) and Cessna/USAF class II (via Roskam Part V), plus Gudmundsson Ch. 6 where it differs. Same protocol and unit discipline as above. Exponents must be transcribed from the source texts and referenced in docstrings with edition and equation number.

## 5. Electric, DEP and quick-swap models (bottom-up)

**All numeric defaults below are placeholder priors.** They are flagged `assumption=True` and must be replaced by vendor data or analysis. Each parameter is a distribution in Monte Carlo.

### 5.1 Battery pack

m_pack = E_nom / (e_cell · η_c2p)

| Parameter | Meaning |
|---|---|
| E_nom | nominal installed energy (Wh), from `hs1_design` |
| e_cell | cell gravimetric energy (Wh/kg). **The 300 Wh/kg target must be declared as cell- or pack-level in config. If pack-level, set η_c2p = 1 and e_cell = e_pack.** |
| η_c2p | cell-to-pack mass ratio. Placeholder prior U(0.55, 0.70), covering structure, BMS, thermal-runaway containment, enclosure and busbars. |

Outputs: pack mass, per-bay pack mass (split by `BayLayout`), and pack-level Wh/kg (reported).

### 5.2 Motors and inverters

- m_motor,j = P_cont,j / sp_motor
- m_inv,j = P_cont,j / sp_inv

Specific powers are taken per vendor datasheet when the motor is selected. Before selection, use placeholder priors with wide variance, flagged.

Mount mass: see §5.5.

### 5.3 Quick-swap battery bays

m_bays = N_bays · m_bay_hw + c_ret · (n_fwd · g · m_pack)

| Term | Meaning |
|---|---|
| m_bay_hw | per-bay fixed hardware: rails, latches, HV/LV connectors, guides, sensing. Supplier data, else placeholder prior. |
| n_fwd | 18.0, the ESS mount forward factor from the envelope (doc 03) |
| c_ret | retention structure mass per unit design load (kg/N). Placeholder prior; replace with a sized load path (FEA or hand-sized frames). |

The scaling with N_bays is deliberate: more, smaller bays cost more hardware mass at constant energy. The report must show the N_bays sensitivity.

### 5.4 Fuselage cutout penalty

W_fus,HS1 = W_fus · (1 + k_cut · A_cut/S_f)

A_cut is the total access-opening area for the bays. k_cut is a placeholder prior and is flagged.

### 5.5 DEP nacelles, mounts and wing inertia relief

**Nacelles and mounts.** m_nac,j = m_mount,j + m_fairing,j. Size mounts to the envelope powerplant-mount factors.

**Inertia relief on the wing.** Point masses outboard reduce root bending moment at ultimate load:

- M_root,net = M_lift − Σ_j (m_j · g · n_ult · y_j)
- M_lift = (n_ult · W_dg · g / 2) · (4b / 6π)  (elliptic half-span centroid)
- W_wing,DEP = W_wing,Raymer · (M_root,net / M_lift)^0.49

The exponent matches Raymer's (N_z·W_dg) exponent. Apply this only to the wing-mounted DEP masses (motor + inverter if wing-mounted + nacelle + prop). Report the relief in kg.

### 5.6 Thermal management

m_th = k_th · Q_rej,  Q_rej = Σ P_cont,j · (1 − η_chain,j) + Q_pack

Q_pack is pack heat at the design C-rate. Size at the **hot-day ambient** case (India operations; default ISA+30, configurable). k_th (kg/kW) is a placeholder prior, split by air-cooled vs. liquid.

### 5.7 HV harness

For each run r (pack → inverter → motor), with current I_r = P_r / V_bus:

- A_r = I_r / J_allow
- m_r = L_r · A_r · ρ_cond · f_ins

| Parameter | Meaning |
|---|---|
| J_allow | allowable current density (A/mm²) by conductor material and installation; placeholder |
| ρ_cond | Cu 8,960 kg/m³ or Al 2,700 kg/m³ |
| f_ins | insulation, shielding and connector factor; placeholder prior |

Add contactors, fuses and the pyro-disconnect as fixed items.

DEP routing lengths L_r are taken from layout stations, not estimated.

### 5.8 Fixed items

An equipment list with mass and station for each item. Mandatory entries for HS1: avionics (installed), seats, ballistic parachute (if fitted), interior, LV battery, lighting.

Items with no vendor mass are flagged `estimate=True`.

## 6. Calibrated vs. uncalibrated output

For each calibrated component: mass_i = k_i(group) · f_i, where f_i is the method output and k_i comes from the posterior.

Uncalibrated (bottom-up) components carry only their parameter uncertainty.

The report states which applies, per component.
