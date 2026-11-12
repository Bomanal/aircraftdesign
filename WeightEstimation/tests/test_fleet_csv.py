"""Clustered fleet CSV import (data/fleet), NI cluster imputation and its propagation into calibration sigma."""
import json
import math

import pytest
from conftest import ROOT

from hs1_weights.calibration import calibrate
from hs1_weights.calibration.fleet import build_fleet_matrix
from hs1_weights.data.dataset import build_dataset, build_from_records
from hs1_weights.data.fleet_csv import load_fleet_csv, map_cert, map_ew_def
from hs1_weights.data.impute import IMPUTABLE, NEVER_IMPUTED
from hs1_weights.data.normalize import NormContext, run_pipeline
from hs1_weights.schema import AircraftRecord, CertTag, EmptyWeightDef, Material, Powertrain, Source, Tier, V

FLEET = ROOT / "data" / "fleet" / "fleet_clusters.csv"
ELIGIBLE = ["cessna_172", "cessna_182", "diamond_da40", "pip_velis_electro", "tecnam_p2008", "vans_rv12"]


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    return build_dataset(ROOT / "data" / "records", ROOT / "data" / "sources.csv", tmp_path_factory.mktemp("b"),
                         fleet_csv=FLEET, fleet_accessed="2026-11-12")


# --- import -------------------------------------------------------------------------------------------
def test_import_records_sources_and_tiers():
    recs, srcs, rep = load_fleet_csv(FLEET, "2026-11-12")
    assert len(recs) == rep.n_rows == 47 and len({r.id for r in recs}) == 47
    assert all(s.accessed == "2026-11-12" and s.url for s in srcs.values())
    by = {r.id: r for r in recs}
    c172 = by["cessna_172"]
    assert srcs[c172.mtow.source].tier == Tier.A and srcs[c172.empty_weight.source].tier == Tier.B
    assert srcs[c172.mtow.source].url.endswith(".pdf")                    # TCDS URL picked for the tier A source
    assert srcs[by["cessna_150"].empty_weight.source].tier == Tier.C_PLUS
    assert c172.cluster == "C4" and c172.rated_power_total.value == pytest.approx(134.23)
    assert by["diamond_da42"].rated_power_total.value == pytest.approx(250.0)   # 2 x 125 kW
    assert by["pip_velis_electro"].fuel_capacity_total is None and by["pip_velis_electro"].empty_weight_includes_pack
    assert by["shark_aero"].gear.value == "fixed_tricycle"                 # tier Z gear dropped -> cluster mode
    assert any(s.startswith("shark_aero.gear: tier Z") for s in rep.imputed_categorical)


@pytest.mark.parametrize("text,tag", [
    ("CS-LSA/EASA TC + SC", CertTag.CS_LSA), ("CS-VLA / ASTM F2245", CertTag.CS_VLA),
    ("Part 23 using JAR-VLA (DGCA)", CertTag.CS_VLA), ("ASTM F2245 / experimental", CertTag.ASTM_F2245),
    ("experimental/ASTM", CertTag.EXPERIMENTAL), ("FAR23 normal/utility", CertTag.FAR23_NORMAL),
    ("FAR23/JAR23 utility/aerobatic", CertTag.FAR23_UTILITY), ("FAR23 aerobatic", CertTag.FAR23_AEROBATIC),
    ("CS-23 commuter", CertTag.FAR23_COMMUTER), ("UL/LSA", CertTag.EU_UL), ("LSA/UL", CertTag.ASTM_F2245),
    ("CAR 4a", CertTag.FAR23_NORMAL), ("CS-LSA / FAR23", CertTag.CS_LSA)])
def test_map_cert(text, tag):
    assert map_cert(text) == tag


def test_map_ew_def():
    assert map_ew_def("Basic Empty Weight") == (EmptyWeightDef.BASIC_GAMA, None)
    assert map_ew_def("basic empty weight - with batteries") == (EmptyWeightDef.BASIC_GAMA, True)
    assert map_ew_def("Empty Weight, Standard")[0] == EmptyWeightDef.STANDARD_NO_OPTIONS
    assert map_ew_def("w/options")[0] == EmptyWeightDef.UNKNOWN


# --- dataset / eligibility -------------------------------------------------------------------------------
def test_only_tier_ab_variant_records_are_eligible(build):
    m = build.manifest
    assert m["n_records"] == 47 and m["eligible_ids"] == ELIGIBLE
    assert "empty_weight source not tier A/B" in m["ineligible"]["cessna_150"]
    assert m["ineligible"]["vans_rv7"] == "missing type/variant"
    assert m["fleet_csv"]["clusters"]["C3"]["n"] == 9 and m["fleet_csv"]["clusters"]["C3"]["deficiencies"]


def test_observations_never_imputed_and_provenance_complete(build):
    assert not set(IMPUTABLE) & set(NEVER_IMPUTED)
    for _, row in build.rows.iterrows():
        prov = json.loads(row["provenance"])
        for f in NEVER_IMPUTED:
            assert not prov.get(f, {}).get("method", "").startswith("NI.")
        for f in json.loads(row["imputed_fields"]):
            if f == "fuel_tanks":
                continue
            p = prov[f]
            assert p["method"].startswith("NI.") and p["derived_from"] and p["confidence"] <= 0.5
            assert p["sd"] > 0 or p["method"] == "NI.fus_width_eq_height"


