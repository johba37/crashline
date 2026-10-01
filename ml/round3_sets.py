"""K3: point sets and teacher labels for a student with vol as a live input.

Product pinned as K2 (ki 6000, ac 10000, coupon 25 bps/week, weekly observations,
26 after strike); volBpsAnnual is now an axis, VOL_LO..VOL_HI. Labels come from
teacher v3 (`ml/teacher.py` with `ml/teacher_config_v3.json`): TSLA's jump shape
with sizes scaled to each label's vol, a fixed 57% jump share of variance
(docs/k3-vol-input.md). K2's frozen teacher is not used here.

Sharding and seeding follow round2_sets: fixed shard size, one seed per shard
derived from the set's label seed, so labels don't depend on worker count.
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

import round2_sets as k2  # noqa: E402  (grid helpers and constants only; never its teacher)

WEEK, DAY = k2.WEEK, k2.DAY
KI, AC, CPN = k2.KI, k2.AC, k2.CPN
SPOT_LO, SPOT_HI, OBS_MAX = k2.SPOT_LO, k2.SPOT_HI, k2.OBS_MAX
SHARD = 8192   # rows per labelling shard (seeds depend on it); 512 (K2) leaves the GPU idle at 2^14-2^15 paths
CUDA_MAX_ELEMS = k2.CUDA_MAX_ELEMS

DOMAIN_PATH = ROOT / "tools" / "domains" / "k3.json"
# PricerInputs field order (pricer_quant.FIELD_NAMES; asserted equal in round3_eval). A local
# copy so point construction runs in the CUDA venv, which has no keccak for pricer_quant.
FIELD_NAMES = ("spotBpsOfInitial", "distToKnockInBps", "volBpsAnnual", "kiBarrierBps", "acBarrierBps",
               "couponBpsPerPeriod", "timeToMaturitySecs", "timeToNextObsSecs", "observationsRemaining", "flags")


def rows(spot, tnext, obs, flag, vol) -> np.ndarray:
    """Raw integer features (N, 10) in PricerInputs order, derived fields exact."""
    spot, tnext, obs, flag, vol = (np.asarray(a, np.int64) for a in (spot, tnext, obs, flag, vol))
    n = np.broadcast(spot, tnext, obs, flag, vol).size
    b = lambda a: np.broadcast_to(a, (n,))
    spot, tnext, obs, flag, vol = map(b, (spot, tnext, obs, flag, vol))
    return np.column_stack([
        spot, spot - KI, vol, np.full(n, KI), np.full(n, AC), np.full(n, CPN),
        tnext + obs * WEEK, tnext, obs, flag,
    ]).astype(np.int64)


# ---------------------------------------------------------------------------
# labelling (teacher v3)
# ---------------------------------------------------------------------------

def _teacher():
    import teacher
    assert pathlib.Path(teacher.__file__).resolve().parent == HERE, \
        f"K3 needs ml/teacher.py (v3), got {teacher.__file__}"
    return teacher


def teacher_config():
    T = _teacher()
    return T.load_config(T.CONFIG_V3_PATH)


def teacher_fingerprint() -> dict:
    """sha256 of the v3 teacher code (numpy and CUDA backends) and its config."""
    return {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
            for name in ("teacher.py", "teacher_config_v3.json", "teacher_torch.py")}


def _to_F(X: np.ndarray) -> dict:
    T = _teacher()
    F = {k: X[:, i].astype(np.float64) for i, k in enumerate(T.FEATURE_KEYS)}
    F["obs"] = X[:, 8].astype(np.int64)
    F["knockedIn"] = X[:, 9].astype(np.int64)
    return F


def _label_shard(args):
    X, paths, seed = args
    import torch
    torch.set_num_threads(1)
    return _teacher().price_batch(_to_F(X), total_paths=paths, seed=seed, cfg=teacher_config())


def _cuda_results(jobs):
    _teacher()
    import teacher_torch
    cfg = teacher_config()
    for X, paths, seed in jobs:
        yield teacher_torch.price_batch(_to_F(X), total_paths=paths, seed=seed, cfg=cfg, device="cuda",
                                        max_elems=CUDA_MAX_ELEMS)


def label(X: np.ndarray, paths: int, label_seed: int, workers: int = 4, round_: int = 0,
          shard_ids: np.ndarray | None = None, backend: str = "cuda", quiet: bool = False):
    """Teacher v3 labels for X. Shard i always gets seed f(label_seed, i, round_)."""
    import multiprocessing as mp
    n = len(X)
    ids = np.arange((n + SHARD - 1) // SHARD) if shard_ids is None else shard_ids
    jobs = [(X[i * SHARD:(i + 1) * SHARD], paths, k2._shard_seed(label_seed, int(i), round_)) for i in ids]
    y = np.empty(n)
    se = np.empty(n)
    t0 = time.time()
    with (mp.get_context("fork").Pool(workers) if backend == "numpy" else contextlib.nullcontext()) as pool:
        results = pool.imap(_label_shard, jobs, chunksize=1) if backend == "numpy" else _cuda_results(jobs)
        for done, (i, (p, s)) in enumerate(zip(ids, results), 1):
            y[i * SHARD:i * SHARD + len(p)] = p
            se[i * SHARD:i * SHARD + len(p)] = s
            if not quiet and done % max(1, len(ids) // 20) == 0:
                print(f"  {done}/{len(ids)} shards ({time.time() - t0:.0f}s)", flush=True)
    return y, se


# ---------------------------------------------------------------------------
# domain and regions
# ---------------------------------------------------------------------------

def load_domain(path=DOMAIN_PATH) -> dict:
    return json.loads(pathlib.Path(path).read_text())


def excluded(X: np.ndarray, domain: dict | None = None) -> np.ndarray:
    """Boolean mask: inside any exclusion band of the K3 domain."""
    domain = domain or load_domain()
    m = np.zeros(len(X), bool)
    for ex in domain["exclusions"]:
        inside = np.ones(len(X), bool)
        for b in ex["bounds"]:
            f = FIELD_NAMES.index(b["field"])
            inside &= (X[:, f] >= b["min"]) & (X[:, f] <= b["max"])
        m |= inside
    return m


VOL_BUCKETS = ((1500, 2999), (3000, 4499), (4500, 5999), (6000, 7499), (7500, 9000), (9001, 15000))


def region_masks(X: np.ndarray) -> dict[str, np.ndarray]:
    m = k2.region_masks(X)
    for lo, hi in VOL_BUCKETS:
        sel = (X[:, 2] >= lo) & (X[:, 2] <= hi)
        if sel.any():
            m[f"vol {lo / 100:.0f}-{hi / 100:.0f}%"] = sel
    return m


def worst(X, err, mask, k=5):
    """round2.worst with the vol of each point."""
    idx = np.flatnonzero(mask)
    order = idx[np.argsort(-np.abs(err[idx]))[:k]]
    return [{"spot": int(X[i, 0]), "vol": int(X[i, 2]), "tNext": int(X[i, 7]), "obs": int(X[i, 8]),
             "knockedIn": int(X[i, 9]), "err": float(err[i])} for i in order]


# ---------------------------------------------------------------------------
# points (every sampler reads the vol range and band edges from the domain)
# ---------------------------------------------------------------------------

def vol_range(domain: dict | None = None) -> tuple[int, int]:
    r = next(x for x in (domain or load_domain())["ranges"] if x["name"] == "volBpsAnnual")
    return int(r["min"]), int(r["max"])


def band_edge(domain: dict, spot, vol, obs, flag) -> np.ndarray:
    """Per row: the largest tNext an exclusion refuses at this (spot, vol, obs,
    knockedIn), whatever its tNext; -1 where no exclusion applies. The first
    certified tNext is edge + 1."""
    X = rows(spot, 0, obs, flag, vol)
    edge = np.full(len(X), -1, np.int64)
    for ex in domain["exclusions"]:
        inside, tmax = np.ones(len(X), bool), None
        for b in ex["bounds"]:
            if b["field"] == "timeToNextObsSecs":
                tmax = int(b["max"])
                continue
            assert b["field"] in ("spotBpsOfInitial", "volBpsAnnual", "observationsRemaining", "flags"), b
            f = FIELD_NAMES.index(b["field"])
            inside &= (X[:, f] >= b["min"]) & (X[:, f] <= b["max"])
        if tmax is not None:
            edge = np.where(inside, np.maximum(edge, tmax), edge)
    return edge


def _vols(n, rng, domain, skew=1.0):
    lo, hi = vol_range(domain)
    return np.rint(lo + (hi - lo) * rng.random(n) ** skew).astype(np.int64)


def _obs_skewed(n, rng, top=OBS_MAX):
    return np.minimum(1 + np.floor(top * rng.random(n) ** 2).astype(np.int64), top)


def _past_edge(edge, rng, near_days):
    """tNext just past the band edge: half within near_days of it, half anywhere up to a week."""
    start = edge + 1
    near = np.minimum(start + near_days * DAY, WEEK)
    t_near = start + np.floor(rng.random(len(edge)) * (near - start + 1)).astype(np.int64)
    t_any = start + np.floor(rng.random(len(edge)) * (WEEK - start + 1)).astype(np.int64)
    return np.where(rng.random(len(edge)) < 0.5, t_near, t_any)


def uniform_points(n: int, rng: np.random.Generator, domain: dict | None = None) -> np.ndarray:
    domain = domain or load_domain()
    return rows(rng.integers(SPOT_LO, SPOT_HI + 1, n), rng.integers(0, WEEK + 1, n),
                rng.integers(1, OBS_MAX + 1, n), rng.integers(0, 2, n), _vols(n, rng, domain))


def steep_points(n: int, rng: np.random.Generator, domain: dict | None = None, barrier: str = "ki") -> np.ndarray:
    """Just past the band edge of one barrier, where the teacher is steepest
    outside the exclusions. ki: not knocked in, spot 5000-7400, obs skewed to 1;
    ac: both flags, spot 9000-11000. Vol skewed low (steeper). Rows whose whole
    week is excluded are redrawn."""
    domain = domain or load_domain()
    out, need = [], n
    while need > 0:
        k = int(need * 1.3) + 16
        vol = _vols(k, rng, domain, skew=1.5)
        if barrier == "ki":
            spot, obs, flag = rng.integers(SPOT_LO, KI + 1401, k), _obs_skewed(k, rng, 10), np.zeros(k, np.int64)
            e = band_edge(domain, np.full(k, KI), vol, obs, flag)
        else:
            spot, obs, flag = rng.integers(AC - 1000, AC + 1001, k), rng.integers(1, OBS_MAX + 1, k), rng.integers(0, 2, k)
            e = band_edge(domain, np.full(k, AC), vol, obs, flag)
        ok = e < WEEK
        X = rows(spot, _past_edge(np.maximum(e, 0), rng, 2), obs, flag, vol)[ok]
        out.append(X[:need])
        need -= len(out[-1])
    return np.vstack(out)


def edge_points(n: int, rng: np.random.Generator, domain: dict | None = None) -> np.ndarray:
    """The corner where the certified teacher is steepest: not knocked in, spot
    5700-6400, obs 1-3, tNext within 12 hours past the knock-in band edge at the
    row's own vol, vol uniform (run a's worst V points all sit here)."""
    domain = domain or load_domain()
    vol = _vols(n, rng, domain)
    obs = rng.integers(1, 4, n)
    e = band_edge(domain, np.full(n, KI), vol, obs, np.zeros(n, np.int64))
    tn = np.minimum(e + 1 + rng.integers(0, 12 * 3600 + 1, n), WEEK)
    return rows(rng.integers(KI - 300, KI + 401, n), tn, obs, 0, vol)


def train_points(n: int, rng: np.random.Generator, domain: dict | None = None) -> np.ndarray:
    """K2's mixture with vol uniform over the domain and each band edge taken at
    the row's own vol: 30% uniform, 10% tNext <= 4 days, 25% just past the ki
    band, 5% near ki anywhere in the week, 20% near ac (just past its band or
    anywhere), 10% exact edges (tNext 0 / edge + 1 / 604800, obs 1, 2, 25, 26)."""
    domain = domain or load_domain()
    parts = [uniform_points(int(n * 0.30), rng, domain)]
    k = int(n * 0.10)
    parts.append(rows(rng.integers(SPOT_LO, SPOT_HI + 1, k), rng.integers(0, 4 * DAY + 1, k),
                      rng.integers(1, OBS_MAX + 1, k), rng.integers(0, 2, k), _vols(k, rng, domain)))
    parts.append(steep_points(int(n * 0.25), rng, domain, "ki"))
    k = int(n * 0.05)
    parts.append(rows(rng.integers(SPOT_LO, KI + 1201, k), rng.integers(0, WEEK + 1, k), _obs_skewed(k, rng),
                      (rng.random(k) < 0.4).astype(np.int64), _vols(k, rng, domain)))
    k = int(n * 0.10)
    parts.append(steep_points(k, rng, domain, "ac"))
    parts.append(rows(rng.integers(AC - 1200, AC + 1201, k), rng.integers(0, WEEK + 1, k),
                      rng.integers(1, OBS_MAX + 1, k), rng.integers(0, 2, k), _vols(k, rng, domain)))
    k = n - sum(len(p) for p in parts)
    vol, obs = _vols(k, rng, domain), np.where(rng.random(k) < 0.5, rng.choice(np.array([1, 2, 25, 26]), k),
                                               rng.integers(1, OBS_MAX + 1, k))
    spot, flag = rng.integers(SPOT_LO, SPOT_HI + 1, k), rng.integers(0, 2, k)
    e = np.maximum(band_edge(domain, spot, vol, obs, flag), DAY) + 1
    tn = np.where(rng.random(k) < 1 / 3, 0, np.where(rng.random(k) < 0.5, np.minimum(e, WEEK), WEEK))
    parts.append(rows(spot, tn, obs, flag, vol))
    X = np.vstack(parts)
    return X[rng.permutation(len(X))]


# ---------------------------------------------------------------------------
# held-out and selection sets (seeds fixed here and used nowhere else)
# ---------------------------------------------------------------------------

TEST_POINT_SEED, TEST_LABEL_SEED = 0x7E53_0001, 0x7E53_0002      # T, the gate
TEST2_POINT_SEED, TEST2_LABEL_SEED = 0x7E54_0001, 0x7E54_0002    # T2, confirmation, labelled once at the end
VAL_POINT_SEED, VAL_LABEL_SEED = 0x7A13_0001, 0x7A13_0002        # V, selection
SPREAD_POINT_SEED, SPREAD_LABEL_SEED = 0x5B3D_0001, 0x5B3D_0002  # S, vol-band spread
# Added after the first T evaluation (GATE FAIL, 71.2 bps at vol exactly 2000; ml/round3_eval_first.log),
# before any model trained on the fix: V's grid never reached the vol endpoints.
TEST3_POINT_SEED, TEST3_LABEL_SEED = 0x7E55_0001, 0x7E55_0002    # T3, held out: the vol endpoints
VEDGE_POINT_SEED, VEDGE_LABEL_SEED = 0x7A14_0001, 0x7A14_0002    # V_edge, selection: the vol endpoints
SPREAD_DVOL = 200                                                # 2 vol points either side


def _grid(domain, vols, spots, obs_vals, base_tn, edge_offsets=(1, 3601)) -> np.ndarray:
    """Grid over vol x obs x spot x tNext x knockedIn. tNext = base values plus,
    at each (vol, obs), each band's first certified tNext (+ offsets)."""
    out = []
    spots = np.unique(spots)
    for v in vols:
        for o in obs_vals:
            e = set()
            for b in (KI, AC):
                for f in (0, 1):
                    ed = int(band_edge(domain, [b], [v], [o], [f])[0])
                    if 0 <= ed < WEEK:
                        e |= {min(ed + d, WEEK) for d in edge_offsets}
            tn = np.array(sorted(set(base_tn) | e))
            s, t, f = np.meshgrid(spots, tn, (0, 1), indexing="ij")
            out.append(rows(s.ravel(), t.ravel(), o, f.ravel(), v))
    return np.vstack(out)


