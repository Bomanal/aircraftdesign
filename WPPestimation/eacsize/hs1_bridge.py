"""Run the WeightEstimation (hs1_weights) pipeline for a stored eacsize run, on demand from the UI.

Standard library only: the pipeline runs in a subprocess with the WeightEstimation interpreter
(``WeightEstimation/.venv/bin/python`` if present, else ``$HS1_PYTHON`` or this interpreter with
``WeightEstimation/src`` on PYTHONPATH), so eacsize itself keeps no third-party dependency.

Results are stored per run and coupling mode: ``<data-dir>/hs1_weights/<run id>.json`` (partial coupling) and
``<run id>.full.json`` (full coupling); runs.json is not modified.

Aircraft structural config: the HS1 layout/structure inputs eacsize does not model are chosen per run from a
catalogue (``WeightEstimation/src/hs1_weights/structure_config.py``, stdlib-only, loaded here directly). A saved
selection lives in ``<run id>.structure.json`` and is used by every HS1 run of that run id; without one the
default overlay (``HS1_OVERLAY``, illustrative placeholders) is used.

Fleet calibration: every HS1 run uses the newest calibration artefact (``calibration_<YYYYMMDD>_<hash12>.nc`` or
``.json``, written by ``hs1-weights calibrate``) in ``WeightEstimation/artefacts``. ``$HS1_CALIBRATION`` (or the
``calibration`` argument) overrides this: a directory (newest artefact in it), one artefact file, or ``none`` for
the uncalibrated priors. It is resolved at each run, so a new calibration is used without restarting the server;
results computed with an older one are flagged in the UI.

Coupling modes (see WeightEstimation README):
  partial  the airframe is frozen at the eacsize run; only the mission energy follows the mass in the closure
  full     eacsize re-sizes wing, power and geometry at every closure iteration
"""
from __future__ import annotations
import datetime, importlib.util, json, os, re, subprocess, sys, tempfile, threading

HERE = os.path.dirname(os.path.abspath(__file__))
WPP_DIR = os.path.dirname(HERE)
DEFAULT_HS1_DIR = os.path.join(os.path.dirname(WPP_DIR), "WeightEstimation")
TIMEOUT_S = 600
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
COUPLINGS = ("partial", "full")
_ARTEFACT = re.compile(r"^calibration_(\d{8})_([0-9a-f]{12})\.(nc|json)$")
_NO_CALIBRATION = ("none", "off", "prior", "priors")


def latest_artefact(directory):
    """Newest calibration artefact in a directory: latest date in the file name, then latest modification time."""
    if not os.path.isdir(directory):
        return None
    found = [(m.group(1), os.path.getmtime(os.path.join(directory, f)), f)
             for f in os.listdir(directory) for m in [_ARTEFACT.match(f)] if m]
    return os.path.join(directory, max(found)[2]) if found else None


