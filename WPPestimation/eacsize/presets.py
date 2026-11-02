"""
Presets: starting points for experiments and the calibration / cross-check cases.
Each preset holds partial overrides per section; everything else takes the defaults.
"""
from __future__ import annotations
import copy

# risen_eas settings that make this module reproduce risen_eas exactly (regression mode)
_RISEN_L1 = dict(
    inputs=dict(geometry_mode="scaled_reference", gear="retractable", category="none"),
    mission=dict(v_climb_kmh=144.0, v_descent_kmh=198.0, climb_power_frac=65 / 75),
    assumptions=dict(weight_method="risen_L1", cd0_override=0.0120, e_method="fixed", oswald_e=0.80,
                     pack_wh_kg=300.0),
)


def _merge(base, extra):
    out = copy.deepcopy(base)
    for sec, vals in extra.items():
        out.setdefault(sec, {}).update(vals)
    return out


PRESETS = {
    "e750_new_design": dict(
        title="E750 two-seat new design (Raymer class II, constraint-sized)",
        inputs=dict(mode="fixed_mtom", mtom_kg=750, seats=2, seat_layout="side_by_side", gear="retractable",
                    geometry_mode="constraint", category="CS-VLA"),
    ),
    "risen_e600_regression": dict(
        title="Risen E600, risen_eas regression (L1 weights, scaled Risen)",
        **_merge(_RISEN_L1, dict(inputs=dict(mode="fixed_mtom", mtom_kg=600, seats=2))),
    ),
    "risen_e750_regression": dict(
        title="Risen E750, risen_eas regression (L1 weights, scaled Risen)",
        **_merge(_RISEN_L1, dict(inputs=dict(mode="fixed_mtom", mtom_kg=750, seats=2))),
    ),
    "risen_e600_class2": dict(
        title="Risen E600 airframe, Raymer class II weights",
        inputs=dict(mode="fixed_mtom", mtom_kg=600, seats=2, geometry_mode="fixed", wing_area_m2=9.70, span_m=8.99,
                    installed_power_kw=75, gear="retractable", category="EU-UL600"),
    ),
    "four_seat_solve_200km": dict(
        title="Four-seat, solve MTOM for 200 km at 216 km/h (end of life, 30 min reserve)",
        inputs=dict(mode="solve_mtom", target="range", target_range_km=200, size_at_eol=True, seats=4,
                    seat_layout="side_by_side", gear="retractable", geometry_mode="constraint",
                    category="MOSAIC-LSA"),
    ),
    "hansa_ng_e950": dict(
        title="E-Hansa conversion at 950 kg (fixed Hansa-NG wing, 100 kW, 190 Wh/kg)",
        inputs=dict(mode="fixed_mtom", mtom_kg=950, seats=2, seat_layout="side_by_side", gear="fixed",
                    geometry_mode="fixed", wing_area_m2=13.0, span_m=10.51, installed_power_kw=100,
                    oew_override_kg=510, payload_override_kg=150, category="MOSAIC-LSA"),
        mission=dict(v_cruise_kmh=130, h_cruise_m=914),
        requirements=dict(s_to50_max_m=900, s_ld50_max_m=700, roc_min_ms=1.0, ceiling_min_m=3000),
        assumptions=dict(pack_wh_kg=190, cd0_override=0.042, e_method="fixed", oswald_e=0.75,
                         cl_max_clean=1.51, cl_max_land=1.89, cl_max_to=1.7),
    ),
    "velis_electro_check": dict(
        title="Velis Electro cross-check (600 kg, 24.8 kWh, 57.6 kW, fixed gear)",
        inputs=dict(mode="fixed_mtom", mtom_kg=600, seats=2, gear="fixed", geometry_mode="fixed",
                    wing_area_m2=9.51, span_m=10.71, installed_power_kw=57.6, oew_override_kg=288,
                    payload_override_kg=172, category="EU-UL600"),
        mission=dict(v_cruise_kmh=170, h_cruise_m=600, reserve_min=10),
        assumptions=dict(pack_wh_kg=177),
    ),
}


def preset(name):
    p = copy.deepcopy(PRESETS[name])
    p.pop("title", None)
    return p
