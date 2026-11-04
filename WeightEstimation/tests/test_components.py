"""Phase 2 component tests: hand-calc pins, SI-wrapper checks, property tests (doc 08 §3–4)."""
import json
import math

import numpy as np
import pytest
from conftest import ROOT
from hypothesis import given, settings
from hypothesis import strategies as st

from hs1_weights.components import REGISTRY, ComponentModel, ModelContext, evaluate_tree
from hs1_weights.components import raymer_ga as R
from hs1_weights.components import roskam_cessna as C
from hs1_weights.components import torenbeek as T
from hs1_weights.components.base import HarnessRunCtx, ItemCtx, MotorCtx
from hs1_weights.components.battery_bays import bays_mass_kg, cutout_factor
from hs1_weights.components.dep import inertia_relief
from hs1_weights.components.electric import harness_run_mass_kg, pack_mass_kg
from hs1_weights.components.fixed_items import validate_equipment
from hs1_weights.errors import MissingInputError

LB, FT, IN, PSF = 0.45359237, 0.3048, 0.0254, 47.880258980335840

# --- imperial cores: pinned against an independent scalar evaluation of the doc 06 §3 equations ---------
# Inputs: S=118.4 ft², W_fw=1 lb, A=8, Λ=0, q=36 psf, λ=0.6, t/c=0.15, N_z=6, W_dg=1543.24 lb.
PINS = [
    (lambda: R._raymer_wing_imperial(118.4, 1.0, 8.0, 0.0, 36.0, 0.6, 0.15, 6.0, 1543.24), 182.3980052163598),
    (lambda: R._raymer_htail_imperial(6.0, 1543.24, 36.0, 21.5, 0.12, 0.0, 4.5, 0.0, 0.7), 15.969604678811002),
    (lambda: R._raymer_vtail_imperial(0.0, 6.0, 1543.24, 36.0, 12.9, 0.12, 0.5236, 1.5, 0.5), 11.232883225547694),
    (lambda: R._raymer_fuselage_imperial(200.0, 6.0, 1543.24, 14.0, 6.0, 36.0), 150.52093158370343),
    (lambda: R._raymer_main_gear_imperial(4.5, 1543.24, 20.0), 104.43446843631935),
    (lambda: R._raymer_nose_gear_imperial(4.5, 1543.24, 18.0), 26.307450055223537),
    (lambda: R._raymer_engine_installed_imperial(200.0, 1), 340.6645082018225),
    (lambda: R._raymer_fuel_system_imperial(26.0, 0.0, 2, 1), 31.355898985217184),
    (lambda: R._raymer_flight_controls_imperial(20.0, 30.0, 6.0, 1543.24), 17.536289478236448),
    (lambda: R._raymer_electrical_imperial(31.355898985217184, 40.0), 110.81141931318272),
    (lambda: R._raymer_avionics_imperial(20.0), 34.64031590730698),
    (lambda: R._raymer_ac_anti_ice_imperial(1543.24, 2, 34.64031590730698, 0.2), 31.02722887302646),
    (lambda: R._raymer_furnishings_imperial(1543.24), 24.816568000000004),
]


@pytest.mark.parametrize("fn,expected", PINS)
def test_raymer_imperial_pins(fn, expected):
    assert float(fn()) == pytest.approx(expected, rel=1e-12)


def test_raymer_furnishings_floor():
    assert R._raymer_furnishings_imperial(500.0) == 0.0


def test_raymer_hydraulics():
    assert R._raymer_hydraulics_imperial(2000.0) == pytest.approx(2.0)


def hs1_like_ctx(**kw) -> ModelContext:
    """Illustrative inputs only (doc 03 §4 reference point); not HS1 design data."""
    base = dict(
        W_dg=700.0, W_l=700.0, n_ult=6.0, n_ult_case="n_ult=6.00", N_l=4.5, q=0.5 * 1.225 * (105 * 0.514444) ** 2,
        q_D=0.5 * 1.225 * (150 * 0.514444) ** 2, V_C=105 * 0.514444, V_D=150 * 0.514444, V_H=115 * 0.514444,
        mach=0.16, S_w=11.0, b=10.0, sweep_qc=0.0, taper=0.6, tc_root=0.15, W_fw=0.0,
        S_ht=2.0, A_ht=4.5, sweep_ht=0.0, taper_ht=0.7, tc_ht=0.12, S_vt=1.2, A_vt=1.5, sweep_vt=0.5236,
        taper_vt=0.5, tc_vt=0.12, L_t=4.3, fus_L=7.0, fus_L_struct=6.5, fus_W=1.15, fus_H=1.3, S_f=18.0,
        L_m=0.5, L_n=0.45, W_uav=9.0, N_p=2,
    )
    base.update(kw)
    return ModelContext(**base)


