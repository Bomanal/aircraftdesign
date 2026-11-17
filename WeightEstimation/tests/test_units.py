import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hs1_weights import certification as cert
from hs1_weights import units as u

finite = st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False)


def test_constants_match_doc05_n1():
    assert u.LB == 0.45359237 and u.US_GAL == 3.785411784 and u.KT == 0.514444
    assert u.MPH == 0.44704 and u.FT == 0.3048


def test_constants_consistent_with_certification():
    assert u.KT == cert.KT and u.LB == cert.LB and u.G == cert.G and u.RHO0 == cert.RHO0
    assert u.PSF == pytest.approx(cert.PSF, rel=1e-8)


def test_hand_values():
    assert u.lb_to_kg(1000.0) == pytest.approx(453.59237)
    assert u.psf_to_pa(1.0) == pytest.approx(47.880259, rel=1e-8)
    assert u.kt_to_mps(100.0) == pytest.approx(51.4444)
    assert u.to_si(1.0, "hp") == (pytest.approx(745.69987158), "W")
    assert u.to_si(10.0, "deg")[0] == pytest.approx(math.radians(10.0))


@pytest.mark.parametrize("fwd,back", [
    (u.kg_to_lb, u.lb_to_kg), (u.m_to_ft, u.ft_to_m), (u.m_to_in, u.in_to_m), (u.m2_to_ft2, u.ft2_to_m2),
    (u.pa_to_psf, u.psf_to_pa), (u.mps_to_kt, u.kt_to_mps), (u.litre_to_gal, u.gal_to_litre)])
@given(x=finite)
def test_round_trip_identity(fwd, back, x):
    assert back(fwd(x)) == pytest.approx(x, rel=1e-12, abs=1e-12)


@given(x=finite, unit=st.sampled_from(sorted(u.UNIT_TO_SI)))
def test_to_from_si_round_trip(x, unit):
    si, _ = u.to_si(x, unit)
    assert u.from_si(si, unit) == pytest.approx(x, rel=1e-12, abs=1e-12)


def test_unknown_unit_raises():
    with pytest.raises(u.UnknownUnitError):
        u.to_si(1.0, "furlong")
