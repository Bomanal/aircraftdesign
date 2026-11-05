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
def server(tmp_path_factory):
    sys.path.insert(0, str(WPP))
    from eacsize.hs1_bridge import HS1Bridge
    from eacsize.store import RunStore
    from eacsize.ui import make_handler
    data = tmp_path_factory.mktemp("runs")
    shutil.copy(WPP / "runs" / "runs.json", data / "runs.json")
    store = RunStore(str(data))
    bridge = HS1Bridge(store.json_path, store.dir, hs1_dir=str(ROOT), python=sys.executable)
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
    assert code == 200 and st["available"] and st["results"] == []


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
    assert call(f"{base}/api/hs1")[1]["results"] == ["R0003"]
    assert call(f"{base}/api/runs/R0003/hs1", "DELETE")[0] == 200
    assert call(f"{base}/api/runs/R0003/hs1")[0] == 404


def test_errors(server):
    base, _ = server
    assert call(f"{base}/api/runs/R9999/hs1", "POST", {})[0] == 404
    assert call(f"{base}/api/runs/bad%20id/hs1")[0] == 400
