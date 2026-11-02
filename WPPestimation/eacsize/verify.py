"""
Verification:
  1. Regression - with the risen_eas settings (L1 weights, scaled Risen, C_D0 0.0120), eacsize must
     reproduce risen_eas E600 / E750 metrics within 0.1 %.
  2. Calibration - the Raymer class-II path on the Risen airframe must return the calibration
     targets (OEW excl. battery 280 kg, C_D0 0.0120).
  3. Cross-checks (reported, not asserted) - Velis Electro published data and the E-Hansa study.
Exit code 1 on any failure of 1 or 2.
"""
from __future__ import annotations
import os, sys

from .presets import preset
from .sizing import run_from_dict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _regression():
    sys.path.insert(0, HERE)
    import risen_eas as R
    ref = R.all_metrics()
    rows = []
    for key, name in (("E600", "risen_e600_regression"), ("E750", "risen_e750_regression")):
        res = run_from_dict(preset(name))["results"]
        s, p, m = res["summary"], res["performance"], res["mission"]
        r = ref[key]
        pairs = [("battery mass kg", s["battery_kg"], r["m_bat"]), ("OEW kg", s["oew_excl_battery_kg"], r["oew"]),
                 ("energy kWh", s["battery_kwh"], r["E_kWh"]), ("V_md km/h", p["v_md_kmh"], r["vmd_kmh"]),
                 ("V_mp km/h", p["v_mp_kmh"], r["vmp_kmh"]), ("P_batt 216 kW", p["p_batt_cruise_kw"], r["P216_kW"]),
                 ("overhead kWh", m["e_overhead_kwh"], r["ovh_kWh"]), ("reserve kWh", m["e_reserve_kwh"], r["res_kWh"]),
                 ("range 216 km", m["range_cruise_km"], r["R_216"]), ("range 216 EOL km", m["range_cruise_eol_km"], r["R_216_eol"]),
                 ("range 216 no res km", m["range_cruise_no_reserve_km"], r["R_216_nores"]),
                 ("range V_md km", m["range_vmd_km"], r["R_vmd"]), ("endurance V_mp h", m["endurance_vmp_h"], r["T_vmp"]),
                 ("endurance EOL h", m["endurance_vmp_eol_h"], r["T_vmp_eol"]), ("ideal range km", m["range_ideal_km"], r["R_ideal"]),
                 ("ideal endurance h", m["endurance_ideal_h"], r["T_ideal"]), ("take-off C-rate", p["c_rate_takeoff"], r["c_to"])]
        for n, a, b in pairs:
            rows.append((f"{key} {n}", a, b, 0.001))
    return rows


def _calibration():
    res = run_from_dict(preset("risen_e600_class2"))["results"]
    return [("Risen class II OEW excl. battery kg", res["summary"]["oew_excl_battery_kg"], 280.0, 0.001),
            ("Risen class II C_D0", res["summary"]["cd0"], 0.0120, 0.005)]


def _cross_checks():
    v = run_from_dict(preset("velis_electro_check"))["results"]
    h = run_from_dict(preset("hansa_ng_e950"))["results"]
    return [
        ("Velis: total endurance at V_mp, h", v["mission"]["endurance_vmp_no_reserve_h"], 1.0, "published ~60 min total"),
        ("Velis: take-off C-rate", v["performance"]["c_rate_takeoff"], 2.6, "57.6 kW / 0.885 / 24.8 kWh"),
        ("Velis: V_S0 KCAS", v["performance"]["vs0_kcas"], 45.0, "published 45 KCAS"),
        ("E-Hansa 950: battery kg", h["summary"]["battery_kg"], 290.0, "E-Hansa study"),
        ("E-Hansa 950: total endurance h", h["mission"]["endurance_vmp_no_reserve_h"], 65 / 60, "E-Hansa study 65 min"),
        ("E-Hansa 950: V_S1 KCAS", h["performance"]["vs1_kcas"], 54.0, "E-Hansa study 54 KCAS"),
        ("E-Hansa 950: L/D max", h["summary"]["ld_max"], 11.0, "back-calculated ~11"),
    ]


def main():
    fails = 0
    print(f"{'check':42s} {'eacsize':>10s} {'reference':>10s} {'error':>8s} {'tol':>6s}")
    for name, a, b, tol in _regression() + _calibration():
        err = (a - b) / b
        ok = abs(err) <= tol
        fails += not ok
        print(f"{name:42s} {a:10.3f} {b:10.3f} {err * 100:7.3f}% {tol * 100:5.2f}% {'OK' if ok else 'FAIL'}")
    print("\nCross-checks (reported only)")
    for name, a, b, src in _cross_checks():
        print(f"{name:42s} {a:10.2f} {b:10.2f} {(a - b) / b * 100:+7.1f}%  {src}")
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
