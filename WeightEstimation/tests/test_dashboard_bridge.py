"""eacsize dashboard -> HS1 pipeline: bridge and HTTP routes (POST/GET/DELETE /api/runs/<id>/hs1)."""
import json
import shutil
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from conftest import ROOT

WPP = ROOT.parent / "WPPestimation"
pytestmark = pytest.mark.skipif(not (WPP / "runs" / "runs.json").exists(), reason="WPPestimation not present")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    sys.path.insert(0, str(WPP))
    from eacsize.hs1_bridge import HS1Bridge
    from eacsize.store import RunStore
    data = tmp_path_factory.mktemp("runs")
    shutil.copy(WPP / "runs" / "runs.json", data / "runs.json")
    store = RunStore(str(data))
    # an empty artefact directory: these tests run on the priors whatever is in WeightEstimation/artefacts
    bridge = HS1Bridge(store.json_path, store.dir, hs1_dir=str(ROOT), python=sys.executable,
                       calibration=str(tmp_path_factory.mktemp("no_artefacts")))
    return store, bridge, data


@pytest.fixture(scope="module")
def server(env):
    from eacsize.ui import make_handler
    store, bridge, data = env
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store, bridge))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", data
    httpd.shutdown()


def call(url, method="GET", body=None):
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_status(server):
    base, _ = server
    code, st = call(f"{base}/api/hs1")
    assert code == 200 and st["available"] and st["results"] == [] and st["couplings"] == ["partial", "full"]
    assert st["calibration"]["mode"] == "prior" and "no calibration artefact" in st["calibration"]["error"]


def test_run_store_fetch_delete(server):
    base, data = server
    assert call(f"{base}/api/runs/R0003/hs1")[0] == 404
    code, res = call(f"{base}/api/runs/R0003/hs1", "POST", {"close": True, "mc": False})
    assert code == 200, res
    assert res["run_id"] == "R0003" and res["closure_point"]["converged"] and "closure_mc" not in res
    assert res["report"]["components"]["battery_pack"]["mass_kg"]["p50"] > 0 and res["comparison"]
    assert (data / "hs1_weights" / "R0003.json").exists()
    assert json.loads((data / "runs.json").read_text()) == json.loads((WPP / "runs" / "runs.json").read_text())
    code, again = call(f"{base}/api/runs/R0003/hs1")
    assert code == 200 and again["generated"] == res["generated"]
    assert call(f"{base}/api/hs1")[1]["results"] == ["R0003|partial"]
    assert call(f"{base}/api/runs/R0003/hs1", "DELETE")[0] == 200
    assert call(f"{base}/api/runs/R0003/hs1")[0] == 404


def test_errors(server):
    base, _ = server
    assert call(f"{base}/api/runs/R9999/hs1", "POST", {})[0] == 404
    assert call(f"{base}/api/runs/bad%20id/hs1")[0] == 400


def test_structure_config_endpoints_and_run(server):
    base, data = server
    code, d = call(f"{base}/api/runs/R0003/structure-config")
    assert code == 200 and not d["saved"] and d["selection"] == d["defaults"] and len(d["catalogue"]) >= 60
    assert d["changed"] == [] and d["groups"][0] == "Wing"
    code, err = call(f"{base}/api/runs/R0003/structure-config", "POST",
                     {"selection": {"htail.taper": 3.0, "dep.layout": "quad"}})
    assert code == 400 and len(err["errors"]) == 2 and "htail.taper" in err["errors"][0]
    assert not (data / "hs1_weights" / "R0003.structure.json").exists()
    code, d = call(f"{base}/api/runs/R0003/structure-config", "POST",
                   {"selection": {"dep.layout": "wing_2", "harness.conductor": "Al"}})
    assert code == 200 and d["saved"] and d["changed"] == ["dep.layout", "harness.conductor"]
    assert (data / "hs1_weights" / "R0003.structure.json").exists()
    assert call(f"{base}/api/runs/R0003/structure-config")[1]["hash"] == d["hash"]
    st = call(f"{base}/api/hs1")[1]
    assert st["results"] == [] and st["structure_configs"] == ["R0003"] and st["structure_config"]
    code, res = call(f"{base}/api/runs/R0003/hs1", "POST", {"coupling": "partial", "close": False, "mc": False})
    assert code == 200, res
    assert res["structure_config"]["source"] == "selection" and res["structure_config"]["hash"] == d["hash"]
    assert res["options"]["structure"] == "saved selection" and res["overlay"] is None
    assert call(f"{base}/api/runs/R9999/structure-config")[0] == 404
    code, d = call(f"{base}/api/runs/R0003/structure-config", "DELETE")
    assert code == 200 and not d["saved"] and not (data / "hs1_weights" / "R0003.structure.json").exists()
    code, res = call(f"{base}/api/runs/R0003/hs1", "POST", {"coupling": "partial", "close": False, "mc": False})
    assert res["structure_config"]["source"] == "overlay" and res["options"]["structure"] == "default overlay"
    call(f"{base}/api/runs/R0003/hs1", "DELETE")


