"""P1 (the perpetual note's student): points, labels and regions.

Inputs (featureSpecVersion 2, the PerpPricerInputs order):
    0 spotBpsOfReference     x = spot / H in bps
    1 volBpsAnnual           total annualized vol
    2 timeToNextFixingSecs   0 .. 604800
    3 fixingsBeforeEarnings  n: fixings before the next earnings release
                             (0 = the release comes before or at the next fixing)
    4 flags                  bit0 knocked in
Label: the teacher's principal value (ml/teacher_perp.py, bps of live notional)
and the formula's (ml/perp_formula.py, at the integer inputs); the student is
trained on the difference.

Sets, all defined here before any of them was labelled; the seeds are used
nowhere else:
    T      gate: 6 vols x spot grid (every 100 bps, every 20 near k and near 1)
           x 14 times to the fixing (both candidate band edges) x 10 values of
           n x both flags, + 30,000 uniform
    V      selection: disjoint values (vols between T's and next to the ends,
           spots offset 50 / 10, other times and n), + 30,000 uniform
           + 40,000 steep (clean, near k, just outside the bands)
           + 10,000 steep (knocked in, near 1)
    T2     confirmation: T's construction, vols + 1 point, spots + 30 / + 6,
           other n, a new uniform seed; labelled once, at the end
    train  a mixture (uniform, near k, near 1, the release next to the fixing,
           the vol ends), vols from a fixed list of random values
    S      spread (reported, not gated): 15,000 states, each at vol - 2, vol and
           vol + 2 points; half uniform, half clean near k just outside the
           bands; 100 base vols

  python ml/perp_sets.py --set T --out /opt/ai/cache/sp-perp/p1_test_labels.npz
  python ml/perp_sets.py --set train --n 1000000 --seed 1 --n-k 128 --out /opt/ai/cache/sp-perp/train_s1.npz
  python ml/perp_sets.py --compact /opt/ai/cache/sp-perp/p1_test_labels.npz --out ml/perp_p1_test_labels.npz

--compact keeps the teacher's labels only (float32, 0.001 bps), with a hash of
the points: the points and the formula are recomputed from this file's
construction by ml/perp_eval.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import time
from multiprocessing import Pool

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
# ml/ first: tools/ has a perp_formula.py of its own (the integer twin)
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))

import perp_formula as pf  # noqa: E402

# Labelling needs scipy (ml/teacher_perp.py), training needs torch and keccak
# (tools/pricer_quant.py); no environment here has all three, so both are
# imported where they are used.

SPOT, VOL, TAU, NE, FLAG = range(5)
WEEK = 604_800
DAY = 86_400
YEAR_SECS = 31_536_000
# (name, normalization min, max): must equal pricer_quant.FIELDS_V2
FIELDS = (
    ("spotBpsOfReference", 1_000, 15_000),
    ("volBpsAnnual", 1_500, 15_000),
    ("timeToNextFixingSecs", 0, WEEK),
    ("fixingsBeforeEarnings", 0, 20),
    ("flags", 0, 1),
)
DOMAIN_PATH = ROOT / "tools" / "domains" / "p1.json"
SPOT_RANGE = (2_000, 13_000)
VOL_RANGE = (2_000, 9_000)
N_MAX = 18
KI_BAND_SPOT = (5_000, 7_000)       # clean notes: the knock-in jump at x = k
HEAL_BAND_SPOT = (9_500, 10_500)    # knocked-in notes: the heal jump at x = 1
BAND_CANDIDATES = (21_600, 86_400)  # 6 hours, 1 day

SEEDS = {"T": 20261001, "V": 20261002, "T2": 20261003, "V_steep": 20261004, "train_vols": 20261005, "S": 20261006}
SPREAD_DVOL = 200                   # the Desk's vol band in the dev listing: 2 vol points


def ensure_fields(pq) -> None:
    """The integer reference (tools/pricer_quant.py) must describe these five
    inputs: field 3 is the count n, not a time (docs/p1-perp-student.md,
    "Deviations from the spec")."""
    want = tuple(pq.Field(*f) for f in FIELDS)
    if tuple(pq.FIELDS_V2) != want:
        pq.FIELDS_V2 = want
        pq.SPEC_FIELDS[2] = want


def domain(band_secs: int) -> dict:
    """The certified domain with both fixing bands `band_secs` long."""
    return {
        "ranges": [
            {"name": "spotBpsOfReference", "min": SPOT_RANGE[0], "max": SPOT_RANGE[1]},
            {"name": "volBpsAnnual", "min": VOL_RANGE[0], "max": VOL_RANGE[1]},
            {"name": "timeToNextFixingSecs", "min": 0, "max": WEEK},
            {"name": "fixingsBeforeEarnings", "min": 0, "max": N_MAX},
            {"name": "flags", "min": 0, "max": 1},
        ],
        "consistency": {},
        "exclusions": [
            {"name": "knockInFixingBand",
             "bounds": [{"field": "timeToNextFixingSecs", "min": 0, "max": band_secs},
                        {"field": "spotBpsOfReference", "min": KI_BAND_SPOT[0], "max": KI_BAND_SPOT[1]},
                        {"field": "flags", "min": 0, "max": 0}]},
            {"name": "healFixingBand",
             "bounds": [{"field": "timeToNextFixingSecs", "min": 0, "max": band_secs},
                        {"field": "spotBpsOfReference", "min": HEAL_BAND_SPOT[0], "max": HEAL_BAND_SPOT[1]},
                        {"field": "flags", "min": 1, "max": 1}]},
        ],
    }


def excluded(X: np.ndarray, dom: dict) -> np.ndarray:
    """Rows the domain's exclusions refuse (ranges are not checked here)."""
    names = [r["name"] for r in dom["ranges"]]
    out = np.zeros(len(X), dtype=bool)
    for ex in dom["exclusions"]:
        m = np.ones(len(X), dtype=bool)
        for b in ex["bounds"]:
            c = X[:, names.index(b["field"])]
            m &= (c >= b["min"]) & (c <= b["max"])
        out |= m
    return out


