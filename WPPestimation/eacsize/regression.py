"""
Electric-aircraft mass regression (weight method 'electric_regression').

The dataset (data/electric_aircraft.json) holds flown battery-electric light aircraft with sourced
MTOM, empty mass, battery mass or energy, and motor power. For each aircraft the airframe mass is

    m_airframe = empty mass excl. battery - e-powertrain(P) - propeller(P)

Where only battery kWh is published, battery mass = kWh / regression_pack_wh_kg (an assumption).

using the same powertrain and propeller models as the sizing (so the powertrain is not counted
twice when the sizing adds it back). The fit is log-linear least squares (Roskam class I form):

    ln m_airframe = ln a + b ln MTOM [+ c ln seats]

Statistical, so it captures what real electric aircraft weigh (composite structure, fixed
equipment, installation) without a component breakdown. It does not respond to AR, load factor
or gear type, and should not be extrapolated far outside the data range.
"""
from __future__ import annotations
import json, math, os
from dataclasses import dataclass, field

DATA = os.path.join(os.path.dirname(__file__), "data", "electric_aircraft.json")


def load_dataset(path=DATA):
    with open(path) as f:
        return json.load(f)["aircraft"]


def empty_excl_battery(ac, A):
    """(empty mass excl. battery [kg], battery estimated?) or (None, None) if not derivable."""
    if ac.get("empty_excl_battery_kg"):
        return ac["empty_excl_battery_kg"], False
    if ac.get("empty_includes_battery") is False:
        return ac["empty_kg"], False
    if ac.get("empty_includes_battery") is None:
        return None, None
    if ac.get("battery_kg"):
        return ac["empty_kg"] - ac["battery_kg"], False
    if A.regression_estimate_battery and ac.get("battery_kwh"):
        return ac["empty_kg"] - ac["battery_kwh"] * 1e3 / A.regression_pack_wh_kg, True
    return None, None


def airframe_mass(ac, A):
    """Empty mass excl. battery, less the modelled e-powertrain and propeller [kg]."""
    empty, est = empty_excl_battery(ac, A)
    if empty is None:
        return None, None
    P = ac["power_kw"]
    return empty - P / A.pt_kw_per_kg - A.prop_mass_coeff * P ** 0.7, est


def _lstsq(X, y):
    """Solve normal equations (X^T X) beta = X^T y by Gaussian elimination."""
    n = len(X[0])
    M = [[sum(r[i] * r[j] for r in X) for j in range(n)] + [sum(r[i] * yi for r, yi in zip(X, y))] for i in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-12:
            raise ValueError("regression is singular (not enough spread in the data)")
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [a - f * b for a, b in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


@dataclass
class Fit:
    a: float
    b: float
    c: float
    use_seats: bool
    n: int
    r2: float
    rms_pct: float
    mtom_range: tuple
    points: list = field(default_factory=list)
    b_fixed: bool = False

    def predict(self, mtom, seats):
        m = self.a * mtom ** self.b
        if self.use_seats:
            m *= max(seats, 1) ** self.c
        return m

    def as_dict(self):
        return dict(a=self.a, b=self.b, c=self.c if self.use_seats else None, n=self.n, r2=self.r2,
                    rms_error_pct=self.rms_pct, mtom_min_kg=self.mtom_range[0], mtom_max_kg=self.mtom_range[1],
                    b_fixed=self.b_fixed, points=self.points,
                    form="m_airframe = a * MTOM^b" + (" * seats^c" if self.use_seats else "")
                         + (f" (b fixed at {self.b:g})" if self.b_fixed else ""))


_CACHE = {}


def fit(A, path=DATA):
    names = lambda txt: tuple(sorted(x.strip().lower() for x in txt.split(",") if x.strip()))
    excl, add = names(A.regression_exclude), names(A.regression_add)
    key = (path, os.path.getmtime(path), A.pt_kw_per_kg, A.prop_mass_coeff, A.regression_use_seats, excl, add,
           A.regression_estimate_battery, A.regression_pack_wh_kg, A.regression_fixed_exponent)
    if key in _CACHE:
        return _CACHE[key]
    rows, ys, est = [], [], []
    for ac in load_dataset(path):
        nm = ac["name"].lower()
        if nm in excl or not (ac.get("include", True) or nm in add):
            continue
        y, e = airframe_mass(ac, A)
        if y is None:
            continue
        if y <= 0:
            raise ValueError(f"non-positive airframe mass for {ac['name']} after removing battery and powertrain")
        rows.append(ac); ys.append(y); est.append(e)
    if len(rows) < 2:
        raise ValueError("electric_regression needs at least 2 usable aircraft in the dataset")
    b_fixed = A.regression_fixed_exponent > 0
    use_seats = (not b_fixed and A.regression_use_seats and len({ac["seats"] for ac in rows}) > 1
                 and len(rows) >= 4)
    X = [[1.0, math.log(ac["mtom_kg"])] + ([math.log(ac["seats"])] if use_seats else []) for ac in rows]
    ly = [math.log(y) for y in ys]
    if b_fixed:   # fit the coefficient only: ln a = mean(ln m - b ln MTOM)
        bf = A.regression_fixed_exponent
        beta = [sum(l - bf * x[1] for l, x in zip(ly, X)) / len(ly), bf]
    else:
        beta = _lstsq(X, ly)
    a, b, c = math.exp(beta[0]), beta[1], (beta[2] if use_seats else 0.0)
    pred = [sum(bi * xi for bi, xi in zip(beta, x)) for x in X]
    mean = sum(ly) / len(ly)
    ss_res = sum((l - p) ** 2 for l, p in zip(ly, pred))
    ss_tot = sum((l - mean) ** 2 for l in ly)
    pts = []
    for ac, y, p, e in zip(rows, ys, pred, est):
        pts.append(dict(name=ac["name"], mtom_kg=ac["mtom_kg"], seats=ac["seats"], airframe_kg=y,
                        predicted_kg=math.exp(p), error_pct=(math.exp(p) - y) / y * 100,
                        battery_estimated=e, confidence=ac.get("confidence", "")))
    rms = math.sqrt(sum(pt["error_pct"] ** 2 for pt in pts) / len(pts))
    f = Fit(a, b, c, use_seats, len(rows), 1 - ss_res / ss_tot if ss_tot > 0 else 1.0, rms,
            (min(ac["mtom_kg"] for ac in rows), max(ac["mtom_kg"] for ac in rows)), pts, b_fixed)
    _CACHE[key] = f
    return f
