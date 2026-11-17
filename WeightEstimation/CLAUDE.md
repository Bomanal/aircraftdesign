# Instructions for the coding assistant

You are building `hs1_weights`, the weight-prediction module for the HS1 electric aircraft. The specifications are in `docs/`. Read them in numbered order before writing code.

## Operating rules

1. **Spec is authoritative.** Where a spec is ambiguous or conflicts with another, stop and record the conflict in `docs/12_open_items_and_risks.md` under "Spec conflicts". Do not resolve it silently.
2. **Do not invent data.** Never fill a fleet record value from model memory. Every value needs a `Source` row with tier, URL or citation, and access date. Missing is better than guessed.
3. **Do not change certification criteria values.** Values in `certification.py` change only when a purchased standard is cited. Update `ref`, set `verify=False`, and add a changelog line.
4. **Units.** SI internally (kg, m, m², s, N, Pa, W, J). Raymer/Torenbeek equations are imperial: implement them as private functions with explicit unit conversion wrappers, and test each wrapper.
5. **Determinism.** All stochastic routines take a `seed`. Calibration results are versioned artefacts (`artefacts/calibration_<date>_<hash>.nc`).
6. **Provenance through derivations.** A derived value (for example, wetted area from L/W/H) records `derived_from=[field ids]` and `method=<routine id>`.
7. **Tests first for numerics.** Every equation gets a hand-calculated test (doc 08 §3). Every routine gets property tests (monotonicity, units, bounds).
8. **No silent fallbacks.** If an input is missing, raise `MissingInputError` naming the field. Defaults are allowed only where the spec lists them, and they are logged in the report's `assumptions` list.
9. **Style.** Python ≥3.10, type hints throughout, `ruff` + `mypy --strict` on `src/`. Pydantic v2 for I/O models; dataclasses for internal state.
10. **Scope discipline.** Build phase by phase (doc 11). Do not start Phase N+1 until Phase N acceptance criteria pass.

## Domain context you must not lose

- HS1 is battery-electric. There is **no fuel burn**: landing weight = MTOW, and there is **no wing-fuel bending relief**.
- Raymer's GA wing equation contains `W_fw^0.0035`. Passing `W_fw = 0` makes the wing weight zero. Use the override in doc 06 §3.1.
- Certification stall speed is **power-idle**. DEP blowing earns **no** credit against the 59 kt / 61 kt limits.
- Battery packs and motor mounts behind the occupants must use the **18 g forward** retention case (FAA MOC to F3083). This drives battery-bay structure mass.
- Many LSA/UL fleet aircraft have MTOW set by a **regulatory cap**, not structural capability. Treat these as censored (doc 05 N5).