def in_ranges(X: np.ndarray, dom: dict) -> np.ndarray:
    m = np.ones(len(X), dtype=bool)
    for i, r in enumerate(dom["ranges"]):
        m &= (X[:, i] >= r["min"]) & (X[:, i] <= r["max"])
    return m


def region_masks(X: np.ndarray) -> dict[str, np.ndarray]:
    clean, ki = X[:, FLAG] == 0, X[:, FLAG] == 1
    near_k = (X[:, SPOT] >= KI_BAND_SPOT[0]) & (X[:, SPOT] <= KI_BAND_SPOT[1])
    near_1 = (X[:, SPOT] >= HEAL_BAND_SPOT[0]) & (X[:, SPOT] <= HEAL_BAND_SPOT[1])
    return {
        "clean, spot 5000-7000": clean & near_k,
        "clean, elsewhere": clean & ~near_k,
        "knocked in, spot 9500-10500": ki & near_1,
        "knocked in, elsewhere": ki & ~near_1,
        "tau <= 1 day": X[:, TAU] <= DAY,
        "tau 1-3 days": (X[:, TAU] > DAY) & (X[:, TAU] <= 3 * DAY),
        "tau > 3 days": X[:, TAU] > 3 * DAY,
        "n = 0 (release before the fixing)": X[:, NE] == 0,
        "n = 1": X[:, NE] == 1,
        "n >= 2": X[:, NE] >= 2,
        "vol <= 2500": X[:, VOL] <= 2500,
        "vol 2501-4999": (X[:, VOL] > 2500) & (X[:, VOL] < 5000),
        "vol 5000-7499": (X[:, VOL] >= 5000) & (X[:, VOL] < 7500),
        "vol >= 7500": X[:, VOL] >= 7500,
        "spot < 5000": X[:, SPOT] < 5000,
        "spot > 10500": X[:, SPOT] > 10500,
    }


# ---------------------------------------------------------------------------
# points
# ---------------------------------------------------------------------------

def _grid(vols, spots, taus, ns) -> np.ndarray:
    g = np.array(np.meshgrid(spots, vols, taus, ns, [0, 1], indexing="ij"), dtype=np.int64)
    return g.reshape(5, -1).T


def _uniform(rng: np.random.Generator, n: int, vols: np.ndarray) -> np.ndarray:
    return np.stack([
        rng.integers(SPOT_RANGE[0], SPOT_RANGE[1] + 1, n),
        rng.choice(vols, n),
        rng.integers(0, WEEK + 1, n),
        rng.integers(0, N_MAX + 1, n),
        rng.integers(0, 2, n),
    ], axis=1).astype(np.int64)


def _spots(offset_coarse: int, offset_fine: int) -> np.ndarray:
    s = set(range(SPOT_RANGE[0] + offset_coarse, SPOT_RANGE[1] + 1, 100))
    s |= set(range(5_500 + offset_fine, 6_501, 20)) | set(range(9_500 + offset_fine, 10_501, 20))
    return np.array(sorted(s), dtype=np.int64)


T_VOLS = [2000, 3000, 4000, 5500, 7000, 9000]
T_TAUS = [0, 3_600, 21_600, 21_601, 43_200, 86_400, 86_401, 90_000, 172_800, 259_200, 345_600, 475_200,
          WEEK - 1, WEEK]
