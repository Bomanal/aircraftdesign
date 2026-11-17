"""Seed tests for certification.py. Expected values are hand-calculated (see docs/08_validation_and_tests.md)."""
import math

import pytest

from hs1_weights.certification import (
    ASTM_F2245,
    BASES,
    FAR23_L1,
    KT,
    LB,
    PSF,
    RHO0,
    G,
    envelope,
    evaluate,
    gust_load_factors,
    landing_descent_velocity,
    min_area_for_stall,
    stall_speed,
)

CFG = dict(mass_kg=700.0, area_m2=11.0, cbar_m=1.10, a_per_rad=5.0,
           cl_max_clean=1.5, cl_max_land=2.0, vc_kt=105.0, vh_kt=115.0)


def test_far23_n_pos_capped_at_3_8_for_light_aircraft():
    # 700 kg = 1543.2 lb -> 2.1 + 24000/11543.2 = 4.179 -> capped 3.8
    assert FAR23_L1.n_pos(700.0) == pytest.approx(3.8)


def test_far23_n_pos_formula_for_heavy_aircraft():
    m = 5000.0
    expected = 2.1 + 24000.0 / (m / LB + 10000.0)
    assert expected < 3.8
    assert FAR23_L1.n_pos(m) == pytest.approx(expected)


def test_far23_negative_is_0_4_of_positive():
    assert FAR23_L1.n_neg(700.0) == pytest.approx(-0.4 * 3.8)


def test_astm_fixed_manoeuvre_factors():
    assert ASTM_F2245.n_pos(700.0) == 4.0 and ASTM_F2245.n_neg(700.0) == -2.0


def test_far23_vc_coefficient_matches_33_sqrt_psf():
    ws_psf = 13.0
    vc_kt = FAR23_L1.vc_min_coeff.value * math.sqrt(ws_psf * PSF) / KT
    assert vc_kt == pytest.approx(33.0 * math.sqrt(ws_psf), rel=1e-6)


def test_gust_formula_hand_calc():
    ws, c, a, u, v = 624.06, 1.10, 5.0, 15.24, 105.0 * KT
    mu = 2 * ws / (RHO0 * c * a * G)
    kg = 0.88 * mu / (5.3 + mu)
    dn = kg * RHO0 * u * v * a / (2 * ws)
    up, dn_, kg_out = gust_load_factors(ws, c, a, u, v)
    assert kg_out == pytest.approx(kg) and up == pytest.approx(1 + dn) and dn_ == pytest.approx(1 - dn)


def test_stall_and_min_area_are_inverse():
    s = min_area_for_stall(700.0, 59.0, 1.5)
    assert stall_speed(700.0, s, 1.5) / KT == pytest.approx(59.0)


def test_landing_sink_clamped():
    assert landing_descent_velocity(1.0) / 0.3048 == pytest.approx(7.0)
    assert landing_descent_velocity(1e6) / 0.3048 == pytest.approx(10.0)


def test_envelope_governing_bases_for_reference_case():
    env = envelope([evaluate(b, **CFG) for b in BASES.values()])
    assert env["n_ult_pos"]["governing"] == "ASTM_F2245"
    assert env["n_ult_pos"]["value"] == pytest.approx(6.0)
    assert env["items_of_mass_aft"]["forward"]["value"] == 18.0
    assert env["items_of_mass_aft"]["forward"]["governing"] == "FAR23_L1"
    assert env["dynamic_seat_required"] is True


def test_gust_governs_at_low_mass():
    r = evaluate(FAR23_L1, **{**CFG, "mass_kg": 600.0})
    assert r["pos_governed_by"] == "gust"


@pytest.mark.parametrize("m", [600.0, 700.0, 900.0])
def test_ultimate_is_1_5_x_limit(m):
    for b in BASES.values():
        r = evaluate(b, **{**CFG, "mass_kg": m})
        assert r["n_ult_pos"] == pytest.approx(1.5 * r["n_limit_pos"])
