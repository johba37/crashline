"""Checks of the perpetual-note teacher (ml/teacher_perp.py).

    python ml/test_teacher_perp.py > ml/test_teacher_perp.log      # ~4 min on 12 cores

(a) the weekly return law: martingale and variance (ordinary and earnings week)
(b) the fixing rule: float rule of the teacher vs the integer rule of the spec
    (docs/v2-spec.md section 2) on random fixing paths
(c) the FFT form of the weekly operator vs explicit matrices
(d) fixed-point residual; second-order convergence in the grid step, and the
    extrapolated teacher (two grids) against the next refinement
(e) GBM limit (no jumps, no earnings, rho = r, dt = 1/52) vs ml/perp_note_check.mc
(f) model P1 world: quadrature vs brute-force Monte Carlo, 36 states
(g) a full week ahead reproduces the fixed point; tau -> 0 is continuous
(h) no earnings jump: the price does not depend on tE
(i) deep knocked-in notes are worth alpha * x
"""

from __future__ import annotations

import math
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import perp_note_check as pnc  # noqa: E402
import teacher_perp as tp  # noqa: E402

RAY, WAD = 10**27, 10**18
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if not ok:
        FAILS.append(name)


# --- (a) -----------------------------------------------------------------------

def week_law(cfg: tp.PerpConfig) -> None:
    print("(a) weekly return law")
    dt = tp.P1.dt
    for vol in (0.20, 0.55, 0.90):
        var_cycle, worst = 0.0, 0.0
        for earn, weeks in ((False, cfg.cycle_fixings - 1), (True, 1)):
            w, m, sd = tp.mixture(cfg, vol, dt, dt, earn)
            w, m, sd = w[0], m[0], sd[0]
            growth = float((w * np.exp(m + 0.5 * sd * sd)).sum())
            mean = float((w * m).sum())
            var_cycle += weeks * (float((w * (sd * sd + m * m)).sum()) - mean * mean)
            worst = max(worst, abs(growth / math.exp(cfg.r * dt) - 1.0))
        want = vol * vol * cfg.cycle_fixings * dt
        check(f"vol {vol:.0%}", worst < 1e-12 and abs(var_cycle / want - 1.0) < 1e-5,
              f"|E[e^Z] / e^(r dt) - 1| {worst:.1e}; variance of a 13-week cycle / (vol^2 * 13 dt) = "
              f"{var_cycle / want:.7f}")


# --- (b) -----------------------------------------------------------------------

def spec_fixing(state: dict, f: int, k_bps: int, a_wad: int, R: int = 0, UNIT: int = 10**6) -> None:
    """docs/v2-spec.md section 2, fixing n >= 1, integers."""
    if f >= state["H"]:
        state["H"], state["ki"] = f, False
    elif f * 10_000 < k_bps * state["H"]:
        state["ki"] = True
    m = state["s"] * a_wad // WAD
    total = m * (UNIT + R) // UNIT
    note = total if not state["ki"] else m * (f * UNIT + R * state["H"]) // (state["H"] * UNIT)
    state["s"] -= m
    state["note"] += note
    state["writer"] += total - note


def fixing_rule() -> None:
    print("(b) fixing rule: teacher (float) vs spec (integers), 400 random paths of 150 fixings")
    rng = np.random.default_rng(7)
    a_wad, k_bps = 18_995_352_771_274_247, 6000
    a, k = a_wad / WAD, k_bps / 1e4
    worst, flags_equal = 0.0, True
    for _ in range(400):
        H0 = int(rng.integers(10**8, 10**11))
        st = {"H": H0, "ki": False, "s": RAY, "note": 0, "writer": 0}
        H, flag, live, note, writer = float(H0), np.array(False), 1.0, 0.0, 0.0
        f = H0
        for _ in range(150):
            f = max(1, int(f * math.exp(rng.normal(-0.003, 0.09))))
            if rng.random() < 0.05:                       # exactly on a barrier now and then
                f = st["H"] if rng.random() < 0.5 else st["H"] * k_bps // 10_000
            spec_fixing(st, f, k_bps, a_wad)
            x2, flag, pay = tp.fixing_step(np.array(f / H), flag, k)
            if float(x2) == 1.0:
                H = float(f)
            note += live * a * float(pay)
            writer += live * a * (1.0 - float(pay))
            live *= 1.0 - a
            flags_equal &= bool(flag) == st["ki"] and H == float(st["H"])
        worst = max(worst, abs(note - st["note"] / RAY), abs(writer - st["writer"] / RAY), abs(live - st["s"] / RAY))
    check("released principal, both legs, and the live notional", flags_equal and worst < 1e-12,
          f"states equal at every fixing: {flags_equal}; max |float - integer| {worst:.1e}")


# --- (c), (d) --------------------------------------------------------------------

