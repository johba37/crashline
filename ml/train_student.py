"""Train the float32 student MLP for the K1 boundary-fidelity experiment.

Architecture (matches the on-chain quantized-MLP budget; export is 16-bit per
docs/model-export-format.md v1 — see ml/round1_16bit.py): 10 -> 24 -> 24 -> 24 -> 1,
ReLU, linear output in bps of notional. Exact parameter count: 1489
(10*24+24 + 24*24+24 + 24*24+24 + 24*1+1).

Data: ~200k teacher labels.
  - 50% uniform over the NoteQuoter ranges (ki/ac/coupon/obsRemaining all
    randomized so the net learns them; distToKnockInBps derived as spot - ki).
  - 50% boundary-oversampled: spot within +-500 bps of KI or AC barrier,
    70% of those with timeToNextObs <= 1 day.
Labels: teacher.price_batch with 4096 antithetic paths (2^12; v0 deviation
from the spec's 2^20 — MSE training is unbiased under zero-mean label noise;
the *eval* labels in run_k1.py use 2^18 paths).

Saves ml/student_k1.pt with state_dict + normalization ranges.
"""

import argparse
import os
import time

import numpy as np
import torch
import torch.nn as nn

import teacher

WEEK = teacher.WEEK_SECS
MAT_MAX = 63_072_000  # NoteQuoter.MATURITY_MAX_SECS

# Feature order and normalization ranges (NoteQuoter.sol constants).
RANGES = np.array([
    (2000.0, 30000.0),     # spotBpsOfInitial
    (-8000.0, 20000.0),    # distToKnockInBps
    (1500.0, 15000.0),     # volBpsAnnual
    (4000.0, 9000.0),      # kiBarrierBps
    (9000.0, 11000.0),     # acBarrierBps
    (0.0, 1500.0),         # couponBpsPerPeriod
    (604800.0, 63072000.0),  # timeToMaturitySecs
    (0.0, 604800.0),       # timeToNextObsSecs
    (1.0, 104.0),          # observationsRemaining
    (0.0, 1.0),            # flags (bit0 knockedIn)
])

N_TRAIN = 200_000
LABEL_PATHS = 2**12       # 2048 antithetic pairs
LABEL_SEED = 20260929
TORCH_SEED = 0


_FIXED_TERMS = False  # round-0b: pin the candidate note's terms (per-note weightsHash)


def _base_terms(n, rng):
    if _FIXED_TERMS:
        ki = np.full(n, 6000.0)
        ac = np.full(n, 10000.0)
        vol = np.full(n, 5500.0)
        cpn = np.full(n, 25.0)
        obs = rng.integers(1, 27, n).astype(np.int64)  # state var: counts down mid-life
    else:
        ki = rng.uniform(4000, 9000, n)
        ac = rng.uniform(9000, 11000, n)
        ac = np.maximum(ac, ki + 1.0)
        vol = rng.uniform(1500, 15000, n)
        cpn = rng.uniform(0, 1500, n)
        obs = rng.integers(1, 105, n).astype(np.int64)
    tmax = np.minimum(float(WEEK), MAT_MAX - obs * WEEK)
    return ki, ac, vol, cpn, obs, tmax


def sample_uniform(n, rng):
    ki, ac, vol, cpn, obs, tmax = _base_terms(n, rng)
    spot = rng.uniform(2000, 30000, n)
    bad = (spot - ki < -8000) | (spot - ki > 20000)  # keep dist in range
    while bad.any():
        spot[bad] = rng.uniform(2000, 30000, bad.sum())
        bad = (spot - ki < -8000) | (spot - ki > 20000)
    tnext = rng.uniform(0, 1, n) * tmax
    flags = rng.integers(0, 2, n).astype(np.int64)
    return spot, ki, ac, vol, cpn, obs, tnext, flags


def sample_boundary(n, rng):
    ki, ac, vol, cpn, obs, tmax = _base_terms(n, rng)
    barrier = np.where(rng.random(n) < 0.5, ki, ac)
    spot = np.clip(barrier + rng.uniform(-500, 500, n), 2000, 30000)
    near = rng.random(n) < 0.7
    tnext = np.where(near, rng.uniform(0, 86400, n), rng.uniform(0, 1, n) * tmax)
    tnext = np.minimum(tnext, tmax)
    flags = rng.integers(0, 2, n).astype(np.int64)
    return spot, ki, ac, vol, cpn, obs, tnext, flags


def make_features(n, rng):
    half = n // 2
    cols = []
    for sampler in (sample_uniform, sample_boundary):
        spot, ki, ac, vol, cpn, obs, tnext, flags = sampler(half, rng)
        cols.append(np.column_stack([
            spot, spot - ki, vol, ki, ac, cpn,
            obs * WEEK + tnext, tnext, obs, flags,
        ]))
    X = np.vstack(cols)
    F = {k: X[:, i] for i, k in enumerate(teacher.FEATURE_KEYS)}
    F["obs"] = F["obs"].astype(np.int64)
    F["knockedIn"] = F["knockedIn"].astype(np.int64)
    return X, F


