"""Fit Merton jump-diffusion to TSLA daily log returns by maximum likelihood.

    python ml/calibrate_jumps.py [--csv ml/data/tsla_daily.csv] [--start 2016-09-28]

Model, per trading day (trading-day clock: every return is one step, weekends
and holidays are not time):

    r = m + sigma_d * e + sum_{k=1..N} Y_k,   N ~ Poisson(lam),  Y_k ~ N(muJ, sigJ^2)

so the density is a Poisson mixture of normals,
    f(r) = sum_n Pois(n; lam) * phi(r; m + n muJ, sigma_d^2 + n sigJ^2),
truncated at n <= 30 (the Poisson tail beyond it is < 1e-30 for lam < 0.5/day).

Standard errors: inverse of the numerical Hessian of the negative
log-likelihood at the optimum (observed information), delta method for the
derived quantities.

Clock conversion (the teacher's clock is calendar time, 365-day year, uniform
diffusion; see docs/teacher-v2.md): D = returns per calendar year in the
window. Annual jump rate lam_year = D * lam; muJ and sigJ are per jump and
need no conversion; annual diffusion variance = D * sigma_d^2. This keeps the
per-year variance of each component equal on both clocks.

Writes ml/jump_fit.json (read by ml/make_teacher_config.py); print output is
the log (committed as ml/calibrate_jumps.log).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os

import numpy as np
from scipy import optimize, special, stats

HERE = os.path.dirname(os.path.abspath(__file__))
NMAX = 30
QUANTILES = (0.001, 0.01, 0.99, 0.999)


def load(csv: str, start: str | None, end: str | None):
    dates, close = [], []
    with open(csv) as f:
        next(f)
        for line in f:
            d, c, _ = line.strip().split(",")
            dates.append(d)
            close.append(float(c))
    dates = np.array(dates)
    close = np.array(close)
    keep = np.ones(len(dates), dtype=bool)
    if start:
        keep &= dates >= start
    if end:
        keep &= dates <= end
    dates, close = dates[keep], close[keep]
    r = np.diff(np.log(close))
    return dates, close, r


def unpack(th):
    """Unconstrained -> (m, sd, lam, muJ, sJ)."""
    m, log_sd, logit_lam, muJ, log_sJ = th
    return m, np.exp(log_sd), special.expit(logit_lam), muJ, np.exp(log_sJ)


def merton_logpdf(r, m, sd, lam, muJ, sJ):
    n = np.arange(NMAX + 1)[:, None]
    logw = stats.poisson.logpmf(n, lam)
    var = sd**2 + n * sJ**2
    lp = logw - 0.5 * np.log(2 * np.pi * var) - 0.5 * (r[None, :] - m - n * muJ) ** 2 / var
    return special.logsumexp(lp, axis=0)


def nll_nat(p, r):
    m, sd, lam, muJ, sJ = p
    if sd <= 0 or sJ <= 0 or lam <= 0:
        return np.inf
    return -merton_logpdf(r, m, sd, lam, muJ, sJ).sum()


def hessian(f, x, h):
    k = len(x)
    H = np.zeros((k, k))
    for i in range(k):
        for j in range(i, k):
            ei = np.zeros(k); ei[i] = h[i]
            ej = np.zeros(k); ej[j] = h[j]
            v = (f(x + ei + ej) - f(x + ei - ej) - f(x - ei + ej) + f(x - ei - ej)) / (4 * h[i] * h[j])
            H[i, j] = H[j, i] = v
    return H


def mixture_cdf(x, m, sd, lam, muJ, sJ):
    n = np.arange(NMAX + 1)
    w = stats.poisson.pmf(n, lam)
    return np.sum(w * stats.norm.cdf((x - m - n * muJ) / np.sqrt(sd**2 + n * sJ**2)))


def mixture_quantile(q, p):
    return optimize.brentq(lambda x: mixture_cdf(x, *p) - q, -5.0, 5.0, xtol=1e-12)


def mixture_moments(m, sd, lam, muJ, sJ):
    """Mean, variance, excess kurtosis of the Poisson-normal mixture (closed form
    via cumulants: k1 = m + lam muJ, k2 = sd^2 + lam(muJ^2+sJ^2),
    k4 = lam(muJ^4 + 6 muJ^2 sJ^2 + 3 sJ^4))."""
    k1 = m + lam * muJ
    k2 = sd**2 + lam * (muJ**2 + sJ**2)
    k3 = lam * (muJ**3 + 3 * muJ * sJ**2)
    k4 = lam * (muJ**4 + 6 * muJ**2 * sJ**2 + 3 * sJ**4)
    return k1, k2, k3 / k2**1.5, k4 / k2**2


def fit(r):
    s = r.std()
    starts = []
    for lam0 in (0.02, 0.1, 0.3):
        for sJ0 in (2 * s, 4 * s):
            for muJ0 in (-0.01, 0.01):
                starts.append(np.array([r.mean(), np.log(0.7 * s), special.logit(lam0), muJ0, np.log(sJ0)]))
    best = None
    for th0 in starts:
        res = optimize.minimize(lambda th: nll_nat(unpack(th), r), th0, method="Nelder-Mead",
                                options={"xatol": 1e-6, "fatol": 1e-6, "maxiter": 3000, "maxfev": 3000})
        res = optimize.minimize(lambda th: nll_nat(unpack(th), r), res.x, method="BFGS",
                                options={"gtol": 1e-8})
        if best is None or res.fun < best.fun - 1e-9:
            best = res
    return np.array(unpack(best.x)), best.fun, len(starts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.join(HERE, "data", "tsla_daily.csv"))
    ap.add_argument("--start", default="2016-09-28", help="first close in the window (inclusive)")
    ap.add_argument("--end", default="2026-09-28", help="last close in the window (inclusive)")
    ap.add_argument("--out", default=os.path.join(HERE, "jump_fit.json"))
    ap.add_argument("--sensitivity", action="store_true",
                    help="also fit the full history and the last 5 years (reported, not pinned)")
    args = ap.parse_args()

    dates, close, r = load(args.csv, args.start, args.end)
    d0, d1 = dt.date.fromisoformat(dates[0]), dt.date.fromisoformat(dates[-1])
    years = (d1 - d0).days / 365.0
    D = len(r) / years
    print(f"data: {args.csv}")
    print(f"window: {dates[0]} .. {dates[-1]}  closes {len(close)}  returns {len(r)}  "
          f"calendar years {years:.4f}  returns/yr D = {D:.3f}")
    print(f"sample: mean {r.mean():.6e}/day  std {r.std(ddof=1):.6f}/day  "
          f"ann. vol (sqrt(D)*std) {np.sqrt(D) * r.std(ddof=1):.4f}  "
          f"skew {stats.skew(r):.4f}  excess kurtosis {stats.kurtosis(r):.4f}")
    print(f"largest moves: min {r.min():+.4f} on {dates[1:][r.argmin()]}  max {r.max():+.4f} on {dates[1:][r.argmax()]}")
    xcsv = os.path.join(os.path.dirname(args.csv), "tsla_daily_nasdaq.csv")
    if os.path.exists(xcsv):
        nd = np.loadtxt(xcsv, delimiter=",", skiprows=1, usecols=1)
        ndates = np.loadtxt(xcsv, delimiter=",", skiprows=1, usecols=0, dtype=str)
        same_dates = len(ndates) == len(dates) and bool(np.all(ndates == dates))
        if same_dates:
            dr = np.abs(np.diff(np.log(nd)) - r)
            print(f"cross-check vs Nasdaq ({os.path.basename(xcsv)}): same {len(dates)} dates; "
                  f"|log-return diff| max {dr.max():.2e}, returns differing by > 1e-3: {int(np.sum(dr > 1e-3))}")
        else:
            common = np.intersect1d(ndates, dates)
            print(f"cross-check vs Nasdaq: dates differ ({len(ndates)} vs {len(dates)}, {len(common)} common)")

    p, nll, nstarts = fit(r)
    m, sd, lam, muJ, sJ = p
    # observed information in natural parameters
    h = np.maximum(np.abs(p) * 1e-4, 1e-7)
    H = hessian(lambda x: nll_nat(x, r), p, h)
    cov = np.linalg.inv(H)
    se = np.sqrt(np.diag(cov))

    # Gaussian (GBM) benchmark: normal MLE
    g_m, g_s = r.mean(), r.std(ddof=0)
    g_nll = -stats.norm.logpdf(r, g_m, g_s).sum()
    lr = 2 * (g_nll - nll)

    print(f"\nMerton MLE ({nstarts} starts, NMAX {NMAX}): -logL {nll:.4f}   GBM -logL {g_nll:.4f}   "
          f"LR stat {lr:.1f} (3 extra params)")
    names = ["m (drift/day)", "sigma_d (/sqrt day)", "lambda (jumps/trading day)", "muJ (mean log jump)",
             "sigmaJ (std log jump)"]
    print(f"{'param':32s} {'estimate':>12s} {'stderr':>12s}")
    for nm, v, e in zip(names, p, se):
        print(f"{nm:32s} {v:12.6f} {e:12.6f}")
    print("correlations:")
    corr = cov / np.outer(se, se)
    for i, nm in enumerate(names):
        print(f"  {nm:30s} " + " ".join(f"{c:+.2f}" for c in corr[i]))

    # derived, calendar-year clock (delta method)
    def derived(x):
        m_, sd_, lam_, muJ_, sJ_ = x
        jv = D * lam_ * (muJ_**2 + sJ_**2)
        dv = D * sd_**2
        kappa = np.exp(muJ_ + 0.5 * sJ_**2) - 1
        return np.array([D * lam_, jv, dv, np.sqrt(jv + dv), kappa, jv / (jv + dv)])

    dvals = derived(p)
    Jd = np.zeros((len(dvals), 5))
    for i in range(5):
        e = np.zeros(5); e[i] = h[i]
        Jd[:, i] = (derived(p + e) - derived(p - e)) / (2 * h[i])
    dse = np.sqrt(np.diag(Jd @ cov @ Jd.T))
    dnames = ["lambda_year (jumps/yr)", "jump variance /yr", "diffusion variance /yr",
              "total vol /yr (model)", "kappa = E[e^Y]-1", "jump share of variance"]
    print(f"\ncalendar-year quantities (D = {D:.3f} returns/yr):")
    for nm, v, e in zip(dnames, dvals, dse):
        print(f"{nm:32s} {v:12.6f} {e:12.6f}")
    jv = dvals[1]
    print(f"jump vol /yr sqrt(lambda_year (muJ^2+sigJ^2)) = {np.sqrt(jv):.4f}")
    for tot in (0.55,):
        rem = tot**2 - jv
        print(f"at total vol {tot:.2f}: diffusion variance = {tot**2:.6f} - {jv:.6f} = {rem:.6f} "
              f"-> sigma_d {np.sqrt(rem) if rem > 0 else float('nan'):.4f} ({'OK > 0' if rem > 0 else 'NOT > 0'})")

    # goodness of fit
    mm, mv, msk, mku = mixture_moments(*p)
    print("\ngoodness of fit (daily log returns):")
    print(f"{'statistic':14s} {'data':>10s} {'Merton':>10s} {'GBM':>10s}")
    print(f"{'std':14s} {r.std(ddof=1):10.5f} {np.sqrt(mv):10.5f} {g_s:10.5f}")
    print(f"{'skew':14s} {stats.skew(r):10.4f} {msk:10.4f} {0.0:10.4f}")
    print(f"{'ex. kurtosis':14s} {stats.kurtosis(r):10.4f} {mku:10.4f} {0.0:10.4f}")
    gof = {"std": [float(r.std(ddof=1)), float(np.sqrt(mv)), float(g_s)],
           "skew": [float(stats.skew(r)), float(msk), 0.0],
           "excessKurtosis": [float(stats.kurtosis(r)), float(mku), 0.0], "quantiles": {}}
    for q in QUANTILES:
        dq = np.quantile(r, q)
        mq = mixture_quantile(q, p)
        gq = stats.norm.ppf(q, g_m, g_s)
        print(f"{'q ' + format(q * 100, 'g') + '%':14s} {dq:10.5f} {mq:10.5f} {gq:10.5f}")
        gof["quantiles"][str(q)] = [float(dq), float(mq), float(gq)]
    # tail counts: how many returns beyond k sample-std, data vs expected
    print("\ntail counts |r - mean| > k*std (data / Merton expected / GBM expected):")
    tails = {}
    for k in (3, 4, 5, 6):
        thr = k * r.std(ddof=1)
        nd = int(np.sum(np.abs(r - r.mean()) > thr))
        pm = 1 - mixture_cdf(r.mean() + thr, *p) + mixture_cdf(r.mean() - thr, *p)
        pg = 2 * stats.norm.sf(thr / g_s)
        print(f"  k={k}: {nd:5d} / {pm * len(r):8.1f} / {pg * len(r):8.2f}")
        tails[str(k)] = [nd, float(pm * len(r)), float(pg * len(r))]

    out = {
        "source": json.load(open(os.path.join(os.path.dirname(args.csv), "tsla_daily.source.json")))["primary"]["url"],
        "csv": os.path.relpath(args.csv, os.path.dirname(HERE)),
        "window": [str(dates[0]), str(dates[-1])], "returns": int(len(r)),
        "returnsPerYear": float(D),
        "trading_day": {"m": m, "sigma_d": sd, "lambda": lam, "muJ": muJ, "sigmaJ": sJ,
                        "stderr": dict(zip(["m", "sigma_d", "lambda", "muJ", "sigmaJ"], se.tolist()))},
        "calendar_year": dict(zip(["lambdaYear", "jumpVarYear", "diffusionVarYear", "totalVolYear",
                                   "kappa", "jumpShare"], dvals.tolist())),
        "calendar_year_stderr": dict(zip(["lambdaYear", "jumpVarYear", "diffusionVarYear", "totalVolYear",
                                          "kappa", "jumpShare"], dse.tolist())),
        "negLogLik": {"merton": float(nll), "gbm": float(g_nll)},
        "goodnessOfFit": gof, "tailCounts": tails,
    }
    out = json.loads(json.dumps(out, default=float))
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    print(f"\nwrote {os.path.relpath(args.out, os.path.dirname(HERE))}")

    if args.sensitivity:
        print("\nsensitivity (other windows; reported, not pinned):")
        print(f"{'window':26s} {'n':>5s} {'D':>7s} {'lam_yr':>8s} {'muJ':>8s} {'sigJ':>7s} "
              f"{'sd_yr':>7s} {'jumpVol':>8s} {'totVol':>7s}")
        for lo, hi in ((None, None), ("2021-09-28", "2026-09-28"), (args.start, args.end)):
            dd, _, rr = load(args.csv, lo, hi)
            yy = (dt.date.fromisoformat(dd[-1]) - dt.date.fromisoformat(dd[0])).days / 365.0
            DD = len(rr) / yy
            pp, _, _ = fit(rr)
            _, sd_, lam_, muJ_, sJ_ = pp
            jv_ = DD * lam_ * (muJ_**2 + sJ_**2)
            print(f"{dd[0] + '..' + dd[-1]:26s} {len(rr):5d} {DD:7.2f} {DD * lam_:8.3f} {muJ_:+8.4f} {sJ_:7.4f} "
                  f"{np.sqrt(DD) * sd_:7.4f} {np.sqrt(jv_):8.4f} {np.sqrt(jv_ + DD * sd_**2):7.4f}")


if __name__ == "__main__":
    main()