T_NS = [0, 1, 2, 3, 5, 8, 12, 13, 14, 18]
V_VOLS = [2050, 2500, 3500, 4750, 6250, 8000, 8950]
V_TAUS = [1_800, 10_800, 21_700, 64_800, 86_500, 129_600, 216_000, 302_400, 432_000, 561_600, 604_000]
V_NS = [0, 1, 2, 4, 6, 9, 11, 13, 15, 17]
T2_NS = [0, 1, 2, 4, 6, 7, 10, 13, 16, 18]


def _random_vols(rng: np.random.Generator, n: int) -> np.ndarray:
    return rng.integers(VOL_RANGE[0], VOL_RANGE[1] + 1, n)


def test_points() -> np.ndarray:
    rng = np.random.default_rng(SEEDS["T"])
    return np.vstack([_grid(T_VOLS, _spots(0, 0), T_TAUS, T_NS), _uniform(rng, 30_000, _random_vols(rng, 64))])


def test2_points() -> np.ndarray:
    rng = np.random.default_rng(SEEDS["T2"])
    vols = [min(v + 100, VOL_RANGE[1] - 50) for v in T_VOLS]
    return np.vstack([_grid(vols, _spots(30, 6), T_TAUS, T2_NS), _uniform(rng, 30_000, _random_vols(rng, 64))])


