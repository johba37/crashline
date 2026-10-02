"""Squared-move note: the numbers behind docs/squared-move-note.md (an idea, nothing built).

The v2 frame (docs/v2-spec.md) with another rule per fixing. With f the fixing
and f0 the one before:

    d2 = (f - f0)^2 / (f * f0)            = g + 1/g - 2 = 2 cosh(ln g) - 2,  g = f / f0
    q  = min(1, d2 / M)                   M = m^2, m = the capped move
    WRITER gets a * q, NOTE gets a * (1 - q + R)  per unit of live notional,
    then the live notional shrinks by (1 - a).

No reference, no flag: the only state is the last fixing. Weeks don't depend on
each other, so every expectation is one week's, and one week's log return is a
Poisson mixture of normals (ml/teacher_perp.mixture: the P1 teacher's world),
for which E[q] is a closed form in the normal cdf. Payouts are discounted at 0,
as for P1.

    python ml/squared_note_check.py > ml/squared_note_check.log     (numpy, scipy; ~20 s)
"""

from __future__ import annotations

import csv
import datetime as dt
import math
import os

import numpy as np
from scipy.special import ndtr

import perp_formula as pf
import teacher_perp as tp

HERE = os.path.dirname(os.path.abspath(__file__))
DT = tp.P1.dt                    # one week in years (7 / 365)
A1 = tp.P1.a                     # P1's melt share
CYCLE = 13                       # fixings per earnings cycle
VOLS = (0.20, 0.30, 0.40, 0.55, 0.70, 0.90)
CAPS = (0.15, 0.20, 0.30, 0.50)
SETS = (("A", A1, 0.20), ("B", 0.05, 0.30))   # (name, share released per fixing, capped move)


# ---------------------------------------------------------------------------
# one week's expectation
# ---------------------------------------------------------------------------

def d2(g):
    return g + 1.0 / g - 2.0


def q_of(g, M):
    return np.minimum(1.0, d2(np.asarray(g, dtype=np.float64)) / M)


def eq_normal(m, s, M):
    """E[min(1, (2 cosh X - 2) / M)] for X ~ N(m, s^2); M = None: E[2 cosh X - 2]."""
    if M is None:
        return np.exp(m + 0.5 * s * s) + np.exp(-m + 0.5 * s * s) - 2.0
    L = math.acosh(1.0 + 0.5 * M)
    hi, lo = (L - m) / s, (-L - m) / s
    p_in = ndtr(hi) - ndtr(lo)
    e_up = np.exp(m + 0.5 * s * s) * (ndtr(hi - s) - ndtr(lo - s))
    e_dn = np.exp(-m + 0.5 * s * s) * (ndtr(hi + s) - ndtr(lo + s))
    return (e_up + e_dn - 2.0 * p_in) / M + 1.0 - p_in


def expect_q(cfg, vol, M, step=DT, earnings=False, y=1.0):
    """E[q at the next fixing] with `step` years to go, the stock at y * f0 now and an
    earnings release still to come before the fixing or not."""
    if step <= 0.0:
        return float(q_of(y, M)) if M is not None else float(d2(y))
    w, m, sd = tp.mixture(cfg, vol, DT, step, earnings)
    return float((w[0] * eq_normal(m[0] + math.log(y), sd[0], M)).sum())


def formula_q(cfg, vol, M, step=DT, earnings=False, y=1.0):
    """The same expectation with ONE normal that has the step's variance (jumps and the
    earnings move included) and mean r * step - variance / 2: six normal cdfs, no series."""
    if step <= 0.0:
        return float(q_of(y, M))
    sd_d, lam, mu, sj, _, se = cfg.components(vol, DT)
    var = (sd_d * sd_d + lam * (mu * mu + sj * sj)) * step + (se * se if earnings else 0.0)
    return float(eq_normal(cfg.r * step - 0.5 * var + math.log(y), math.sqrt(var), M))


