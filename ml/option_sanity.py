"""Sanity check (not a calibration): listed TSLA option prices vs the teacher's
55% total vol and its fitted jumps.

    python ml/option_sanity.py --fetch    # snapshot -> ml/data/tsla_options_<date>.json
    python ml/option_sanity.py            # IVs from the committed snapshot -> log

Source: Nasdaq's public option-chain API, one expiry (2027-03-19, ~24 weeks,
close to the note's 26-week life). Implied vols from OTM mid quotes (puts
below spot, calls above), Black-Scholes with r = 4% (the teacher's), no
dividends (TSLA pays none), T in 365-day years from 16:00 New York on the
quote date to 16:00 on expiry (American puts priced as European: the early
exercise premium is small for OTM strikes at 4% but not zero; stated, not
corrected). The model column is the Black-Scholes implied vol of the jump
teacher's European price (Merton series, pinned jumps, 55% total vol).
"""

import argparse
import datetime as dt
import json
import math
import os
import sys
import urllib.request

import numpy as np
from scipy import optimize

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import teacher as T  # noqa: E402
from test_teacher import bs_put, merton_put  # noqa: E402

EXPIRY = "2027-03-19"
URL = ("https://api.nasdaq.com/api/quote/TSLA/option-chain?assetclass=stocks&limit=500"
       f"&fromdate={EXPIRY}&todate={EXPIRY}&money=all&type=all")


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        js = json.load(r)
    js["_fetch"] = {"url": URL, "fetchedAtUtc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()}
    last = js["data"]["lastTrade"]          # "LAST TRADE: $352.84 (AS OF SEP 29, 2026)"
    asof = dt.datetime.strptime(last.split("AS OF ")[1].rstrip(")"), "%b %d, %Y").date()
    path = os.path.join(HERE, "data", f"tsla_options_{asof.strftime('%Y%m%d')}.json")
    with open(path, "w") as f:
        json.dump(js, f, indent=1)
        f.write("\n")
    print(f"wrote {os.path.relpath(path, os.path.dirname(HERE))}")


def num(x):
    try:
        return float(x.replace(",", ""))
    except (AttributeError, ValueError):
        return None


def bs_iv(price, S, K, t, r, put):
    f = lambda s: (bs_put(S, K, r, s, t) + (0 if put else S - K * math.exp(-r * t))) - price
    try:
        return optimize.brentq(f, 0.01, 5.0, xtol=1e-10)
    except ValueError:
        return float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--snapshot", default=None)
    args = ap.parse_args()
    if args.fetch:
        fetch()
        return
    snap = args.snapshot or sorted(f for f in os.listdir(os.path.join(HERE, "data")) if f.startswith("tsla_options_"))[-1]
    js = json.load(open(os.path.join(HERE, "data", os.path.basename(snap))))
    last = js["data"]["lastTrade"]
    S = float(last.split("$")[1].split()[0])
    asof = dt.datetime.strptime(last.split("AS OF ")[1].rstrip(")"), "%b %d, %Y").date()
    t = (dt.date.fromisoformat(EXPIRY) - asof).days / 365.0
    cfg = T.load_config()
    r = cfg.r
    print(f"snapshot {snap}: {js['_fetch']['url']} fetched {js['_fetch']['fetchedAtUtc']}")
    print(f"spot {S} ({last}); expiry {EXPIRY}; t = {t:.4f} y; r = {r}")
    print("| strike | K/S | side | bid | ask | mid IV | model IV (jump teacher, 55% total) |")
    print("|---:|---:|---|---:|---:|---:|---:|")
    rows = []
    for row in js["data"]["table"]["rows"]:
        K = num(row.get("strike"))
        if K is None:
            continue
        m = K / S
        if not 0.55 <= m <= 1.25:
            continue
        put = K <= S
        bid, ask = num(row["p_Bid" if put else "c_Bid"]), num(row["p_Ask" if put else "c_Ask"])
        if bid is None or ask is None or bid <= 0 or ask <= bid:
            continue
        iv = bs_iv(0.5 * (bid + ask), S, K, t, r, put)
        mp = merton_put(1.0, m, t, 0.55, cfg)
        miv = bs_iv(mp, 1.0, m, t, r, True)
        rows.append((m, iv, miv))
        print(f"| {K:.0f} | {m:.3f} | {'put' if put else 'call'} | {bid:.2f} | {ask:.2f} | {iv:.4f} | {miv:.4f} |")
    rows = np.array(rows)
    for lo, hi in ((0.55, 0.7), (0.7, 0.9), (0.95, 1.05), (1.05, 1.25)):
        sel = (rows[:, 0] >= lo) & (rows[:, 0] <= hi)
        if sel.any():
            print(f"# K/S {lo:.2f}-{hi:.2f}: market IV mean {np.nanmean(rows[sel, 1]):.4f} "
                  f"(min {np.nanmin(rows[sel, 1]):.4f}, max {np.nanmax(rows[sel, 1]):.4f}); "
                  f"model {np.nanmean(rows[sel, 2]):.4f}")


if __name__ == "__main__":
    main()