def _steep(rng: np.random.Generator, n: int, vols: np.ndarray, near_k: bool) -> np.ndarray:
    """Just outside the fixing bands, near the barrier: where the value is steepest."""
    lo, hi = KI_BAND_SPOT if near_k else HEAL_BAND_SPOT
    edge = rng.choice(BAND_CANDIDATES, n)
    tau = np.minimum(edge + 1 + (rng.random(n) ** 2 * 2 * DAY).astype(np.int64), WEEK)
    low = np.sort(vols)[: max(1, len(vols) // 2)]            # the low half: steeper
    v = np.where(rng.random(n) < 0.6, rng.choice(low, n), rng.choice(vols, n))
    return np.stack([rng.integers(lo, hi + 1, n), v, tau, rng.integers(0, N_MAX + 1, n),
                     np.full(n, 0 if near_k else 1)], axis=1).astype(np.int64)


def val_points() -> np.ndarray:
    rng = np.random.default_rng(SEEDS["V"])
    rs = np.random.default_rng(SEEDS["V_steep"])
    vols = _random_vols(rng, 64)
    return np.vstack([_grid(V_VOLS, _spots(50, 10), V_TAUS, V_NS), _uniform(rng, 30_000, vols),
                      _steep(rs, 40_000, vols, True), _steep(rs, 10_000, vols, False)])


def train_vols() -> np.ndarray:
    rng = np.random.default_rng(SEEDS["train_vols"])
    return np.unique(np.concatenate([_random_vols(rng, 1_500), [VOL_RANGE[0], VOL_RANGE[1]],
                                     np.arange(VOL_RANGE[0], VOL_RANGE[0] + 200, 10),
                                     np.arange(VOL_RANGE[1] - 190, VOL_RANGE[1] + 1, 10)]))


def train_points(n: int, seed: int) -> np.ndarray:
    """55% uniform, 20% clean near k, 10% knocked in near 1, 10% the release next
    to the fixing (n = 0 or 1), 5% the vol ends. Times to the fixing are skewed
    towards 0 in the barrier parts."""
    rng = np.random.default_rng(seed)
    vols = train_vols()
    ends = vols[(vols < VOL_RANGE[0] + 200) | (vols > VOL_RANGE[1] - 200)]
    parts = []
    m = int(n * 0.55)
    parts.append(_uniform(rng, m, vols))

    def barrier(count, lo, hi, flag_p):
        tau = (rng.random(count) ** 2 * WEEK).astype(np.int64)
        return np.stack([rng.integers(lo, hi + 1, count), rng.choice(vols, count), tau,
                         rng.integers(0, N_MAX + 1, count), (rng.random(count) < flag_p).astype(np.int64)], axis=1)

    parts.append(barrier(int(n * 0.20), KI_BAND_SPOT[0] - 300, KI_BAND_SPOT[1] + 300, 0.15))
    parts.append(barrier(int(n * 0.10), HEAL_BAND_SPOT[0] - 300, HEAL_BAND_SPOT[1] + 300, 0.85))
    u = _uniform(rng, int(n * 0.10), vols)
    u[:, NE] = rng.integers(0, 2, len(u))
    parts.append(u)
    e = _uniform(rng, n - sum(len(p) for p in parts), ends)
    parts.append(e)
    X = np.vstack(parts).astype(np.int64)
    return X[rng.permutation(len(X))]


def spread_points() -> np.ndarray:
    """Triplets (vol - d, vol, vol + d) of the same state, rows 3i, 3i + 1, 3i + 2."""
    rng = np.random.default_rng(SEEDS["S"])
    vols = rng.integers(VOL_RANGE[0] + SPREAD_DVOL, VOL_RANGE[1] - SPREAD_DVOL + 1, 100)
    base = np.vstack([_uniform(rng, 7_500, vols), _steep(rng, 7_500, vols, True)])
    out = np.repeat(base, 3, axis=0)
    out[0::3, VOL] -= SPREAD_DVOL
    out[2::3, VOL] += SPREAD_DVOL
    return out


POINTS = {"T": test_points, "T2": test2_points, "V": val_points, "S": spread_points}


def load_labels(path):
    """(X, teacher bps, formula bps) of a labelled file, full or compact; the
    labels must come from the current teacher."""
    z = np.load(path)
    assert str(z["fingerprint"]) == fingerprint(), f"{path}: labelled by another teacher"
    if "X" in z:
        return z["X"].astype(np.int64), z["y"].astype(np.float64), z["fml"]
    name = str(z["set"])
    X = train_points(len(z["y"]), int(z["seed"])) if name == "train" else POINTS[name]()
    assert len(X) == len(z["y"]) and points_hash(X) == str(z["points"]), f"{path}: points differ from the construction"
    return X, z["y"].astype(np.float64), formula_bps(X)


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------

def formula_bps(X: np.ndarray) -> np.ndarray:
    """The formula's principal at the integer inputs, bps (float)."""
    return pf.principal(X[:, SPOT] / 1e4, X[:, VOL] / 1e4, X[:, FLAG] == 1, **pf.P1) * 1e4


def points_hash(X: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(X, dtype=np.int32).tobytes()).hexdigest()


def fingerprint() -> str:
    h = hashlib.sha256()
    for name in ("teacher_perp.py", "perp_formula.py", "teacher_perp_config.json"):
        h.update((HERE / name).read_bytes())
    return h.hexdigest()


_NK = 256


def _init(n_k: int) -> None:
    global _NK
    _NK = n_k


def _label(task):
    import teacher_perp as tp
    vol_bps, rows = task
    t = tp.Teacher(vol_bps / 1e4, tp.load_config(), n_k=_NK)
    return t.value_n(rows[:, SPOT] / 1e4, rows[:, FLAG] == 1, rows[:, TAU] / YEAR_SECS, rows[:, NE]) * 1e4


def label(X: np.ndarray, n_k: int = 256, procs: int | None = None, chunk: int = 3_000) -> np.ndarray:
    """Teacher principal in bps for every row; one fixed point per vol and task."""
    tasks, where = [], []
    for v in np.unique(X[:, VOL]):
        idx = np.flatnonzero(X[:, VOL] == v)
        for lo in range(0, idx.size, chunk):
            where.append(idx[lo:lo + chunk])
            tasks.append((int(v), X[where[-1]]))
    order = np.argsort([-len(w) for w in where])
    y = np.empty(len(X))
    with Pool(procs or min(12, os.cpu_count() or 1), initializer=_init, initargs=(n_k,)) as pool:
        for i, res in zip(order, pool.imap(_label, [tasks[i] for i in order])):
            y[where[i]] = res
    return y


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["T", "T2", "V", "S", "train"])
    ap.add_argument("--compact", help="a labelled npz to reduce to its labels")
    ap.add_argument("--n", type=int, default=1_000_000, help="rows (train only)")
    ap.add_argument("--seed", type=int, default=1, help="train only")
    ap.add_argument("--n-k", type=int, default=256, help="fine grid of the extrapolated teacher")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.compact:
        z = np.load(args.compact)
        np.savez_compressed(args.out, y=z["y"].astype(np.float32), points=points_hash(z["X"]), n_k=z["n_k"],
                            set=z["set"], seed=z["seed"], fingerprint=z["fingerprint"], teacher=z["teacher"])
        print(f"{args.compact} -> {args.out}: {len(z['y'])} labels, {os.path.getsize(args.out) / 1e6:.2f} MB")
        return
    t0 = time.time()
    X = train_points(args.n, args.seed) if args.set == "train" else POINTS[args.set]()
    assert in_ranges(X, domain(BAND_CANDIDATES[-1])).all()
    f = formula_bps(X)
    y = label(X, args.n_k)
    teacher = json.loads((HERE / "teacher_perp_config.json").read_text())["name"]
    np.savez_compressed(args.out, X=X.astype(np.int32), y=y, fml=f, n_k=args.n_k, set=args.set,
                        seed=args.seed if args.set == "train" else SEEDS[args.set],
                        fingerprint=fingerprint(), teacher=teacher)
    r = y - f
    print(f"{args.set}: {len(X)} rows, {len(np.unique(X[:, VOL]))} vols, n_k {args.n_k}; teacher {y.min():.0f}..{y.max():.0f} bps; "
          f"teacher - formula min {r.min():.1f} max {r.max():.1f} mean |.| {np.abs(r).mean():.2f}; "
          f"{time.time() - t0:.0f} s -> {args.out}")


if __name__ == "__main__":
    main()