def qbar(cfg, vol, M, n=None, a=A1, week=None):
    """Expected q per unit of live notional right after a fixing: sum of a (1 - a)^j E[q_j].
    n = fixings before the earnings week (0: the coming week); None: no earnings timing
    (the cycle average)."""
    week = week or expect_q
    qn = week(cfg, vol, M)
    if cfg.vol_ref <= 0.0:
        return qn
    qe = week(cfg, vol, M, earnings=True)
    if n is None:
        return qn + (qe - qn) / CYCLE
    return qn + (qe - qn) * a * (1.0 - a) ** n / (1.0 - (1.0 - a) ** CYCLE)


def sample_week(cfg, vol, rng, size, earnings=False, step=DT):
    """Log return over `step`, simulated from its parts (not from the mixture)."""
    sd_d, lam, mu, sj, kappa, se = cfg.components(vol, DT)
    n = rng.poisson(lam * step, size) if lam > 0 else np.zeros(size)
    x = (cfg.r - lam * kappa - 0.5 * sd_d * sd_d) * step + sd_d * math.sqrt(step) * rng.standard_normal(size)
    x = x + n * mu + sj * np.sqrt(n) * rng.standard_normal(size)
    if earnings:
        x = x - 0.5 * se * se + se * rng.standard_normal(size)
    return x


def pct(x):
    return f"{100.0 * x:6.2f}"


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------

def worked_week(cfg):
    print("== (0) one fixing, per 1,000 USDG of live notional (R fair at vol 55%, cycle average) ==")
    for name, a, m in SETS:
        M = m * m
        R = qbar(cfg, 0.55, M)
        half = math.log(2) / -math.log1p(-a)
        print(f"  set {name}: a {a:.4f} (half of the notional is back after {half:.1f} weeks), cap {m:.2f}, R {R:.4f}: "
              f"slice {1000 * a:.2f}, coupon {1000 * a * R:.2f}")
        print("    move      " + "".join(f"{100 * mv:+8.0f}%" for mv in (0, 0.05, -0.05, 0.10, -0.10, 0.20, -0.20, 0.35, -0.35)))
        qs = [float(q_of(1 + mv, M)) for mv in (0, 0.05, -0.05, 0.10, -0.10, 0.20, -0.20, 0.35, -0.35)]
        print("    q         " + "".join(f"{q:9.4f}" for q in qs))
        print("    WRITER    " + "".join(f"{1000 * a * q:9.2f}" for q in qs))
        print("    NOTE      " + "".join(f"{1000 * a * (1 - q + R):9.2f}" for q in qs))


def check(cfg):
    print("\n== (1) closed form vs simulation (teacher's world, 2^22 weeks each) ==")
    rng = np.random.default_rng(20261002)
    worst = 0.0
    for vol, m, earn, step, y in ((0.55, 0.20, False, DT, 1.0), (0.55, 0.20, True, DT, 1.0),
                                  (0.90, 0.15, True, DT, 1.0), (0.20, 0.30, False, DT, 1.0),
                                  (0.55, 0.20, False, DT * 2 / 7, 0.88), (0.55, 0.20, True, DT / 7, 1.10)):
        M = m * m
        x = sample_week(cfg, vol, rng, 1 << 22, earn, step) + math.log(y)
        s = q_of(np.exp(x), M)
        cf = expect_q(cfg, vol, M, step, earn, y)
        z = (s.mean() - cf) / (s.std() / math.sqrt(s.size))
        worst = max(worst, abs(z))
        print(f"  vol {vol:.2f} cap {m:.2f} earnings {int(earn)} days {step / DT * 7:3.1f} move so far {y - 1:+.2f}: "
              f"closed form {cf:.6f}  simulated {s.mean():.6f}  z {z:+.2f}")
    print(f"  max |z| {worst:.2f}")
    for vol in (0.20, 0.55, 0.90):
        w, m, sd = tp.mixture(cfg, vol, DT, DT, False)
        mart = float((w[0] * np.exp(m[0] + 0.5 * sd[0] ** 2)).sum()) - math.exp(cfg.r * DT)
        assert abs(mart) < 1e-12, mart
    print("  E[f / f0] = e^(r dt) to 1e-12")


