"""Run the WeightEstimation (hs1_weights) pipeline for a stored eacsize run, on demand from the UI.

Standard library only: the pipeline runs in a subprocess with the WeightEstimation interpreter
(``WeightEstimation/.venv/bin/python`` if present, else ``$HS1_PYTHON`` or this interpreter with
``WeightEstimation/src`` on PYTHONPATH), so eacsize itself keeps no third-party dependency.

Results are stored per run as ``<data-dir>/hs1_weights/<run id>.json``; runs.json is not modified.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, tempfile, threading

HERE = os.path.dirname(os.path.abspath(__file__))
WPP_DIR = os.path.dirname(HERE)
DEFAULT_HS1_DIR = os.path.join(os.path.dirname(WPP_DIR), "WeightEstimation")
TIMEOUT_S = 600
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class HS1Bridge:
    def __init__(self, runs_json, data_dir, hs1_dir=None, overlay=None, python=None):
        self.runs_json = runs_json
        self.hs1_dir = os.path.abspath(hs1_dir or os.environ.get("HS1_DIR") or DEFAULT_HS1_DIR)
        self.overlay = os.path.abspath(overlay or os.environ.get("HS1_OVERLAY")
                                       or os.path.join(self.hs1_dir, "examples", "eacsize_overlay.json"))
        self.out_dir = os.path.join(data_dir, "hs1_weights")
        venv = os.path.join(self.hs1_dir, ".venv", "bin", "python")
        self.python = python or os.environ.get("HS1_PYTHON") or (venv if os.path.isfile(venv) else sys.executable)
        self._locks = {}
        self._guard = threading.Lock()

    def status(self):
        ok = os.path.isdir(os.path.join(self.hs1_dir, "src", "hs1_weights"))
        return dict(available=ok and os.path.isfile(self.overlay), hs1_dir=self.hs1_dir, overlay=self.overlay,
                    python=self.python, results=sorted(f[:-5] for f in os.listdir(self.out_dir))
                    if os.path.isdir(self.out_dir) else [])

    def _path(self, run_id):
        if not _SAFE_ID.match(run_id or ""):
            raise ValueError(f"invalid run id {run_id!r}")
        return os.path.join(self.out_dir, f"{run_id}.json")

    def get(self, run_id):
        p = self._path(run_id)
        if not os.path.isfile(p):
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def delete(self, run_id):
        p = self._path(run_id)
        if os.path.isfile(p):
            os.remove(p)

    def run(self, run_id, close=True, mc=True, n=2000, seed=0):
        """Run the pipeline for one stored eacsize run; returns the stored result (or raises RuntimeError)."""
        path = self._path(run_id)
        with self._guard:
            lock = self._locks.setdefault(run_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise RuntimeError(f"HS1 pipeline already running for {run_id}")
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(suffix=".json", dir=self.out_dir)
            os.close(fd)
            cmd = [self.python, "-m", "hs1_weights", "from-eacsize", "--runs", self.runs_json, "--run", run_id,
                   "--overlay", self.overlay, "--wpp", WPP_DIR, "--n", str(int(n)), "--seed", str(int(seed)),
                   "--out", tmp]
            if close:
                cmd.append("--close")
            if mc:
                cmd.append("--mc")
            env = dict(os.environ, PYTHONPATH=os.pathsep.join(
                [os.path.join(self.hs1_dir, "src")] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])))
            try:
                p = subprocess.run(cmd, cwd=self.hs1_dir, env=env, capture_output=True, text=True, timeout=TIMEOUT_S)
            except subprocess.TimeoutExpired:
                os.remove(tmp)
                raise RuntimeError(f"HS1 pipeline timed out after {TIMEOUT_S} s")
            if p.returncode != 0:
                os.remove(tmp)
                lines = [l for l in (p.stderr or p.stdout).strip().splitlines() if l.strip()]
                raise RuntimeError("HS1 pipeline failed: " + (lines[-1] if lines else f"exit code {p.returncode}"))
            with open(tmp, encoding="utf-8") as f:
                res = json.load(f)
            res["options"] = dict(close=close, mc=mc, n=n, seed=seed)
            res["stdout_tail"] = p.stdout.splitlines()[-4:]
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(res, f, indent=1)
            os.replace(tmp, path)
            return res
        finally:
            lock.release()
