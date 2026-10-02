"""Earnings-aware jump calibration for the perpetual-note teacher (docs/v2-spec.md section 6).

    python ml/calibrate_earnings.py > ml/calibrate_earnings.log

Model per trading day, as ml/calibrate_jumps.py (Merton: diffusion + Poisson
jumps), plus one extra normal move on each earnings reaction day:

    ordinary day   r = m + sigma_d e + sum_{k<=N} Y_k
    reaction day   r = (the same) + Y_E,   Y_E ~ N(0, sigmaE^2)

Steps:
  1. reaction days from ml/data/tsla_earnings.json (SEC EDGAR 8-K item 2.02,
     ml/data/fetch_tsla_earnings.py): 40 releases in the 10-year window, all
     after the close, so each moves the next session;
  2. Merton MLE on the other days (calibrate_jumps.fit, same likelihood);
  3. sigmaE by maximum likelihood on the reaction-day returns with the ordinary-day
     parameters held fixed (the Merton density with sigma_d^2 + sigmaE^2), and
     by moments as a cross-check;
  4. calendar-year clock as in calibrate_jumps (D = returns per calendar year);
     earnings recur every 91 days in the teacher, so their variance per year is
     sigmaE^2 * 365 / 91.

Writes ml/perp_earnings_fit.json and ml/teacher_perp_config.json (constants
rounded to 6 significant digits, as ml/make_teacher_config.py does). In the
config the sizes are those at volRef, the fit's total vol; the teacher scales
muJ, sigmaJ and sigmaE with vol / volRef, so each component keeps its share
of the variance and the input vol stays the TOTAL annualized vol.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os

import numpy as np
from scipy import optimize, stats

import calibrate_jumps as cj

HERE = os.path.dirname(os.path.abspath(__file__))
YEAR_SECS = 31_536_000
WEEK_SECS = 604_800
CYCLE_SECS = 91 * 86_400           # 13 fixings


def sig6(x: float) -> float:
    return float(f"{x:.6g}")


def earnings_nll(sig_e: float, r_e: np.ndarray, p: np.ndarray) -> float:
    m, sd, lam, muJ, sJ = p
    return -cj.merton_logpdf(r_e, m, math.sqrt(sd**2 + sig_e**2), lam, muJ, sJ).sum()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.join(HERE, "data", "tsla_daily.csv"))
    ap.add_argument("--earnings", default=os.path.join(HERE, "data", "tsla_earnings.json"))
    ap.add_argument("--start", default="2016-09-28")
    ap.add_argument("--end", default="2026-09-28")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    dates, close, r = cj.load(args.csv, args.start, args.end)
    rdates = dates[1:]
    d0, d1 = dt.date.fromisoformat(dates[0]), dt.date.fromisoformat(dates[-1])
    years = (d1 - d0).days / 365.0
    D = len(r) / years
    ej = json.load(open(args.earnings))
    reaction = sorted(x["reactionDay"] for x in ej["releases"] if x["kind"] == "earnings")
    is_e = np.isin(rdates, reaction)
    assert int(is_e.sum()) == len(reaction), "a reaction day is not a trading day in the window"
    r_o, r_e = r[~is_e], r[is_e]

    print(f"data: {args.csv}  window {dates[0]} .. {dates[-1]}  returns {len(r)}  returns/yr D = {D:.3f}")
    print(f"earnings: {args.earnings}  ({ej['source']}; fetched {ej['fetchedAtUtc']})")
    print(f"reaction days: {len(r_e)}  ({reaction[0]} .. {reaction[-1]}), days between releases "
          f"{ej['daysBetweenEarnings']}")
    print(f"ordinary days: {len(r_o)}  std {r_o.std(ddof=1):.6f}/day  excess kurtosis {stats.kurtosis(r_o):.3f}")
    print(f"reaction days: mean {r_e.mean():+.5f}  std {r_e.std(ddof=1):.6f}  rms {np.sqrt(np.mean(r_e**2)):.6f}  "
          f"mean |r| {np.abs(r_e).mean():.5f}  min {r_e.min():+.4f}  max {r_e.max():+.4f}")
    print(f"  variance ratio reaction / ordinary: {r_e.var(ddof=1) / r_o.var(ddof=1):.2f}")
    t = r_e.mean() / (r_e.std(ddof=1) / math.sqrt(len(r_e)))
    print(f"  mean reaction return: t = {t:+.2f} (not different from 0; the teacher's earnings jump is driftless)")
    lev = stats.levene(r_e, r_o)
    print(f"  Levene test, equal variances: W = {lev.statistic:.1f}, p = {lev.pvalue:.2e}")

    # 2. Merton on ordinary days
    p, nll, nstarts = cj.fit(r_o)
    m, sd, lam, muJ, sJ = p
    h = np.maximum(np.abs(p) * 1e-4, 1e-7)
    cov = np.linalg.inv(cj.hessian(lambda x: cj.nll_nat(x, r_o), p, h))
    se = np.sqrt(np.diag(cov))
    print(f"\nMerton MLE on ordinary days ({nstarts} starts): -logL {nll:.4f}")
    names = ["m (drift/day)", "sigma_d (/sqrt day)", "lambda (jumps/trading day)", "muJ", "sigmaJ"]
    old = json.load(open(os.path.join(HERE, "jump_fit.json")))["trading_day"]
    old_v = [old["m"], old["sigma_d"], old["lambda"], old["muJ"], old["sigmaJ"]]
    print(f"{'param':28s} {'estimate':>12s} {'stderr':>12s} {'all days (v3)':>14s}")
    for nm, v, e, o in zip(names, p, se, old_v):
        print(f"{nm:28s} {v:12.6f} {e:12.6f} {o:14.6f}")

    # 3. sigmaE
    res = optimize.minimize_scalar(lambda s: earnings_nll(s, r_e, p), bounds=(1e-4, 0.5), method="bounded",
                                   options={"xatol": 1e-10})
    sig_e = float(res.x)
    hh = 1e-4
    d2 = (earnings_nll(sig_e + hh, r_e, p) - 2 * res.fun + earnings_nll(sig_e - hh, r_e, p)) / hh**2
    se_e = 1.0 / math.sqrt(d2)
    var_ord = sd**2 + lam * (muJ**2 + sJ**2)
    mom = math.sqrt(max(np.mean((r_e - m - lam * muJ) ** 2) - var_ord, 0.0))
    lr = 2 * (earnings_nll(1e-9, r_e, p) - res.fun)
    print(f"\nsigmaE (std of the extra log move on a reaction day): MLE {sig_e:.6f} +- {se_e:.6f}   "
          f"moments {mom:.6f}")
    print(f"  likelihood ratio vs no earnings jump: {lr:.1f} (1 parameter)")

    # 4. calendar-year clock
    lam_year = sig6(D * lam)
    mu_j, sig_j, sig_e6 = sig6(muJ), sig6(sJ), sig6(sig_e)
    dv = D * sd**2
    jv = lam_year * (mu_j**2 + sig_j**2)
    ev = sig_e6**2 * YEAR_SECS / CYCLE_SECS
    vol_ref = sig6(math.sqrt(dv + jv + ev))
    jump_share, earn_share = jv / vol_ref**2, ev / vol_ref**2
    print(f"\ncalendar year: lambda {lam_year}/yr  diffusion var {dv:.6f}  jump var {jv:.6f}  "
          f"earnings var {ev:.6f} ({YEAR_SECS / CYCLE_SECS:.4f} releases/yr)")
    print(f"  total vol (volRef) {vol_ref}   shares: diffusion {1 - jump_share - earn_share:.4f}  "
          f"jumps {jump_share:.4f}  earnings {earn_share:.4f}")
    print(f"  v3 (all days, no earnings): volRef {json.load(open(os.path.join(HERE, 'teacher_config_v3.json')))['jumps']['volRef']}"
          f"  jump share {json.load(open(os.path.join(HERE, 'teacher_config_v3.json')))['jumps']['derived_jumpShare']}")
    print(f"  sample: sqrt(D) * std of all returns = {math.sqrt(D) * r.std(ddof=1):.4f}")

    fit = {
        "csv": "ml/data/tsla_daily.csv", "earnings": "ml/data/tsla_earnings.json",
        "window": [str(dates[0]), str(dates[-1])], "returns": int(len(r)), "returnsPerYear": D,
        "reactionDays": len(reaction),
        "ordinary_day": {"m": m, "sigma_d": sd, "lambda": lam, "muJ": muJ, "sigmaJ": sJ,
                         "stderr": dict(zip(["m", "sigma_d", "lambda", "muJ", "sigmaJ"], map(float, se)))},
        "reaction_day": {"sigmaE": sig_e, "stderr": se_e, "sigmaE_moments": mom, "meanReturn": float(r_e.mean()),
                         "std": float(r_e.std(ddof=1)), "likelihoodRatio": lr},
        "calendar_year": {"lambdaYear": D * lam, "diffusionVarYear": dv, "jumpVarYear": jv, "earningsVarYear": ev,
                          "totalVolYear": math.sqrt(dv + jv + ev)},
    }
    cfg = {
        "name": f"merton-earnings-tsla-{dates[0][:4]}-{dates[-1][:4]}",
        "teacherVersion": "perp-1",
        "rFree": 0.04,
        "rDiscount": 0.0,
        "yearSecs": YEAR_SECS,
        "weekSecs": WEEK_SECS,
        "clock": "calendar time, 365-day year, uniform diffusion and jump intensity",
        "jumps": {
            "lambdaYear": lam_year,
            "muJ": mu_j,
            "sigmaJ": sig_j,
            "volRef": vol_ref,
            "derived_jumpShare": sig6(jump_share),
        },
        "earnings": {
            "sigmaE": sig_e6,
            "cycleSecs": CYCLE_SECS,
            "derived_earningsShare": sig6(earn_share),
        },
        "derived_diffusionShare": sig6(1 - jump_share - earn_share),
        "volConvention": "volBpsAnnual is TOTAL annualized vol; muJ, sigmaJ and sigmaE are the sizes at volRef and "
                         "scale with vol / volRef, so jump variance = derived_jumpShare * vol^2 per year, earnings "
                         "variance = derived_earningsShare * vol^2 per year (one release every cycleSecs), and "
                         "diffusion variance = the rest",
        "riskNeutral": "drift rFree - lambdaYear*kappa(vol) between releases, each earnings move is "
                       "N(-sigmaE(vol)^2/2, sigmaE(vol)^2) in log price; payouts discounted at rDiscount (the "
                       "escrow earns nothing); Q parameters = fitted P parameters",
        "calibration": {
            "script": "ml/calibrate_earnings.py", "log": "ml/calibrate_earnings.log",
            "fit": "ml/perp_earnings_fit.json", "data": "ml/data/tsla_daily.csv",
            "earnings": "ml/data/tsla_earnings.json", "earningsSource": ej["urls"],
            "window": [str(dates[0]), str(dates[-1])], "returns": int(len(r)), "returnsPerYear": D,
        },
    }
    if not args.no_write:
        for name, obj in (("perp_earnings_fit.json", fit), ("teacher_perp_config.json", cfg)):
            with open(os.path.join(HERE, name), "w") as f:
                json.dump(obj, f, indent=2)
                f.write("\n")
        print("\nwrote ml/perp_earnings_fit.json and ml/teacher_perp_config.json")


if __name__ == "__main__":
    main()
