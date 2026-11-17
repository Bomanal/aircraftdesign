"""Unit tests per routine N1–N12 plus pipeline idempotence (P1-3). Synthetic records only."""
import math

import pytest
from conftest import synthetic_record, synthetic_sources, v
from hypothesis import given, settings
from hypothesis import strategies as st

from hs1_weights.data import normalize as N
from hs1_weights.data.derive import fuselage_wetted_area_torenbeek, mac_trapezoidal, tc_from_airfoil
from hs1_weights.data.io import record_from_dict
from hs1_weights.units import KT, LB


def ctx(**kw):
    return N.NormContext(synthetic_sources(), **kw)


def run1(**over):
    recs, logs = N.run_pipeline([record_from_dict(synthetic_record(**over))], ctx())
    return recs[0], logs


# N1 -----------------------------------------------------------------------------
def test_n1_converts_to_si():
    r, _ = run1(mtow=v(1650.0, "lb"), v_c=v(110.0, "KCAS"), fuel_capacity_total=v(26.0, "US gal"))
    assert r.get("mtow") == pytest.approx(1650.0 * LB)
    assert r.get("v_c") == pytest.approx(110.0 * KT)
    assert r.get("fuel_capacity_total") == pytest.approx(26.0 * 3.785411784)


def test_n1_kias_flagged():
    r, _ = run1(v_ne=v(150.0, "KIAS"))
    assert "IAS" in r.fields["v_ne"].note and any("KIAS" in w for w in r.warnings)


def test_n1_ambiguous_kmh_mph_resolved_by_plausibility():
    # 270 as km/h = 75 m/s (plausible V_NE); as mph = 120.7 m/s (also in band but above... ) -> use stall
    r, _ = run1(v_s0=v(80.0, "km/h|mph"), v_ne=v(150.0, "KCAS"), v_c=v(110.0, "KCAS"))
    # 80 km/h = 22.2 m/s plausible; 80 mph = 35.8 m/s also plausible -> unresolved, confidence <= 0.5
    assert r.fields["v_s0"].confidence <= 0.5
    r2, _ = run1(v_ne=v(320.0, "km/h|mph"), v_c=v(110.0, "KCAS"))
    # 320 km/h = 88.9 m/s plausible; 320 mph = 143 m/s plausible but...both in band -> check order vs v_c=56.6
    assert r2.fields["v_ne"].unit == "m/s"
    r3, _ = run1(v_s1=v(25.0, "km/h|mph"))  # 6.9 m/s implausible, 11.2 m/s implausible -> unresolved
    assert r3.fields["v_s1"].confidence <= 0.5
    r4, _ = run1(v_s1=v(40.0, "km/h|mph"))  # 11.1 m/s out of band, 17.9 m/s in band -> mph
    assert r4.get("v_s1") == pytest.approx(40.0 * 0.44704) and r4.fields["v_s1"].confidence == 0.9


# N2 -----------------------------------------------------------------------------
def test_n2_selects_highest_tier_then_latest_and_widens_sigma():
    r, _ = run1(empty_weight=v(500.0, source="sB"),
                alternates={"empty_weight": [v(520.0, source="sA"), v(530.0, source="sA2")]})
    assert r.get("empty_weight") == 530.0          # tier A, later date
    spread = (530.0 - 500.0) / 530.0
    assert r.sigma_parts["spread"] == pytest.approx(spread)
    assert any("spread" in w for w in r.warnings)


def test_n2_duplicate_key_marked_not_dropped():
    a = record_from_dict(synthetic_record(0))
    b = record_from_dict(synthetic_record(1, tcds_model="S0", mtow=v(750.0)))
    recs, _ = N.run_pipeline([a, b], ctx())
    assert len(recs) == 2 and recs[1].duplicate_of == "synthetic_000" and not recs[1].calibration_eligible


# N3 -----------------------------------------------------------------------------
def test_n3_licensed_adds_oil_and_records_provenance():
    r, _ = run1(empty_weight_def="licensed_empty", oil_capacity=v(5.7, "L"))
    assert r.get("empty_weight") == pytest.approx(500.0 + 5.7 * 0.88)
    assert r.fields["empty_weight"].method == "N3 licensed+oil"
    assert "oil_capacity" in r.fields["empty_weight"].derived_from
    assert r.sigma_parts["def"] == pytest.approx(0.01)


def test_n3_licensed_without_oil_inflates_sigma():
    d = synthetic_record(empty_weight_def="licensed_empty")
    del d["oil_capacity"]
    r = N.run_pipeline([record_from_dict(d)], ctx())[0][0]
    assert r.get("empty_weight") == 500.0 and r.sigma_parts["def"] == pytest.approx(0.03)


def test_n3_dry_adds_three_percent_of_total_fuel():
    r, _ = run1(empty_weight_excludes_unusable_fuel=True)
    assert r.get("empty_weight") == pytest.approx(500.0 + 0.03 * 100.0 * 0.72)


