"""What each stock's jump fit does to note prices, in bps of notional.

    /opt/ai/cache/venv-cuda/bin/python ml/multi_stock_prices.py > ml/multi_stock_prices.log

Reads the per-stock Merton fits in ml/multi_stock_jumps.json. A fit's jump
*shape* is kept (jumps per year, and jump sizes relative to the stock's total
vol, hence its jump share of variance) and scaled to a target total vol V:
lambda = lambda_s, muJ = muJ_s * V / V_s, sigmaJ = sigmaJ_s * V / V_s, where V_s
is the fit's model total vol. Then jump variance / V^2 = the fit's jump share.

A. The K2 product at 55% total vol: each stock's shape vs the pinned teacher
   (ml/teacher_config.json, what model/k2 learned). Question: at the same vol,
   does the stock's jump shape move the price by more than the student's error?
   The row "TSLA 10y" is the fixed-share rule itself (57%); the pinned teacher
   has a fixed jump variance, which is a 66% share at 55% vol.
B. Each stock at its own fitted total vol: own shape vs the fixed-share rule
   (TSLA 10y shape scaled to that vol) vs GBM. Question: if one teacher used the
   fixed-share rule for every stock, how far is it from the stock's own fit?

Product and states as ml/teacher_v2_table.py: ki 6000, ac 10000, coupon 25
bps/week; spot {5500, 7000, 8500, 9500} x knockedIn {0, 1} x
observationsRemaining {26, 13, 2}, tNext 86400 s. torch CUDA teacher, 2^20
paths, one seed for every config (common random numbers); the stderr of a
difference is taken as the hypot of the two (conservative under CRN).

Smoothing terms: MSTR's 10-year fit has 231 jumps/yr (4.4 per week), where the
pinned 24 Poisson terms drop 8e-11 of mass (the teacher asserts < 1e-12). This
script raises teacher.SMOOTH_TERMS to 40 in-process, after the pinned config is
loaded. Extra terms below 1e-17 weight are skipped, so the pinned teacher's
prices are unchanged; ml/teacher_config.json is not touched.
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import teacher as T  # noqa: E402
import teacher_torch as TT  # noqa: E402

PATHS = 2**20
SEED = 9100
TNEXT = 86400
ROWS = np.array([(s, k, n) for n in (26, 13, 2) for k in (0, 1) for s in (5500, 7000, 8500, 9500)],
                dtype=np.float64)


def shape_cfg(fit, V, name):
    td, Vs = fit["trading_day"], fit["derived"]["totalVol"]
    f = V / Vs
    return T.TeacherConfig(name=name, lambda_year=fit["derived"]["lambdaYear"],
                           mu_j=td["muJ"] * f, sigma_j=td["sigmaJ"] * f)


def price(cfg, V):
    F = T.features(ROWS[:, 0], round(V * 1e4), TNEXT, ROWS[:, 2].astype(np.int64), ROWS[:, 1].astype(np.int64))
    return TT.price_batch(F, PATHS, seed=SEED, cfg=cfg, device="cuda")


def state(i):
    s, k, n = ROWS[i]
    return f"obs {n:.0f} {'KI' if k else 'clean'} spot {s:.0f}"


def cmp(a, b):
    d, e = a[0] - b[0], np.hypot(a[1], b[1])
    i = int(np.argmax(np.abs(d)))
    return d, e, i


def main():
    fits = json.load(open(os.path.join(HERE, "multi_stock_jumps.json")))["fits"]
    tsla10 = next(x for x in fits if x["symbol"] == "TSLA" and x["window"] == "10y")
    pinned = T.load_config()
    T.SMOOTH_TERMS = 40
    print(f"# device {torch.cuda.get_device_name()}; {PATHS} paths, seed {SEED}; product ki 6000 / ac 10000 / "
          f"coupon 25, tNext {TNEXT} s, 24 states")
    t0 = time.time()

    V = 0.55
    base = price(pinned, V)
    gbm = price(T.GBM, V)
    d, e, i = cmp(base, gbm)
    print(f"\n## A. K2 product at total vol 55%: stock's jump shape vs the pinned teacher (jump share 66% at 55%)")
    print(f"# reference: pinned − GBM max |diff| {abs(d[i]):.1f} bps ({state(i)}), "
          f"max label stderr {max(base[1].max(), gbm[1].max()):.2f} bps")
    print("| fit | window | λ/yr | jump share | max \\|Δ\\| vs pinned (bps) | at state | mean \\|Δ\\| | states \\|z\\|>3 |")
    print("|---|---|---:|---:|---:|---|---:|---:|")
    outA = []
    for x in fits:
        p = price(shape_cfg(x, V, f"{x['symbol']}-{x['window']}@55"), V)
        d, e, i = cmp(p, base)
        outA.append({"symbol": x["symbol"], "window": x["window"], "maxAbs": float(abs(d[i])), "at": state(i),
                     "meanAbs": float(np.mean(np.abs(d))), "nz3": int(np.sum(np.abs(d / e) > 3)),
                     "diff": d.tolist(), "stderr": e.tolist()})
        print(f"| {x['symbol']} | {x['window']} | {x['derived']['lambdaYear']:.0f} | {x['derived']['jumpShare']:.0%} | "
              f"{d[i]:+.1f} ± {e[i]:.1f} | {state(i)} | {np.mean(np.abs(d)):.1f} | {int(np.sum(np.abs(d / e) > 3))} |")
    mx = [r["maxAbs"] for r in outA]
    print(f"# max |Δ| across fits: median {np.median(mx):.1f}, max {max(mx):.1f} bps; {time.time() - t0:.0f}s")

    print(f"\n## B. Each stock at its own fitted total vol: own shape vs the fixed-share rule (TSLA 10y shape, 57%) and GBM")
    print("| fit | window | total vol | jump share | max \\|own − rule\\| (bps) | at state | "
          "max \\|own − GBM\\| (bps) | at state |")
    print("|---|---|---:|---:|---:|---|---:|---|")
    outB = []
    for x in fits:
        V = round(x["derived"]["totalVol"], 4)
        own = price(shape_cfg(x, V, "own"), V)
        rule = price(shape_cfg(tsla10, V, "rule"), V)
        g = price(T.GBM, V)
        d1, e1, i1 = cmp(own, rule)
        d2, e2, i2 = cmp(own, g)
        outB.append({"symbol": x["symbol"], "window": x["window"], "vol": V,
                     "ownMinusRule": {"maxAbs": float(abs(d1[i1])), "at": state(i1), "diff": d1.tolist(),
                                      "stderr": e1.tolist()},
                     "ownMinusGBM": {"maxAbs": float(abs(d2[i2])), "at": state(i2), "diff": d2.tolist(),
                                     "stderr": e2.tolist()}})
        print(f"| {x['symbol']} | {x['window']} | {V:.1%} | {x['derived']['jumpShare']:.0%} | "
              f"{d1[i1]:+.1f} ± {e1[i1]:.1f} | {state(i1)} | {d2[i2]:+.1f} ± {e2[i2]:.1f} | {state(i2)} |")
    r1 = [r["ownMinusRule"]["maxAbs"] for r in outB]
    r2 = [r["ownMinusGBM"]["maxAbs"] for r in outB]
    print(f"# max |own − rule|: median {np.median(r1):.1f}, max {max(r1):.1f} bps;  "
          f"max |own − GBM|: median {np.median(r2):.1f}, max {max(r2):.1f} bps; {time.time() - t0:.0f}s")

    with open(os.path.join(HERE, "multi_stock_prices.json"), "w") as f:
        json.dump({"paths": PATHS, "seed": SEED, "states": [state(i) for i in range(len(ROWS))],
                   "A_at55": outA, "B_ownVol": outB}, f, indent=2)
        f.write("\n")
    print("\nwrote ml/multi_stock_prices.json")


if __name__ == "__main__":
    main()
