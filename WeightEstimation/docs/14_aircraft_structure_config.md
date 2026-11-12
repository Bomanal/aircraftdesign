# 14 — Aircraft Structure Config

Module: `src/hs1_weights/structure_config.py`. Dashboard: the **Aircraft Structural Config** card in the eacsize
UI. Added 2026-11-12.

## 1. Purpose

In conceptual design the HS1 detail that eacsize does not model is not known yet; it gets iterated. This
detail covers:
- DEP split, quick-swap bays and equipment;
- materials, CG stations and tail shape;
- the bottom-up priors for motors, inverters, harness, thermal and mounts.

Until now this detail came from one hand-written overlay, `examples/eacsize_overlay.json`, filled with
illustrative placeholders. The structure config replaces that overlay with a **catalogue of 67 parameters**.
For each parameter the user chooses one of these:

| Kind | What the user chooses | What enters the configuration |
|---|---|---|
| `choice` | one categorical option (each has a short description) | the option |
| `range` | one value inside [min, max] | the value |
| `band` | an interval [low, high] inside [min, max] | `Param(dist="uniform", low, high)`, so the Monte Carlo carries the uncertainty (fixed if low = high) |

The source tag shows where each default comes from:
- **E (eacsize):** the default is centred on the eacsize run. These are the tail arm, and the wing lift slope
  from the run's aspect ratio (Helmbold).
- **S (scaled):** an HS1 reference placeholder, scaled to the eacsize aircraft. Stations and lengths are
  fractions of the fuselage length, and the wing motor station is a fraction of the semi-span.
- **P (placeholder):** an HS1 reference placeholder, the same value the old overlay held.

With every parameter at its default, the result matches the old overlay: R0003 W_E P50 is 647.0 kg against
646.9 kg.

## 2. Selection, overlay and provenance

A **selection** is a flat JSON object `{parameter id: value}`; any parameter left out takes its default. Files
may also wrap it as `{"selection": {...}}`.

`validate(selection, run)` returns the complete selection. Otherwise it raises `StructureConfigError`, and its
`.errors` list names every offending parameter. The checks are:
- unknown id;
- a value that is not one of the options;
- out of range;
- band low > high;
- nose gear not ahead of the main gear;
- the last bay beyond 0.95 of the fuselage length.

`build_overlay(selection, run)` returns the overlay that `adapters.eacsize.config_from_eacsize` merges onto the
eacsize-mapped fields. Two notes:
- **Motor power shares come from the layout**, so they always sum to 1. `hs1_3` splits `dep.wing_share`
  equally over the two wing motors, with the aft motor taking the rest; `wing_2` gives 0.5 each; the single
  layouts give 1.0. Parameters that do not apply to the chosen layout are ignored, and they don't count as
  changes.
- **Tail arm:** `tail.arm_frac` is emitted only when it is changed. Left at its default, eacsize's own tail arm
  is used, and it keeps following eacsize when the aircraft is re-sized.

The configuration's provenance, and therefore the report's `assumptions`, records:
- the selection hash (sha256 of the canonical selection, first 12 characters);
- the parameters changed from default;
- how many relative values were resolved, and against which fuselage length and span.

## 3. Relative layout and iterations

Layout quantities are stored relative to the aircraft:
- stations as `{"x_frac": f, "z_m": z}`;
- lengths as `{"frac": f, "of": "fuselage_length" | "span" | "semi_span"}`.

`config_from_eacsize` resolves them (`structure_config.resolve_relative`) against the fuselage length and span
of the configuration being built.

- **Partial coupling:** resolved once, against the eacsize run.
- **Full coupling:** `full_coupling(...).config_at(W)` rebuilds the configuration from eacsize re-sized at each
  iteration's MTOM, so stations, gear positions, x_LEMAC, structural length and harness lengths scale with the
  re-sized fuselage, and the wing motors scale with the span. For R0003, going from 750 kg to 950 kg lengthens
  the fuselage from 6.95 m to 7.34 m, and the htail station stays at 0.943 L.

z coordinates are absolute (m) and are not scaled. CG stations that are not exposed as parameters are reference
placeholders, scaled with fuselage length. Their x/L values are:

