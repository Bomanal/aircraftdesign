"""Command line: ``hs1-weights <command>`` (or ``python -m hs1_weights``)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _report_table(rep: Any) -> str:
    lines = [f"{'component':22s}{'P10':>9s}{'P50':>9s}{'P90':>9s}  {'method':15s}{'k P50':>7s}  governing load case"]
    for c, r in rep.components.items():
        k = f"{r.k.p50:.3f}" if r.k else "-"
        flag = "" if r.transcription_verified else " [unverified transcription]"
        lines.append(f"{c:22s}{r.mass_kg.p10:9.1f}{r.mass_kg.p50:9.1f}{r.mass_kg.p90:9.1f}  {r.method:15s}{k:>7s}  "
                     f"{r.governing_load_case}{flag}")
    e = rep.empty_weight
    lines.append(f"{'EMPTY WEIGHT':22s}{e.p10:9.1f}{e.p50:9.1f}{e.p90:9.1f}")
    if rep.mtow:
        lines.append(f"{'MTOW (W_E + payload)':22s}{rep.mtow.p10:9.1f}{rep.mtow.p50:9.1f}{rep.mtow.p90:9.1f}")
    s = rep.stall_area_min_m2
    vs1 = s["VS1_59kt_clean"]
    lines.append(f"\nmin wing area: V_S1<=59 kt {vs1['p50']:.2f} m² (P90 {vs1['p90']:.2f});"
                 f" V_S0<=61 kt {s['VS0_61kt_landing']['p50']:.2f} m²; current {s['current_area_m2']:.2f} m²")
    if rep.sobol:
        lines.append("Sobol total indices (W_E): " + ", ".join(f"{k} {v:.2f}" for k, v in rep.sobol.items()))
    lines.append("rule flags:")
    lines += [f"  {f.id} [{f.severity}] {f.message}" for f in rep.rule_flags]
    lines.append(f"calibration: {rep.calibration.get('engine')}; {len(rep.assumptions)} assumptions; "
                 f"{len(rep.unverified_criteria)} unverified criteria")
    return "\n".join(lines)


def _from_eacsize(a: argparse.Namespace) -> None:
    import datetime as dt
    import time
    t0 = time.time()
    from .adapters import eacsize as ea
    from .api import predict
    from .closure import close_mc, close_point
    run = ea.load_run(a.runs, a.run)
    overlay, source = _overlay_for(a, run)
    if a.coupling == "full":
        _from_eacsize_full(a, run, overlay, t0, source)
        return
    cfg = ea.config_from_eacsize(run, overlay)
    if a.write_config:
        Path(a.write_config).write_text(json.dumps(cfg.model_dump(mode="json", exclude_none=True), indent=2) + "\n")
    print(f"configuration: {cfg.name}")
    rep = predict(cfg, a.n, a.seed, a.calibration)
    print(_report_table(rep))
    print(f"\n{'eacsize vs hs1_weights':28s}{'eacsize':>9s}{'P10':>9s}{'P50':>9s}{'P90':>9s}")
    for r in ea.comparison(run, rep):
        print(f"{r['component']:28s}{r['eacsize_kg']:9.1f}{r['p10']:9.1f}{r['p50']:9.1f}{r['p90']:9.1f}")
    out: dict[str, Any] = {"run_id": run.get("id"), "run_label": run.get("label"), "config_name": cfg.name,
                           "overlay": source["path"] if source["source"] == "overlay" else None,
                           "structure_config": source, "eacsize_mtom_kg": run["results"]["summary"]["mtom_kg"],
                           "eacsize_battery_kwh": run["results"]["summary"]["battery_kwh"],
                           "comparison": ea.comparison(run, rep), "report": rep.model_dump(mode="json")}
    if a.close or a.mc:
        energy = ea.energy_fn_from_eacsize(run, a.wpp)
        payload = cfg.payload_kg or 0.0
        cal = None
        if a.calibration:
            from .calibration import load_artefact
            cal = load_artefact(a.calibration)
        pt = close_point(cfg, energy, payload, n_samples=a.n, seed=a.seed, calibration=cal, final_report=False)
        print(f"\nclosure (eacsize mission energy): converged={pt.converged} in {pt.iterations} iterations; "
              f"MTOW {pt.mtow_kg:.1f} kg (eacsize {run['results']['summary']['mtom_kg']:.1f} kg), "
              f"W_E P50 {pt.empty_weight_kg:.1f} kg, pack {pt.energy_wh / 1e3:.1f} kWh "
              f"(eacsize {run['results']['summary']['battery_kwh']:.1f} kWh)")
        out["closure_point"] = {"converged": pt.converged, "iterations": pt.iterations, "mtow_kg": pt.mtow_kg,
                                "empty_weight_p50_kg": pt.empty_weight_kg, "energy_wh": pt.energy_wh,
                                "history": pt.history}
        if a.mc:
            m = close_mc(cfg, energy, payload, a.n, a.seed, cal)
            out["closure_mc"] = m
            if "mtow" in m:
                print(f"Monte Carlo closure: MTOW P10/P50/P90 {m['mtow']['p10']:.0f} / {m['mtow']['p50']:.0f} / "
                      f"{m['mtow']['p90']:.0f} kg; converged {m['converged_fraction']:.1%}")
            if "note" in m:
                print("  " + m["note"])
    out["coupling"] = "partial"
    out["eacsize_original"] = ea.summary_of(run)
    out["mission_target"] = ea.mission_target(run)
    out["generated"] = dt.datetime.now().isoformat(timespec="seconds")
    out["runtime_s"] = time.time() - t0
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=2, default=str) + "\n")


def _overlay_for(a: argparse.Namespace, run: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Overlay from ``--structure-config`` (a selection) or ``--overlay`` (a hand-written partial HS1Config)."""
    if a.structure_config:
        from . import structure_config as sc
        sel = sc.validate(sc.load_selection(a.structure_config), run)
        ch = sc.changed(sel, run)
        return sc.build_overlay(sel, run), {"source": "selection", "path": str(a.structure_config),
                                            "hash": sc.selection_hash(sel), "n_changed": len(ch), "changed": ch,
                                            "selection": sel}
    return json.loads(Path(a.overlay).read_text()), {"source": "overlay", "path": str(a.overlay)}


