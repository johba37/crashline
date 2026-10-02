"""v2 perpetual note: the closed form, float reference (docs/v2-spec.md section 3).

Per unit of live notional:

    NOTE   = coupon + principal
    coupon = R * a * e^(-rho tau) / (1 - (1 - a) e^(-rho dt))     exact, = R at rho = 0
    principal ~ F(x, sigma, knockedIn)                             the formula below

World: r = risk-neutral drift, rho = discount rate of the payouts, phi = melt
rate per year, dt = fixing interval in years, a = 1 - e^(-phi dt), sigma =
total annualized vol, x = spot / H, k = knock-in level.

    beta+-  = (-b +- sqrt(b^2 + 2 sigma^2 (rho + phi))) / sigma^2,  b = r - sigma^2/2
    alpha   = phi / (rho + phi - r)        value of "pays x" (1 at rho = r)
    P0      = phi / (rho + phi)            value of "pays 1"
    top     = e^(BGK sigma sqrt(dt)),  k' = k / top,  u = x / top,  kappa = k' / top
    g(y)    = alpha y - P0
    B'      = (g(k') - g(top) kappa^beta+) / (kappa^beta- - kappa^beta+)
    D'      = g(top) - B',   A' = -B' beta- / beta+,   C' = A' - D'
    clean       F0(x) = P0 + A' u^beta+ + B' u^beta-     k' <= x < top
    knocked in  F1(x) = alpha x + C' u^beta+             x < top
    x >= top (either state): F0(top);   clean and x < k': F1(x)

With rho = r this is the formula of docs/v2-perpetual-note.md ("Price") minus
its coupon constant c / (r + phi); `python ml/perp_formula.py --check`
reproduces that document's numbers and ml/perp_note_check.py's closed form.

The integer twin of contracts/src/PerpFormula.sol is tools/perp_formula.py; it
is compared against `principal` here, so keep the signatures stable.
"""

from __future__ import annotations

import argparse
import math

import numpy as np

BGK = 0.5826                      # -zeta(1/2) / sqrt(2 pi), as pinned in the spec
YEAR_SECS = 31_536_000
WEEK_SECS = 604_800
MELT_SHARE_WAD = 18_995_352_771_274_247      # a for phi = 1/yr, weekly, 365-day year
P1 = dict(r=0.04, rho=0.0, phi=1.0, k=0.60, dt=WEEK_SECS / YEAR_SECS)   # model P1 (spec section 4)


def betas(sigma, r, rho, phi):
    """Roots of sigma^2/2 beta (beta - 1) + r beta - (rho + phi) = 0: (beta+, beta-)."""
    sigma = np.asarray(sigma, dtype=np.float64)
    s2 = sigma * sigma
    b = r - 0.5 * s2
    d = np.sqrt(b * b + 2.0 * s2 * (rho + phi))
    return (-b + d) / s2, (-b - d) / s2


def coefficients(sigma, r, rho, phi, k, dt, bgk=BGK):
    """Everything of the formula that doesn't depend on x, as a dict of arrays."""
    sigma = np.asarray(sigma, dtype=np.float64)
    bp, bm = betas(sigma, r, rho, phi)
    alpha = phi / (rho + phi - r)
    p0 = phi / (rho + phi)
    top = np.exp(bgk * sigma * math.sqrt(dt))
    kp = k / top
    kappa = kp / top
    g_k = alpha * kp - p0
    g_top = alpha * top - p0
    kb_p, kb_m = kappa**bp, kappa**bm
    B = (g_k - g_top * kb_p) / (kb_m - kb_p)
    D = g_top - B
    A = -B * bm / bp
    C = A - D
    return dict(bp=bp, bm=bm, alpha=alpha, p0=p0, top=top, kp=kp, A=A, B=B, C=C)


def principal(x, sigma, knocked_in, r, rho, phi, k, dt, bgk=BGK):
    """Formula value of the principal part per unit of live notional.

    x, sigma, knocked_in broadcast against each other (numpy). bgk = 0 gives the
    continuous-monitoring formula (top = 1, k' = k)."""
    x = np.asarray(x, dtype=np.float64)
    ki = np.asarray(knocked_in).astype(bool)
    c = coefficients(sigma, r, rho, phi, k, dt, bgk)
    x, ki, top = np.broadcast_arrays(x, ki, c["top"])
    xe = np.minimum(x, top)
    u = xe / top
    f0 = c["p0"] + c["A"] * u ** c["bp"] + c["B"] * u ** c["bm"]
    f1 = c["alpha"] * xe + c["C"] * u ** c["bp"]
    # x >= top: both branches equal F0(top) there (F0(top) = F1(top) by construction)
    use1 = (ki | (x < c["kp"])) & (x < top)
    return np.where(use1, f1, f0)


def coupon_value(R, a, rho, dt, tau):
    """Exact value of the coupon stream per unit of live notional: R * a paid at
    every fixing on the notional alive then, the next one tau years from now."""
    return R * a * np.exp(-rho * np.asarray(tau, dtype=np.float64)) / (1.0 - (1.0 - a) * math.exp(-rho * dt))


def note_value(x, sigma, knocked_in, R, tau, r, rho, phi, k, dt, bgk=BGK):
    """coupon + formula principal (no student)."""
    a = -math.expm1(-phi * dt)
    return coupon_value(R, a, rho, dt, tau) + principal(x, sigma, knocked_in, r, rho, phi, k, dt, bgk)