def variance_table(cfg):
    print("\n== (2) one week's expected squared move, uncapped, in units of vol^2 * dt ==")
    print("  vol   GBM     teacher normal week   earnings week   cycle average")
    for vol in VOLS:
        base = vol * vol * DT
        g = expect_q(tp.GBM, vol, None)
        qn, qe = expect_q(cfg, vol, None), expect_q(cfg, vol, None, earnings=True)
        print(f"  {vol:.2f}  {g / base:6.4f}  {qn / base:6.4f}                {qe / base:6.4f}          "
              f"{(qn + (qe - qn) / CYCLE) / base:6.4f}")


def coupon_table(cfg):
    phi = tp.P1.phi
    print(f"\n== (3) fair coupon, %/yr = phi * R, R = expected q (phi {phi:.6f}/yr, a {A1:.6f}) ==")
    print("  teacher's world, n = 0..12 fixings before the earnings week: lowest .. highest;  [GBM at the same vol]")
    print("  share cut = part of the expected squared move the cap removes (cycle average)")
    for m in CAPS:
        M = m * m
        up, dn = math.exp(math.acosh(1 + M / 2)) - 1, 1 - math.exp(-math.acosh(1 + M / 2))
        print(f"  cap m {m:.2f} (M {M:.4f}: a week of +{100 * up:.1f}% or -{100 * dn:.1f}% pays the whole slice)")
        for vol in VOLS:
            vals = [qbar(cfg, vol, M, n) for n in range(CYCLE)]
            cut = 1.0 - qbar(cfg, vol, M) * M / qbar(cfg, vol, None)
            print(f"    vol {vol:.2f}: {pct(phi * min(vals))} .. {pct(phi * max(vals))}   "
                  f"[GBM {pct(phi * qbar(tp.GBM, vol, M))}]   normal week q {expect_q(cfg, vol, M):.4f}, "
                  f"earnings week {expect_q(cfg, vol, M, earnings=True):.4f}; share cut {pct(cut)}%")
    print("  knock-in note P1 (docs/p1-perp-student.md): 1.36 / 5.83 / 11.59 / 20.22 / 27.99 / 36.93 at these vols")
    print("  the two sets, cash coupon per year for a position kept at a constant size = a * R * 365 / 7 (cycle average R):")
    for name, a, m in SETS:
        print(f"    set {name} (a {a:.4f}, cap {m:.2f}): "
              + "  ".join(f"{100 * v:.0f}%: R {qbar(cfg, v, m * m):.4f} -> {pct(a * qbar(cfg, v, m * m) / DT)}" for v in VOLS))


def dials(cfg):
    print("\n== (4) the two dials: share at stake per week (a) and the cap (m); teacher's world, vol 55% ==")
    print("  a       cap m   fair R    coupon per year at a constant position (a R / dt)   share cut   "
          "one week's stake a*R, bps of notional")
    for a, m in ((A1, 0.15), (A1, 0.20), (A1, 0.30), (0.05, 0.30), (0.10, 0.45), (0.25, 0.70), (0.50, 1.00), (1.0, 1.40)):
        M = m * m
        vals = [qbar(cfg, 0.55, M, n, a=a) for n in range(CYCLE)] if a < 1 else [expect_q(cfg, 0.55, M)]
        R = qbar(cfg, 0.55, M)
        cut = 1.0 - R * M / qbar(cfg, 0.55, None)
        print(f"  {a:6.4f}  {m:.2f}    {R:.4f}    {pct(a * R / DT)}%  (R over the cycle {min(vals):.4f}..{max(vals):.4f})"
              f"          {pct(cut)}%     {1e4 * a * R:7.1f}")