def test_n3_dry_uses_tcds_unusable_when_given():
    r, _ = run1(empty_weight_excludes_unusable_fuel=True, fuel_unusable=v(5.0, "L"), fuel_type="jet_a")
    assert r.get("empty_weight") == pytest.approx(500.0 + 5.0 * 0.80)


@pytest.mark.parametrize("d,s", [("basic_empty_gama", 0.0), ("standard_empty", 0.03), ("marketing_from", 0.06),
                                 ("unknown", 0.03)])
def test_n3_definition_sigma(d, s):
    r, _ = run1(empty_weight_def=d)
    assert r.sigma_parts["def"] == pytest.approx(s)


# N4 -----------------------------------------------------------------------------
def test_n4_does_not_infer_missing_fuel():
    d = synthetic_record()
    del d["fuel_capacity_total"]
    r, logs = N.run_pipeline([record_from_dict(d)], ctx())
    assert r[0].get("fuel_capacity_total") is None
    assert any(e.routine == "N4" and e.field == "fuel_capacity_total" for e in logs)


# N5 -----------------------------------------------------------------------------
def test_n5_censored_uses_structural_weight_when_available():
    r, _ = run1(cert="astm_f2245_lsa", mtow=v(600.0), structural_design_weight=v(640.0))
    assert r.mtow_censored and r.design_weight_kg == 640.0 and not r.design_weight_latent


def test_n5_censored_latent():
    r, _ = run1(cert="astm_f2245_lsa", mtow=v(1320.0, "lb"))
    assert r.mtow_censored and r.design_weight_kg is None and r.design_weight_latent


# N6 -----------------------------------------------------------------------------
@pytest.mark.parametrize("tag,npos,nneg", [
    ("far23_normal", 3.8, -1.52), ("far23_utility", 4.4, -1.76), ("far23_aerobatic", 6.0, -3.0),
    ("cs_vla", 3.8, -1.5), ("astm_f2245_lsa", 4.0, -2.0), ("eu_ultralight", 4.0, -2.0)])
def test_n6_inference_table(tag, npos, nneg):
    r, _ = run1(cert=tag)
    assert r.get("n_limit_pos") == pytest.approx(npos) and r.get("n_limit_neg") == pytest.approx(nneg)
    assert r.fields["n_limit_pos"].method == "N6"


def test_n6_far23_formula_heavy():
    r, _ = run1(mtow=v(5000.0))
    assert r.get("n_limit_pos") == pytest.approx(2.1 + 24000.0 / (5000.0 / LB + 10000.0))


def test_n6_experimental_no_inference_and_published_kept():
    r, _ = run1(cert="experimental")
    assert r.get("n_limit_pos") is None
    r2, _ = run1(cert="experimental", n_limit_pos=v(4.4, "g"))
    assert r2.get("n_limit_pos") == 4.4


def test_n6_multi_category_uses_matching_mtow():
    r, _ = run1(cert="far23_normal", mtow=v(900.0),
                cert_categories={"far23_normal": v(1000.0), "far23_utility": v(900.0)})
    assert r.cert_used == "far23_utility" and r.get("n_limit_pos") == 4.4


# N7 -----------------------------------------------------------------------------
def test_n7_vd_from_vne_and_q():
    r, _ = run1()
    assert r.get("v_d") == pytest.approx(150.0 * KT / 0.9)
    assert r.get("q_c") == pytest.approx(0.5 * 1.225 * (110.0 * KT) ** 2)
    assert r.fields["v_d"].derived_from == ("v_ne",)


def test_n7_vc_from_vno_then_formula():
    d = synthetic_record(v_no=v(105.0, "KCAS"))
    del d["v_c"]
    r = N.run_pipeline([record_from_dict(d)], ctx())[0][0]
    assert r.get("v_c") == pytest.approx(105.0 * KT) and r.fields["v_c"].method == "N7"
    del d["v_no"]
    del d["v_h"]
    r = N.run_pipeline([record_from_dict(d)], ctx())[0][0]
    ws_psf = 750.0 * 9.80665 / 11.0 / 47.880259
    assert r.get("v_c") / KT == pytest.approx(33.0 * math.sqrt(ws_psf), rel=1e-5)
    assert any("formula minimum" in w for w in r.warnings)


# N8 -----------------------------------------------------------------------------
def test_n8_mac_hand_calc():
    # S=11, b=10, λ=0.8: c_r = 22/18 = 1.2222; c̄ = (2/3)(1.2222)(1+0.8+0.64)/1.8 = 1.10425
    assert mac_trapezoidal(11.0, 10.0, 0.8) == pytest.approx(2 / 3 * (22 / 18) * 2.44 / 1.8, rel=1e-12)
    assert mac_trapezoidal(11.0, 10.0, 1.0) == pytest.approx(1.1)


def test_n8_torenbeek_wetted_area_hand_calc():
    """L = 6.5, W = 1.2, H = 1.3 m.
    D = sqrt(1.56) = 1.2489995997; λ_f = 6.5/D = 5.2041649987
    (1 − 2/λ_f) = 0.6156924309; ^(2/3) = 0.7237288352; (1 + 1/λ_f²) = 1 + 1.56/42.25 = 1.0369230769
    π·D·L = 25.5050117835;  S = 25.5050117835 × 0.7237288352 × 1.0369230769 = 19.1402649299 m²."""
    assert fuselage_wetted_area_torenbeek(6.5, 1.2, 1.3) == pytest.approx(19.1402649299, abs=1e-9)


