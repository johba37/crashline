"""v2 perpetual note: closed form vs Monte Carlo (docs/v2-perpetual-note.md).

State x = S/H, H = reference (high-water mark). Per unit of live notional:
  coupon c per year, streamed
  notional amortizes at rate phi (exponentially weighted maturities)
  regime 0 (clean):      amortized slice pays 1
  regime 1 (knocked in): amortized slice pays x (= S/H)
  x >= 1  -> H ratchets to S, regime 0   (autocall analogue: re-strike, not redeem)
  x <  k  -> regime 1                    (knock-in latch)
GBM, rate r, no dividends (same world as ml/teacher.py).
"""
import numpy as np

BGK = 0.5826

def betas(sig, r, phi):
    a = 0.5 * sig**2
    b = r - a
    d = np.sqrt(b * b + 4 * a * (r + phi))
    return (-b + d) / (2 * a), (-b - d) / (2 * a)

def closed_form(sig, r, phi, c, k, top=1.0):
    """Returns V0(x), V1(x). Boundaries: knock-in at k, ratchet/heal at `top`."""
    bp, bm = betas(sig, r, phi)
    p0 = (c + phi) / (r + phi)
    p1 = c / (r + phi)
    # unknowns A0, B0, A1
    M = np.array([
        [bp * top**(bp - 1), bm * top**(bm - 1), 0.0],      # V0'(top) = 0
        [k**bp, k**bm, -k**bp],                              # V0(k) = V1(k)
        [top**bp, top**bm, -top**bp],                        # V0(top) = V1(top)
    ])
    rhs = np.array([0.0, p1 + k - p0, p1 + top - p0])
    A0, B0, A1 = np.linalg.solve(M, rhs)
    V0 = lambda x: p0 + A0 * x**bp + B0 * x**bm
    V1 = lambda x: p1 + x + A1 * x**bp
    return V0, V1

def fair_coupon(sig, r, phi, k, top=1.0, kk=None):
    kk = k if kk is None else kk
    v0 = closed_form(sig, r, phi, 0.0, kk, top)[0](top)
    v1 = closed_form(sig, r, phi, 1.0, kk, top)[0](top)
    return (1 - v0) / (v1 - v0)

def mc(x0, flag0, sig, r, phi, c, k, dt, years=14.0, paths=2**16, seed=1):
    """Monitoring every dt; coupon + amortization settled at each monitoring date."""
    rng = np.random.default_rng(seed)
    n = int(round(years / dt))
    x = np.full(paths, x0); flag = np.full(paths, bool(flag0))
    live = np.ones(paths); pv = np.zeros(paths)
    a = 1 - np.exp(-phi * dt)
    mu = (r - 0.5 * sig**2) * dt; sd = sig * np.sqrt(dt)
    half = paths // 2
    for i in range(1, n + 1):
        z = rng.standard_normal(half); z = np.concatenate([z, -z])
        x = x * np.exp(mu + sd * z)
        df = np.exp(-r * i * dt)
        heal = x >= 1.0
        flag = np.where(heal, False, flag | (x < k))
        x = np.where(heal, 1.0, x)
        # coupon accrued over the period on live notional, avg live over period
        pv += df * live * c * (1 - np.exp(-phi * dt)) / phi   # int of e^{-phi s} ds
        live_end = live * np.exp(-phi * dt)
        pv += df * (live - live_end) * np.where(flag, x, 1.0)
        live = live_end
    pm = 0.5 * (pv[:half] + pv[half:])
    return pm.mean(), pm.std(ddof=1) / np.sqrt(half)

if __name__ == "__main__":
    sig, r, phi, k = 0.55, 0.04, 1.0, 0.60
    cstar = fair_coupon(sig, r, phi, k)
    print(f"beta+/-: {betas(sig, r, phi)}")
    print(f"fair coupon, continuous monitoring: {cstar*100:.2f}% / yr")
    c = cstar
    V0, V1 = closed_form(sig, r, phi, c, k)
    print("\n-- convergence of MC to closed form as monitoring gets finer (x0=0.8, clean) --")
    for name, dt in [("weekly", 1/52), ("daily", 1/365), ("6-hourly", 1/1460)]:
        p, se = mc(0.8, 0, sig, r, phi, c, k, dt, paths=2**14 if dt < 1/400 else 2**16)
        print(f"{name:9s} MC {p*1e4:8.1f} +- {se*1e4:4.1f} bps | closed {V0(0.8)*1e4:8.1f}")
    print("\n-- weekly fixings: plain closed form vs BGK-shifted closed form vs MC --")
    dt = 1/52
    sh = np.exp(BGK * sig * np.sqrt(dt))
    V0b, V1b = closed_form(sig, r, phi, c, k / sh, top=sh)
    print(" x0  flag |   MC (se)      | closed   err | BGK      err")
    for flag0, xs in [(0, [1.0, 0.95, 0.9, 0.8, 0.7, 0.65, 0.61]), (1, [0.95, 0.8, 0.6, 0.5, 0.4])]:
        for x0 in xs:
            p, se = mc(x0, flag0, sig, r, phi, c, k, dt, paths=2**17)
            f, fb = (V1, V1b) if flag0 else (V0, V0b)
            print(f"{x0:4.2f}  {flag0}   | {p*1e4:7.1f} ({se*1e4:3.1f}) | {f(x0)*1e4:7.1f} {1e4*(f(x0)-p):6.1f} | {fb(x0)*1e4:7.1f} {1e4*(fb(x0)-p):6.1f}")
    print("\n-- fair coupon (% / yr), weekly fixings via BGK, phi=1 --")
    for s in (0.3, 0.55, 0.8):
        shs = np.exp(BGK * s * np.sqrt(dt))
        print(f"vol {s:.0%}: " + "  ".join(f"k={kk:.0%}: {fair_coupon(s, r, phi, kk, top=shs, kk=kk/shs)*100:5.1f}" for kk in (0.5, 0.6, 0.7, 0.8)))
