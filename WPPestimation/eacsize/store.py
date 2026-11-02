"""
Run store: every run is appended to runs.json (full record) and runs.csv is regenerated with
one column per run (rows = flattened inputs, assumptions and scalar results).
"""
from __future__ import annotations
import csv, json, os, threading
from datetime import datetime

LOCK = threading.Lock()

# result sections flattened into the CSV (charts and per-check detail stay in the JSON)
CSV_SECTIONS = ("inputs", "mission", "requirements", "assumptions")
CSV_RESULT_SECTIONS = ("summary", "performance", "mission", "mass_kg", "closure", "calibration")


class RunStore:
    def __init__(self, data_dir):
        self.dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.json_path = os.path.join(data_dir, "runs.json")
        self.csv_path = os.path.join(data_dir, "runs.csv")

    # --- json -----------------------------------------------------------------------------
    def load(self):
        if not os.path.exists(self.json_path):
            return []
        with open(self.json_path) as f:
            return json.load(f)

    def _save(self, runs):
        tmp = self.json_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(runs, f, indent=1)
        os.replace(tmp, self.json_path)
        self.write_csv(runs)

    def add(self, record, label=""):
        with LOCK:
            runs = self.load()
            n = max([int(r["id"][1:]) for r in runs] + [0]) + 1
            rec = dict(id=f"R{n:04d}", label=label or f"Run {n}",
                       timestamp=datetime.now().isoformat(timespec="seconds"), **record)
            runs.append(rec)
            self._save(runs)
            return rec

    def get(self, run_id):
        return next((r for r in self.load() if r["id"] == run_id), None)

    def delete(self, run_id):
        with LOCK:
            runs = [r for r in self.load() if r["id"] != run_id]
            self._save(runs)

    # --- csv: one column per run ----------------------------------------------------------
    @staticmethod
    def flatten(rec):
        row = {"run.id": rec["id"], "run.label": rec["label"], "run.timestamp": rec["timestamp"],
               "run.status": rec["results"]["status"]}
        for sec in CSV_SECTIONS:
            for k, v in rec.get(sec, {}).items():
                row[f"{sec}.{k}"] = v
        res = rec["results"]
        for sec in CSV_RESULT_SECTIONS:
            for k, v in res.get(sec, {}).items():
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        row[f"result.{sec}.{k}.{k2}"] = v2
                else:
                    row[f"result.{sec}.{k}"] = v
        for c in res.get("checks", []):
            row[f"result.check.{c['name']}"] = c["severity"]
        row["result.design.wing_loading_driver"] = res["design"]["wing_loading_driver"]
        row["result.design.power_driver"] = res["design"]["power_driver"]
        return row

    def write_csv(self, runs=None):
        runs = self.load() if runs is None else runs
        cols = [self.flatten(r) for r in runs]
        keys = []
        for c in cols:
            for k in c:
                if k not in keys:
                    keys.append(k)
        with open(self.csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["parameter"] + [c["run.id"] for c in cols])
            for k in keys:
                w.writerow([k] + ["" if c.get(k) is None else c.get(k) for c in cols])