def midweek(cfg):
    print("\n== (5) NOTE principal mid-week, bps of live notional (teacher's world, vol 55%, no earnings in the week) ==")
    vol = 0.55
    moves = (-0.40, -0.30, -0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20, 0.30, 0.40)
    for name, a, m in SETS:
        M = m * m
        rest = qbar(cfg, vol, M, 5, a=a)
        print(f"  set {name} (a {a:.4f}, cap {m:.2f}): right after a fixing {1e4 * (1 - qbar(cfg, vol, M, 6, a=a)):.1f}; "
              f"the notional not released at the next fixing is worth {1e4 * (1 - rest):.1f} per unit")
        print("    move so far " + "".join(f"{100 * mv:+7.0f}%" for mv in moves))
        lo, hi = 1e9, -1e9
        for days in (5.0, 3.5, 1.0, 0.25, 0.0):
            row = []
            for mv in moves:
                v = 1e4 * (a * (1 - expect_q(cfg, vol, M, DT * days / 7, False, 1 + mv)) + (1 - a) * (1 - rest))
                lo, hi = min(lo, v), max(hi, v)
                row.append(f"{v:8.1f}")
            print(f"    {days:4.2f} d to go" + "".join(row))
        print(f"    range over the table {hi - lo:.1f} bps; the bound is a = {1e4 * a:.1f} bps")


def vol_reading(cfg):
    print("\n== (6) what vol does to the price, and reading vol from the price (right after a fixing, P1's a) ==")
    print("  spread = P(vol - 2 points) - P(vol + 2 points), bps of notional (P1: mean 121, max 296)")
    for m in (0.20, 0.30, 0.50):
        M = m * m
        print(f"  cap m {m:.2f}")
        for vol in VOLS:
            spread = 1e4 * (qbar(cfg, vol + 0.02, M) - qbar(cfg, vol - 0.02, M))
            target = qbar(cfg, vol, M)
            naive = math.sqrt(target * M / DT)          # no cap, no model: q = vol^2 dt / M
            lo_v, hi_v = 0.01, 3.0
            for _ in range(60):                          # capped GBM, inverted
                mid = 0.5 * (lo_v + hi_v)
                lo_v, hi_v = (mid, hi_v) if qbar(tp.GBM, mid, M) < target else (lo_v, mid)
            print(f"    vol {vol:.2f}: spread {spread:6.1f}   vol read back: no cap, no model {100 * naive:5.1f}%   "
                  f"capped smooth walk {100 * lo_v:5.1f}%")


def model_gap(cfg):
    print("\n== (7) what a model would have to add: NOTE principal, teacher - formula, bps of live notional ==")
    print("  formula = one normal per week with that week's variance (formula_q); the gap is the shape of the tail")
    print("  right after a fixing, largest over n = 0..12; in brackets teacher - smooth walk without earnings timing")
    print("  vol        " + "          ".join(f"{100 * v:3.0f}%" for v in VOLS))
    for m in CAPS:
        M = m * m
        row = []
        for vol in VOLS:
            gap = max((qbar(cfg, vol, M, n, week=formula_q) - qbar(cfg, vol, M, n) for n in range(CYCLE)), key=abs)
            plain = max((qbar(tp.GBM, vol, M) - qbar(cfg, vol, M, n) for n in range(CYCLE)), key=abs)
            row.append(f"{1e4 * gap:+6.1f} [{1e4 * plain:+6.1f}]")
        print(f"  cap m {m:.2f}: " + "  ".join(row))
    print("  mid-week, the slice released next: a * max |teacher - formula| over a move so far of -40%..+40%,")
    print("  6 hours..7 days to go, with and without an earnings release ahead in the week")
    ys = np.linspace(0.60, 1.40, 81)
    for a, m in ((A1, 0.20), (A1, 0.30), (0.05, 0.30)):
        M = m * m
        row = []
        for vol in VOLS:
            worst = 0.0
            for days in (0.25, 0.5, 1, 2, 3.5, 5, 7):
                for earn in (False, True):
                    for y in ys:
                        step = DT * days / 7
                        worst = max(worst, abs(expect_q(cfg, vol, M, step, earn, y) - formula_q(cfg, vol, M, step, earn, y)))
            row.append(f"{1e4 * a * worst:5.1f}")
        print(f"  a {a:.4f} cap m {m:.2f}: " + "  ".join(row))