# --- SI wrappers -------------------------------------------------------------------------------------------
def test_wing_wrapper_converts_units():
    ctx = hs1_like_ctx(W_fw=100.0 * LB)
    q_psf = ctx.q / PSF
    expected_lb = R._raymer_wing_imperial(11.0 / FT ** 2, 100.0, 100.0 / 11.0, 0.0, q_psf, 0.6, 0.15, 6.0,
                                          700.0 / LB)
    assert R.raymer_wing(ctx).mass_kg == pytest.approx(expected_lb * LB, rel=1e-12)


def test_fuselage_wrapper_converts_units():
    ctx = hs1_like_ctx()
    exp = R._raymer_fuselage_imperial(18.0 / FT ** 2, 6.0, 700.0 / LB, 4.3 / FT, 7.0 / 1.3, ctx.q / PSF) * LB
    assert R.raymer_fuselage(ctx).mass_kg == pytest.approx(exp, rel=1e-12)


def test_gear_wrappers_use_inches():
    ctx = hs1_like_ctx()
    assert R.raymer_main_gear(ctx).mass_kg == pytest.approx(
        R._raymer_main_gear_imperial(4.5, 700.0 / LB, 0.5 / IN) * LB, rel=1e-12)
    assert R.raymer_nose_gear(ctx).mass_kg == pytest.approx(
        R._raymer_nose_gear_imperial(4.5, 700.0 / LB, 0.45 / IN) * LB, rel=1e-12)


def test_fuel_system_wrapper_uses_gallons():
    ctx = hs1_like_ctx(V_t=100.0, V_i=0.0, N_t=2)
    assert R.raymer_fuel_system(ctx).mass_kg == pytest.approx(
        R._raymer_fuel_system_imperial(100.0 / 3.785411784, 0.0, 2, 1) * LB, rel=1e-12)


def test_electric_wfw_override():
    """W_fw = 0 -> finite wing mass equal to the W_fw = 1 lb result (doc 08 §3)."""
    m0 = R.raymer_wing(hs1_like_ctx(W_fw=0.0, is_electric=True)).mass_kg
    m1 = R.raymer_wing(hs1_like_ctx(W_fw=1.0 * LB, is_electric=True)).mass_kg
    assert m0 > 0 and m0 == pytest.approx(m1, rel=1e-12)
    raw = R._raymer_wing_imperial(118.4, 0.0, 8.0, 0.0, 36.0, 0.6, 0.15, 6.0, 1543.24)
    assert raw == 0.0, "the trap the override exists to avoid"


def test_missing_input_named():
    with pytest.raises(MissingInputError) as ei:
        R.raymer_fuselage(hs1_like_ctx(L_t=None))
    assert ei.value.field == "L_t" and "fuselage" in ei.value.where


def test_tailwheel_nose_zero_with_assumption():
    r = R.raymer_nose_gear(hs1_like_ctx(gear_type="fixed_tailwheel"))
    assert r.mass_kg == 0.0 and r.assumptions


# --- alternative methods -------------------------------------------------------------------------------------
def test_unverified_methods_flagged():
    for comp in ("wing", "htail", "vtail", "fuselage", "main_gear"):
        for mid in REGISTRY.methods(comp):
            r = REGISTRY.get(comp, mid)(hs1_like_ctx())
            assert r.transcription_verified == (mid == "raymer_ga"), (comp, mid)


def test_torenbeek_cores_pinned():
    assert T._torenbeek_tail_imperial(6.0, 30.0) == pytest.approx(0.04 * (6.0 * 900.0) ** 0.75, rel=1e-12)
    assert T._torenbeek_gear_imperial(1500.0, (20.0, 0.10, 0.019, 0.0), 1.0) == pytest.approx(
        20.0 + 0.10 * 1500.0 ** 0.75 + 0.019 * 1500.0, rel=1e-12)


