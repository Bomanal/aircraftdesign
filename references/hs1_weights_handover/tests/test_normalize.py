import pytest

from hs1_weights.normalize import detect_cap, normalize, OIL_DENSITY
from hs1_weights.schema import (AircraftRecord, V, CertTag, EmptyWeightDef, Powertrain, Tier)


def _rec(**kw):
    base = dict(id="x", type_name="Test", variant="t", year_first_flight=2000, cert=CertTag.FAR23_NORMAL,
                mtow=V(1111.0, "kg", "s1"), empty_weight=V(700.0, "kg", "s1"),
                empty_weight_def=EmptyWeightDef.BASIC_GAMA)
    base.update(kw)
    return AircraftRecord(**base)


@pytest.mark.parametrize("mtow,expected", [(600.0, "EU UL 600 kg / CS-LSA"), (598.7, "US LSA 1,320 lb (pre-MOSAIC)"),
                                           (750.0, "CS-VLA / JAR-VLA"), (472.5, "EU UL with ballistic rescue (legacy)")])
def test_cap_detection_picks_nearest(mtow, expected):
    assert detect_cap(mtow) == (True, expected)


def test_uncapped():
    assert detect_cap(1111.0) == (False, "")


def test_licensed_empty_adds_oil():
    n = normalize(_rec(empty_weight_def=EmptyWeightDef.LICENSED), {"s1": Tier.A}, oil_litres=5.7)
    assert n.empty_weight_basic_kg == pytest.approx(700.0 + 5.7 * OIL_DENSITY)


def test_tier_c_excluded():
    n = normalize(_rec(), {"s1": Tier.C})
    assert n.sigma_rel is None


def test_censored_mtow_gives_no_design_weight():
    n = normalize(_rec(mtow=V(600.0, "kg", "s1"), cert=CertTag.ASTM_F2245), {"s1": Tier.A})
    assert n.mtow_censored and n.design_weight_kg is None


def test_prototype_inflates_sigma():
    a = normalize(_rec(), {"s1": Tier.B}).sigma_rel
    b = normalize(_rec(production_status="prototype"), {"s1": Tier.B}).sigma_rel
    assert b > a


def test_electric_pack_warning():
    n = normalize(_rec(powertrain=Powertrain.ELECTRIC, battery_energy_total=V(24.8, "kWh", "s1")), {"s1": Tier.B})
    assert any("pack" in w for w in n.warnings)
