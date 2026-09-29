"""Throughput of the jump teacher backends and the projected lane-B label budget.

    python ml/bench_teacher.py [--device cuda|cpu|numpy] [--states 256] [--paths 262144]

Prices a batch of random K2-domain states (spot 5000-12000, obs 1-26, tNext on
the K2 grid, knockedIn 0/1, total vol 5500) and reports path-steps per second
(one path-step = one antithetic-leg path advanced over one observation
interval). The budget projection uses the lane-B sizes from
docs/sp-three-lanes.md: test set >= 60k states at 2^18 paths, validation set
of the same size, >= 400k training labels at 2^14 paths, average 13.5 steps.
Machine load (other lanes, a GPU shared with another process) is part of the
measurement; the log records it.
"""

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import teacher as T  # noqa: E402

BUDGET = [("test T (60k x 2^18)", 60_000, 2**18), ("validation V (60k x 2^18)", 60_000, 2**18),
          ("train (400k x 2^14)", 400_000, 2**14)]
AVG_STEPS = 13.5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="numpy")
    ap.add_argument("--states", type=int, default=256)
    ap.add_argument("--paths", type=int, default=2**18)
    args = ap.parse_args()
    rng = np.random.default_rng(12345)
    n = args.states
    F = T.features(rng.integers(5000, 12001, n).astype(float), 5500,
                   rng.choice([0, 3600, 43200, 86400, 90000, 172800, 345600, 604800], n).astype(float),
                   rng.integers(1, 27, n), rng.integers(0, 2, n))
    steps = float(np.sum(F["obs"]))
    if args.device == "numpy":
        fn = lambda: T.price_batch(F, args.paths, seed=1)
    else:
        import torch
        import teacher_torch as TT
        fn = lambda: TT.price_batch(F, args.paths, seed=1, device=args.device)
        if args.device == "cuda":
            print(f"# device {torch.cuda.get_device_name()}, torch {torch.__version__}")
    t0 = time.time()
    _, se = fn()
    dt = time.time() - t0
    rate = steps * args.paths / dt
    print(f"# backend {args.device}: {n} states, {args.paths} paths, mean obs {steps / n:.2f}, "
          f"{dt:.1f}s -> {rate:.3e} path-steps/s (one process); label stderr mean {se.mean():.2f} "
          f"max {se.max():.2f} bps; loadavg {os.getloadavg()[0]:.1f}")
    total = 0.0
    for name, states, paths in BUDGET:
        h = states * paths * AVG_STEPS / rate / 3600
        total += h
        print(f"#   {name}: {h:.2f} h")
    print(f"#   total lane-B label budget at this rate: {total:.2f} h (one process)")


if __name__ == "__main__":
    main()
