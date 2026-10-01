"""K2 round 2: point sets and teacher labels (test T, validation V, training).

Every set lives inside the certified domain's *ranges and consistency rules*
(`tools/domains/k2.json`), exclusions included: the eval reports in-band errors
separately, and the gate is taken outside them.

Sets (seeds are fixed here and used nowhere else):
  T  held-out test set, the gate. Grid (spot step 50 bps over 5000-12000 and
     10 bps within +-500 of ki and ac) x 11 observationsRemaining values x
     9 timeToNextObs values x both knockedIn, plus 20,000 uniform domain
     points. Point seed TEST_POINT_SEED, label seed TEST_LABEL_SEED. Labels at
     >= 2^18 paths, topped up until max stderr <= 4 bps. Generated once;
     never used for training or model selection.
  T2 confirmation set, added after the final model was selected: T's
     construction with new seeds and a shifted spot grid (test2_points),
     labelled and evaluated once on the final model, like T.
  V  validation set, model selection only. Same structure with disjoint
     values: spots offset by 25 / 5 bps, other obs and tNext values, 20,000
     uniform points from another seed.
     Plus 30,000 random points in the steep knock-in region (steep_ki_points).
  train  mixture sampler (uniform + barrier / observation-day oversampling),
     seeded per call.
  steep  extra training points from steep_ki_points, seeded per call.

Labels come from `teacher.price_batch`, sharded (fixed shard size, one seed
per shard derived from the set's label seed with SeedSequence) so the result
does not depend on the number of worker processes.

Usage:
  python round2_sets.py --set T --out k2_test_labels.npz --paths 262144 --max-se 4
  python round2_sets.py --set V --out k2_val_labels.npz --paths 131072
  python round2_sets.py --set train --n 1000000 --seed 1 --paths 16384 \\
      --out /opt/ai/cache/sp-student/train_s1.npz
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import sys
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

WEEK = 604_800
DAY = 86_400
KI, AC, VOL, CPN = 6000, 10000, 5500, 25
SPOT_LO, SPOT_HI = 5000, 12000
OBS_MAX = 26

TEST_POINT_SEED = 0x7E57_0001      # T: points (uniform part)
TEST_LABEL_SEED = 0x7E57_0002      # T: teacher paths
TEST2_POINT_SEED = 0x7E52_0001     # T2: points (uniform part)
TEST2_LABEL_SEED = 0x7E52_0002     # T2: teacher paths
VAL_POINT_SEED = 0x7A11_0001       # V: points
VAL_LABEL_SEED = 0x7A11_0002       # V: teacher paths

DOMAIN_PATH = ROOT / "tools" / "domains" / "k2.json"
# The frozen K2 teacher: byte-identical copies of ml/teacher.py, teacher_torch.py and
# teacher_config.json as they labelled every K2 set (sha256 recorded in each cache).
# ml/teacher.py moved on to v3; K2 keeps labelling and gating with this copy.
K2_TEACHER_DIR = HERE / "reference" / "k2"
SHARD = 512                        # rows per labelling shard (fixed: seeds depend on it)
CUDA_MAX_ELEMS = 1 << 23          # labels x paths per GPU chunk (the 3090 is shared); part of the RNG layout, recorded


def load_domain(path=DOMAIN_PATH) -> dict:
    return json.loads(pathlib.Path(path).read_text())


def rows(spot, tnext, obs, flag) -> np.ndarray:
    """Raw integer features (N, 10) in PricerInputs order, derived fields exact."""
    spot = np.asarray(spot, np.int64)
    tnext = np.asarray(tnext, np.int64)
    obs = np.asarray(obs, np.int64)
    flag = np.asarray(flag, np.int64)
    n = spot.size
    return np.column_stack([
        spot, spot - KI, np.full(n, VOL), np.full(n, KI), np.full(n, AC), np.full(n, CPN),
        tnext + obs * WEEK, tnext, obs, flag,
    ]).astype(np.int64)


def excluded(X: np.ndarray, domain: dict | None = None) -> np.ndarray:
    """Boolean mask: inside any exclusion band of the domain."""
    import pricer_quant as pq
    domain = domain or load_domain()
    m = np.zeros(len(X), bool)
    for ex in domain["exclusions"]:
        inside = np.ones(len(X), bool)
        for b in ex["bounds"]:
            f = pq.FIELD_NAMES.index(b["field"])
            inside &= (X[:, f] >= b["min"]) & (X[:, f] <= b["max"])
        m |= inside
    return m


def region_masks(X: np.ndarray) -> dict[str, np.ndarray]:
    spot, tn, obs, fl = X[:, 0], X[:, 7], X[:, 8], X[:, 9]
    return {
        "near ki (5500-6500)": (spot >= KI - 500) & (spot <= KI + 500),
        "near ac (9500-10500)": (spot >= AC - 500) & (spot <= AC + 500),
        "obs 1-2": obs <= 2, "obs 3-6": (obs >= 3) & (obs <= 6),
        "obs 7-13": (obs >= 7) & (obs <= 13), "obs 14-20": (obs >= 14) & (obs <= 20),
        "obs 21-26": obs >= 21,
        "tNext 0": tn == 0, "tNext (0,1d]": (tn > 0) & (tn <= DAY),
        "tNext (1d,2d]": (tn > DAY) & (tn <= 2 * DAY), "tNext (2d,4d]": (tn > 2 * DAY) & (tn <= 4 * DAY),
        "tNext (4d,7d]": tn > 4 * DAY,
        "knockedIn 0": fl == 0, "knockedIn 1": fl == 1,
    }


def uniform_points(n: int, rng: np.random.Generator) -> np.ndarray:
    return rows(rng.integers(SPOT_LO, SPOT_HI + 1, n), rng.integers(0, WEEK + 1, n),
                rng.integers(1, OBS_MAX + 1, n), rng.integers(0, 2, n))


def _grid(spots, obs_vals, tnexts) -> np.ndarray:
    s, o, t, f = np.meshgrid(np.unique(spots), obs_vals, tnexts, (0, 1), indexing="ij")
    return rows(s.ravel(), t.ravel(), o.ravel(), f.ravel())


def test_points() -> np.ndarray:
    spots = np.concatenate([np.arange(SPOT_LO, SPOT_HI + 1, 50),
                            np.arange(KI - 500, KI + 501, 10), np.arange(AC - 500, AC + 501, 10)])
    obs = (1, 2, 3, 4, 6, 9, 13, 17, 21, 25, 26)
    tn = (0, 3600, 43200, 86400, 86401, 90000, 172800, 345600, 604800)
    g = _grid(spots, obs, tn)
    u = uniform_points(20_000, np.random.default_rng(TEST_POINT_SEED))
    return np.vstack([g, u])


def test2_points() -> np.ndarray:
    """Confirmation set T2, fixed before any T2 result was seen: T's
    construction (same obs and tNext values, spot step 50 bps over the domain
    and 10 bps within +-500 of ki and ac, both knockedIn, 20,000 uniform
    points) with the spot grid shifted (+10 / +3 bps) and a new uniform seed,
    so no point is shared with T or V."""
    spots = np.concatenate([[SPOT_LO, SPOT_HI], np.arange(SPOT_LO + 10, SPOT_HI, 50),
                            np.arange(KI - 497, KI + 500, 10), np.arange(AC - 497, AC + 500, 10)])
    obs = (1, 2, 3, 4, 6, 9, 13, 17, 21, 25, 26)
    tn = (0, 3600, 43200, 86400, 86401, 90000, 172800, 345600, 604800)
    X = np.vstack([_grid(spots, obs, tn), uniform_points(20_000, np.random.default_rng(TEST2_POINT_SEED))])
    seen = set(map(tuple, test_points().tolist())) | set(map(tuple, val_points().tolist()))
    return X[np.array([tuple(r) not in seen for r in X.tolist()])]


def val_points() -> np.ndarray:
    spots = np.concatenate([np.arange(SPOT_LO + 25, SPOT_HI, 50),
                            np.arange(KI - 495, KI + 500, 10), np.arange(AC - 495, AC + 500, 10)])
    obs = (1, 2, 3, 5, 7, 10, 15, 19, 23, 24, 26)
    tn = (0, 1800, 64800, 86401, 93600, 129600, 259200, 475200, 604800)
    g = _grid(spots, obs, tn)
    rng = np.random.default_rng(VAL_POINT_SEED)
    u = uniform_points(20_000, rng)
    # dense block where the teacher is steepest outside the bands (round2_bands.log:
    # up to 144 bps per 10 bps of spot at obs 1, knockedIn 0, just past tNext = 1 day)
    h = steep_ki_points(30_000, rng)
    X = np.vstack([g, u, h])
    t = set(map(tuple, test_points().tolist()))  # keep V disjoint from T
    return X[np.array([tuple(r) not in t for r in X.tolist()])]


def steep_ki_points(n: int, rng: np.random.Generator) -> np.ndarray:
    """Not knocked in, spot 5000-7400, tNext just past the band edge to a
    week (half within 3 days), obs 1-10 skewed to 1: the steep knock-in
    region outside the exclusion band."""
    tn = np.where(rng.random(n) < 0.5, rng.integers(DAY + 1, 3 * DAY + 1, n), rng.integers(DAY + 1, WEEK + 1, n))
    obs = np.minimum(1 + np.floor(10 * rng.random(n) ** 2).astype(np.int64), 10)
    return rows(rng.integers(SPOT_LO, KI + 1401, n), tn, obs, np.zeros(n, np.int64))


def train_points(n: int, rng: np.random.Generator) -> np.ndarray:
    """Mixture: uniform, observation-day, near-barrier (both sides of the band
    edge, obs skewed small near ki), exact tNext edges."""
    parts = []

    def obs_skewed(k):  # more mass at small obs, where the ki step is tallest
        return np.minimum(1 + np.floor(OBS_MAX * rng.random(k) ** 2).astype(np.int64), OBS_MAX)

    k = int(n * 0.30)
    parts.append(uniform_points(k, rng))
    k = int(n * 0.10)  # observation-week start: tNext small, anywhere in spot
    parts.append(rows(rng.integers(SPOT_LO, SPOT_HI + 1, k), rng.integers(0, 4 * DAY + 1, k),
                      rng.integers(1, OBS_MAX + 1, k), rng.integers(0, 2, k)))
    k = int(n * 0.28)  # near ki, not knocked in mostly, just past the band edge
    tn = np.where(rng.random(k) < 0.6, rng.integers(DAY, 5 * DAY + 1, k), rng.integers(0, WEEK + 1, k))
    parts.append(rows(rng.integers(SPOT_LO, KI + 1201, k), tn, obs_skewed(k),
                      (rng.random(k) < 0.25).astype(np.int64)))
    k = int(n * 0.22)  # near ac, both flags
    tn = np.where(rng.random(k) < 0.6, rng.integers(0, 4 * DAY + 1, k), rng.integers(0, WEEK + 1, k))
    parts.append(rows(rng.integers(AC - 1200, AC + 1201, k), tn, rng.integers(1, OBS_MAX + 1, k),
                      rng.integers(0, 2, k)))
    k = n - sum(len(p) for p in parts)  # exact edges: tNext 0 / 604800, obs 1 / 26
    tn = rng.choice(np.array([0, WEEK, DAY + 1]), k)
    ob = np.where(rng.random(k) < 0.5, rng.choice(np.array([1, 2, 25, 26]), k), rng.integers(1, OBS_MAX + 1, k))
    parts.append(rows(rng.integers(SPOT_LO, SPOT_HI + 1, k), tn, ob, rng.integers(0, 2, k)))
    X = np.vstack(parts)
    return X[rng.permutation(len(X))]


# ---------------------------------------------------------------------------
# labelling
# ---------------------------------------------------------------------------

def _k2_teacher():
    """(teacher, teacher_torch) from K2_TEACHER_DIR. The copies import each other
    by their plain names, so the directory goes first on sys.path; a process that
    already imported ml/teacher.py (v3) fails here instead of labelling K2 with it."""
    if sys.path[0] != str(K2_TEACHER_DIR):
        sys.path.insert(0, str(K2_TEACHER_DIR))
    import teacher
    assert pathlib.Path(teacher.__file__).resolve().parent == K2_TEACHER_DIR, \
        f"K2 needs the frozen teacher, got {teacher.__file__}"
    return teacher


def _k2_teacher_torch():
    _k2_teacher()
    import teacher_torch
    assert pathlib.Path(teacher_torch.__file__).resolve().parent == K2_TEACHER_DIR, \
        f"K2 needs the frozen teacher, got {teacher_torch.__file__}"
    return teacher_torch


def teacher_fingerprint() -> dict:
    """sha256 of the frozen K2 teacher code (numpy and CUDA backends) and its
    pinned config, None for a file that doesn't exist."""
    out = {}
    for name in ("teacher.py", "teacher_config.json", "teacher_torch.py"):
        p = K2_TEACHER_DIR / name
        out[name] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    return out