def fair_reserve(sigma, r, rho, phi, k, dt, bgk=BGK, at_top=False):
    """The coupon reserve R = c / phi at which a clean note right after a fixing
    at x = 1 is worth 1 (formula alone). at_top evaluates at x = top instead."""
    a = -math.expm1(-phi * dt)
    x = coefficients(sigma, r, rho, phi, k, dt, bgk)["top"] if at_top else 1.0
    return (1.0 - principal(x, sigma, False, r, rho, phi, k, dt, bgk)) / coupon_value(1.0, a, rho, dt, dt)


def fair_coupon(sigma, r, rho, phi, k, dt, bgk=BGK, at_top=False):
    """Fair coupon rate per year, c = R * phi."""
    return fair_reserve(sigma, r, rho, phi, k, dt, bgk, at_top) * phi


def doc_note_value(x, sigma, knocked_in, c, r, phi, k, dt, bgk=BGK):
    """The value exactly as docs/v2-perpetual-note.md writes it (rho = r, coupon
    constant c / (r + phi)); only for --check."""
    return c / (r + phi) + principal(x, sigma, knocked_in, r, r, phi, k, dt, bgk)


def _check() -> int:
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import perp_note_check as pnc

    fails = 0
    r, phi, k, dt = 0.04, 1.0, 0.60, 1 / 52

    print("-- vs ml/perp_note_check.closed_form (rho = r = 4%, dt = 1/52, c = 23.5%) --")
    worst = 0.0
    for sig in (0.30, 0.55, 0.80):
        for shifted in (False, True):
            sh = math.exp(BGK * sig * math.sqrt(dt)) if shifted else 1.0
            V0, V1 = pnc.closed_form(sig, r, phi, 0.235, k / sh, top=sh)
            for x in (1.0, 0.95, 0.9, 0.8, 0.7, 0.65, 0.61):
                mine = float(doc_note_value(x, sig, False, 0.235, r, phi, k, dt, BGK if shifted else 0.0))
                worst = max(worst, abs(mine - V0(x)))
            for x in (0.95, 0.8, 0.6, 0.5, 0.4):
                mine = float(doc_note_value(x, sig, True, 0.235, r, phi, k, dt, BGK if shifted else 0.0))
                worst = max(worst, abs(mine - V1(x)))
    ok = worst < 1e-12
    fails += not ok
    print(f"max |diff| over 72 points: {worst:.2e}  {'PASS' if ok else 'FAIL'}")

    print("\n-- fair coupon, the document's numbers (rho = r = 4%, dt = 1/52, k 60%, V0(top) = 1) --")
    want = {0.30: 9.5, 0.55: 23.5, 0.80: 35.7}
    for sig, w in want.items():
        # the document's coupon constant is c / (r + phi): c = (1 - F0) (r + phi)
        f_top = float(principal(coefficients(sig, r, r, phi, k, dt)["top"], sig, False, r, r, phi, k, dt))
        f_one = float(principal(1.0, sig, False, r, r, phi, k, dt))
        c_top, c_one = (1 - f_top) * (r + phi) * 100, (1 - f_one) * (r + phi) * 100
        ref = pnc.fair_coupon(sig, r, phi, k, top=math.exp(BGK * sig * math.sqrt(dt)),
                              kk=k / math.exp(BGK * sig * math.sqrt(dt))) * 100
        ok = abs(c_top - ref) < 1e-9 and round(c_top, 1) == w
        fails += not ok
        print(f"vol {sig:.0%}: {c_top:6.3f}%/yr (document {w}, perp_note_check {ref:.3f})  "
              f"at x = 1 instead of top: {c_one:6.3f}  {'PASS' if ok else 'FAIL'}")

    print("\n-- region rules --")
    c = coefficients(0.55, **P1)
    top, kp = float(c["top"]), float(c["kp"])
    f = lambda x, ki: float(principal(x, 0.55, ki, **P1))
    checks = [
        ("F0(top) = F1(top)", abs(f(top - 1e-12, False) - f(top - 1e-12, True)) < 1e-9),
        ("F0(k') = F1(k')", abs(f(kp + 1e-12, False) - f(kp - 1e-12, False)) < 1e-9),
        ("x >= top flat, clean", f(top, False) == f(1.3, False)),
        ("x >= top flat, knocked in", f(top, True) == f(1.3, True) == f(top, False)),
        ("clean below k' = knocked in", f(0.4, False) == f(0.4, True)),
        ("F0'(top) = 0", abs(f(top - 1e-6, False) - f(top - 2e-6, False)) < 1e-10),
    ]
    for name, ok in checks:
        fails += not ok
        print(f"{name:32s} {'PASS' if ok else 'FAIL'}")

    print("\n-- model P1 convention (drift r 4%, discount rho 0, 365-day year, weekly) --")
    a = -math.expm1(-P1["phi"] * P1["dt"])
    ok = abs(a * 1e18 - MELT_SHARE_WAD) < 1.0
    fails += not ok
    print(f"a = 1 - e^(-phi dt) = {a:.18f}  spec {MELT_SHARE_WAD / 1e18:.18f}  {'PASS' if ok else 'FAIL'}")
    print(f"coupon value at rho = 0 for R = 0.235: {float(coupon_value(0.235, a, 0.0, P1['dt'], 0.01)):.6f} (= R)")
    print("fair coupon %/yr = fair reserve R (phi = 1), formula alone, clean at x = 1 right after a fixing:")
    for kk in (0.5, 0.6, 0.7, 0.8):
        row = "  ".join(f"vol {s:.0%}: {float(fair_coupon(s, P1['r'], P1['rho'], P1['phi'], kk, P1['dt'])) * 100:5.2f}"
                        for s in (0.2, 0.3, 0.4, 0.55, 0.7, 0.8, 0.9))
        print(f"  k {kk:.0%}:  {row}")
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    return fails


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    raise SystemExit(_check() if args.check else 0)
