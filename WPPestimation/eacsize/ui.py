"""
Local web UI (standard library only).

    python3 -m eacsize ui [--port 8765] [--data-dir runs] [--no-browser]

Serves a single page that builds its form from the model schema, runs a design on the server,
stores each run in runs.json / runs.csv and shows the latest and previous runs.
"""
from __future__ import annotations
import json, os, threading, traceback, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from .model import schema
from .presets import PRESETS
from .sizing import run_from_dict
from .store import RunStore

STATIC = os.path.join(os.path.dirname(__file__), "static")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".csv": "text/csv; charset=utf-8"}


def run_index(r):
    s = r["results"]["summary"]
    return dict(id=r["id"], label=r["label"], timestamp=r["timestamp"], status=r["results"]["status"],
                mode=r["inputs"]["mode"], seats=r["inputs"]["seats"], pack_wh_kg=r["assumptions"]["pack_wh_kg"],
                weight_method=r["assumptions"]["weight_method"], geometry_mode=r["inputs"]["geometry_mode"],
                summary=s)


def make_handler(store: RunStore):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype="application/json", extra=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _file(self, path, download=None):
            if not os.path.isfile(path):
                return self._send(404, {"error": "not found"})
            with open(path, "rb") as f:
                data = f.read()
            extra = {"Content-Disposition": f'attachment; filename="{download}"'} if download else None
            self._send(200, data, TYPES.get(os.path.splitext(path)[1], "application/octet-stream"), extra)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._file(os.path.join(STATIC, "index.html"))
            if path.startswith("/static/"):
                name = os.path.basename(path)
                return self._file(os.path.join(STATIC, name))
            if path == "/api/schema":
                return self._send(200, dict(schema=schema(), presets=PRESETS))
            if path == "/api/runs":
                return self._send(200, [run_index(r) for r in store.load()])
            if path.startswith("/api/runs/"):
                rec = store.get(path.rsplit("/", 1)[1])
                return self._send(200, rec) if rec else self._send(404, {"error": "run not found"})
            if path == "/data/runs.json":
                return self._file(store.json_path, "runs.json")
            if path == "/data/runs.csv":
                return self._file(store.csv_path, "runs.csv")
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if urlparse(self.path).path != "/api/run":
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                record = run_from_dict(body)
                rec = store.add(record, label=body.get("label", ""))
                self._send(200, rec)
            except Exception as exc:  # report model errors to the page
                traceback.print_exc()
                self._send(400, {"error": f"{type(exc).__name__}: {exc}"})

        def do_DELETE(self):
            path = urlparse(self.path).path
            if path.startswith("/api/runs/"):
                store.delete(path.rsplit("/", 1)[1])
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

    return Handler


def serve(port=8765, data_dir="runs", open_browser=True):
    store = RunStore(os.path.abspath(data_dir))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store))
    url = f"http://127.0.0.1:{port}/"
    print(f"eacsize UI on {url}  (runs stored in {store.dir})  Ctrl+C to stop")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
