"""Listed option smiles and a Merton jump fit to them (teacher improvement 2, step 1).

    python ml/options_calibrate.py --fetch   # snapshot -> ml/data/options/<symbol>_<asof>.json
    python ml/options_calibrate.py           # smiles + fits from the latest snapshot -> log, json
    /opt/ai/cache/venv-cuda/bin/python ml/options_calibrate.py --prices >> ml/options_calibrate.log

History (ml/calibrate_jumps.py) says how a stock moved; option prices say what the market
charges for the same risk. This fits the teacher's jump model to the second.

Data: Nasdaq's public option-chain API (as ml/option_sanity.py), four expiries per symbol
(2026-11-20, 2026-12-18, 2027-03-19, 2027-06-17: about 7 weeks to 9 months, around the
note's 26-week life), the 13 symbols of ml/multi_stock_jumps.py. End-of-day bid/ask.

Per expiry:
  - forward F from put-call parity, F = K + e^{rT} (C - P), median over strikes within 10%
    of spot with both sides quoted (absorbs dividends and borrow; no yield is assumed);
  - implied vols from out-of-the-money mids (puts below F, calls above), Black-76 on F,
    r = 4%, T = calendar days / 365. Listed equity options are American; OTM, the early
    exercise premium is small and is ignored (stated, not corrected).
  - quotes kept: bid > 0, ask > bid, mid >= 0.05, (ask - bid) / mid <= 0.6, K/F in 0.45-1.6.

Fit: Merton jump-diffusion under Q on the forward (the teacher's model; closed form as a
Poisson sum of Black-76 prices), one parameter set per symbol across its expiries:
sigma_d, lambda (jumps/yr), muJ, sigmaJ. Least squares on vega-scaled price errors (about
IV errors), 8 starts. Reported next to the history fit (ml/multi_stock_jumps.json, 10 y)
and teacher v3 (TSLA's history shape scaled to the market's ATM vol).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import urllib.request

import numpy as np
from scipy import optimize, special

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = os.path.join(HERE, "data", "options")
R = 0.04
EXPIRIES = ("2026-11-20", "2026-12-18", "2027-03-19", "2027-06-17")
SYMBOLS = {"TSLA": "stocks", "AAPL": "stocks", "NVDA": "stocks", "MSFT": "stocks", "AMZN": "stocks",
           "META": "stocks", "GOOGL": "stocks", "AMD": "stocks", "NFLX": "stocks", "COIN": "stocks",
           "PLTR": "stocks", "MSTR": "stocks", "SPY": "etf"}
KEEP = ("strike", "c_Bid", "c_Ask", "c_Openinterest", "p_Bid", "p_Ask", "p_Openinterest")
GRID = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2)    # K/F where smiles are reported
# fit bounds: sigma_d, lambda (/yr), muJ, sigmaJ. Wide on purpose: the market prices rare
# large crashes, and a fit that still sits on a bound is flagged rather than trusted.
BOUNDS = ((0.01, 2.0), (0.01, 150.0), (-0.9, 0.3), (0.005, 1.5))
PARAMS = ("sigma_d", "lambdaYear", "muJ", "sigmaJ")


# ---------------------------------------------------------------- fetch

def _get(sym, aclass, expiry):
    url = (f"https://api.nasdaq.com/api/quote/{sym}/option-chain?assetclass={aclass}&limit=1000"
           f"&fromdate={expiry}&todate={expiry}&money=all&type=all")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return url, json.load(r)


def fetch():
    os.makedirs(DATA, exist_ok=True)
    for sym, aclass in SYMBOLS.items():
        snap = {"symbol": sym, "fetchedAtUtc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                "expiries": {}, "urls": []}
        for e in EXPIRIES:
            url, js = _get(sym, aclass, e)
            d = js.get("data") or {}
            rows = [{k: r.get(k) for k in KEEP} for r in ((d.get("table") or {}).get("rows") or []) if r.get("strike")]
            snap["urls"].append(url)
            if not rows:
                continue
            last = d["lastTrade"]                       # "LAST TRADE: $354.81 (AS OF SEP 30, 2026)"
            snap["spot"] = float(last.split("$")[1].split()[0].replace(",", ""))
            snap["asOf"] = dt.datetime.strptime(last.split("AS OF ")[1].rstrip(")"), "%b %d, %Y").date().isoformat()
            snap["expiries"][e] = rows
        path = os.path.join(DATA, f"{sym.lower()}_{snap['asOf'].replace('-', '')}.json")
        with open(path, "w") as f:
            json.dump(snap, f, indent=0)
            f.write("\n")
        print(f"{sym}: spot {snap['spot']} as of {snap['asOf']}, expiries "
              + ", ".join(f"{e} ({len(r)} strikes)" for e, r in snap["expiries"].items()), file=sys.stderr)


# ---------------------------------------------------------------- pricing

def black(F, K, T, sig, put):
    sq = sig * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sq**2) / sq
    d2 = d1 - sq
    disc = np.exp(-R * T)
    call = disc * (F * special.ndtr(d1) - K * special.ndtr(d2))
    return np.where(put, call - disc * (F - K), call)


def black_vega(F, K, T, sig):
    sq = sig * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sq**2) / sq
    return np.exp(-R * T) * F * np.sqrt(T) * np.exp(-0.5 * d1**2) / math.sqrt(2 * math.pi)


def implied_vol(price, F, K, T, put):
    out = np.full(len(price), np.nan)
    for i in range(len(price)):
        f = lambda s: float(black(F[i], K[i], T[i], s, put[i])) - price[i]
        try:
            out[i] = optimize.brentq(f, 1e-3, 6.0, xtol=1e-10)
        except ValueError:
            pass
    return out


def merton(F, K, T, put, sd, lam, mu, dj):
    """European option on S with forward F under Merton (Q): Poisson sum of Black-76 prices."""
    kap = math.expm1(mu + 0.5 * dj**2)
    lt = lam * T
    nmax = int(np.max(lt) + 10 * math.sqrt(np.max(lt) + 1) + 20)
    n = np.arange(nmax + 1)[:, None]
    w = np.exp(-lt + n * np.log(np.maximum(lt, 1e-300)) - special.gammaln(n + 1))
    Fn = F * np.exp(-lam * kap * T + n * (mu + 0.5 * dj**2))
    vn = np.sqrt(sd**2 + n * dj**2 / T)
    return np.sum(w * black(Fn, K, T, vn, put), axis=0)


# ---------------------------------------------------------------- smiles

def num(x):
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


def smile(rows, S, T):
    """(F, arrays K, mid, put, iv) for one expiry."""
    C, P = {}, {}
    for r in rows:
        K = num(r["strike"])
        for side, store in (("c", C), ("p", P)):
            b, a = num(r[f"{side}_Bid"]), num(r[f"{side}_Ask"])
            if K and b is not None and a is not None and b > 0 and a > b:
                store[K] = (b, a)
    par = [K + math.exp(R * T) * ((C[K][0] + C[K][1]) / 2 - (P[K][0] + P[K][1]) / 2)
           for K in C if K in P and abs(K / S - 1) <= 0.10]
    if len(par) < 3:
        return None
    F = float(np.median(par))
    Ks, mids, puts = [], [], []
    for K in sorted(set(C) | set(P)):
        put = K < F
        q = (P if put else C).get(K)
        if q is None:
            continue
        b, a = q
        mid = 0.5 * (b + a)
        if mid < 0.05 or (a - b) / mid > 0.6 or not 0.45 <= K / F <= 1.6:
            continue
        Ks.append(K); mids.append(mid); puts.append(put)
    K, mid, put = np.array(Ks), np.array(mids), np.array(puts)
    iv = implied_vol(mid, np.full(len(K), F), K, np.full(len(K), T), put)
    ok = np.isfinite(iv)
    return F, K[ok], mid[ok], put[ok], iv[ok]


def iv_at(m, K, F, iv):
    k = np.log(K / F)
    o = np.argsort(k)
    km = math.log(m)
    if km < k[o][0] - 0.02 or km > k[o][-1] + 0.02:
        return float("nan")
    return float(np.interp(km, k[o], iv[o]))


# ---------------------------------------------------------------- fit

def fit_merton(pts):
    """pts: list of (F, K, T, put, mid, vega). One parameter set across expiries."""
    F = np.concatenate([np.full(len(p[1]), p[0]) for p in pts])
    K = np.concatenate([p[1] for p in pts])
    T = np.concatenate([np.full(len(p[1]), p[2]) for p in pts])
    put = np.concatenate([p[3] for p in pts])
    mid = np.concatenate([p[4] for p in pts])
    vega = np.concatenate([p[5] for p in pts])
    # unconstrained -> (sd, lam, mu, dj) inside BOUNDS
    def unpack(x):
        return tuple(lo + (hi - lo) * special.expit(v) for v, (lo, hi) in zip(x, BOUNDS))

    def res(x):
        return (merton(F, K, T, put, *unpack(x)) - mid) / np.maximum(vega, 1e-4 * F)

    best = None
    for lam0 in (0.3, 3.0, 30.0):
        for mu0 in (-0.25, -0.05):
            for dj0 in (0.05, 0.25):
                if lam0 == 30.0 and dj0 == 0.25:
                    continue
                x0 = special.logit(np.clip(np.array([(v - lo) / (hi - lo) for v, (lo, hi) in
                                                     zip((0.3, lam0, mu0, dj0), BOUNDS)]), 1e-6, 1 - 1e-6))
                r = optimize.least_squares(res, x0, method="trf", x_scale=1.0, max_nfev=4000)
                if best is None or r.cost < best.cost:
                    best = r
    p = unpack(best.x)
    return p, res(best.x)


def _at_bound(name, v, lo, hi):
    """Within 0.2% of the range from a bound (lambda on a log scale, it spans 4 decades)."""
    if name == "lambdaYear":
        u = (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo))
    else:
        u = (v - lo) / (hi - lo)
    return min(u, 1 - u) < 0.002


def model_iv(F, K, T, p):
    put = K < F
    price = merton(np.full(len(K), F), K, np.full(len(K), T), put, *p)
    return implied_vol(price, np.full(len(K), F), K, np.full(len(K), T), put)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--prices", action="store_true", help="note prices under the fitted jumps (CUDA venv)")
    args = ap.parse_args()
    if args.fetch:
        fetch()
        return
    if args.prices:
        note_prices()
        return
    import teacher as Tch
    v3 = Tch.load_config(Tch.CONFIG_V3_PATH)
    hist = {(x["symbol"], x["window"]): x for x in json.load(open(os.path.join(HERE, "multi_stock_jumps.json")))["fits"]}
    files = sorted(os.listdir(DATA))
    out = {}
    print(f"# Merton under Q fitted to listed OTM option mids (Black-76 on parity forwards, r = {R}); IV in %")
    for sym in SYMBOLS:
        snaps = [f for f in files if f.startswith(sym.lower() + "_")]
        if not snaps:
            continue
        snap = json.load(open(os.path.join(DATA, snaps[-1])))
        S, asof = snap["spot"], dt.date.fromisoformat(snap["asOf"])
        pts, sm = [], {}
        for e, rows in snap["expiries"].items():
            T = (dt.date.fromisoformat(e) - asof).days / 365.0
            s = smile(rows, S, T)
            if s is None or len(s[1]) < 6:
                continue
            F, K, mid, put, iv = s
            pts.append((F, K, T, put, mid, black_vega(F, K, T, iv)))
            sm[e] = (F, K, T, iv)
        p, r = fit_merton(pts)
        sd, lam, mu, dj = p
        jv = lam * (mu**2 + dj**2)
        tot = math.sqrt(sd**2 + jv)
        rec = {"spot": S, "asOf": snap["asOf"], "source": snap["urls"][0].split("&fromdate")[0],
               "fit": {"sigma_d": sd, "lambdaYear": lam, "muJ": mu, "sigmaJ": dj, "totalVol": tot,
                       "jumpShare": jv / tot**2, "kappa": math.expm1(mu + 0.5 * dj**2),
                       "rmseVolPts": float(np.sqrt(np.mean(r**2)) * 100),
                       "atBound": [n for n, v, (lo, hi) in zip(PARAMS, p, BOUNDS) if _at_bound(n, v, lo, hi)]},
               "expiries": {}}
        print(f"\n## {sym}  spot {S} as of {snap['asOf']}  ({sum(len(x[1]) for x in pts)} quotes, {len(pts)} expiries)")
        print(f"fit: lambda {lam:.2f}/yr  muJ {mu:+.3f}  sigmaJ {dj:.3f}  sigma_d {sd:.3f}  -> total vol {tot:.1%}, "
              f"jump share {jv / tot**2:.0%}, RMSE {rec['fit']['rmseVolPts']:.2f} vol pts"
              + (f"  AT BOUND: {', '.join(rec['fit']['atBound'])}" if rec["fit"]["atBound"] else ""))
        h = hist.get((sym, "10y"))
        if h:
            print(f"history (10y): lambda {h['derived']['lambdaYear']:.1f}/yr  muJ {h['trading_day']['muJ']:+.4f}  "
                  f"sigmaJ {h['trading_day']['sigmaJ']:.4f}  share {h['derived']['jumpShare']:.0%}")
        print("| expiry | T (y) | fwd / spot | " + " | ".join(f"{m:.1f}" for m in GRID) + " | skew 0.7-1.0 | fit RMSE |")
        print("|---|---:|---:|" + "---:|" * (len(GRID) + 2))
        off = 0
        for (e, (F, K, T, iv)), pt in zip(sm.items(), pts):
            n = len(K)
            rm = float(np.sqrt(np.mean(r[off:off + n] ** 2)) * 100)
            off += n
            row = [iv_at(m, K, F, iv) * 100 for m in GRID]
            fitrow = model_iv(F, np.array(GRID) * F, T, p) * 100
            sk = row[1] - row[4]
            print(f"| {e} mkt | {T:.3f} | {F / S:.4f} | " + " | ".join(f"{v:.1f}" for v in row)
                  + f" | {sk:+.1f} | {rm:.2f} |")
            print(f"| {e} fit | | | " + " | ".join(f"{v:.1f}" for v in fitrow)
                  + f" | {fitrow[1] - fitrow[4]:+.1f} | |")
            atm = row[4] / 100
            if np.isfinite(atm):
                js = atm / v3.vol_ref
                v3p = (math.sqrt(atm**2 * (1 - v3.jump_share)), v3.lambda_year, v3.mu_j * js, v3.sigma_j * js)
                v3row = model_iv(F, np.array(GRID) * F, T, v3p) * 100
                print(f"| {e} v3 @ATM | | | " + " | ".join(f"{v:.1f}" for v in v3row)
                      + f" | {v3row[1] - v3row[4]:+.1f} | |")
            rec["expiries"][e] = {"T": T, "forward": F, "quotes": n, "ivGrid": dict(zip(map(str, GRID), row)),
                                  "fitIvGrid": dict(zip(map(str, GRID), map(float, fitrow))), "rmseVolPts": rm}
        out[sym] = rec
    with open(os.path.join(HERE, "options_calibrate.json"), "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    print("\n# summary: 6-month (2027-03-19) market smile vs fitted jumps")
    print("| symbol | ATM IV | IV at 0.7 | skew 0.7 - ATM | fit lambda/yr | fit muJ | fit sigmaJ | jump share | RMSE | "
          "history share | note |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for sym, rec in out.items():
        e = rec["expiries"].get("2027-03-19") or list(rec["expiries"].values())[-1]
        g = e["ivGrid"]
        fit = rec["fit"]
        h = hist.get((sym, "10y"))
        print(f"| {sym} | {g['1.0']:.1f} | {g['0.7']:.1f} | {g['0.7'] - g['1.0']:+.1f} | {fit['lambdaYear']:.2f} | "
              f"{fit['muJ']:+.3f} | {fit['sigmaJ']:.3f} | {fit['jumpShare']:.0%} | {fit['rmseVolPts']:.2f} | "
              f"{h['derived']['jumpShare']:.0%} | {'at bound: ' + ', '.join(fit['atBound']) if fit['atBound'] else ''} |")
    print("\nwrote ml/options_calibrate.json")



# ---------------------------------------------------------------- note prices (--prices, CUDA venv)

def note_prices():
    """The K2/K3 note (ki 6000, ac 10000, coupon 25 bps/week) at 24 states (as
    ml/multi_stock_prices.py), each stock at its options-fitted total vol: teacher v3's
    shape (TSLA history, 57% share) vs the options-fitted jumps, and GBM for scale. Also v3
    at the 6-month at-the-money implied vol, as a curator would list it: the comparison
    that decides what the Desk's quotes miss.
    rDiscount 0 as teacher v3. torch CUDA teacher, 2^20 paths, one seed for all."""
    import torch
    import teacher as Tch
    import teacher_torch as TT
    v3 = Tch.load_config(Tch.CONFIG_V3_PATH)
    fits = json.load(open(os.path.join(HERE, "options_calibrate.json")))
    rows = np.array([(s, k, n) for n in (26, 13, 2) for k in (0, 1) for s in (5500, 7000, 8500, 9500)], np.float64)
    names = [f"obs {n:.0f} {'KI' if k else 'clean'} spot {s:.0f}" for s, k, n in rows]
    print(f"\n# note prices at the options-fitted total vol: options jumps - teacher v3 shape, bps "
          f"(torch {torch.cuda.get_device_name()}, 2^20 paths, seed 4242; 24 states, tNext 1 day)")
    print("| symbol | total vol | max \\|options - v3\\| | at state | mean, not knocked in | mean, knocked in | "
          "max \\|v3 - GBM\\| | ATM IV | options - v3 at ATM: max | at state | mean not KI / KI |")
    print("|---|---:|---:|---|---:|---:|---:|---:|---:|---|---|")
    out = {}
    for sym, rec in fits.items():
        f = rec["fit"]
        V = round(f["totalVol"], 4)
        F = Tch.features(rows[:, 0], V * 1e4, 86400, rows[:, 2].astype(np.int64), rows[:, 1].astype(np.int64))
        opt = Tch.TeacherConfig(name=f"{sym}-options", r=v3.r, r_disc=v3.r_disc, lambda_year=f["lambdaYear"],
                                mu_j=f["muJ"], sigma_j=f["sigmaJ"])
        gbm = Tch.TeacherConfig(name="gbm", r=v3.r, r_disc=v3.r_disc)
        pv3, sv3 = TT.price_batch(F, 2**20, seed=4242, cfg=v3, device="cuda")
        po, so = TT.price_batch(F, 2**20, seed=4242, cfg=opt, device="cuda")
        pg, _ = TT.price_batch(F, 2**20, seed=4242, cfg=gbm, device="cuda")
        atm = rec["expiries"]["2027-03-19"]["ivGrid"]["1.0"] / 100
        Fa = Tch.features(rows[:, 0], round(atm * 1e4), 86400, rows[:, 2].astype(np.int64), rows[:, 1].astype(np.int64))
        pa, sa = TT.price_batch(Fa, 2**20, seed=4242, cfg=v3, device="cuda")
        d = po - pv3
        i = int(np.argmax(np.abs(d)))
        da = po - pa
        j = int(np.argmax(np.abs(da)))
        ki = rows[:, 1] == 1
        print(f"| {sym} | {V:.1%} | {d[i]:+.1f} ± {np.hypot(sv3[i], so[i]):.1f} | {names[i]} | {d[~ki].mean():+.1f} | "
              f"{d[ki].mean():+.1f} | {np.abs(pv3 - pg).max():.1f} | {atm:.1%} | {da[j]:+.1f} ± {np.hypot(sa[j], so[j]):.1f} "
              f"| {names[j]} | {da[~ki].mean():+.1f} / {da[ki].mean():+.1f} |", flush=True)
        out[sym] = {"totalVol": V, "atmIv6m": atm, "states": names, "v3": pv3.tolist(), "v3AtAtm": pa.tolist(),
                    "options": po.tolist(), "gbm": pg.tolist()}
    with open(os.path.join(HERE, "options_note_prices.json"), "w") as fh:
        json.dump(out, fh, indent=1)
        fh.write("\n")


if __name__ == "__main__":
    main()