def _from_eacsize_full(a: argparse.Namespace, run: dict[str, Any], overlay: dict[str, Any], t0: float,
                       source: dict[str, Any]) -> None:
    """Full coupling: eacsize re-sizes the aircraft at every iteration; final eacsize run at the closed state."""
    import copy
    import datetime as dt
    import time

    from .adapters import eacsize as ea
    from .closure import close_mc_coupled, close_point_coupled
    cal = None
    if a.calibration:
        from .calibration import load_artefact
        cal = load_artefact(a.calibration)
    config_at, sizer = ea.full_coupling(run, overlay, a.wpp)
    s0 = run["results"]["summary"]
    payload = float(s0["payload_kg"])
    pt = close_point_coupled(config_at, float(s0["mtom_kg"]), payload, n_samples=a.n, seed=a.seed, calibration=cal)
    out: dict[str, Any] = {"coupling": "full", "run_id": run.get("id"), "run_label": run.get("label"),
                           "overlay": source["path"] if source["source"] == "overlay" else None,
                           "structure_config": source, "eacsize_mtom_kg": s0["mtom_kg"],
                           "eacsize_battery_kwh": s0["battery_kwh"], "eacsize_original": ea.summary_of(run),
                           "mission_target": ea.mission_target(run),
                           "closure_point": {"converged": pt.converged, "diverged": pt.diverged,
                                             "iterations": pt.iterations, "mtow_kg": pt.mtow_kg,
                                             "empty_weight_p50_kg": pt.empty_weight_kg, "energy_wh": pt.energy_wh,
                                             "history": pt.history}}
    print(f"configuration: eacsize {run.get('id')} with full coupling")
    if pt.converged and pt.report is not None and pt.config is not None:
        rep, cfg = pt.report, pt.config
        print(_report_table(rep))
        pack_kg = rep.components["battery_pack"].mass_kg.p50
        # eacsize at the closed MTOM with HS1's empty weight injected: its pack = MTOW - OEW - payload = HS1 pack
        closed = sizer.sized(pt.mtow_kg, oew_excl_battery_kg=pt.empty_weight_kg - pack_kg)
        out["eacsize_closed"] = ea.summary_of(closed)
        # component comparison against eacsize's own class-II masses at the closed MTOM (pack = mission energy)
        ref = copy.deepcopy(sizer.sized(pt.mtow_kg))
        ref["results"]["mass_kg"]["battery_pack"] = pt.energy_wh / sizer.pack_wh_kg
        ref["results"]["summary"]["battery_kg"] = pt.energy_wh / sizer.pack_wh_kg
        out.update(config_name=cfg.name, comparison=ea.comparison(ref, rep), report=rep.model_dump(mode="json"))
        sc = out["eacsize_closed"]
        print(f"\nfull coupling: converged in {pt.iterations} iterations; MTOW {pt.mtow_kg:.1f} kg "
              f"(eacsize {s0['mtom_kg']:.1f}); S {sc['wing_area_m2']:.2f} m² (was {s0['wing_area_m2']:.2f}), "
              f"P {sc['installed_power_kw']:.1f} kW (was {s0['installed_power_kw']:.1f}), "
              f"pack {pt.energy_wh / 1e3:.1f} kWh; eacsize status at closed state: {sc['status']}")
    else:
        print(f"\nfull coupling did not converge ({'mission infeasible' if pt.diverged else 'iteration limit'}) "
              f"after {pt.iterations} iterations")
    if a.mc:
        m = close_mc_coupled(config_at, float(s0["mtom_kg"]), payload, a.n, a.seed, cal)
        out["closure_mc"] = m
        if "mtow" in m:
            print(f"Monte Carlo (full coupling): MTOW P10/P50/P90 {m['mtow']['p10']:.0f} / {m['mtow']['p50']:.0f} / "
                  f"{m['mtow']['p90']:.0f} kg; converged {m['converged_fraction']:.1%}")
        if "note" in m:
            print("  " + m["note"])
    out["generated"] = dt.datetime.now().isoformat(timespec="seconds")
    out["runtime_s"] = time.time() - t0
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=2, default=str) + "\n")
    if not pt.converged:
        raise SystemExit(f"full coupling did not converge for {run.get('id')}: "
                         f"{'mission infeasible at the iterated mass' if pt.diverged else 'iteration limit reached'}")


