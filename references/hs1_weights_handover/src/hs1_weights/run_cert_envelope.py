"""Dual-basis load envelope for an HS1-like configuration.

Inputs below are ILLUSTRATIVE placeholders; replace with results.json values from hs1_design.
Run:  python -m hs1_weights.run_cert_envelope   (after pip install -e .)
"""
from .certification import BASES, evaluate, envelope, min_area_for_stall, unverified_criteria, SPORT_PILOT_VS1_MAX

CFG = dict(mass_kg=700.0, area_m2=11.0, cbar_m=1.10, a_per_rad=5.0,
           cl_max_clean=1.5, cl_max_land=2.0, vc_kt=105.0, vh_kt=115.0)

def main():
    res = [evaluate(b, **CFG) for b in BASES.values()]
    keys = ["wing_loading_Pa", "VS1_kt", "VS0_kt", "VA_kt", "VC_min_kt", "VD_kt", "n_man_pos", "n_man_neg",
            "n_gust_VC", "n_gust_VD", "n_limit_pos", "n_limit_neg", "n_ult_pos", "n_ult_neg",
            "pos_governed_by", "q_D_Pa", "landing_sink_mps"]
    print(f"{'quantity':22s}" + "".join(f"{r['basis']:>22s}" for r in res))
    for k in keys:
        row = []
        for r in res:
            v = r[k]
            row.append("(%.2f, %.2f)" % v if isinstance(v, tuple) else (f"{v:.2f}" if isinstance(v, float) else str(v)))
        print(f"{k:22s}" + "".join(f"{s:>22s}" for s in row))
    env = envelope(res)
    print("\nENVELOPE (design-to):")
    for k, v in env.items():
        print(f"  {k}: {v}")
    print("\nStall-constrained minimum wing area at", CFG["mass_kg"], "kg:")
    print(f"  sport-pilot VS1<=59 kt, CLmax_clean={CFG['cl_max_clean']}: "
          f"{min_area_for_stall(CFG['mass_kg'], SPORT_PILOT_VS1_MAX.value, CFG['cl_max_clean']):.2f} m2")
    print(f"  VS0<=61 kt, CLmax_land={CFG['cl_max_land']}: "
          f"{min_area_for_stall(CFG['mass_kg'], 61.0, CFG['cl_max_land']):.2f} m2")
    print("\nSensitivity of governing ultimate load factor to mass:")
    for m in (600, 700, 800, 900, 1000):
        rr = [evaluate(b, **{**CFG, 'mass_kg': float(m)}) for b in BASES.values()]
        e = envelope(rr)
        print(f"  {m:5d} kg  n_ult+ = {e['n_ult_pos']['value']:.2f} ({e['n_ult_pos']['governing']}, "
              f"{[r['pos_governed_by'] for r in rr]})  n_ult- = {e['n_ult_neg']['value']:.2f}")
    print(f"\n{len(unverified_criteria())} criteria flagged for verification against purchased standards.")

if __name__ == "__main__":
    main()