def operator_and_grid(cfg: tp.PerpConfig) -> None:
    print("(c) weekly operator: FFT form vs explicit matrices (n_k = 64)")
    s = tp.Solver(0.55, cfg, n_k=64)
    worst = 0.0
    for earn in (False, True):
        a = s._apply(s._week(earn), s.W0[3], s.W1[3])
        b = s.apply_dense(s.dense_week(earn), s.W0[3], s.W1[3])
        worst = max(worst, float(np.abs(a[0] - b[0]).max()), float(np.abs(a[1] - b[1]).max()))
    check("max |difference|", worst < 1e-13, f"{worst:.1e}")

    print("(d) fixed point, grid convergence, and the extrapolated teacher (4 * fine - coarse) / 3")
    dt = tp.P1.dt
    rng = np.random.default_rng(3)
    n = 400
    x = np.concatenate([rng.uniform(0.2, 1.3, n - 40), rng.uniform(0.58, 0.62, 20), rng.uniform(0.98, 1.02, 20)])
    f = rng.integers(0, 2, n)
    tau = np.concatenate([rng.uniform(0, dt, n - 60), np.zeros(20), np.full(40, dt)])
    te = rng.uniform(0, 98 / 365, n)
    for vol in (0.20, 0.55, 0.90):
        vals, res = {}, 0.0
        for nk in (128, 256, 512):
            sv = tp.Solver(vol, cfg, n_k=nk)
            vals[nk] = sv.value(x, f, tau, te) * 1e4
            res = max(res, sv.residual)
        d1, d2 = np.abs(vals[128] - vals[256]).max(), np.abs(vals[256] - vals[512]).max()
        r1 = vals[256] + (vals[256] - vals[128]) / 3
        r2 = vals[512] + (vals[512] - vals[256]) / 3
        dr = np.abs(r1 - r2).max()
        check(f"vol {vol:.0%}, {n} states", res < 1e-12 and 3.5 < d1 / d2 < 4.5 and dr < 0.05,
              f"residual {res:.1e}; plain grids: max |n_k 128 - 256| {d1:.3f} bps, |256 - 512| {d2:.3f} bps "
              f"(ratio {d1 / d2:.2f}, second order = 4); extrapolated: |(128, 256) - (256, 512)| {dr:.4f} bps")


# --- (e), (f) --------------------------------------------------------------------

def _mc_doc(args):
    x0, flag, seed = args
    return pnc.mc(x0, flag, 0.55, 0.04, 1.0, 0.0, 0.6, 1 / 52, paths=2**18, seed=seed)


def _mc_p1(args):
    x0, vol, flag, tau, te, seed = args
    return tp.mc(x0, vol, bool(flag), tau, te, tp.load_config(), paths=2**18, seed=seed)


def vs_monte_carlo(cfg: tp.PerpConfig, pool: Pool) -> None:
    print("(e) GBM limit (rho = r = 4%, dt = 1/52, vol 55%) vs ml/perp_note_check.mc, 2^18 paths, coupon 0")
    gbm = tp.PerpConfig(name="gbm-doc", r=0.04, r_disc=0.04)
    prod = tp.Product(k=0.6, a=-math.expm1(-1 / 52), dt=1 / 52)
    s = tp.Teacher(0.55, gbm, prod)
    states = [(x, 0) for x in (1.0, 0.95, 0.9, 0.8, 0.7, 0.65, 0.61)] + [(x, 1) for x in (0.95, 0.8, 0.6, 0.5, 0.4)]
    res = pool.map(_mc_doc, [(x, f, 11 + i) for i, (x, f) in enumerate(states)])
    worst = 0.0
    for (x, f), (p, se) in zip(states, res):
        q = float(s.value(np.array([x]), f, prod.dt, 9.0)[0])
        zscore = (q - p) / se
        worst = max(worst, abs(zscore))
        print(f"      x {x:.2f} flag {f}: quadrature {q * 1e4:8.2f}  MC {p * 1e4:8.2f} ({se * 1e4:.2f})  "
              f"diff {(q - p) * 1e4:+.2f} bps  z {zscore:+.2f}")
    check("12 states", worst < 3.5, f"max |z| {worst:.2f}")

    print("(f) model P1 world: quadrature vs brute-force MC, 2^18 paths")
    dt, day = tp.P1.dt, 1 / 365
    base = [
        (1.00, 0, dt, 40 * day), (0.90, 0, dt, 3 * day), (0.80, 0, dt / 2, 2 * day), (0.80, 0, dt / 2, 5 * day),
        (0.62, 0, dt / 7, 70 * day), (0.60, 0, dt, 20 * day), (0.55, 0, dt / 2, 4 * day), (1.08, 0, 2 * day, 60 * day),
        (0.50, 1, dt, 11 * day), (0.95, 1, 3 * day, 2.9 * day), (0.95, 1, 3 * day, 3.1 * day), (1.05, 1, dt / 3, 55 * day),
        (0.30, 1, 5 * day, 97 * day), (0.90, 0, 0.0, 0.0), (0.90, 1, 0.0, 36 * day), (0.70, 0, 0.0, 91 * day),
    ]
    states = [(x, vol, f, tau, te) for vol in (0.20, 0.55, 0.90) for (x, f, tau, te) in base
              if not (vol != 0.55 and (x, f) in ((0.80, 0), (0.95, 1)))]
    res = pool.map(_mc_p1, [st + (101 + i,) for i, st in enumerate(states)])
    cache: dict = {}
    worst, n = 0.0, 0
    for (x, vol, f, tau, te), (p, se) in zip(states, res):
        q = float(tp.price(x, vol, f, tau * tp.YEAR_SECS, te * tp.YEAR_SECS, cfg, cache=cache))
        zscore = (q - p) / max(se, 1e-7)
        worst = max(worst, abs(zscore))
        n += 1
        print(f"      vol {vol:.0%} x {x:.2f} flag {f} tau {tau / day:4.1f} d tE {te / day:5.1f} d: quadrature "
              f"{q * 1e4:8.2f}  MC {p * 1e4:8.2f} ({se * 1e4:.2f})  diff {(q - p) * 1e4:+.2f} bps  z {zscore:+.2f}")
    check(f"{n} states", worst < 3.5, f"max |z| {worst:.2f}")