def _spots(offset_coarse=0, offset_fine=0):
    return np.concatenate([[SPOT_LO, SPOT_HI], np.arange(SPOT_LO + offset_coarse, SPOT_HI + 1, 100),
                           np.arange(KI - 500 + offset_fine, KI + 501, 20), np.arange(AC - 500 + offset_fine, AC + 501, 20)])


def _test_vols(domain, shift=0):
    lo, hi = vol_range(domain)
    return sorted({min(max(v + shift, lo), hi) for v in (lo, 3000, 4000, 5500, 7000, hi)})


T_OBS = (1, 2, 3, 5, 9, 13, 19, 26)
T_TN = (0, 3600, 43200, 86400, 172800, 345600, 604800)


def test_points(domain: dict | None = None) -> np.ndarray:
    """T: grid (6 vols incl. both ends, spot every 100 bps and every 20 within
    +-500 of ki and ac, 8 obs, 7 tNext values plus each band's first certified
    tNext and an hour after it, both knockedIn) plus 30,000 uniform points."""
    domain = domain or load_domain()
    g = _grid(domain, _test_vols(domain), _spots(), T_OBS, T_TN)
    return np.vstack([g, uniform_points(30_000, np.random.default_rng(TEST_POINT_SEED), domain)])


def val_points(domain: dict | None = None) -> np.ndarray:
    """V: disjoint values (vols between T's, spots offset 50 / 10 bps, other obs
    and tNext), 30,000 uniform, 40,000 steep-ki and 10,000 steep-ac points."""
    domain = domain or load_domain()
    lo, hi = vol_range(domain)
    vols = sorted({int(v) for v in np.linspace(lo, hi, 8)[1:-1]} | {lo + 100})
    g = _grid(domain, vols, _spots(50, 10), (1, 2, 4, 7, 11, 16, 23, 25),
              (1800, 21600, 64800, 129600, 259200, 475200, 604800), edge_offsets=(1, 1801))
    rng = np.random.default_rng(VAL_POINT_SEED)
    X = np.vstack([g, uniform_points(30_000, rng, domain), steep_points(40_000, rng, domain, "ki"),
                   steep_points(10_000, rng, domain, "ac")])
    t = set(map(tuple, test_points(domain).tolist()))
    return X[np.array([tuple(r) not in t for r in X.tolist()])]