def test_cessna_wing_core_pinned():
    assert C._cessna_wing_imperial(1500.0, 120.0, 5.7, 8.0) == pytest.approx(
        0.04674 * 1500.0 ** 0.397 * 120.0 ** 0.360 * 5.7 ** 0.397 * 8.0 ** 1.712, rel=1e-12)


GOLDEN = ROOT / "tests" / "golden"


@pytest.mark.parametrize("method", ["raymer_ga", "torenbeek", "cessna", "usaf"])
def test_textbook_worked_example(method):
    """R4 / P2-2 / P2-3: reproduce the textbook worked example to <= 1%. Needs the project's copy of the text."""
    p = GOLDEN / f"{method}_example.json"
    spec = json.loads(p.read_text())
    if not spec.get("verified_from_text"):
        pytest.skip(f"{p.name}: worked example not yet transcribed from the project's copy (doc 12 SC-10)")
    ctx = ModelContext(**spec["context_si"])
    for comp, exp in spec["expected_kg"].items():
        assert REGISTRY.get(comp, method)(ctx).mass_kg == pytest.approx(exp, rel=0.01)


# --- electric hand calcs ----------------------------------------------------------------------------------
def test_pack_hand_calc():
    assert pack_mass_kg(60_000.0, 300.0, 0.625) == pytest.approx(320.0)


def test_harness_hand_calc():
    assert harness_run_mass_kg(100.0, 4.0, 2.0, 8960.0, 1.0) == pytest.approx(0.448)


def test_inertia_relief_zero_masses_is_exactly_one():
    f, kg = inertia_relief(100.0, 6.0, 700.0, 10.0, [])
    assert f == 1.0 and kg == 0.0


def test_inertia_relief_hand_calc():
    # M_lift = (6·700·g/2)(4·10/6π); M_rel = 2·(20·g·6·2.5); ratio = 1 − 600g/(2100g·2.12207) = 0.865363
    f, kg = inertia_relief(100.0, 6.0, 700.0, 10.0, [(20.0, 2.5), (20.0, 2.5)])
    ratio = 1.0 - 600.0 / (2100.0 * 40.0 / (6.0 * math.pi))
    assert f == pytest.approx(ratio ** 0.49, rel=1e-12) and kg == pytest.approx(100.0 * (1 - ratio ** 0.49))


def test_cutout_factor():
    assert cutout_factor(1.5, 0.4, 20.0) == pytest.approx(1.03)


@settings(deadline=None)
@given(n=st.integers(1, 7), hw=st.floats(0.1, 20.0), c=st.floats(1e-6, 1e-3), mp=st.floats(10.0, 600.0))
def test_bays_strictly_increasing_in_n(n, hw, c, mp):
    assert bays_mass_kg(n + 1, hw, c, 18.0, mp) > bays_mass_kg(n, hw, c, 18.0, mp)


# --- property tests: structural masses non-decreasing in n_ult, W_dg, S, q -------------------------------
STRUCT = ("wing", "htail", "vtail", "fuselage", "main_gear", "nose_gear", "flight_controls")
SCALE = st.floats(1.0, 3.0)


@settings(max_examples=30, deadline=None)
@given(s=SCALE, method=st.sampled_from(["raymer_ga", "torenbeek", "cessna", "usaf"]))
def test_monotone_in_load_and_weight(s, method):
    base, up = hs1_like_ctx(), hs1_like_ctx(n_ult=6.0 * s, W_dg=700.0 * s, W_l=700.0 * s, N_l=4.5 * s)
    for comp in STRUCT:
        if method not in REGISTRY.methods(comp):
            continue
        m = REGISTRY.get(comp, method)
        assert m(up).mass_kg >= m(base).mass_kg - 1e-12, (comp, method)


@settings(max_examples=30, deadline=None)
@given(s=SCALE)
def test_monotone_in_area_and_q(s):
    base = hs1_like_ctx()
    pairs = {"wing": dict(S_w=11.0 * s, b=10.0 * math.sqrt(s)), "htail": dict(S_ht=2.0 * s),
             "vtail": dict(S_vt=1.2 * s), "fuselage": dict(S_f=18.0 * s)}
    for comp, kw in pairs.items():
        m = REGISTRY.get(comp, "raymer_ga")
        assert m(hs1_like_ctx(**kw)).mass_kg >= m(base).mass_kg
        assert m(hs1_like_ctx(q=base.q * s)).mass_kg >= m(base).mass_kg


