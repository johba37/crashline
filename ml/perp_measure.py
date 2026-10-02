"""v2 perpetual note: what the formula gets wrong, and where the value jumps.

    python ml/perp_measure.py > ml/perp_measure.log        # ~3 min on 12 cores

Measures the open items 1 and 2 of docs/v2-perpetual-note.md with the
quadrature teacher (ml/teacher_perp.py), model P1 terms (k 60%, phi 1/yr,
weekly, drift 4%, discount 0), principal part in bps of live notional:

  1. formula error right after a fixing, per vol, in two worlds:
       gap 1  smooth random walk (GBM): only the contract rules differ from the formula
       gap 2  the teacher's world: jumps and scheduled earnings
  2. the same mid-week, by time to the next fixing
  3. the two jumps at a fixing: knock-in at x = k (a clean note) and heal at
     x = 1 (a knocked-in note); the clean note at x = 1 has a kink only
  4. slope of the teacher and of the residual (teacher - formula) just outside
     candidate exclusion bands, in bps per 10 bps of spot
  5. earnings just before vs just after the next fixing: n = 0 vs n = 1, with
     n = the number of fixings before the release (the price depends on the
     time to earnings only through n)
  6. how much n moves the price
"""

from __future__ import annotations

import os
import sys
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import perp_formula as pf  # noqa: E402
import teacher_perp as tp  # noqa: E402

VOLS = (0.20, 0.30, 0.40, 0.55, 0.70, 0.90)
DT = tp.P1.dt
DAY, HOUR, SEC = 1 / 365, 1 / 8760, 1 / tp.YEAR_SECS
TAUS = [("0", 0.0), ("1 h", HOUR), ("6 h", 6 * HOUR), ("1 d", DAY), ("2 d", 2 * DAY), ("3.5 d", 3.5 * DAY),
        ("5 d", 5 * DAY), ("7 d", DT)]
BANDS = [("1 h", HOUR), ("6 h", 6 * HOUR), ("1 d", DAY), ("2 d", 2 * DAY)]
TE_MID = 45 * DAY
GBM_P1 = tp.PerpConfig(name="gbm-p1", r=0.04, r_disc=0.0)


def formula(x, vol, flag):
    return pf.principal(x, vol, flag, **pf.P1)


def measure(args):
    world, vol = args
    cfg = GBM_P1 if world == "gap1" else tp.load_config()
    t = tp.Teacher(vol, cfg)
    out = {"world": world, "vol": vol}

    # 1. right after a fixing
    xc = np.linspace(0.60, 1.0, 81)
    xk = np.linspace(0.20, 0.995, 160)
    ec = (formula(xc, vol, False) - t.value(xc, False, DT, TE_MID)) * 1e4
    ek = (formula(xk, vol, True) - t.value(xk, True, DT, TE_MID)) * 1e4
    out["after"] = (ec.min(), ec.max(), np.abs(ec).mean(), ek.min(), ek.max(), np.abs(ek).mean())
    out["par"] = (float(t.value(1.0, False, DT, TE_MID)[0]) * 1e4, float(formula(1.0, vol, False)) * 1e4)

    # 2. mid-week, whole spot range
    xs = np.arange(0.20, 1.3001, 0.01)
    mid = []
    for _, tau in TAUS:
        row = []
        for flag in (False, True):
            e = (formula(xs, vol, flag) - t.value(xs, flag, tau, TE_MID)) * 1e4
            i = int(np.abs(e).argmax())
            row.append((float(np.abs(e).max()), float(xs[i]), float(np.abs(e).mean())))
        mid.append(row)
    out["mid"] = mid

    # 3. jumps at the fixing (tau = 0, cum-release)
    bp = 1e-4
    k = tp.P1.k
    v = lambda x, flag: float(t.value(x, flag, 0.0, TE_MID)[0]) * 1e4
    out["jumps"] = (v(k, False) - v(k - bp, False), v(1.0, True) - v(1.0 - bp, True), v(1.0, False) - v(1.0 - bp, False))

    # 4. slopes just outside candidate bands
    slopes = []
    for _, band in BANDS:
        tau = band + SEC
        row = []
        for flag, lo, hi in ((False, 0.50, 0.70), (True, 0.90, 1.10)):
            g = np.arange(lo, hi + 1e-9, 1e-3)
            a, b = t.value(g, flag, tau, TE_MID) * 1e4, t.value(g + 1e-3, flag, tau, TE_MID) * 1e4
            fa, fb = formula(g, vol, flag) * 1e4, formula(g + 1e-3, vol, flag) * 1e4
            row.append((float(np.abs(b - a).max()), float(np.abs((b - fb) - (a - fa)).max())))
        slopes.append(row)
    out["slopes"] = slopes

    # 5. / 6. earnings (gap 2 only): n = fixings before the release
    if world == "gap2":
        step = []
        xs2 = np.arange(0.20, 1.3001, 0.005)
        for name, tau in TAUS[3:]:
            best = (0.0, 0.0, False)
            for flag in (False, True):
                d = (t.value_n(xs2, flag, tau, 0) - t.value_n(xs2, flag, tau, 1)) * 1e4
                i = int(np.abs(d).argmax())
                if abs(d[i]) > abs(best[0]):
                    best = (float(d[i]), float(xs2[i]), flag)
            step.append(best)
        out["step"] = step
        xp = np.array([0.30, 0.50, 0.58, 0.62, 0.70, 0.80, 0.90, 1.00, 1.10, 1.30])
        out["profile"] = [((t.value_n(xp, flag, 3.5 * DAY, 0) - t.value_n(xp, flag, 3.5 * DAY, 1)) * 1e4).tolist()
                          for flag in (False, True)]
        ns = np.arange(0, 19)
        rng_all, rng_later = (0.0, 0.0, False), (0.0, 0.0, False)
        for flag in (False, True):
            vals = np.array([t.value_n(xs2, flag, 3.5 * DAY, n) for n in ns]) * 1e4
            for lo, cur in ((0, "all"), (1, "later")):
                sp = vals[lo:].max(axis=0) - vals[lo:].min(axis=0)
                i = int(sp.argmax())
                if cur == "all" and sp[i] > rng_all[0]:
                    rng_all = (float(sp[i]), float(xs2[i]), flag)
                if cur == "later" and sp[i] > rng_later[0]:
                    rng_later = (float(sp[i]), float(xs2[i]), flag)
        out["nRange"] = (rng_all, rng_later)
    return out