def _structure_config(a: argparse.Namespace) -> None:
    from . import structure_config as sc
    from .adapters import eacsize as ea
    run = ea.load_run(a.runs, a.run)
    sel = sc.load_selection(a.selection) if a.selection else None
    try:
        d = sc.describe(run, sel)
    except sc.StructureConfigError as e:
        print(json.dumps({"ok": False, "errors": e.errors}, indent=2))
        raise SystemExit(2) from e
    if a.overlay_out:
        Path(a.overlay_out).write_text(json.dumps(sc.build_overlay(d["selection"], run), indent=2) + "\n")
    print(json.dumps({"ok": True, "run_id": run.get("id"), **d}, indent=2))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hs1-weights", description="HS1 weight-prediction module")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("predict", help="predict component masses, W_E, MTOW, CG")
    p.add_argument("config")
    p.add_argument("--calibration")
    p.add_argument("--fleet", help="dataset build dir (normalized.parquet) for analogs and rules")
    p.add_argument("--n", type=int, default=2000)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out")
    c = sub.add_parser("close", help="MTOW closure at constant pack energy (stand-in for hs1_design)")
    c.add_argument("config")
    c.add_argument("--payload", type=float, required=True)
    c.add_argument("--energy-wh", type=float, required=True)
    c.add_argument("--mc", action="store_true")
    c.add_argument("--n", type=int, default=2000)
    c.add_argument("--calibration")
    b = sub.add_parser("build-dataset", help="normalize records (N1-N12) and freeze the dataset")
    b.add_argument("--records", default="data/records")
    b.add_argument("--sources", default="data/sources.csv")
    b.add_argument("--out", default="build")
    b.add_argument("--fleet-csv", default=None, help="clustered owner fleet table, e.g. data/fleet/fleet_clusters.csv")
    b.add_argument("--fleet-accessed", default=None, help="source access date for the fleet CSV (default: file date)")
    b.add_argument("--no-impute", action="store_true", help="disable NI cluster imputation")
    b.add_argument("--no-design-priors", action="store_true", help="NI from fleet data only (doc 12 SC-29)")
    k = sub.add_parser("calibrate", help="fit technology factors and write a versioned artefact")
    k.add_argument("--dataset", default="build")
    k.add_argument("--engine", default="auto", choices=["auto", "nuts", "map"])
    k.add_argument("--seed", type=int, default=0)
    k.add_argument("--out", default="artefacts")
    v = sub.add_parser("validate", help="LOO / LOCO / benchmark report (doc 08 §5)")
    v.add_argument("--dataset", default="build")
    v.add_argument("--out")
    s = sub.add_parser("schema", help="export JSON schemas of the I/O models")
    s.add_argument("--out", default="schemas")
    sub.add_parser("registry", help="list component methods and their transcription status")
    w = sub.add_parser("from-eacsize", help="predict (and close) a configuration sized in WPPestimation/eacsize")
    w.add_argument("--runs", default="../WPPestimation/runs/runs.json", help="eacsize runs.json or one-run export")
    w.add_argument("--run", help="run id (e.g. R0003) or exact label")
    w.add_argument("--overlay", default="examples/eacsize_overlay.json", help="HS1 data eacsize does not model")
    w.add_argument("--structure-config", help="Aircraft structure config selection JSON (replaces --overlay); "
                                              "see `hs1-weights structure-config`")
    w.add_argument("--wpp", default="../WPPestimation", help="WPPestimation directory (for the mission model)")
    w.add_argument("--write-config", help="save the merged HS1Config JSON here (inspect / edit / re-run)")
    w.add_argument("--close", action="store_true", help="MTOW closure with eacsize's mission energy")
    w.add_argument("--mc", action="store_true", help="also run the Monte Carlo closure")
    w.add_argument("--coupling", choices=["partial", "full"], default="partial",
                   help="partial: airframe frozen at the eacsize run, only mission energy follows the mass; "
                        "full: eacsize re-sizes wing, power and geometry at every iteration (implies --close)")
    w.add_argument("--calibration")
    w.add_argument("--n", type=int, default=2000)
    w.add_argument("--seed", type=int, default=0)
    w.add_argument("--out", help="write the WeightReport JSON here")
    sc_p = sub.add_parser("structure-config", help="Aircraft structure config: catalogue and defaults for an eacsize "
                                                   "run, or validate a selection and show its overlay")
    sc_p.add_argument("--runs", default="../WPPestimation/runs/runs.json")
    sc_p.add_argument("--run", help="run id (e.g. R0003) or exact label")
    sc_p.add_argument("--selection", help="selection JSON to validate ({id: value} or {'selection': {...}})")
    sc_p.add_argument("--overlay-out", help="write the overlay built from the selection here")
    a = ap.parse_args(argv)

    if a.cmd == "predict":
        from .api import predict
        from .config import load_config
        fleet = None
        if a.fleet:
            from .data.dataset import load_dataset
            rows, comps, _ = load_dataset(a.fleet)
            fleet = (rows, comps)
        rep = predict(load_config(a.config), a.n, a.seed, a.calibration, fleet)
        if a.out:
            Path(a.out).write_text(json.dumps(rep.model_dump(), indent=2, default=str) + "\n")
        print(_report_table(rep))
    elif a.cmd == "close":
        from .closure import close_mc, close_point
        from .config import load_config
        cfg = load_config(a.config)
        cal = None
        if a.calibration:
            from .calibration import load_artefact
            cal = load_artefact(a.calibration)
        if a.mc:
            print(json.dumps(close_mc(cfg, lambda w: a.energy_wh, a.payload, a.n, calibration=cal), indent=2))
        else:
            r = close_point(cfg, lambda w: a.energy_wh, a.payload, n_samples=a.n, calibration=cal,
                            final_report=False)
            print(json.dumps({"converged": r.converged, "iterations": r.iterations, "mtow_kg": r.mtow_kg,
                              "empty_weight_p50_kg": r.empty_weight_kg, "history": r.history}, indent=2))
    elif a.cmd == "build-dataset":
        from .data.dataset import build_dataset
        bd = build_dataset(a.records, a.sources, a.out, fleet_csv=a.fleet_csv, fleet_accessed=a.fleet_accessed,
                           impute=False if a.no_impute else None, design_priors=not a.no_design_priors)
        man = {k: v for k, v in bd.manifest.items() if k not in ("ineligible", "fleet_csv")}
        if "fleet_csv" in bd.manifest:
            man["fleet_csv"] = {k: v for k, v in bd.manifest["fleet_csv"].items() if k != "mappings"}
        print(json.dumps(man, indent=2))
    elif a.cmd == "calibrate":
        from .calibration import calibrate, write_artefact
        from .data.dataset import load_dataset
        rows, comps, man = load_dataset(a.dataset)
        res = calibrate(rows, comps, man["content_hash"], engine=a.engine, seed=a.seed)
        path = write_artefact(res, a.out)
        print(json.dumps({"artefact": str(path), "engine": res.posterior.engine,
                          "collapse_struct": res.metadata["collapse_struct"],
                          "diagnostics": {k: v for k, v in res.diagnostics.items() if k not in ("map", "rhat", "ess")}},
                         indent=2, default=str))
    elif a.cmd == "validate":
        from .calibration.validate import validation_report
        from .data.dataset import load_dataset
        rows, comps, _ = load_dataset(a.dataset)
        vrep = validation_report(rows, comps)
        txt = json.dumps(vrep, indent=2, default=str)
        if a.out:
            Path(a.out).write_text(txt + "\n")
        print(json.dumps(vrep["checks"], indent=2))
    elif a.cmd == "schema":
        from .api import export_schemas
        for path in export_schemas(a.out):
            print(path)
    elif a.cmd == "from-eacsize":
        _from_eacsize(a)
    elif a.cmd == "structure-config":
        _structure_config(a)
    elif a.cmd == "registry":
        from .components import REGISTRY
        print(json.dumps(REGISTRY.describe(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