def test_n8_derived_fields_carry_provenance():
    r, _ = run1()
    assert r.fields["fuselage_wetted_area"].derived_from == ("fuselage_length", "fuselage_max_width",
                                                             "fuselage_max_height")
    assert r.fields["fuselage_wetted_area"].method == "N8.swet_torenbeek"
    assert r.get("aspect_ratio") == pytest.approx(100.0 / 11.0)


def test_n8_constant_chord_and_missing_taper():
    d = synthetic_record()
    del d["taper"]
    r = N.run_pipeline([record_from_dict(d)], ctx())[0][0]
    assert r.get("taper") is None and r.get("mac") is None
    r2 = N.run_pipeline([record_from_dict({**d, "constant_chord_3view": True})], ctx())[0][0]
    assert r2.get("taper") == 1.0 and any("3-view" in w for w in r2.warnings)


@pytest.mark.parametrize("des,tc", [("NACA 23015", 0.15), ("NACA 2412", 0.12), ("NACA 63-415", 0.15),
                                    ("NACA 64A215", 0.15), ("GA(W)-1", None), ("", None)])
def test_n8_tc_from_airfoil(des, tc):
    assert tc_from_airfoil(des) == (pytest.approx(tc) if tc else None)


def test_n8_tail_arm_not_defaulted():
    d = synthetic_record()
    del d["tail_arm"]
    assert N.run_pipeline([record_from_dict(d)], ctx())[0][0].get("tail_arm") is None


# N9 -----------------------------------------------------------------------------
def test_n9_sigma_composition():
    r, _ = run1(empty_weight=v(500.0, source="sB"), empty_weight_def="unknown", production_status="prototype")
    assert r.sigma_rel == pytest.approx(math.sqrt(0.04 ** 2 + 0.03 ** 2 + 0.04 ** 2))


def test_n9_tier_c_excluded():
    r, _ = run1(empty_weight=v(500.0, source="sC"))
    assert r.sigma_rel is None and not r.calibration_eligible


# N10 ----------------------------------------------------------------------------
def test_n10_flags_never_drops():
    recs = [record_from_dict(synthetic_record(i)) for i in range(8)]
    recs.append(record_from_dict(synthetic_record(9, empty_weight=v(300.0))))
    out, _ = N.run_pipeline(recs, ctx())
    assert len(out) == 9
    flagged = [r for r in out if r.outlier]
    assert [r.id for r in flagged] == ["synthetic_009"] and not flagged[0].calibration_eligible
    out2, _ = N.run_pipeline(recs, ctx(confirmed_outliers=frozenset({"synthetic_009"})))
    assert [r for r in out2 if r.id == "synthetic_009"][0].calibration_eligible


# N11 ----------------------------------------------------------------------------
def test_n11_electric():
    r, _ = run1(powertrain="battery_electric", battery_energy_total=v(24.8, "kWh"),
                battery_mass_installed=v(140.0), max_landing_weight=v(700.0))
    assert r.sigma_parts["def"] >= 0.03 and any("pack" in w for w in r.warnings)
    assert r.get("pack_wh_per_kg") == pytest.approx(24800.0 / 140.0)
    assert r.get("landing_weight") == r.get("mtow")
    r2, _ = run1(powertrain="battery_electric", empty_weight_includes_pack=True)
    assert r2.sigma_parts["def"] == 0.0


# N12 ----------------------------------------------------------------------------
def test_n12_eligibility():
    r, _ = run1()
    assert r.calibration_eligible and r.ineligible_reasons == []
    d = synthetic_record()
    del d["span"]
    del d["v_ne"]
    del d["v_c"]
    r = N.run_pipeline([record_from_dict(d)], ctx())[0][0]
    assert {"missing span", "missing v_ne and v_c"} <= set(r.ineligible_reasons)


# idempotence --------------------------------------------------------------------
over_strategy = st.fixed_dictionaries({}, optional={
    "empty_weight_def": st.sampled_from(["basic_empty_gama", "licensed_empty", "unknown", "marketing_from"]),
    "cert": st.sampled_from(["far23_normal", "far23_utility", "astm_f2245_lsa", "experimental", "cs_vla"]),
    "powertrain": st.sampled_from(["piston_avgas", "battery_electric"]),
    "empty_weight_excludes_unusable_fuel": st.booleans(),
    "production_status": st.sampled_from(["production", "prototype"]),
    "mtow": st.sampled_from([v(600.0), v(1650.0, "lb"), v(750.0), v(980.0)]),
})


@settings(max_examples=40, deadline=None)
@given(over=over_strategy)
def test_pipeline_idempotent(over):
    recs = [record_from_dict(synthetic_record(i, **over)) for i in range(4)]
    once, _ = N.run_pipeline(recs, ctx())
    twice, _ = N.run_pipeline(once, ctx())
    assert once == twice