# --- (g), (h), (i) -----------------------------------------------------------------

def consistency(cfg: tp.PerpConfig) -> None:
    print("(g) a full week ahead reproduces the fixed point; tau -> 0 is continuous")
    dt = tp.P1.dt
    s = tp.Solver(0.55, cfg)
    worst = 0.0
    for j in (0, 1, 7, 12, 13):
        te = (j + 0.4) * dt                                  # the release falls in week j + 1
        for flag, nodes in ((0, [0, 40, s.n_k]), (1, [0, 100, 700, 2000])):
            x = np.exp(s.z[nodes])
            worst = max(worst, float(np.abs(s.value(x, flag, dt, te) - (s.W1 if flag else s.W0)[j, nodes]).max()))
    check("value(tau = dt) = W_j at grid nodes", worst < 1e-11, f"max |difference| {worst:.1e}")
    eps = 1e-10
    xs = np.array([0.3, 0.55, 0.65, 0.8, 0.95, 1.05, 1.2])
    worst = 0.0
    for flag in (0, 1):
        for te in (0.01, 0.1):
            worst = max(worst, float(np.abs(s.value(xs, flag, eps, te) - s.value(xs, flag, 0.0, te)).max()))
    check("value(tau = 3 ms) = value(tau = 0), away from the barriers", worst < 1e-6, f"max |difference| {worst:.1e}")

    print("(h) no earnings jump: no dependence on tE")
    flat = tp.PerpConfig(name="no-earnings", lambda_year=cfg.lambda_year, mu_j=cfg.mu_j, sigma_j=cfg.sigma_j,
                         sigma_e=0.0, vol_ref=cfg.vol_ref)
    s0 = tp.Solver(0.55, flat)
    v = np.array([s0.value(xs, 0, dt / 3, te) for te in (0.0, 0.004, 0.1, 0.26)])
    check("spread over tE", float(np.ptp(v, axis=0).max()) == 0.0, f"{float(np.ptp(v, axis=0).max()):.1e}")

    print("(i) deep knocked-in notes: value / x -> alpha (no heal in sight)")
    g = math.exp((cfg.r - cfg.r_disc) * dt)
    alpha = tp.P1.a * g / (1.0 - (1.0 - tp.P1.a) * g)
    for vol in (0.20, 0.55):
        sv = tp.Solver(vol, cfg)
        ratio = float(sv.value(0.05, 1, dt, 0.1)[0]) / 0.05
        check(f"vol {vol:.0%}", abs(ratio / alpha - 1.0) < (0.002 if vol < 0.5 else 0.03),
              f"value(0.05) / 0.05 = {ratio:.5f}, alpha = {alpha:.5f}")


def main() -> int:
    t0 = time.time()
    cfg = tp.load_config()
    print(f"teacher config: {cfg.name}  r {cfg.r}  rDiscount {cfg.r_disc}  lambda {cfg.lambda_year}/yr  "
          f"sigmaJ {cfg.sigma_j}  sigmaE {cfg.sigma_e}  volRef {cfg.vol_ref}  cycle {cfg.cycle_fixings} fixings")
    week_law(cfg)
    fixing_rule()
    operator_and_grid(cfg)
    with Pool(min(12, os.cpu_count() or 1)) as pool:
        vs_monte_carlo(cfg, pool)
    consistency(cfg)
    print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}  ({time.time() - t0:.0f} s)")
    return len(FAILS)


if __name__ == "__main__":
    sys.exit(main())
