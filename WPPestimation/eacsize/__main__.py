"""
Command line:

  python3 -m eacsize                      start the UI (same as `ui`)
  python3 -m eacsize ui [--port 8765] [--data-dir DIR] [--no-browser]
  python3 -m eacsize run [--preset NAME] [--set section.key=value ...] [--label TEXT] [--no-save]
  python3 -m eacsize presets              list presets
  python3 -m eacsize verify               regression vs risen_eas + reference cross-checks
"""
from __future__ import annotations
import argparse, json, os, sys

from .model import SECTIONS
from .presets import PRESETS, preset
from .sizing import run_from_dict
from .store import RunStore

DEFAULT_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runs")


def parse_set(items):
    out = {s: {} for s in SECTIONS}
    keys = {s: {f.name for f in __import__("dataclasses").fields(c)} for s, c in SECTIONS.items()}
    for it in items:
        k, _, v = it.partition("=")
        if "." in k:
            sec, key = k.split(".", 1)
        else:
            sec = next((s for s in SECTIONS if k in keys[s]), None)
            key = k
        if sec not in SECTIONS or key not in keys[sec]:
            sys.exit(f"unknown parameter: {k}")
        out[sec][key] = v
    return out


def print_summary(rec):
    r = rec["results"]
    s, p, m = r["summary"], r["performance"], r["mission"]
    print(f"{rec.get('id', '')} {rec.get('label', '')}  status: {r['status'].upper()}")
    for msg in r["messages"]:
        print("  !", msg)
    print(f"  MTOM {s['mtom_kg']:.1f} kg | OEW {s['oew_excl_battery_kg']:.1f} | payload {s['payload_kg']:.0f} | "
          f"battery {s['battery_kg']:.1f} kg = {s['battery_kwh']:.1f} kWh ({s['battery_mass_frac']:.1%})")
    print(f"  S {s['wing_area_m2']:.2f} m2, b {s['span_m']:.2f} m, W/S {s['wing_loading_kgm2']:.1f} kg/m2 "
          f"({r['design']['wing_loading_driver']}) | P {s['installed_power_kw']:.1f} kW ({r['design']['power_driver']})")
    print(f"  CD0 {s['cd0']:.4f}, e {s['oswald_e']:.3f}, L/D {s['ld_max']:.1f} | V_S0 {p['vs0_kcas']:.1f} KCAS | "
          f"ROC {p['roc_sl_cont_ms']:.2f} m/s | T/O {p['takeoff_15m_m'] or float('nan'):.0f} m | LDG {p['landing_15m_m']:.0f} m")
    print(f"  range {m['range_cruise_km']:.0f} km (EOL {m['range_cruise_eol_km']:.0f}), V_md {m['range_vmd_km']:.0f} km | "
          f"endurance {m['endurance_vmp_h']:.2f} h (no reserve {m['endurance_vmp_no_reserve_h']:.2f})")
    bad = [c for c in r["checks"] if not c["ok"]]
    for c in bad:
        print(f"  [{c['severity']}] {c['name']}: {c['value']} vs {c['limit']} {c['unit']}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="eacsize", description="Electric aircraft conceptual sizing")
    sub = ap.add_subparsers(dest="cmd")
    u = sub.add_parser("ui")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--data-dir", default=DEFAULT_DATA)
    u.add_argument("--no-browser", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--preset", choices=list(PRESETS))
    r.add_argument("--set", nargs="*", default=[], metavar="section.key=value")
    r.add_argument("--label", default="")
    r.add_argument("--data-dir", default=DEFAULT_DATA)
    r.add_argument("--no-save", action="store_true")
    r.add_argument("--json", action="store_true", help="print the full record as JSON")
    sub.add_parser("presets")
    sub.add_parser("verify")
    a = ap.parse_args(argv)

    if a.cmd in (None, "ui"):
        from .ui import serve
        serve(getattr(a, "port", 8765), getattr(a, "data_dir", DEFAULT_DATA), not getattr(a, "no_browser", False))
    elif a.cmd == "presets":
        for k, v in PRESETS.items():
            print(f"{k:24s} {v.get('title', '')}")
    elif a.cmd == "run":
        body = preset(a.preset) if a.preset else {}
        for sec, vals in parse_set(a.set).items():
            body.setdefault(sec, {}).update(vals)
        rec = run_from_dict(body)
        label = a.label or (PRESETS[a.preset]["title"] if a.preset else "CLI run")
        if not a.no_save:
            rec = RunStore(a.data_dir).add(rec, label=label)
        else:
            rec = dict(label=label, **rec)
        print(json.dumps(rec, indent=1) if a.json else "", end="")
        if not a.json:
            print_summary(rec)
    elif a.cmd == "verify":
        from .verify import main as vmain
        sys.exit(vmain())


if __name__ == "__main__":
    main()