def one_year(cfg):
    print("\n== (8) NOTE over one year, per 1,000 USDG of notional, no reinvestment (teacher's world, vol 55%, fair R) ==")
    vol, paths, weeks = 0.55, 200_000, 52
    for name, a, m in SETS:
        M = m * m
        R = qbar(cfg, vol, M)
        rng = np.random.default_rng(7)
        wts = a * (1 - a) ** np.arange(weeks)
        pnl = np.zeros(paths)
        for wk in range(weeks):
            x = sample_week(cfg, vol, rng, paths, earnings=(wk % CYCLE == 6))
            pnl += 1000.0 * wts[wk] * (R - q_of(np.exp(x), M))
        print(f"  set {name} (a {a:.4f}, cap {m:.2f}, R {R:.4f}): principal released if nothing is lost {1000 * wts.sum():.1f}, "
              f"coupon {1000 * wts.sum() * R:.1f}, left in the note {1000 * (1 - a) ** weeks:.1f}")
        print(f"    profit against holding USDG: mean {pnl.mean():+.1f}, std {pnl.std():.1f}; percentiles "
              + ", ".join(f"{p}%: {np.percentile(pnl, p):+.1f}" for p in (1, 5, 25, 50, 75, 95, 99)))
        print(f"    worst of {paths} years {pnl.min():+.1f}; the floor (every week at the cap) {1000 * wts.sum() * (R - 1):+.1f}; "
              f"the most one week can cost {1000 * a * (R - 1):+.1f}")


def history(cfg):
    print("\n== (9) TSLA 2011-2026, weekly (last close of each week, ml/data/tsla_daily.csv) ==")
    rows = []
    with open(os.path.join(HERE, "data", "tsla_daily.csv")) as f:
        for r in csv.DictReader(f):
            rows.append((dt.date.fromisoformat(r["date"]), float(r["close"])))
    last = {}
    for d, c in rows:
        last[d.isocalendar()[:2]] = (d, c)
    wk = sorted(last.values())
    dates = [d for d, _ in wk[1:]]
    g = np.array([wk[i + 1][1] / wk[i][1] for i in range(len(wk) - 1)])
    x = np.log(g)
    phi = tp.P1.phi
    R_ref = qbar(cfg, cfg.vol_ref, 0.04)
    print(f"  NOTE profit: per 1,000 USDG of notional at the start of the year, cap 0.20, R {R_ref:.4f} "
          f"(fair at vol {100 * cfg.vol_ref:.1f}%), no reinvestment")
    print("  year  weeks  vol %   break-even coupon %/yr at cap 0.15 / 0.20 / 0.30   model at that vol (0.20)   "
          "weeks at the cap (0.20)   largest week   NOTE profit")
    years = sorted({d.year for d in dates})
    be = []
    for yr in years:
        idx = np.array([d.year == yr for d in dates])
        if idx.sum() < 30:
            continue
        gy, xy = g[idx], x[idx]
        vol = math.sqrt((xy ** 2).mean() / DT)
        qs = [q_of(gy, m * m) for m in (0.15, 0.20, 0.30)]
        wts = A1 * (1 - A1) ** np.arange(idx.sum())
        pnl = 1000.0 * float((wts * (R_ref - qs[1])).sum())
        be.append(phi * qs[1].mean())
        big = xy[np.argmax(np.abs(xy))]
        print(f"  {yr}  {idx.sum():3d}   {100 * vol:5.1f}   {pct(phi * qs[0].mean())} / {pct(phi * qs[1].mean())} / "
              f"{pct(phi * qs[2].mean())}                       {pct(phi * qbar(cfg, vol, 0.04))}"
              f"                 {int((qs[1] >= 1).sum()):2d}                 {100 * (math.exp(big) - 1):+6.1f}%      {pnl:+7.1f}")
    print(f"  break-even coupon (cap 0.20) over these years: min {100 * min(be):.1f}, median {100 * float(np.median(be)):.1f}, "
          f"max {100 * max(be):.1f} %/yr")
    w0, w1 = dt.date(2016, 9, 28), dt.date(2026, 9, 28)
    idx = np.array([w0 < d <= w1 for d in dates])
    vol = math.sqrt((x[idx] ** 2).mean() / DT)
    print(f"  the teacher's fit window {w0} .. {w1}: {idx.sum()} weeks, weekly vol {100 * vol:.1f}% "
          f"(the daily fit: {100 * cfg.vol_ref:.1f}%)")
    for m in CAPS:
        real = phi * q_of(g[idx], m * m).mean()
        se = phi * q_of(g[idx], m * m).std() / math.sqrt(idx.sum())
        print(f"    cap {m:.2f}: realized break-even coupon {pct(real)} +- {pct(se)} %/yr;  teacher at vol "
              f"{100 * vol:.1f}%: {pct(phi * qbar(cfg, vol, m * m))};  smooth walk: {pct(phi * qbar(tp.GBM, vol, m * m))}")
    ac = np.corrcoef(x[idx][1:] ** 2, x[idx][:-1] ** 2)[0, 1]
    yr_v = [math.sqrt((x[np.array([d.year == y for d in dates])] ** 2).mean() / DT) for y in years[1:-1]]
    print(f"    squared weekly moves, correlation with the week before: {ac:.2f};  vol by calendar year: "
          f"{100 * min(yr_v):.0f}% .. {100 * max(yr_v):.0f}%")


