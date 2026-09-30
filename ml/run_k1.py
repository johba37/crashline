"""K1 adversarial eval: dense spot sweep across both barriers.

Grid: spotBpsOfInitial 5000..12000 step 5 (1401 points) x timeToNextObsSecs in
{0, 3600, 86400, 302400} x knockedIn in {0, 1} = 11208 labels. Fixed terms:
ki 6000, ac 10000, coupon 25, obsRemaining 26, ttm = 26*WEEK + tNext, vol 5500.

Teacher labels: 2^18 paths antithetic (cached in k1_eval_cache.npz).
Student: ml/student_k1.pt from train_student.py.

Writes docs/k1-round0.png and prints the metrics that go into docs/k1-round0.md.
"""

import argparse
import os
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch

from reference import teacher_gbm_k1 as teacher  # K1 labels: the GBM teacher, frozen copy (docs/teacher-v2.md)
from train_student import Student, RANGES, normalize

WEEK = teacher.WEEK_SECS
CACHE = "k1_eval_cache.npz"
EVAL_PATHS = 2**18
EVAL_SEED = 777
TNEXTS = [0, 3600, 86400, 302400]

OUT_PNG = os.path.join("..", "docs", "k1-round0.png")


def build_grid():
    spots = np.arange(5000, 12001, 5, dtype=np.float64)
    rows = []
    for flag in (0, 1):
        for tn in TNEXTS:
            for s in spots:
                rows.append((s, tn, flag))
    rows = np.array(rows, dtype=np.float64)
    spot, tnext, flag = rows[:, 0], rows[:, 1], rows[:, 2]
    F = {
        "spot": spot,
        "dist": spot - 6000.0,
        "vol": np.full_like(spot, 5500.0),
        "ki": np.full_like(spot, 6000.0),
        "ac": np.full_like(spot, 10000.0),
        "coupon": np.full_like(spot, 25.0),
        "ttm": 26 * WEEK + tnext,
        "tNext": tnext,
        "obs": np.full(spot.shape, 26, dtype=np.int64),
        "knockedIn": flag.astype(np.int64),
    }
    X = np.column_stack([F[k] for k in teacher.FEATURE_KEYS])
    return X, F, spots


def teacher_labels(F):
    if os.path.exists(CACHE):
        z = np.load(CACHE)
        if (z["paths"] == EVAL_PATHS and z["seed"] == EVAL_SEED
                and z["y"].shape[0] == len(F["spot"])):
            print(f"teacher labels loaded from {CACHE}", flush=True)
            return z["y"], z["se"]
    t0 = time.time()
    y, se = teacher.price_batch(F, total_paths=EVAL_PATHS, seed=EVAL_SEED)
    print(f"teacher labels done in {time.time()-t0:.1f}s", flush=True)
    np.savez(CACHE, y=y, se=se, paths=EVAL_PATHS, seed=EVAL_SEED)
    return y, se


def student_preds(X, model_path):
    ckpt = torch.load(model_path, weights_only=False)
    model = Student(width=ckpt.get('width', 24))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    y_mean = ckpt.get("y_mean", 0.0)
    y_std = ckpt.get("y_std", 1.0)
    with torch.no_grad():
        preds = []
        for lo in range(0, len(X), 65536):
            preds.append(model(torch.from_numpy(normalize(X[lo:lo + 65536]))))
        return torch.cat(preds).numpy().astype(np.float64) * y_std + y_mean


def stats(err, mask, name):
    e = np.abs(err[mask])
    return (f"{name:34s} n={mask.sum():5d}  max {e.max():8.2f}  "
            f"p99 {np.percentile(e, 99):8.2f}  mean {e.mean():7.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="student_k1.pt")
    ap.add_argument("--png", default=OUT_PNG)
    args = ap.parse_args()

    X, F, spots = build_grid()
    y, se = teacher_labels(F)
    pred = student_preds(X, args.model)
    err = pred - y

    spot, tnext, flag = F["spot"], F["tNext"], F["knockedIn"]
    near_ac = (spot >= 9500) & (spot <= 10500)
    near_ki = (spot >= 5500) & (spot <= 6500)
    elsewhere = ~(near_ac | near_ki)
    at_obs = tnext == 0

    print(f"eval label MC stderr bps: mean {se.mean():.2f}, p95 "
          f"{np.percentile(se, 95):.2f}, max {se.max():.2f}")
    print(stats(err, np.ones_like(at_obs, bool), "overall"))
    print(stats(err, near_ac, "near AC (9500-10500)"))
    print(stats(err, near_ki, "near KI (5500-6500)"))
    print(stats(err, elsewhere, "elsewhere"))
    print(stats(err, at_obs, "tNext=0 (at observation)"))
    print(stats(err, ~at_obs, "tNext>0 (away from observation)"))
    print(stats(err, near_ac & at_obs, "near AC & at-obs"))
    print(stats(err, near_ac & ~at_obs, "near AC & away"))
    print(stats(err, near_ki & at_obs, "near KI & at-obs"))
    print(stats(err, near_ki & ~at_obs, "near KI & away"))
    print(stats(err, elsewhere & at_obs, "elsewhere & at-obs"))
    print(stats(err, elsewhere & ~at_obs, "elsewhere & away"))

    i = int(np.argmax(np.abs(err)))
    print(f"worst point: spot {spot[i]:.0f} tNext {tnext[i]:.0f} knockedIn {flag[i]:.0f}"
          f"  teacher {y[i]:.2f} (se {se[i]:.2f})  student {pred[i]:.2f}  d {err[i]:+.2f}")

    # Plot: |error| vs spot, one row per knockedIn, colored by tNext.
    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    colors = {0: "crimson", 3600: "darkorange", 86400: "seagreen", 302400: "steelblue"}
    for ax, fl in zip(axes, (0, 1)):
        for tn in TNEXTS:
            m = (flag == fl) & (tnext == tn)
            order = np.argsort(spot[m])
            ax.plot(spot[m][order], np.abs(err[m][order]), color=colors[tn],
                    lw=0.9, label=f"tNext={tn}s")
        ax.axvline(6000, color="k", ls="--", lw=0.8, alpha=0.6)
        ax.axvline(10000, color="k", ls="--", lw=0.8, alpha=0.6)
        ax.axhline(50, color="magenta", ls=":", lw=1.2)
        ax.set_yscale("log")
        ax.set_ylabel("|student - teacher| (bps)")
        ax.set_title(f"knockedIn={fl}   (dashed: KI 6000 / AC 10000, dotted: 50 bps gate)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    axes[1].set_xlabel("spotBpsOfInitial")
    fig.tight_layout()
    fig.savefig(args.png, dpi=150)
    print(f"plot written to {args.png}")


if __name__ == "__main__":
    main()