def test2_points(domain: dict | None = None) -> np.ndarray:
    """T2, fixed before any K3 model exists and labelled once at the end: T's
    construction with vols +100 bps (clipped to the range), spots +30 / +6 bps,
    a new uniform seed; no point shared with T or V."""
    domain = domain or load_domain()
    g = _grid(domain, _test_vols(domain, 100), _spots(30, 6), T_OBS, T_TN)
    X = np.vstack([g, uniform_points(30_000, np.random.default_rng(TEST2_POINT_SEED), domain)])
    seen = set(map(tuple, test_points(domain).tolist())) | set(map(tuple, val_points(domain).tolist()))
    return X[np.array([tuple(r) not in seen for r in X.tolist()])]


def _end_uniform(n, rng, domain, width=100):
    """Uniform points with vol within `width` bps of either end of the range, half exactly at an end."""
    lo, hi = vol_range(domain)
    X = uniform_points(n, rng, domain)
    at_lo = rng.random(n) < 0.6
    near = np.where(at_lo, lo + rng.integers(0, width + 1, n), hi - rng.integers(0, width + 1, n))
    X[:, 2] = np.where(rng.random(n) < 0.5, np.where(at_lo, lo, hi), near)
    return X


def test3_points(domain: dict | None = None) -> np.ndarray:
    """T3, held out, fixed after the first T evaluation and before any model trained on
    the fix: T's construction at the vol endpoints only (both ends), spots +40 / +4 bps,
    plus 10,000 uniform points within 1 vol point of an end; no point shared with T, T2, V."""
    domain = domain or load_domain()
    lo, hi = vol_range(domain)
    g = _grid(domain, (lo, hi), _spots(40, 4), T_OBS, T_TN)
    X = np.vstack([g, _end_uniform(10_000, np.random.default_rng(TEST3_POINT_SEED), domain)])
    seen = set(map(tuple, test_points(domain).tolist())) | set(map(tuple, val_points(domain).tolist())) \
        | set(map(tuple, test2_points(domain).tolist()))
    return X[np.array([tuple(r) not in seen for r in X.tolist()])]