def test_full_coupling_stored_beside_partial(server):
    base, data = server
    code, res = call(f"{base}/api/runs/R0003/hs1", "POST", {"coupling": "full", "mc": False})
    assert code == 200, res
    assert res["coupling"] == "full" and res["options"]["close"] and res["eacsize_closed"]["mtom_kg"] > 0
    assert (data / "hs1_weights" / "R0003.full.json").exists()
    assert call(f"{base}/api/runs/R0003/hs1?coupling=full")[1]["coupling"] == "full"
    code, _ = call(f"{base}/api/runs/R0003/hs1", "POST", {"coupling": "partial", "close": False, "mc": False})
    assert code == 200
    assert sorted(call(f"{base}/api/hs1")[1]["results"]) == ["R0003|full", "R0003|partial"]
    assert call(f"{base}/api/runs/R0003/hs1?coupling=full", "DELETE")[0] == 200
    assert call(f"{base}/api/hs1")[1]["results"] == ["R0003|partial"]
    assert call(f"{base}/api/runs/R0003/hs1", "POST", {"coupling": "sideways"})[0] == 400
    call(f"{base}/api/runs/R0003/hs1", "DELETE")


# --- fleet calibration --------------------------------------------------------------------------------------
def test_latest_artefact_and_calibration_modes(env, tmp_path):
    import os

    from eacsize.hs1_bridge import latest_artefact
    _, bridge, _ = env
    for name, mtime in [("calibration_20261101_aaaaaaaaaaaa.nc", 300), ("calibration_20261112_bbbbbbbbbbbb.nc", 100),
                        ("calibration_20261112_cccccccccccc.json", 200), ("calibration_20261231_x.nc", 900),
                        ("notes.nc", 999)]:
        (tmp_path / name).write_text("{}")
        os.utime(tmp_path / name, (mtime, mtime))
    # latest date in the name wins, then the newest file of that date; malformed names are ignored
    assert latest_artefact(str(tmp_path)).endswith("calibration_20261112_cccccccccccc.json")
    assert latest_artefact(str(tmp_path / "absent")) is None
    keep = bridge.calibration_setting
    try:
        bridge.calibration_setting = str(tmp_path)
        assert bridge.calibration()["mode"] == "latest"
        bridge.calibration_setting = str(tmp_path / "calibration_20261101_aaaaaaaaaaaa.nc")
        c = bridge.calibration()
        assert c["mode"] == "file" and c["name"] == "calibration_20261101_aaaaaaaaaaaa.nc"
        bridge.calibration_setting = "none"
        assert bridge.calibration() == dict(mode="prior", setting="none", path=None, name=None, error=None)
        bridge.calibration_setting = str(tmp_path / "calibration_20990101_dddddddddddd.nc")
        assert bridge.calibration()["mode"] == "missing"
        with pytest.raises(RuntimeError, match="not found"):      # a named artefact that is gone: refuse, no fallback
            bridge.run("R0003", close=False, mc=False)
    finally:
        bridge.calibration_setting = keep


def test_run_uses_newest_calibration_artefact(server, env, tmp_path):
    from hs1_weights.calibration import calibrate, write_artefact
    from hs1_weights.data.dataset import build_dataset
    base, _ = server
    _, bridge, _ = env
    b = build_dataset(ROOT / "data" / "records", ROOT / "data" / "sources.csv", tmp_path / "build",
                      fleet_csv=ROOT / "data" / "fleet" / "fleet_clusters.csv", fleet_accessed="2026-11-12")
    art = write_artefact(calibrate(b.rows, b.components, b.content_hash, engine="map", seed=0), tmp_path / "art")
    keep = bridge.calibration_setting
    try:
        bridge.calibration_setting = str(tmp_path / "art")
        assert call(f"{base}/api/hs1")[1]["calibration"]["name"] == art.name
        code, res = call(f"{base}/api/runs/R0003/hs1", "POST", {"coupling": "partial", "close": False, "mc": False})
        assert code == 200, res
        assert res["report"]["calibration"]["engine"] == "map_laplace"
        assert res["report"]["calibration"]["n_records"] == 6
        assert res["options"]["calibration"] == art.name and res["options"]["calibration_mode"] == "latest"
        assert res["report"]["versions"]["calibration_path"] == str(art)
    finally:
        bridge.calibration_setting = keep
        call(f"{base}/api/runs/R0003/hs1", "DELETE")
