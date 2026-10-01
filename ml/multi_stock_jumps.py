"""Merton jump fit across a spread of stocks: is TSLA's jump share typical?

    python ml/multi_stock_jumps.py [--refetch] > ml/multi_stock_jumps.log

Question (docs/teacher-v2.md, "Listed options" item 2): would a fixed jump
share of total variance work for other stocks, so one teacher (and one
student per product) covers many underlyings? Or does every stock need its own
jump constants?

Same model, likelihood and optimizer as ml/calibrate_jumps.py (imported, not
copied): daily log returns, trading-day clock, Poisson mixture of normals,
12 starts, observed-information stderrs, delta method for derived quantities.
TSLA is read from the committed ml/data/tsla_daily.csv, so its 10-year row must
reproduce ml/jump_fit.json.

Data: Yahoo Finance chart API v8 through ml/data/fetch_tsla.py, stored in
ml/data/multi/. The fit uses `adjclose` (split- and dividend-adjusted, i.e.
total return; the feed series is total-return-ish, teacher-spec A1). For TSLA
adjclose == close. Cross-check: Nasdaq historical closes (split-adjusted, not
dividend-adjusted) against Yahoo `close` on common dates.

Windows (fixed before fitting): 10 years 2016-09-28..2026-09-28 (the pinned
TSLA window; PLTR and COIN listed later and start at their first close), and
the common 5 years 2021-09-28..2026-09-28, which every symbol covers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "data"))
import calibrate_jumps as C  # noqa: E402

DATA = os.path.join(HERE, "data", "multi")
END = dt.date(2026, 9, 29)       # exclusive, as the TSLA fetch
WINDOWS = {"10y": ("2016-09-28", "2026-09-28"), "5y": ("2021-09-28", "2026-09-28")}
# symbol -> Nasdaq asset class
SYMBOLS = {"TSLA": "stocks", "AAPL": "stocks", "NVDA": "stocks", "MSFT": "stocks",
           "AMZN": "stocks", "META": "stocks", "GOOGL": "stocks", "AMD": "stocks",
           "NFLX": "stocks", "COIN": "stocks", "PLTR": "stocks", "MSTR": "stocks",
           "SPY": "etf"}
TSLA_PINNED_JUMP_VOL = 0.447126   # ml/teacher_config.json derived_jumpVolYear


def csv_path(sym):
    if sym == "TSLA":
        return os.path.join(HERE, "data", "tsla_daily.csv")
    return os.path.join(DATA, f"{sym.lower()}_daily.csv")


def nasdaq_path(sym):
    if sym == "TSLA":
        return os.path.join(HERE, "data", "tsla_daily_nasdaq.csv")
    return os.path.join(DATA, f"{sym.lower()}_daily_nasdaq.csv")


def fetch_all(refetch):
    import fetch_tsla as F
    os.makedirs(DATA, exist_ok=True)
    src_path = os.path.join(DATA, "sources.json")
    sources = json.load(open(src_path)) if os.path.exists(src_path) else {}
    for sym, aclass in SYMBOLS.items():
        if sym == "TSLA" or (not refetch and os.path.exists(csv_path(sym))):
            continue
        fetched_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        y_url, y_rows, splits = F.fetch_yahoo(END, sym, "split,div")
        with open(csv_path(sym), "w") as f:
            f.write("date,close,adjclose\n")
            for d, c, a in y_rows:
                f.write(f"{d},{c:.6f},{a:.6f}\n")
        n_url, n_rows = F.fetch_nasdaq(END, sym, aclass)
        with open(nasdaq_path(sym), "w") as f:
            f.write("date,close\n")
            for d, c in n_rows:
                f.write(f"{d},{c:.4f}\n")
        sources[sym] = {
            "fetchedAtUtc": fetched_at, "endExclusiveUtc": END.isoformat(),
            "yahoo": {"url": y_url, "rows": len(y_rows), "first": y_rows[0][0], "last": y_rows[-1][0],
                      "splits": [{"date": dt.datetime.fromtimestamp(s["date"], dt.timezone.utc).date().isoformat(),
                                  "ratio": f'{s["numerator"]}:{s["denominator"]}'} for s in splits]},
            "nasdaq": {"url": n_url, "rows": len(n_rows), "first": n_rows[0][0], "last": n_rows[-1][0]},
        }
        print(f"# fetched {sym}: yahoo {len(y_rows)} rows {y_rows[0][0]}..{y_rows[-1][0]}, "
              f"nasdaq {len(n_rows)} rows", file=sys.stderr)
    with open(src_path, "w") as f:
        json.dump(sources, f, indent=2, sort_keys=True)
        f.write("\n")


def load(sym):
    rows = [line.strip().split(",") for line in open(csv_path(sym)).readlines()[1:]]
    dates = np.array([r[0] for r in rows])
    close = np.array([float(r[1]) for r in rows])
    adj = np.array([float(r[2]) for r in rows])
    return dates, close, adj


def cross_check(sym, dates, close, lo, hi):
    """Max |log-return diff| Yahoo close vs Nasdaq close over common consecutive dates."""
    nd = np.loadtxt(nasdaq_path(sym), delimiter=",", skiprows=1, usecols=0, dtype=str)
    nc = np.loadtxt(nasdaq_path(sym), delimiter=",", skiprows=1, usecols=1)
    common, iy, inq = np.intersect1d(dates, nd, return_indices=True)
    keep = (common >= lo) & (common <= hi)
    iy, inq = iy[keep], inq[keep]
    ry, rn = np.diff(np.log(close[iy])), np.diff(np.log(nc[inq]))
    d = np.abs(ry - rn)
    return int(keep.sum()), float(d.max()), int(np.sum(d > 1e-3))


def derived(x, D):
    m_, sd_, lam_, muJ_, sJ_ = x
    jv = D * lam_ * (muJ_**2 + sJ_**2)
    dv = D * sd_**2
    return np.array([D * lam_, np.sqrt(jv), np.sqrt(dv), np.sqrt(jv + dv), jv / (jv + dv),
                     sJ_ / np.sqrt((jv + dv) / D)])


DNAMES = ["lambdaYear", "jumpVol", "diffusionVol", "totalVol", "jumpShare", "sigmaJ_over_dailyVol"]


def fit_one(job):
    sym, wname = job
    lo, hi = WINDOWS[wname]
    dates, close, adj = load(sym)
    keep = (dates >= lo) & (dates <= hi)
    dates, close, adj = dates[keep], close[keep], adj[keep]
    r = np.diff(np.log(adj))
    years = (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[0])).days / 365.0
    D = len(r) / years
    p, nll, _ = C.fit(r)
    h = np.maximum(np.abs(p) * 1e-4, 1e-7)
    cov = np.linalg.inv(C.hessian(lambda x: C.nll_nat(x, r), p, h))
    se = np.sqrt(np.diag(cov))
    dv = derived(p, D)
    J = np.zeros((len(dv), 5))
    for i in range(5):
        e = np.zeros(5); e[i] = h[i]
        J[:, i] = (derived(p + e, D) - derived(p - e, D)) / (2 * h[i])
    dse = np.sqrt(np.diag(J @ cov @ J.T))
    g_nll = -stats.norm.logpdf(r, r.mean(), r.std()).sum()
    thr = 4 * r.std(ddof=1)
    tail4 = int(np.sum(np.abs(r - r.mean()) > thr))
    pm4 = (1 - C.mixture_cdf(r.mean() + thr, *p) + C.mixture_cdf(r.mean() - thr, *p)) * len(r)
    n_xc, xc_max, xc_n = cross_check(sym, dates, close, lo, hi)
    # volatility clustering: i.i.d. daily jumps predict weekly excess kurtosis = daily / 5
    # and no autocorrelation of |r|; the data show whether busy days bunch together
    n5 = len(r) // 5 * 5
    wk = r[:n5].reshape(-1, 5).sum(1)
    ar = np.abs(r - r.mean()); ar -= ar.mean()
    acf = lambda lags: float(np.mean([ar[:-k] @ ar[k:] / (ar @ ar) for k in lags]))
    return {
        "symbol": sym, "window": wname, "first": str(dates[0]), "last": str(dates[-1]),
        "returns": int(len(r)), "returnsPerYear": float(D),
        "sample": {"annVol": float(np.sqrt(D) * r.std(ddof=1)), "skew": float(stats.skew(r)),
                   "excessKurtosis": float(stats.kurtosis(r)), "min": float(r.min()), "max": float(r.max())},
        "trading_day": dict(zip(["m", "sigma_d", "lambda", "muJ", "sigmaJ"], p.tolist())),
        "trading_day_stderr": dict(zip(["m", "sigma_d", "lambda", "muJ", "sigmaJ"], se.tolist())),
        "derived": dict(zip(DNAMES, dv.tolist())), "derived_stderr": dict(zip(DNAMES, dse.tolist())),
        "lrStatVsGBM": float(2 * (g_nll - nll)),
        "tail4": [tail4, float(pm4)],
        "clustering": {"weeklyExcessKurtosis": float(stats.kurtosis(wk)),
                       "weeklyExcessKurtosisMerton": float(C.mixture_moments(*p)[3] / 5),
                       "acfAbsLag1to5": acf(range(1, 6)), "acfAbsLag20to60": acf(range(20, 61))},
        "crossCheck": {"commonDates": n_xc, "maxAbsLogRetDiff": xc_max, "over1e-3": xc_n},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    fetch_all(args.refetch)

    jobs = [(s, w) for w in WINDOWS for s in SYMBOLS]
    with ProcessPoolExecutor(args.workers) as ex:
        res = list(ex.map(fit_one, jobs))

    pinned = json.load(open(os.path.join(HERE, "jump_fit.json")))
    t10 = next(x for x in res if x["symbol"] == "TSLA" and x["window"] == "10y")
    print(f"# reproduce pinned TSLA fit: lambdaYear {t10['derived']['lambdaYear']:.4f} vs "
          f"{pinned['calendar_year']['lambdaYear']:.4f}, jumpShare {t10['derived']['jumpShare']:.6f} vs "
          f"{pinned['calendar_year']['jumpShare']:.6f}")

    for w, (lo, hi) in WINDOWS.items():
        print(f"\n## window {w}: {lo}..{hi} (adjclose log returns, trading-day clock, annualized per calendar year)")
        print("| symbol | first | returns | xcheck max / >1e-3 | sample vol | ex. kurt | λ/yr | σJ | "
              "σJ / daily vol | jump vol | diffusion vol | jump share | LR vs GBM | >4σ data / Merton | "
              "weekly ex. kurt data / Merton | acf \\|r\\| lags 1–5 / 20–60 |")
        print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for x in (x for x in res if x["window"] == w):
            d, e = x["derived"], x["derived_stderr"]
            td, tse = x["trading_day"], x["trading_day_stderr"]
            xc = x["crossCheck"]
            print(f"| {x['symbol']} | {x['first']} | {x['returns']} | {xc['maxAbsLogRetDiff']:.1e} / {xc['over1e-3']} | "
                  f"{x['sample']['annVol']:.1%} | {x['sample']['excessKurtosis']:.1f} | "
                  f"{d['lambdaYear']:.0f} ± {e['lambdaYear']:.0f} | {td['sigmaJ']:.2%} ± {tse['sigmaJ']:.2%} | "
                  f"{d['sigmaJ_over_dailyVol']:.2f} ± {e['sigmaJ_over_dailyVol']:.2f} | "
                  f"{d['jumpVol']:.1%} | {d['diffusionVol']:.1%} | "
                  f"{d['jumpShare']:.0%} ± {e['jumpShare']:.0%} | {x['lrStatVsGBM']:.0f} | "
                  f"{x['tail4'][0]} / {x['tail4'][1]:.1f} | "
                  f"{x['clustering']['weeklyExcessKurtosis']:.2f} / {x['clustering']['weeklyExcessKurtosisMerton']:.2f} | "
                  f"{x['clustering']['acfAbsLag1to5']:.3f} / {x['clustering']['acfAbsLag20to60']:.3f} |")
        sh = np.array([x["derived"]["jumpShare"] for x in res if x["window"] == w])
        she = np.array([x["derived_stderr"]["jumpShare"] for x in res if x["window"] == w])
        wm = np.sum(sh / she**2) / np.sum(1 / she**2)
        chi2 = float(np.sum(((sh - wm) / she) ** 2))
        print(f"# jump share: min {sh.min():.1%}  median {np.median(sh):.1%}  max {sh.max():.1%}; "
              f"inverse-variance mean {wm:.1%}; chi2 vs one common share {chi2:.1f} on {len(sh) - 1} dof "
              f"(p {stats.chi2.sf(chi2, len(sh) - 1):.2g})")
        cl = [x["clustering"] for x in res if x["window"] == w]
        print(f"# weekly excess kurtosis above the i.i.d.-jump prediction: "
              f"{sum(c['weeklyExcessKurtosis'] > c['weeklyExcessKurtosisMerton'] for c in cl)} of {len(cl)}; "
              f"acf |r| lags 20–60 > 0: {sum(c['acfAbsLag20to60'] > 0 for c in cl)} of {len(cl)}")
        refused = [x["symbol"] for x in res if x["window"] == w
                   and x["sample"]["annVol"] <= TSLA_PINNED_JUMP_VOL]
        print(f"# sample vol at or below TSLA's pinned jump vol {TSLA_PINNED_JUMP_VOL:.1%} "
              f"(refused by today's teacher at their own vol): {', '.join(refused) or 'none'}")

    with open(os.path.join(HERE, "multi_stock_jumps.json"), "w") as f:
        json.dump({"windows": WINDOWS, "fits": res}, f, indent=2)
        f.write("\n")
    print("\nwrote ml/multi_stock_jumps.json")


if __name__ == "__main__":
    main()