def val_edge_points(domain: dict | None = None) -> np.ndarray:
    """V_edge, selection only, used with V: the vol endpoints V's grid missed. Grid at
    both ends with spots +60 / +12, V's obs and tNext values, 10,000 near-end uniform
    and 10,000 steep-ki points at vol exactly 2000; disjoint from T, T2, T3."""
    domain = domain or load_domain()
    lo, hi = vol_range(domain)
    g = _grid(domain, (lo, hi), _spots(60, 12), (1, 2, 4, 7, 11, 16, 23, 25),
              (1800, 21600, 64800, 129600, 259200, 475200, 604800), edge_offsets=(1, 1801))
    rng = np.random.default_rng(VEDGE_POINT_SEED)
    st = steep_points(10_000, rng, {**domain, "ranges": [r if r["name"] != "volBpsAnnual" else
                                                          {**r, "max": lo} for r in domain["ranges"]]}, "ki")
    X = np.vstack([g, _end_uniform(10_000, rng, domain), st])
    seen = set(map(tuple, test_points(domain).tolist())) | set(map(tuple, test2_points(domain).tolist())) \
        | set(map(tuple, test3_points(domain).tolist()))
    return X[np.array([tuple(r) not in seen for r in X.tolist()])]


def ends_points(n: int, rng: np.random.Generator, domain: dict | None = None) -> np.ndarray:
    """Training: the mixture restricted to the ends of the vol range (70% within 2 vol
    points of the floor, 30% of the ceiling), a third of those rows exactly at the end."""
    domain = domain or load_domain()
    lo, hi = vol_range(domain)
    def narrow(a, b):
        return {**domain, "ranges": [r if r["name"] != "volBpsAnnual" else {**r, "min": a, "max": b}
                                     for r in domain["ranges"]]}
    k = int(n * 0.7)
    X = np.vstack([train_points(k, rng, narrow(lo, lo + 200)), train_points(n - k, rng, narrow(hi - 200, hi))])
    exact = rng.random(len(X)) < 1 / 3
    X[exact, 2] = np.where(X[exact, 2] <= (lo + hi) // 2, lo, hi)
    return X[rng.permutation(len(X))]


def spread_points(domain: dict | None = None, n: int = 15_000) -> np.ndarray:
    """S: n states, each at vol - 200, vol, vol + 200 (rows 3i, 3i+1, 3i+2), all
    three certified; half uniform, half steep-ki. For the Desk's vol-band quote
    bid = P(vol + 2 pts), ask = P(vol - 2 pts)."""
    domain = domain or load_domain()
    lo, hi = vol_range(domain)
    rng = np.random.default_rng(SPREAD_POINT_SEED)
    out, need = [], n
    while need > 0:
        k = 2 * need + 64
        base = np.vstack([uniform_points(k // 2, rng, domain), steep_points(k - k // 2, rng, domain, "ki")])
        base[:, 2] = rng.integers(lo + SPREAD_DVOL, hi - SPREAD_DVOL + 1, len(base))
        trip = np.repeat(base, 3, axis=0)
        trip[:, 2] += np.tile([-SPREAD_DVOL, 0, SPREAD_DVOL], len(base))
        ok = ~excluded(trip, domain).reshape(-1, 3).any(axis=1)
        sel = trip.reshape(-1, 3, 10)[ok][:need]
        out.append(sel.reshape(-1, 10))
        need -= len(sel)
    return np.vstack(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _device_name(backend: str) -> str:
    if backend == "numpy":
        return "cpu"
    import torch
    return torch.cuda.get_device_name(0)


def build(name: str, n: int, seed: int):
    """(X, label seed) for a set name."""
    if name == "T":
        return test_points(), TEST_LABEL_SEED
    if name == "T2":
        return test2_points(), TEST2_LABEL_SEED
    if name == "V":
        return val_points(), VAL_LABEL_SEED
    if name == "S":
        return spread_points(), SPREAD_LABEL_SEED
    if name == "T3":
        return test3_points(), TEST3_LABEL_SEED
    if name == "V_edge":
        return val_edge_points(), VEDGE_LABEL_SEED
    tag = {"train": 0x7EA3, "steep_ki": 0x57E3, "steep_ac": 0x57E4, "edge_ki": 0xED9E, "ends": 0xE7D5,
           "pilot_val": 0x9170}[name]
    rng = np.random.default_rng([tag, seed])
    lseed = int(np.random.SeedSequence([tag, seed, 1]).generate_state(1)[0])
    if name == "train":
        return train_points(n, rng), lseed
    if name == "edge_ki":
        return edge_points(n, rng), lseed
    if name == "ends":
        return ends_points(n, rng), lseed
    if name == "pilot_val":   # small selection set for the feasibility pilot: uniform + steep
        return np.vstack([uniform_points(n // 2, rng), steep_points(n - n // 2, rng, None, "ki")]), lseed
    return steep_points(n, rng, None, name[-2:]), lseed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=("T", "T2", "T3", "V", "V_edge", "S", "train", "steep_ki", "steep_ac", "edge_ki",
                                      "ends", "pilot_val"), required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--paths", type=int, default=2**18)
    ap.add_argument("--max-se", type=float, default=None,
                    help="re-label every shard holding a row above this stderr at 4x paths until none")
    ap.add_argument("--n", type=int, default=400_000, help="train/steep/pilot_val: number of points")
    ap.add_argument("--seed", type=int, default=1, help="train/steep/pilot_val: point + label seed")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--backend", choices=("numpy", "cuda"), default="cuda")
    args = ap.parse_args()
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    X, lseed = build(args.set, args.n, args.seed)
    fp = teacher_fingerprint()
    dom_sha = hashlib.sha256(DOMAIN_PATH.read_bytes()).hexdigest()
    print(f"set {args.set}: {len(X)} points, {args.paths} paths, label seed {lseed}, teacher {fp}, domain {dom_sha}",
          flush=True)
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
                                         "max_se_target": args.max_se, "topup_rounds": rnd, "n": len(X),
                                         "seed": args.seed if args.set not in ("T", "T2", "T3", "V", "V_edge", "S")
                                         else None,
                                         "teacher": fp, "domain_sha256": dom_sha, "backend": args.backend,
                                         "device": _device_name(args.backend), "shard": SHARD,
                                         "cuda_max_elems": CUDA_MAX_ELEMS, "seconds": round(dt),
                                         "argv": sys.argv[1:]}))
    print(f"wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