def test_models_broadcast_over_samples():
    n = np.linspace(5.0, 7.0, 11)
    r = R.raymer_wing(hs1_like_ctx(n_ult=n))
    assert np.shape(r.mass_kg) == (11,) and np.all(np.diff(r.mass_kg) > 0)


def test_registry_protocol_and_defaults():
    for comp in REGISTRY.components():
        for mid in REGISTRY.methods(comp):
            assert isinstance(REGISTRY.get(comp, mid), ComponentModel)
    assert REGISTRY.default("wing", "electric") == "raymer_ga"
    assert REGISTRY.default("furnishings", "electric") == "equipment_list"
    assert REGISTRY.default("electrical_lv", "ice") == "raymer_ga"
    with pytest.raises(KeyError, match="available"):
        REGISTRY.get("wing", "nope")


# --- tree and equipment ---------------------------------------------------------------------------------
def electric_ctx(**kw):
    motors = [MotorCtx("L", 40e3, 5e3, 15e3, 0.92, True, 2.2, True, 6.0, 3.0),
              MotorCtx("R", 40e3, 5e3, 15e3, 0.92, True, 2.2, True, 6.0, 3.0),
              MotorCtx("aft", 60e3, 5e3, 15e3, 0.92, False, 0.0, False, 8.0, 4.0)]
    items = [ItemCtx(n, c, m) for n, c, m in [("efis", "avionics", 9.0), ("seats", "seats", 16.0),
                                                ("trim", "interior", 12.0), ("lvbat", "lv_battery", 4.0),
                                                ("lights", "lighting", 2.0), ("brs", "brs", 13.0)]]
    harness = [HarnessRunCtx(m.id, 6.0, m.p_cont_w / 0.95, 400.0, 8960.0, 5.0, 1.4) for m in motors]
    base = dict(is_electric=True, E_nom_Wh=60e3, e_spec_wh_per_kg=300.0, energy_level="cell", eta_c2p=0.625,
                bay_energy_fractions=(0.5, 0.5), motors=motors, c_mount_kg_per_n=2e-4, n_mount=18.0,
                n_mount_case="18 g fwd", N_bays=2, m_bay_hw_kg=4.0, c_ret_kg_per_n=5e-5, A_cut=0.5, k_cut=1.0,
                k_th_kg_per_w=2e-3, Q_pack_w=3e3, cooling="liquid", ambient_isa_delta_c=30.0, harness=harness,
                lv_system_kg=6.0, equipment=items)
    base.update(kw)
    return hs1_like_ctx(**base)


def test_electric_tree_complete():
    out = evaluate_tree(electric_ctx())
    assert "engine_installed" not in out and "fuel_system" not in out
    for c in ("battery_pack", "battery_bays", "motors", "inverters", "thermal_mgmt", "hv_harness",
              "nacelles_mounts", "propellers", "fixed_items"):
        assert c in out and float(np.asarray(out[c].mass_kg)) > 0, c
    assert out["battery_pack"].mass_kg == pytest.approx(320.0)
    assert out["battery_pack"].extra["per_bay_kg"] == [pytest.approx(160.0)] * 2
    assert 0 < out["wing"].extra["inertia_relief_factor"] < 1
    assert out["fuselage"].extra["cutout_factor"] == pytest.approx(1 + 0.5 / 18.0)
    assert out["furnishings"].method == "equipment_list" and out["furnishings"].mass_kg == pytest.approx(28.0)
    assert out["fixed_items"].mass_kg == pytest.approx(19.0)


def test_pack_level_declaration():
    out = evaluate_tree(electric_ctx(energy_level="pack", e_spec_wh_per_kg=200.0, eta_c2p=None))
    assert out["battery_pack"].mass_kg == pytest.approx(300.0)


def test_bays_need_mount_factor():
    with pytest.raises(MissingInputError, match="n_mount"):
        evaluate_tree(electric_ctx(n_mount=None))


def test_equipment_validation():
    items = [ItemCtx("a", "avionics", 9.0), ItemCtx("b", "toaster", 1.0), ItemCtx("c", "seats", 0.0)]
    errs = validate_equipment(items, brs_fitted=True)
    assert any("toaster" in e for e in errs) and any("mass must be" in e for e in errs)
    assert {"mandatory equipment category 'brs' missing",
            "mandatory equipment category 'lighting' missing"} <= set(errs)