def test_build_is_deterministic(build, tmp_path):
    again = build_dataset(ROOT / "data" / "records", ROOT / "data" / "sources.csv", tmp_path, fleet_csv=FLEET,
                          fleet_accessed="2026-11-12")
    assert again.content_hash == build.content_hash


# --- NI methods on a synthetic cluster ---------------------------------------------------------------------
SRC = {"s": Source("s", "synthetic test source", "spec_sheet", Tier.B, "test://", "2026-11-12")}


def _rec(i, mtow, cluster="K1", span=None, pt=Powertrain.PISTON_AVGAS):
    return AircraftRecord(id=f"t{i}", type_name="T", variant="v", year_first_flight=None, cert=CertTag.FAR23_NORMAL,
                          cluster=cluster, powertrain=pt, material_primary=Material.ALUMINIUM,
                          mtow=V(mtow, "kg", "s"), empty_weight=V(0.6 * mtow, "kg", "s"),
                          wing_area=V(0.012 * mtow, "m2", "s"),
                          span=None if span is None else V(span, "m", "s"))


def _span(m):
    return 2.0 * (0.012 * m) ** 0.5


def _norm(recs, priors=False):
    out, _ = run_pipeline(recs, NormContext(SRC, impute=True, design_priors=priors))
    return {r.id: r for r in out}


def test_cluster_regression_recovers_exact_power_law():
    masses = [500, 650, 800, 950, 1100]
    recs = [_rec(i, m, span=_span(m)) for i, m in enumerate(masses)] + [_rec(9, 700)]
    r = _norm(recs)["t9"]
    nv = r.fields["span"]
    assert nv.method == "NI.cluster_regression" and nv.value == pytest.approx(_span(700), rel=1e-6)
    assert nv.sd == pytest.approx(0.03) and "span" in r.imputed          # exact fit -> sd floor


def test_hierarchy_offset_then_fleet_then_prior():
    fleet = [_rec(i, m, cluster="K1", span=_span(m)) for i, m in enumerate([500, 650, 800, 950, 1100])]
    two = [_rec(10 + i, m, cluster="K2", span=1.1 * _span(m)) for i, m in enumerate([600, 900])]
    recs = fleet + two + [_rec(20, 750, cluster="K2"), _rec(21, 750, cluster="K3")]
    rs = _norm(recs)
    assert rs["t20"].fields["span"].method == "NI.cluster_offset"
    # cluster intercept with the pooled fleet slope (K2 donors shift that slope slightly off 0.5)
    assert rs["t20"].fields["span"].value == pytest.approx(1.1 * _span(750), rel=2e-3)
    assert rs["t21"].fields["span"].method == "NI.fleet_regression"
    assert "taper" not in rs["t21"].fields                                  # no data, priors off -> missing
    rp = _norm(recs, priors=True)["t21"]
    assert rp.fields["taper"].method == "NI.design_prior" and rp.fields["taper"].sd > 0


def test_imputed_values_are_never_donors():
    recs = [_rec(i, m, span=_span(m)) for i, m in enumerate([500, 650, 800])] + [_rec(8, 700), _rec(9, 900)]
    rs = _norm(recs)
    # only 3 observed donors: below N_REG, so the cluster median/offset path is used, never a 4th imputed donor
    assert rs["t8"].fields["span"].method in ("NI.cluster_median", "NI.cluster_offset", "NI.fleet_regression")
    assert "t9" not in rs["t8"].fields["span"].note


# --- calibration -------------------------------------------------------------------------------------------
def test_imputation_sigma_widens_likelihood_and_calibration_runs(build):
    fm = build_fleet_matrix(build.rows, build.components)
    assert fm.ids == ELIGIBLE and fm.aircraft_ok.all()
    sig_rel = build.rows.set_index("id").loc[ELIGIBLE, "sigma_rel"].to_numpy()
    assert (fm.sigma_input > 0.03).all()
    assert fm.sigma_meas == pytest.approx([math.hypot(a, b) for a, b in zip(sig_rel, fm.sigma_input, strict=False)])
    # the electric record (pack mass from a design prior) is the least informative
    assert fm.sigma_input[fm.ids.index("pip_velis_electro")] == fm.sigma_input.max()
    res = calibrate(build.rows, build.components, build.content_hash, engine="map", seed=0)
    assert res.diagnostics["success"] and res.metadata["collapse_struct"]
    assert set(res.metadata["imputation"]["sigma_input"]) == set(ELIGIBLE)


def test_without_design_priors_no_record_reaches_aircraft_level(tmp_path):
    recs, srcs, _ = load_fleet_csv(FLEET, "2026-11-12")
    b = build_from_records(recs, srcs, impute=True, design_priors=False)
    fm = build_fleet_matrix(b.rows, b.components)
    assert fm.n == 6 and not fm.aircraft_ok.any()
    with pytest.raises(ValueError, match="no evaluable"):
        calibrate(b.rows, b.components, b.content_hash, engine="map")