def side_by_side(cfg):
    print("\n== (11) side by side on TSLA's weekly closes: cash per 1,000 USDG of notional at the start of each year ==")
    print("  no reinvestment; both coupons fair at vol 59.0% in their own model; cash only: what is still in the note")
    print("  at the year's end is not marked (a knocked-in note carries its loss into the next year)")
    rows = []
    with open(os.path.join(HERE, "data", "tsla_daily.csv")) as f:
        for r in csv.DictReader(f):
            rows.append((dt.date.fromisoformat(r["date"]), float(r["close"])))
    last = {}
    for d, c in rows:
        last[d.isocalendar()[:2]] = (d, c)
    wk = sorted(last.values())
    k = tp.P1.k
    R_ki = pf.fair_reserve(cfg.vol_ref, cfg.r, cfg.r_disc, tp.P1.phi, k, DT)
    caps = tuple((a, m) for _, a, m in SETS)
    R_sq = [qbar(cfg, cfg.vol_ref, m * m, a=a) for a, m in caps]
    print(f"  knock-in note: k {k}, a {A1:.4f}, R {R_ki:.4f} (closed form);  squared-move notes: "
          + ";  ".join(f"a {a:.4f}, cap {m:.2f}, R {R:.4f}" for (a, m), R in zip(caps, R_sq)))
    H, ki = wk[0][1], False
    out = {}
    for i in range(1, len(wk)):
        d, f = wk[i]
        f0 = wk[i - 1][1]
        if f >= H:
            H, ki = f, False
        elif f < k * H:
            ki = True
        p = f / H if ki else 1.0
        out.setdefault(d.year, []).append((p, ki, [float(q_of(f / f0, m * m)) for _, m in caps]))
    print("  squared-move notes: profit without reinvestment, worst week, and profit of a position topped up to 1,000")
    print("  of notional every week at par (the note has no state, so right after a fixing it is at par at the fair vol)")
    print("  year   knock-in note: profit, weeks knocked in, lowest fixing vs reference   "
          + "   ".join(f"squared a {a:.3f} cap {m:.2f}: profit, worst week, constant position" for a, m in caps))
    tot = [0.0] * (1 + 2 * len(caps))
    for yr in sorted(out):
        w = out[yr]
        if len(w) < 30:
            continue
        n = np.arange(len(w))
        p = np.array([x[0] for x in w])
        pnl_ki = 1000.0 * float((A1 * (1 - A1) ** n * (R_ki + p - 1.0)).sum())
        cells = []
        for j, (a, m) in enumerate(caps):
            q = np.array([x[2][j] for x in w])
            per = 1000.0 * a * (1 - a) ** n * (R_sq[j] - q)
            const = 1000.0 * a * float((R_sq[j] - q).sum())
            tot[1 + 2 * j] += float(per.sum())
            tot[2 + 2 * j] += const
            cells.append(f"{per.sum():+8.1f}, {per.min():+6.1f}, {const:+8.1f}")
        tot[0] += pnl_ki
        print(f"  {yr}   {pnl_ki:+8.1f}, {sum(x[1] for x in w):2d}, {100 * p.min():5.1f}%"
              f"                                        " + "          ".join(cells))
    print(f"  sum over the years: knock-in {tot[0]:+.1f}; squared " + "; ".join(
        f"a {a:.3f} cap {m:.2f}: {tot[1 + 2 * j]:+.1f} (constant position {tot[2 + 2 * j]:+.1f})" for j, (a, m) in enumerate(caps)))
    ki_weeks = sum(x[1] for w in out.values() for x in w)
    print(f"  knocked in for {ki_weeks} of {sum(len(w) for w in out.values())} weeks; at the end of the data: "
          f"{'knocked in' if ki else 'clean'}, fixing at {100 * wk[-1][1] / H:.1f}% of the reference")