def main() -> None:
    cfg = tp.load_config()
    print(f"teacher: {cfg.name} (jump share {cfg.lambda_year * (cfg.mu_j**2 + cfg.sigma_j**2) / cfg.vol_ref**2:.3f}, "
          f"earnings sigmaE {cfg.sigma_e} at volRef {cfg.vol_ref}); product P1; n_k 256 extrapolated")
    print("errors are formula - teacher, principal part, bps of live notional\n")
    with Pool(min(12, os.cpu_count() or 1)) as pool:
        res = pool.map(measure, [(w, v) for w in ("gap1", "gap2") for v in VOLS])
    by = {(r["world"], r["vol"]): r for r in res}
    names = {"gap1": "gap 1: smooth random walk (contract rules only)",
             "gap2": "gap 2: the teacher's world (jumps + earnings, tE 45 d)"}

    for w in ("gap1", "gap2"):
        print(f"== 1. right after a fixing, {names[w]} ==")
        print("vol   | clean x 0.60..1.00: min   max  mean|e| | knocked in x 0.20..0.995: min   max  mean|e| | "
              "teacher at par  formula at par")
        for v in VOLS:
            a, p = by[(w, v)]["after"], by[(w, v)]["par"]
            print(f"{v:4.0%}  | {a[0]:25.1f} {a[1]:5.1f} {a[2]:8.1f} | {a[3]:29.1f} {a[4]:5.1f} {a[5]:8.1f} | "
                  f"{p[0]:14.1f} {p[1]:15.1f}")
        print()

    for w in ("gap1", "gap2"):
        print(f"== 2. mid-week, x 0.20..1.30, max |error| (at x) / mean |error|, {names[w]} ==")
        print("vol   state      | " + " | ".join(f"tau {n:>5s}" + " " * 9 for n, _ in TAUS))
        for v in VOLS:
            for fi, fname in ((0, "clean     "), (1, "knocked in")):
                cells = [f"{m[fi][0]:6.1f} ({m[fi][1]:.2f}) {m[fi][2]:4.1f}" for m in by[(w, v)]["mid"]]
                print(f"{v:4.0%}  {fname} | " + " | ".join(cells))
        print()

    print("== 3. jumps at a fixing (tau = 0), teacher's world: value(barrier) - value(1 bps of spot below) ==")
    print("vol   | knock-in at x = k (clean) | heal at x = 1 (knocked in) | clean at x = 1 (kink only)")
    for v in VOLS:
        j = by[("gap2", v)]["jumps"]
        print(f"{v:4.0%}  | {j[0]:25.1f} | {j[1]:26.1f} | {j[2]:10.2f}")
    print()

    for w in ("gap1", "gap2"):
        print(f"== 4. slope just outside a band of tau, bps per 10 bps of spot: teacher / residual, {names[w]} ==")
        print("vol   | " + " | ".join(f"{'tau > ' + n:^29s}" for n, _ in BANDS))
        print("      | " + " | ".join("clean near k   ki near 1    " for _ in BANDS))
        for v in VOLS:
            cells = [f"{s[0][0]:5.1f}/{s[0][1]:5.1f}   {s[1][0]:5.1f}/{s[1][1]:5.1f} " for s in by[(w, v)]["slopes"]]
            print(f"{v:4.0%}  | " + " | ".join(cells))
        print()

    print("== 5. earnings just before vs just after the next fixing: value(n = 0) - value(n = 1) ==")
    print("vol   | " + " | ".join(f"tau {n:>5s}: worst (x, state)" for n, _ in TAUS[3:]))
    for v in VOLS:
        cells = [f"{d:+7.1f} ({x:.3f}, {'ki' if f else 'clean'})".ljust(28) for d, x, f in by[("gap2", v)]["step"]]
        print(f"{v:4.0%}  | " + " | ".join(cells))
    print("\nthe same at tau = 3.5 d, by spot:")
    print("vol   state      | x " + "  ".join(f"{x:5.2f}" for x in (0.30, 0.50, 0.58, 0.62, 0.70, 0.80, 0.90, 1.00, 1.10, 1.30)))
    for v in VOLS:
        for fi, fname in ((0, "clean     "), (1, "knocked in")):
            print(f"{v:4.0%}  {fname} |   " + "  ".join(f"{d:+5.1f}" for d in by[("gap2", v)]["profile"][fi]))
    print()

    print("== 6. price range over n at tau = 3.5 d: largest (x, state) ==")
    print("vol   | n = 0 .. 18                        | n = 1 .. 18 (release after the next fixing)")
    for v in VOLS:
        (d, x, f), (d2, x2, f2) = by[("gap2", v)]["nRange"]
        print(f"{v:4.0%}  | {d:6.1f} bps at x {x:.3f}, {'knocked in' if f else 'clean':10s} | "
              f"{d2:6.1f} bps at x {x2:.3f}, {'knocked in' if f2 else 'clean'}")


if __name__ == "__main__":
    main()