def _to_F(X: np.ndarray) -> dict:
    teacher = _k2_teacher()
    F = {k: X[:, i].astype(np.float64) for i, k in enumerate(teacher.FEATURE_KEYS)}
    F["obs"] = X[:, 8].astype(np.int64)
    F["knockedIn"] = X[:, 9].astype(np.int64)
    return F


def _shard_seed(label_seed: int, shard: int, round_: int) -> int:
    return int(np.random.SeedSequence([label_seed, shard, round_]).generate_state(1)[0])


def _label_shard(args):
    X, paths, seed = args
    import torch
    torch.set_num_threads(1)
    teacher = _k2_teacher()
    return teacher.price_batch(_to_F(X), total_paths=paths, seed=seed)


def _cuda_results(jobs):
    teacher_torch = _k2_teacher_torch()
    for X, paths, seed in jobs:
        yield teacher_torch.price_batch(_to_F(X), total_paths=paths, seed=seed, device="cuda", max_elems=CUDA_MAX_ELEMS)


def label(X: np.ndarray, paths: int, label_seed: int, workers: int, round_: int = 0,
          shard_ids: np.ndarray | None = None, backend: str = "numpy"):
    """Teacher labels for X. Shard i always gets seed f(label_seed, i, round_).
    backend "numpy": teacher.price_batch in `workers` processes; "cuda":
    teacher_torch.price_batch (lane A's GPU port of the same teacher) in-process."""
    import multiprocessing as mp
    n = len(X)
    ids = np.arange((n + SHARD - 1) // SHARD) if shard_ids is None else shard_ids
    jobs = [(X[i * SHARD:(i + 1) * SHARD], paths, _shard_seed(label_seed, int(i), round_)) for i in ids]
    y = np.empty(n)
    se = np.empty(n)
    t0 = time.time()
    with (mp.get_context("fork").Pool(workers) if backend == "numpy" else contextlib.nullcontext()) as pool:
        results = pool.imap(_label_shard, jobs, chunksize=1) if backend == "numpy" else _cuda_results(jobs)
        for done, (i, (p, s)) in enumerate(zip(ids, results), 1):
            y[i * SHARD:i * SHARD + len(p)] = p
            se[i * SHARD:i * SHARD + len(p)] = s
            if done % max(1, len(ids) // 20) == 0:
                print(f"  {done}/{len(ids)} shards ({time.time() - t0:.0f}s)", flush=True)
    return y, se


def _device_name(backend: str) -> str:
    if backend == "numpy":
        return "cpu"
    import torch
    return torch.cuda.get_device_name(0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=("T", "T2", "V", "train", "steep"), required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--paths", type=int, default=2**18)
    ap.add_argument("--max-se", type=float, default=None,
                    help="re-label every shard holding a row above this stderr at 4x paths until none")
    ap.add_argument("--n", type=int, default=400_000, help="train/steep: number of points")
    ap.add_argument("--seed", type=int, default=1, help="train/steep: point + label seed")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--backend", choices=("numpy", "cuda"), default="numpy")
    args = ap.parse_args()
    os.environ.setdefault("OMP_NUM_THREADS", "1")

    if args.set == "T":
        X, lseed = test_points(), TEST_LABEL_SEED
    elif args.set == "T2":
        X, lseed = test2_points(), TEST2_LABEL_SEED
    elif args.set == "V":
        X, lseed = val_points(), VAL_LABEL_SEED
    elif args.set == "train":
        rng = np.random.default_rng([0x7EA1, args.seed])
        X, lseed = train_points(args.n, rng), int(np.random.SeedSequence([0x7EA1, args.seed, 1]).generate_state(1)[0])
    else:  # steep: extra training points in the steep knock-in region
        rng = np.random.default_rng([0x57EE, args.seed])
        X, lseed = steep_ki_points(args.n, rng), int(np.random.SeedSequence([0x57EE, args.seed, 1]).generate_state(1)[0])
    fp = teacher_fingerprint()
    print(f"set {args.set}: {len(X)} points, {args.paths} paths, label seed {lseed}, teacher {fp}", flush=True)
    t0 = time.time()
    y, se = label(X, args.paths, lseed, args.workers, backend=args.backend)
    paths = np.full(len(X), args.paths, np.int64)
    rnd = 0
    while args.max_se is not None and se.max() > args.max_se:
        rnd += 1
        bad = np.unique(np.flatnonzero(se > args.max_se) // SHARD)
        p = args.paths * 4 ** rnd
        print(f"top-up round {rnd}: {len(bad)} shards with stderr > {args.max_se} (max {se.max():.2f}) at {p} paths",
              flush=True)
        y2, se2 = label(X, p, lseed, args.workers, round_=rnd, shard_ids=bad, backend=args.backend)
        for i in bad:
            sl = slice(i * SHARD, min((i + 1) * SHARD, len(X)))
            y[sl], se[sl], paths[sl] = y2[sl], se2[sl], p
    dt = time.time() - t0
    print(f"labels done in {dt:.0f}s; stderr bps mean {se.mean():.2f} p99 {np.percentile(se, 99):.2f} "
          f"max {se.max():.2f}; paths min {paths.min()}", flush=True)
    np.savez_compressed(args.out, X=X.astype(np.int32), y=y, se=se, paths=paths.astype(np.int32),
                        meta=json.dumps({"set": args.set, "label_seed": lseed, "paths": args.paths,
                                         "max_se_target": args.max_se, "topup_rounds": rnd,
                                         "n": len(X), "seed": args.seed if args.set in ("train", "steep") else None,
                                         "teacher": fp, "backend": args.backend, "device": _device_name(args.backend),
                                         "shard": SHARD, "cuda_max_elems": CUDA_MAX_ELEMS,
                                         "seconds": round(dt),
                                         "argv": sys.argv[1:]}))
    print(f"wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
