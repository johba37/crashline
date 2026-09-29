"""GBM vs jump teacher: clean note prices for the K1/K2 product at 24 note states.

    python ml/teacher_v2_table.py > ml/teacher_v2_table.log

Product: ki 6000, ac 10000, coupon 25 bps/week, total vol 5500, weekly
observations, on the consistency manifold (ttm = tNext + obs * WEEK).
States: spot {5500, 7000, 8500, 9500} x knockedIn {0, 1} x
observationsRemaining {26, 13, 2}, tNext = 86400 s (one day before the next
observation). numpy teacher, 2^20 paths, independent seeds per model; the
difference's stderr is the hypot of the two.
"""

import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import teacher as T  # noqa: E402

PATHS = 2**20
TNEXT = 86400


def main():
    cfg = T.load_config()
    rows = [(s, k, n) for n in (26, 13, 2) for k in (0, 1) for s in (5500, 7000, 8500, 9500)]
    rows = np.array(rows, dtype=np.float64)
    F = T.features(rows[:, 0], 5500, TNEXT, rows[:, 2].astype(np.int64), rows[:, 1].astype(np.int64))
    t0 = time.time()
    pg, sg = T.price_batch(F, PATHS, seed=9001, cfg=T.GBM)
    pj, sj = T.price_batch(F, PATHS, seed=9002, cfg=cfg)
    print(f"# jump teacher {cfg.name}: lambdaYear {cfg.lambda_year}, muJ {cfg.mu_j}, sigmaJ {cfg.sigma_j}, "
          f"jump vol {np.sqrt(cfg.jump_var_year):.4f}, diffusion vol at 55% total "
          f"{float(cfg.diffusion_vol(np.array([0.55]))[0]):.4f}")
    print(f"# product ki 6000 / ac 10000 / coupon 25 / total vol 5500, tNext {TNEXT} s, {PATHS} paths, "
          f"seeds 9001 (GBM) / 9002 (jump); {time.time() - t0:.0f}s")
    print("| obs left | knocked in | spot | GBM (bps) | jump (bps) | jump − GBM (bps) | z |")
    print("|---:|---:|---:|---:|---:|---:|---:|")
    for (s, k, n), a, ea, b, eb in zip(rows, pg, sg, pj, sj):
        d, e = b - a, np.hypot(ea, eb)
        print(f"| {n:.0f} | {'yes' if k else 'no'} | {s:.0f} | {a:.1f} ± {ea:.1f} | {b:.1f} ± {eb:.1f} | "
              f"{d:+.1f} ± {e:.1f} | {d / e:+.1f} |")
    d = pj - pg
    e = np.hypot(sg, sj)
    print(f"# max |jump − GBM| {np.max(np.abs(d)):.1f} bps; states with |z| > 3: {int(np.sum(np.abs(d / e) > 3))}"
          f" of {len(d)}; max stderr GBM {sg.max():.2f} / jump {sj.max():.2f} bps")


if __name__ == "__main__":
    main()