class HS1Bridge:
    def __init__(self, runs_json, data_dir, hs1_dir=None, overlay=None, python=None, calibration=None):
        self.runs_json = runs_json
        self.hs1_dir = os.path.abspath(hs1_dir or os.environ.get("HS1_DIR") or DEFAULT_HS1_DIR)
        self.calibration_setting = (calibration or os.environ.get("HS1_CALIBRATION")
                                    or os.path.join(self.hs1_dir, "artefacts"))
        self.overlay = os.path.abspath(overlay or os.environ.get("HS1_OVERLAY")
                                       or os.path.join(self.hs1_dir, "examples", "eacsize_overlay.json"))
        self.out_dir = os.path.join(data_dir, "hs1_weights")
        venv = os.path.join(self.hs1_dir, ".venv", "bin", "python")
        self.python = python or os.environ.get("HS1_PYTHON") or (venv if os.path.isfile(venv) else sys.executable)
        self._locks = {}
        self._guard = threading.Lock()
        self._sc = None

    # --- aircraft structural config -------------------------------------------------------------------
    def structure_module(self):
        """hs1_weights.structure_config, loaded from its file (stdlib only, no package import)."""
        if self._sc is None:
            path = os.path.join(self.hs1_dir, "src", "hs1_weights", "structure_config.py")
            spec = importlib.util.spec_from_file_location("hs1_structure_config", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._sc = mod
        return self._sc

    def _run_record(self, run_id):
        with open(self.runs_json, encoding="utf-8") as f:
            for r in json.load(f):
                if r.get("id") == run_id:
                    return r
        raise KeyError(f"run {run_id} not found")

    def _structure_path(self, run_id):
        if not _SAFE_ID.match(run_id or ""):
            raise ValueError(f"invalid run id {run_id!r}")
        return os.path.join(self.out_dir, f"{run_id}.structure.json")

    def get_structure(self, run_id):
        """Catalogue, defaults and the saved (else default) selection for a run."""
        sc, run, path = self.structure_module(), self._run_record(run_id), self._structure_path(run_id)
        saved = None
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
        try:
            d = sc.describe(run, saved["selection"] if saved else None)
            stale = []
        except sc.StructureConfigError as exc:        # catalogue changed since it was saved: fall back, report
            d, stale = sc.describe(run, None), exc.errors
        return dict(d, run_id=run_id, saved=saved is not None, saved_at=(saved or {}).get("saved_at"),
                    stale_errors=stale)

    def save_structure(self, run_id, selection):
        """Validate and store a selection; raises the module's StructureConfigError (a ValueError) if invalid."""
        sc, run, path = self.structure_module(), self._run_record(run_id), self._structure_path(run_id)
        sel = sc.validate(selection, run)
        os.makedirs(self.out_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="tmp", suffix=".tmp", dir=self.out_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dict(run_id=run_id, version=sc.VERSION, hash=sc.selection_hash(sel),
                           saved_at=datetime.datetime.now().isoformat(timespec="seconds"), selection=sel), f, indent=1)
        os.replace(tmp, path)
        return self.get_structure(run_id)

    def delete_structure(self, run_id):
        path = self._structure_path(run_id)
        if os.path.isfile(path):
            os.remove(path)

    # --- fleet calibration ---------------------------------------------------------------------------
    def calibration(self):
        """The calibration in use now: {mode, setting, path, name, error}. mode is "latest" (newest artefact in a
        directory), "file" (one fixed artefact), "prior" (none: k drawn from priors) or "missing" (a named artefact
        file that does not exist: runs are refused rather than silently falling back to priors)."""
        s = str(self.calibration_setting)
        info = dict(setting=s, path=None, name=None, error=None)
        if s.strip().lower() in _NO_CALIBRATION:
            return dict(info, mode="prior")
        p = os.path.abspath(s if os.path.isabs(s) else os.path.join(self.hs1_dir, s))
        if os.path.isdir(p) or not os.path.splitext(p)[1]:
            path = latest_artefact(p)
            if path is None:
                return dict(info, mode="prior", error=f"no calibration artefact in {p} yet (run hs1-weights calibrate)")
            return dict(info, mode="latest", path=path, name=os.path.basename(path))
        if not os.path.isfile(p):
            return dict(info, mode="missing", error=f"calibration artefact {p} not found (HS1_CALIBRATION)")
        return dict(info, mode="file", path=p, name=os.path.basename(p))

    def status(self):
        ok = os.path.isdir(os.path.join(self.hs1_dir, "src", "hs1_weights"))
        results, structures = [], []
        if os.path.isdir(self.out_dir):
            for f in sorted(os.listdir(self.out_dir)):
                if f.endswith(".structure.json"):
                    structures.append(f[:-15])
                elif f.endswith(".full.json"):
                    results.append(f"{f[:-10]}|full")
                elif f.endswith(".json") and not f.startswith("tmp"):
                    results.append(f"{f[:-5]}|partial")
        return dict(available=ok and os.path.isfile(self.overlay), hs1_dir=self.hs1_dir, overlay=self.overlay,
                    python=self.python, couplings=list(COUPLINGS), results=results,
                    structure_configs=structures, structure_config=os.path.isfile(
                        os.path.join(self.hs1_dir, "src", "hs1_weights", "structure_config.py")),
                    calibration=self.calibration())

    def _path(self, run_id, coupling="partial"):
        if not _SAFE_ID.match(run_id or ""):
            raise ValueError(f"invalid run id {run_id!r}")
        if coupling not in COUPLINGS:
            raise ValueError(f"coupling must be one of {COUPLINGS}, got {coupling!r}")
        return os.path.join(self.out_dir, f"{run_id}.full.json" if coupling == "full" else f"{run_id}.json")

    def get(self, run_id, coupling="partial"):
        p = self._path(run_id, coupling)
        if not os.path.isfile(p):
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def delete(self, run_id, coupling=None):
        """Delete HS1 results for one coupling, or (coupling None: the run itself was deleted) all results and the
        saved structural config."""
        for c in (COUPLINGS if coupling is None else (coupling,)):
            p = self._path(run_id, c)
            if os.path.isfile(p):
                os.remove(p)
        if coupling is None:
            self.delete_structure(run_id)

    def run(self, run_id, close=True, mc=True, n=2000, seed=0, coupling="partial"):
        """Run the pipeline for one stored eacsize run; returns the stored result (or raises RuntimeError)."""
        path = self._path(run_id, coupling)
        with self._guard:
            lock = self._locks.setdefault((run_id, coupling), threading.Lock())
        if not lock.acquire(blocking=False):
            raise RuntimeError(f"HS1 pipeline ({coupling} coupling) already running for {run_id}")
        try:
            cal = self.calibration()
            if cal["mode"] == "missing":
                raise RuntimeError(cal["error"])
            os.makedirs(self.out_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="tmp", suffix=".tmp", dir=self.out_dir)
            os.close(fd)
            structure = self._structure_path(run_id)
            src = ["--structure-config", structure] if os.path.isfile(structure) else ["--overlay", self.overlay]
            cmd = [self.python, "-m", "hs1_weights", "from-eacsize", "--runs", self.runs_json, "--run", run_id,
                   *src, "--wpp", WPP_DIR, "--n", str(int(n)), "--seed", str(int(seed)),
                   "--out", tmp, "--coupling", coupling]
            if cal["path"]:
                cmd += ["--calibration", cal["path"]]
            if coupling == "full":
                close = True                     # full coupling is a closure
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
            res["options"] = dict(close=close, mc=mc, n=n, seed=seed, coupling=coupling,
                                  structure="saved selection" if src[0] == "--structure-config" else "default overlay",
                                  calibration=cal["name"], calibration_mode=cal["mode"])
            res["stdout_tail"] = p.stdout.splitlines()[-4:]
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(res, f, indent=1)
            os.replace(tmp, path)
            return res
        finally:
            lock.release()