| fuselage 0.429 | flight controls 0.5 | avionics 0.179 | LV electrical 0.214 | ECS 0.214 | thermal 0.143 | HV harness 0.429 | inverters 0.571 |
|---|---|---|---|---|---|---|---|

## 4. Use

**Dashboard.**
1. Select a run. The *Aircraft Structural Config* card shows that run's saved selection, or the defaults.
2. Change options or values. Changed values are highlighted next to their default, and parameters that don't
   apply are dimmed.
3. Press **Submit & run HS1 iterations**. This validates and saves the selection to
   `runs/hs1_weights/<id>.structure.json`, then runs the HS1 pipeline in the current Partial/Full coupling mode.
   Every later HS1 run for that run id uses the saved selection.

The HS1 card states which input it used: the saved selection (with its hash), or the default overlay.

**CLI.**

```bash
hs1-weights structure-config --run R0003                       # catalogue + defaults as JSON
hs1-weights structure-config --run R0003 --selection sel.json --overlay-out overlay.json   # validate, export
hs1-weights from-eacsize --run R0003 --structure-config sel.json --coupling full --mc --out r3.json
```

The `from-eacsize` output records `structure_config` as `{source: "selection" | "overlay", path, hash,
n_changed, changed, selection}`. `overlay` is null when a selection was used. `--overlay` still works for
hand-written overlays.

**API** (eacsize UI, standard library only; the bridge loads `structure_config.py` directly with `importlib`):

| Method | Path | Body / result |
|---|---|---|
| GET | `/api/runs/<id>/structure-config` | catalogue, groups, defaults, selection (saved or defaults), `saved`, `saved_at`, `hash`, `changed`, `reference` (eacsize fuselage length, span, AR, tail arm, MAC), `stale_errors` |
| POST | `/api/runs/<id>/structure-config` | `{"selection": {...}}`; 400 with `errors: [...]` if invalid |
| DELETE | `/api/runs/<id>/structure-config` | removes the saved selection, so the run falls back to the default overlay |

`GET /api/hs1` also lists `structure_configs` (the run ids that have a saved selection). Deleting a run deletes
its saved selection.

## 5. Catalogue (defaults for R0003)

Kinds: `choice`; `range` (single value); `band` (low–high, uniform). The source column uses E, S and P as defined
in §1.