def level_squared():
    print("\n== (10) the other reading: the slice pays (f / H)^2 with H fixed (Squeeth's index), smooth walk, no cap ==")
    print("  value = x^2 * a g / (1 - (1 - a) g), g = e^((2 r + vol^2) dt); it is finite only while 2 r + vol^2 < phi")
    r = tp.GBM.r
    for vol in VOLS:
        gg = math.exp((2 * r + vol * vol) * DT)
        den = 1 - (1 - A1) * gg
        print(f"  vol {vol:.2f}: value / x^2 = " + (f"{A1 * gg / den:7.3f}" if den > 0 else "infinite"))
    print(f"  infinite from vol {100 * math.sqrt(tp.P1.phi - 2 * r):.1f}% (phi {tp.P1.phi:.4f}, r {r})")


def other_rules(cfg):
    print("\n== (12) two other rules without state, one week (teacher's world, average over the earnings cycle) ==")
    print("  put-write: q = max(0, 1 - f / f0), NOTE loses the week's fall;  range: q = 1 if the week's move is beyond +-10%")
    for vol in VOLS:
        vals = []
        for earn in (False, True):
            w, m, sd = (x[0] for x in tp.mixture(cfg, vol, DT, DT, earn))
            put = float((w * (ndtr(-m / sd) - np.exp(m + 0.5 * sd * sd) * ndtr(-m / sd - sd))).sum())
            inside = float((w * (ndtr((math.log(1.1) - m) / sd) - ndtr((math.log(0.9) - m) / sd))).sum())
            vals.append((put, 1.0 - inside))
        put = vals[0][0] + (vals[1][0] - vals[0][0]) / CYCLE
        out = vals[0][1] + (vals[1][1] - vals[0][1]) / CYCLE
        print(f"  vol {vol:.2f}: put-write E[q] {100 * put:.2f}% of the slice; coupon per year at a constant size: "
              + ", ".join(f"a {a:.3f}: {100 * a * put / DT:.1f}%" for a in (A1, 0.05, 0.25))
              + f";  P(move beyond 10%) {100 * out:.1f}%")


def main() -> None:
    cfg = tp.load_config()
    print(f"# squared-move note check; teacher world {cfg.name}, drift {cfg.r}, discount {cfg.r_disc}, dt 7/365")
    worked_week(cfg)
    check(cfg)
    variance_table(cfg)
    coupon_table(cfg)
    dials(cfg)
    midweek(cfg)
    vol_reading(cfg)
    model_gap(cfg)
    one_year(cfg)
    history(cfg)
    level_squared()
    side_by_side(cfg)
    other_rules(cfg)


if __name__ == "__main__":
    main()