class Student(nn.Module):
    def __init__(self, width=24):
        super().__init__()
        w = width
        self.net = nn.Sequential(
            nn.Linear(10, w), nn.ReLU(),
            nn.Linear(w, w), nn.ReLU(),
            nn.Linear(w, w), nn.ReLU(),
            nn.Linear(w, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze(-1)


def normalize(X):
    lo, hi = RANGES[:, 0], RANGES[:, 1]
    return (2.0 * (X - lo) / (hi - lo) - 1.0).astype(np.float32)


def main():
    global _FIXED_TERMS
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixed-terms", action="store_true",
                    help="round-0b: pin ki/ac/coupon/vol to the candidate note terms; "
                         "only state variables (spot, obsRemaining, tNext, knockedIn) vary")
    ap.add_argument("--out", default="student_k1.pt")
    ap.add_argument("--data-cache", default=None,
                    help="npz with cached X/y/yse; regenerated if missing")
    ap.add_argument("--tmax", type=int, default=600,
                    help="cosine schedule length; set near expected early-stop epoch")
    ap.add_argument("--width", type=int, default=24)
    args = ap.parse_args()
    _FIXED_TERMS = args.fixed_terms

    t0 = time.time()
    if args.data_cache and os.path.exists(args.data_cache):
        z = np.load(args.data_cache)
        X, y, yse = z["X"], z["y"], z["yse"]
        print(f"data loaded from {args.data_cache}: {X.shape}", flush=True)
    else:
        rng = np.random.default_rng(7)
        X, F = make_features(N_TRAIN, rng)
        print(f"features sampled: {X.shape} ({time.time()-t0:.1f}s)", flush=True)
        t1 = time.time()
        y, yse = teacher.price_batch(F, total_paths=LABEL_PATHS, seed=LABEL_SEED)
        print(f"labels done ({time.time()-t1:.1f}s); label MC stderr bps: "
              f"mean {yse.mean():.2f}, p50 {np.median(yse):.2f}, "
              f"p95 {np.percentile(yse, 95):.2f}, max {yse.max():.2f}", flush=True)
        if args.data_cache:
            np.savez(args.data_cache, X=X, y=y, yse=yse)

    torch.manual_seed(TORCH_SEED)
    Xn = torch.from_numpy(normalize(X))
    # Target standardization (round-0c fix): with raw ~1e4-bps targets, Adam's
    # ~lr-sized steps cannot grow the output layer to the required scale;
    # training plateaued at RMSE ~1.4k-6.8k bps. The affine transform is stored
    # in the checkpoint and inverted at inference (run_k1.py).
    perm = torch.randperm(len(Xn))
    n_val = 10_000
    vidx, tidx = perm[:n_val], perm[n_val:]
    y_mean = float(y[tidx.numpy()].mean())
    y_std = float(y[tidx.numpy()].std())
    yt = torch.from_numpy(((y - y_mean) / y_std).astype(np.float32))
    Xv, yv = Xn[vidx], yt[vidx]
    Xt, yt_ = Xn[tidx], yt[tidx]
    print(f"target standardization: mean {y_mean:.1f} std {y_std:.1f}", flush=True)

    model = Student(width=args.width)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"param count: {n_params}", flush=True)
    opt = torch.optim.Adam(model.parameters(), lr=3e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.tmax)
    loss_fn = nn.MSELoss()

    best_val, best_state, bad_epochs = float("inf"), None, 0
    batch = 4096
    for epoch in range(1, 601):
        model.train()
        p = torch.randperm(len(Xt))
        for lo in range(0, len(Xt), batch):
            i = p[lo:lo + batch]
            opt.zero_grad()
            loss = loss_fn(model(Xt[i]), yt_[i])
            loss.backward()
            opt.step()
        sched.step()
        model.eval()
        with torch.no_grad():
            val = loss_fn(model(Xv), yv).item()
        if val < best_val - 1e-8:
            best_val, best_state, bad_epochs = val, {
                k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            bad_epochs += 1
        if epoch % 25 == 0 or epoch == 1:
            print(f"epoch {epoch:4d}  val RMSE {val**0.5 * y_std:8.2f} bps", flush=True)
        if bad_epochs >= 60:
            print(f"early stop at epoch {epoch}", flush=True)
            break

    model.load_state_dict(best_state)
    torch.save({"state_dict": best_state, "ranges": RANGES, "arch": [10, 24, 24, 24, 1],
                    "fixed_terms": args.fixed_terms, "width": args.width,
                    "y_mean": y_mean, "y_std": y_std,
                    "label_paths": LABEL_PATHS, "label_seed": LABEL_SEED,
                    "label_stderr_mean_bps": float(yse.mean()),
                    "best_val_rmse_bps": float(best_val**0.5 * y_std)},
                   args.out)
    with torch.no_grad():
        tr = loss_fn(model(Xt), yt_).item() ** 0.5 * y_std
    print(f"best val RMSE {best_val**0.5 * y_std:.2f} bps | train RMSE {tr:.2f} bps "
          f"(label-noise floor ~{yse.mean():.1f} bps)", flush=True)
    print(f"total runtime {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