| Group | id | Parameter | Kind | Default | Allowed | Unit | Src |
|---|---|---|---|---|---|---|---|
| Wing | `wing.sweep_qc_deg` | quarter-chord sweep | range | 0 | −5–15 | deg | P |
| | `wing.a_per_rad` | lift-curve slope | range | 4.95 (Helmbold, AR 8.33) | ±20% | 1/rad | E |
| | `wing.x_lemac_frac` | leading edge of MAC | range | 0.293 | 0.15–0.45 | × L | S |
| Empennage | `tail.type` | tail arrangement | choice | conventional | conventional / T-tail | | P |
| | `tail.arm_frac` | tail arm (follows eacsize unless changed) | range | 0.55 | 0.40–0.75 | × L | E |
| | `htail.aspect_ratio` / `taper` / `sweep_qc_deg` / `tc` | horizontal tail shape | range | 4.5 / 0.7 / 0 / 0.12 | 3–6.5 / 0.3–1 / 0–20 / 0.08–0.16 | | P |
| | `vtail.aspect_ratio` / `taper` / `sweep_qc_deg` / `tc` | vertical tail shape | range | 1.5 / 0.5 / 30 / 0.12 | 0.8–2.5 / 0.3–1 / 0–50 / 0.08–0.16 | | P |
| Fuselage | `fuselage.structural_length_frac` | structural length (Raymer L) | range | 0.929 | 0.75–1.0 | × L | S |
| DEP layout | `dep.layout` | propulsor layout | choice | `hs1_3` | `hs1_3` 2 wing + aft · `wing_2` · `nose_1` · `aft_1` | | P |
| | `dep.wing_share` | power on the wing motors (`hs1_3`) | range | 0.6 | 0.2–0.9 | | P |
| | `dep.wing_y_frac` | wing motor spanwise station | range | 0.419 | 0.15–0.9 | × semi-span | S |
| | `dep.wing_motor_x_frac` | wing motor station x | range | 0.229 | 0.1–0.5 | × L | S |
| | `dep.wing_inverter` | wing inverters | choice | nacelle | nacelle / fuselage | | P |
| | `dep.aft_motor_x_frac` | aft motor station x | range | 0.943 | 0.8–1.0 | × L | S |
| | `dep.nose_motor_x_frac` | nose motor station x | range | 0.03 (assumed) | 0–0.15 | × L | P |
| Motors & propellers | `motor.sp_motor_kw_per_kg` | motor specific power | band | 4–6 | 2–12 | kW/kg | P |
| | `motor.sp_inv_kw_per_kg` | inverter specific power | band | 12–20 | 5–40 | kW/kg | P |
| | `prop.wing_mass_kg` / `prop.fus_mass_kg` | propeller mass (each) | band | 3.3–4.7 / 5.0–7.0 | 1–15 / 1–20 | kg | P |
| | `fairing.wing_kg` / `fairing.fus_kg` | fairing mass | band | 1.5–3.5 / 1.0–3.0 | 0–8 | kg | P |
| Harness & bus | `bus.v` | HV bus | choice | 400 | 270 / 400 / 800 | V | P |
| | `harness.conductor` | conductor | choice | Cu | Cu / Al | | P |
| | `harness.length_frac` | conductor length per motor | range | 1.0 | 0.4–2.0 | × L | S |
| | `harness.j_allow_a_per_mm2` | allowable current density | band | 3–6 | 1–10 | A/mm² | P |
| | `harness.f_ins` | insulation and shielding factor | band | 1.3–1.8 | 1.0–2.5 | | P |
| Thermal | `thermal.cooling` | cooling | choice | liquid | liquid / air | | P |
| | `thermal.k_th_kg_per_kw` | thermal mass per kW | band | 1.5–3.0 | 0.5–6 | kg/kW | P |
| | `pack.heat_kw` | pack heat, hot day | band | 2–5 | 0.5–15 | kW | P |
| Systems | `systems.lv_system_kg` | LV system | band | 4–8 | 1–20 | kg | P |
| | `systems.c_mount_kg_per_kn` | motor mount per kN | band | 0.4–0.9 | 0.1–2 | kg/kN | P |
| Battery bays | `bays.count` | bays | choice | 2 | 1–4 | | P |
| | `bays.split` | energy split | choice | equal | equal / forward (first ×1.5) / aft (last ×1.5) | | P |
| | `bays.x_first_frac` / `bays.pitch_frac` | first bay x / pitch | range | 0.314 / 0.071 | 0.15–0.6 / 0.03–0.2 | × L | S |
| | `bays.hw_mass_per_bay_kg` | bay hardware (each) | band | 3–6 | 1–15 | kg | P |
| | `bays.c_ret_kg_per_kn` | 18 g retention per kN | band | 0.15–0.4 | 0.05–1 | kg/kN | P |
| | `bays.cutout_area_m2` | total cut-out area | range | 0.6 | 0–2 | m² | P |
| | `bays.k_cut` | cut-out reinforcement factor | band | 0.5–2.0 | 0.2–4 | | P |
| Equipment | `equip.brs` | BRS | choice | fitted | fitted / none | | P |
| | `equip.<item>_kg` | efis 3.5, com radio 1.5, transponder 1.0, seats 16, interior 12, BRS 12, LV battery 3, lighting 1.5, HV protection 4 | range | as listed | per item | kg | P |
| Materials | `mat.wing` / `htail` / `vtail` / `fuselage` / `gear` | material | choice | carbon ×4, aluminium gear | 6 material classes | | P |
| Mass & CG stations | `mass.light_pilot_kg` | light pilot (CG case) | range | 60 | 45–90 | kg | P |
| | `cg.seat_x_frac` / `wing` / `main_gear` / `nose_gear` / `htail` / `vtail` | stations x | range | 0.286 / 0.321 / 0.407 / 0.086 / 0.943 / 0.929 | see the module | × L | S |

Every placeholder default and band is a **placeholder**, not data: the report flags the related priors as
`assumption=true`. Replace them with vendor data, layout drawings and sized load paths as the design matures.
