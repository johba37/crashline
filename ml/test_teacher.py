"""Checks for the jump teacher (ml/teacher.py) and its torch backend: v2 (a)-(h), v3 (i)-(o).

    tools/.venv/bin/python ml/test_teacher.py [--cuda-python PATH] [--skip-g] [--only abc...]

One PASS/FAIL line per check, exit status 1 if any check fails. Seeds are fixed
here and used nowhere else. Tolerances "3 se" use the Monte Carlo standard
error of the quantities compared (independent streams: hypot of the two).

(a) lambda = 0 is bit-identical to the K1 GBM teacher (ml/reference/teacher_gbm_k1.py)
(b) martingale with jumps: E[S_T] = S0 e^{rT}; and Var[log S_T] = vol^2 T (total-vol convention)
(c) European put under the pinned jumps vs Merton's closed-form series, 3 strikes
(d) smoothed last step vs brute-force last step, 24 note states
(e) determinism
(f) smoke checks: autocall at par, knocked-in martingale (GBM, as in K1) and their
    jump versions (knocked-in, never-autocall note vs the closed form)
(g) torch backend (CUDA when available) vs numpy teacher on 64 note states
(h) barrier equality at tNext = 0: spot == ki does not knock in, spot == ac autocalls

Teacher v3 (vol-scaled jump sizes, ml/teacher_config_v3.json):
(i) the v2 code path is unchanged: numpy and torch are bit-identical to the frozen
    K2 teacher (ml/reference/k2/, byte-identical to the files that labelled K2)
(j) v3 at vol s equals the fixed-jump teacher with sizes scaled to s (same seed),
    six vols 15-120%: the per-label scaling is exactly that
(k) martingale and total variance vol^2 T under v3 at 20% / 55% / 90%
(l) European puts (26 weeks, 3 strikes) and the knocked-in never-autocall note vs
    Merton closed forms under v3 at 25% and 85%
(m) smoothed vs brute-force last step under v3, vols 20-90% mixed in one batch
(n) torch backend vs numpy teacher under v3, 64 states with mixed vols
(o) barrier equality at tNext = 0 under v3
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import math
import os
import subprocess
import sys
import tempfile
import time

import numpy as np
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import teacher as T  # noqa: E402

REF_PATH = os.path.join(HERE, "reference", "teacher_gbm_k1.py")
K2_DIR = os.path.join(HERE, "reference", "k2")   # frozen K2 teacher (v2), byte-identical copies
K2_SHA256 = {"teacher.py": "3e3ff8c33c91887544f6e959beaade8b780db505e6d171e9eadfb31c0f140910",
             "teacher_torch.py": "242f89d3c7360e5935f2ba7c7165867707906a493f1f8c412f891419ae1bd2ba",
             "teacher_config.json": "463782fbc14f5f6d53d5dd44a75fd4ad0dd4305f9e31f2d114a8be10fd020638"}
REF_SHA256 = "af8be22db1856ed2beb5d07b249c5b35f10c048f42652b3d7fe0d37c993a9fab"  # ml/teacher.py @ 42d2fe4
DEFAULT_CUDA_PY = "/opt/ai/cache/venv-cuda/bin/python"

RESULTS: list[tuple[str, bool]] = []


def report(name: str, ok: bool, detail: str):
    RESULTS.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)


def load_reference():
    spec = importlib.util.spec_from_file_location("teacher_gbm_k1", REF_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- closed forms

def bs_put(S, K, r, sig, t):
    sq = sig * math.sqrt(t)
    d1 = (math.log(S / K) + (r + 0.5 * sig**2) * t) / sq
    d2 = d1 - sq
    return K * math.exp(-r * t) * stats.norm.cdf(-d2) - S * stats.norm.cdf(-d1)


def merton_put(S, K, t, vol_total, cfg: T.TeacherConfig, nmax=400):
    """Merton (1976) series: sum_n Pois(n; lam' t) BS_put(S, K, r_n, sig_n, t),
    lam' = lam (1 + kappa), r_n = r - lam kappa + n ln(1 + kappa)/t,
    sig_n^2 = sd^2 + n sigJ^2 / t."""
    sd = float(cfg.diffusion_vol(np.array([vol_total]))[0])
    lam, kap, r = cfg.lambda_year, cfg.kappa, cfg.r
    lp = lam * (1 + kap) * t
    total, wsum = 0.0, 0.0
    for n in range(nmax):
        w = math.exp(-lp + (n * math.log(lp) if n else 0.0) - math.lgamma(n + 1))
        wsum += w
        rn = r - lam * kap + n * math.log1p(kap) / t
        sn = math.sqrt(sd**2 + n * cfg.sigma_j**2 / t)
        total += w * bs_put(S, K, rn, sn, t)
    assert 1 - wsum < 1e-12
    return total


def merton_call(S, K, t, vol_total, cfg):
    return merton_put(S, K, t, vol_total, cfg) + S - K * math.exp(-cfg.r * t)


def bs_call(S, K, r, sig, t):
    return bs_put(S, K, r, sig, t) + S - K * math.exp(-r * t)


# ---------------------------------------------------------------- checks

def check_a():
    with open(REF_PATH, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    ref = load_reference()
    rng = np.random.default_rng(20260930)
    spots = [5000, 5500, 5995, 6005, 7000, 8500, 9500, 9995, 10005, 11000, 12000]
    rows = []
    for s in spots:
        for n in (1, 2, 13, 26):
            for tn in (0, 3600, 86400, 302400, 604800):
                for kin in (0, 1):
                    rows.append((s, n, tn, kin))
    rows = np.array(rows, dtype=np.float64)
    G = len(rows)
    vol = rng.choice([3000.0, 5500.0, 8000.0], G)
    ki = rng.choice([5200.0, 6000.0], G)       # no spot sits exactly on a barrier (see check (h))
    cpn = rng.choice([0.0, 25.0, 100.0], G)
    F = T.features(rows[:, 0], vol, rows[:, 2], rows[:, 1].astype(np.int64), rows[:, 3].astype(np.int64),
                   ki=ki, ac=10000, coupon=cpn)
    worst = 0.0
    same = sha == REF_SHA256
    for paths, seed in ((2**12, 1), (2**15, 2)):
        for smooth in (True,):
            p_new, s_new = T.price_batch(F, paths, seed, smooth=smooth, cfg=T.GBM)
            p_ref, s_ref = ref.price_batch(F, paths, seed, smooth=smooth)
            same &= np.array_equal(p_new, p_ref) and np.array_equal(s_new, s_ref)
            worst = max(worst, float(np.max(np.abs(p_new - p_ref))))
    report("(a) lambda=0 bit-identical to K1 GBM teacher", same,
           f"{G} states x 2 path counts (2^12, 2^15), smoothed; reference sha256 "
           f"{'ok' if sha == REF_SHA256 else 'MISMATCH'}; max |diff| {worst:.3g} bps")


def check_b(cfg):
    ok_all = True
    parts = []
    for i, steps in enumerate(([604800], [86400] + [604800] * 25, [3600] + [604800] * 52)):
        Ty = sum(steps) / T.YEAR_SECS
        lx = T.simulate_log_spot(2**21, 0.55, steps, seed=100 + i, cfg=cfg)
        pm = 0.5 * (np.exp(lx[0]) + np.exp(lx[1]))
        mean, se = pm.mean(), pm.std(ddof=1) / math.sqrt(pm.size)
        want = math.exp(cfg.r * Ty)
        z = (mean - want) / se
        ok = abs(z) <= 3
        # total variance: Var[log S_T] = vol^2 T, via pair-level estimator
        # (legs of a pair share jump counts, so use the pair means of the
        # centred squares; stderr from their spread)
        mu = lx.mean()
        sq = 0.5 * ((lx[0] - mu) ** 2 + (lx[1] - mu) ** 2)
        var, var_se = sq.mean(), sq.std(ddof=1) / math.sqrt(sq.size)
        zv = (var - 0.55**2 * Ty) / var_se
        okv = abs(zv) <= 3
        ok_all &= ok and okv
        parts.append(f"T={Ty:.4f}y E[S_T]={mean:.6f} vs {want:.6f} (z {z:+.2f}), "
                     f"Var[logS]={var:.5f} vs {0.55**2 * Ty:.5f} (z {zv:+.2f})")
    report("(b) martingale with jumps (and total variance = vol^2 T)", ok_all, "; ".join(parts))


def check_c(cfg):
    steps = [604800] * 26
    t = sum(steps) / T.YEAR_SECS
    lx = T.simulate_log_spot(2**21, 0.55, steps, seed=200, cfg=cfg)
    ok_all = True
    parts = []
    for K in (0.6, 1.0, 1.2):
        pay = np.maximum(K - np.exp(lx), 0.0) * math.exp(-cfg.r * t)
        pm = 0.5 * (pay[0] + pay[1])
        mc, se = pm.mean() * 1e4, pm.std(ddof=1) / math.sqrt(pm.size) * 1e4
        cf = merton_put(1.0, K, t, 0.55, cfg) * 1e4
        gbm = bs_put(1.0, K, cfg.r, 0.55, t) * 1e4
        z = (mc - cf) / se
        ok_all &= abs(z) <= 3
        parts.append(f"K={K}: MC {mc:.2f}+-{se:.2f} vs Merton {cf:.2f} (z {z:+.2f}; BS same total vol {gbm:.2f})")
    report("(c) European put vs Merton closed form", ok_all, "; ".join(parts))


def check_d(cfg):
    rows = []
    for s in (5500, 7000, 9000):
        for kin in (0, 1):
            for n, tn in ((1, 0), (1, 302400), (4, 86400), (26, 3600)):
                rows.append((s, n, tn, kin))
    rows = np.array(rows, dtype=np.float64)
    F = T.features(rows[:, 0], 5500, rows[:, 2], rows[:, 1].astype(np.int64), rows[:, 3].astype(np.int64))
    p1, s1 = T.price_batch(F, 2**17, seed=301, smooth=True, cfg=cfg)
    p2, s2 = T.price_batch(F, 2**17, seed=302, smooth=False, cfg=cfg)
    z = (p1 - p2) / np.hypot(s1, s2)
    ok = bool(np.all(np.abs(z) <= 3))
    worst = int(np.argmax(np.abs(z)))
    report("(d) smoothing vs brute-force last step", ok,
           f"{len(rows)} states, 2^17 paths; max |z| {np.max(np.abs(z)):.2f} at spot {rows[worst, 0]:.0f} "
           f"obs {rows[worst, 1]:.0f} tNext {rows[worst, 2]:.0f} ki {rows[worst, 3]:.0f} "
           f"({p1[worst]:.1f} vs {p2[worst]:.1f}); mean z^2 {np.mean(z**2):.2f}; "
           f"stderr smoothed/brute mean {s1.mean():.2f}/{s2.mean():.2f} bps")


def check_e(cfg):
    F = T.features([5800, 9000, 10500, 7000], 5500, [86400, 0, 3600, 604800], [26, 3, 1, 13], [0, 1, 0, 1])
    a = T.price_batch(F, 2**13, seed=5, cfg=cfg)
    b = T.price_batch(F, 2**13, seed=5, cfg=cfg)
    c = T.price_batch(F, 2**13, seed=6, cfg=cfg)
    ok = np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1]) and not np.array_equal(a[0], c[0])
    report("(e) determinism", ok, "same seed -> identical prices and stderrs; other seed differs")


def check_f(cfg):
    ok_all = True
    parts = []
    W, Y = T.WEEK_SECS, T.YEAR_SECS
    for c in (T.GBM, cfg):
        # 1. Deep above AC at an observation: immediate autocall at par, no coupon.
        p, se = T.price(30000, 5500, 26 * W, 0, 26, 0, total_paths=2**12, cfg=c)
        ok = abs(p - 10000.0) <= 3 * se + 1e-9
        ok_all &= ok
        parts.append(f"{c.name} autocall {p:.4f} (want 10000)")
    # 2. K1 smoke: knocked in, tiny vol, spot 0.5 (GBM only: vol 15% < the jump vol).
    Tm = 26 * W
    p, se = T.price(5000, 1500, Tm, 0, 26, 1, total_paths=2**12, cfg=T.GBM)
    want = 5000.0 + np.exp(-T.R_FREE * Tm / Y) * 25 * 26
    ok = abs(p - want) <= 3 * se + 30.0
    ok_all &= ok
    parts.append(f"gbm knocked-in low-vol {p:.1f} vs {want:.1f}")
    # 3. Knocked in, never autocalls (ac far away): value = 1e4 (S0 - e^{-rT} E[(S_T-1)^+])
    #    + PV coupon, closed form; exercises every step, jumps and the smoothed last step.
    for c, spot in ((T.GBM, 7000), (cfg, 7000), (cfg, 9000)):
        F = T.features([spot], 5500, 0, 26, 1, ac=1e9)
        p, se = T.price_batch(F, 2**18, seed=400 + spot, cfg=c)
        t = Tm / Y
        call = bs_call(spot / 1e4, 1.0, c.r, 0.55, t) if not c.jumps else merton_call(spot / 1e4, 1.0, t, 0.55, c)
        want = 1e4 * (spot / 1e4 - call) + math.exp(-c.r * t) * 25 * 26
        z = (p[0] - want) / se[0]
        ok_all &= abs(z) <= 3
        parts.append(f"{c.name} knocked-in no-autocall spot {spot}: {p[0]:.2f}+-{se[0]:.2f} vs {want:.2f} (z {z:+.2f})")
    report("(f) smoke checks", ok_all, "; ".join(parts))


def g_states():
    rng = np.random.default_rng(700)
    G = 64
    spot = rng.integers(5000, 12001, G).astype(np.float64)
    obs = rng.integers(1, 27, G)
    tn = rng.choice([0, 3600, 43200, 86400, 90000, 172800, 345600, 604800], G).astype(np.float64)
    kin = rng.integers(0, 2, G)
    return T.features(spot, 5500, tn, obs, kin)


G_WORKER = r"""
import sys, numpy as np
sys.path.insert(0, sys.argv[1])
import teacher as T, teacher_torch as TT, torch
F = dict(np.load(sys.argv[2]))
p, se = TT.price_batch(F, 2**18, seed=701, device="cuda")
np.savez(sys.argv[3], p=p, se=se, dev=np.array(torch.cuda.get_device_name()))
"""


def check_g(cfg, cuda_python):
    F = g_states()
    t0 = time.time()
    p_np, s_np = T.price_batch(F, 2**18, seed=702, cfg=cfg)
    t_np = time.time() - t0
    backend = None
    try:
        import torch
        if torch.cuda.is_available():
            import teacher_torch as TT
            t0 = time.time()
            p_t, s_t = TT.price_batch(F, 2**18, seed=701, device="cuda")
            backend = f"cuda in-process ({torch.cuda.get_device_name()})"
    except Exception:  # noqa: BLE001
        pass
    if backend is None and cuda_python and os.path.exists(cuda_python):
        with tempfile.TemporaryDirectory() as td:
            fin, fout = os.path.join(td, "F.npz"), os.path.join(td, "out.npz")
            np.savez(fin, **F)
            t0 = time.time()
            subprocess.run([cuda_python, "-c", G_WORKER, HERE, fin, fout], check=True)
            o = np.load(fout)
            p_t, s_t = o["p"], o["se"]
            backend = f"cuda via {cuda_python} ({o['dev']})"
    if backend is None:
        import teacher_torch as TT
        t0 = time.time()
        p_t, s_t = TT.price_batch(F, 2**18, seed=701, device="cpu")
        backend = "torch cpu (no CUDA available)"
    t_t = time.time() - t0
    d = p_t - p_np
    e = np.hypot(s_t, s_np)
    det = e == 0                     # deterministic states (e.g. immediate autocall): exact up to rounding
    z = d[~det] / e[~det]
    ok = bool(np.all(np.abs(z) <= 3)) and bool(np.all(np.abs(d[det]) <= 1e-6))
    report("(g) torch backend vs numpy teacher", ok,
           f"{len(d)} states ({int(det.sum())} deterministic, |diff| {np.max(np.abs(d[det]), initial=0):.1e}), "
           f"2^18 paths, {backend}; max |z| {np.max(np.abs(z)):.2f}, mean z^2 "
           f"{np.mean(z**2):.2f}, max |diff| {np.max(np.abs(d)):.2f} bps; "
           f"time numpy {t_np:.1f}s / torch {t_t:.1f}s")


def check_h(cfg, name="(h) barrier equality at tNext=0"):
    ok_all = True
    parts = []
    W, Y = T.WEEK_SECS, T.YEAR_SECS
    for c in (T.GBM, cfg):
        for ki in (5500.0, 6000.0, 7000.0):
            # obs 1, tNext 0: the only observation is now; not knocked in -> par + coupon at maturity
            p, _ = T.price(ki, 5500, W, 0, 1, 0, kiBarrierBps=ki, total_paths=2**10, cfg=c)
            want = math.exp(-c.r * W / Y) * (1e4 + 25.0)
            ok = abs(p - want) < 1e-6
            ok_all &= ok
            if not ok:
                parts.append(f"{c.name} spot==ki {ki:.0f}: {p:.4f} vs {want:.4f}")
        for ac in (9500.0, 10000.0, 10500.0):
            p, _ = T.price(ac, 5500, 26 * W, 0, 26, 0, acBarrierBps=ac, total_paths=2**10, cfg=c)
            ok = abs(p - 1e4) < 1e-9
            ok_all &= ok
            if not ok:
                parts.append(f"{c.name} spot==ac {ac:.0f}: {p:.4f} vs 10000")
    report(name, ok_all,
           "spot == ki (5500/6000/7000) stays un-knocked, spot == ac (9500/10000/10500) autocalls, GBM and jumps"
           + ("; " + "; ".join(parts) if parts else ""))


# ---------------------------------------------------------------- teacher v3

TORCH_WORKER = r"""
import sys, numpy as np
sys.path.insert(0, sys.argv[1])
import teacher as T, teacher_torch as TT, torch
assert T.__file__.startswith(sys.argv[1]) and TT.__file__.startswith(sys.argv[1])
F = dict(np.load(sys.argv[2]))
cfg = T.load_config(sys.argv[5]) if sys.argv[5] != "-" else None
p, se = TT.price_batch(F, int(sys.argv[4]), seed=int(sys.argv[6]), cfg=cfg, device="cuda")
np.savez(sys.argv[3], p=p, se=se, dev=np.array(torch.cuda.get_device_name()))
"""


def torch_prices(teacher_dir, F, paths, seed, cfg_path, cuda_python):
    """teacher_torch.price_batch from `teacher_dir` in a fresh CUDA process (so the frozen
    and the current modules never share a process). Returns (price, se, device)."""
    with tempfile.TemporaryDirectory() as td:
        fin, fout = os.path.join(td, "F.npz"), os.path.join(td, "out.npz")
        np.savez(fin, **F)
        subprocess.run([cuda_python, "-c", TORCH_WORKER, teacher_dir, fin, fout, str(paths),
                        cfg_path or "-", str(seed)], check=True)
        o = np.load(fout)
        return o["p"], o["se"], str(o["dev"])


def load_k2():
    spec = importlib.util.spec_from_file_location("teacher_k2", os.path.join(K2_DIR, "teacher.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["teacher_k2"] = mod    # dataclasses resolve the defining module by name
    spec.loader.exec_module(mod)
    return mod


def v3_states(n, seed, vols):
    rng = np.random.default_rng(seed)
    spot = rng.integers(5000, 12001, n).astype(np.float64)
    obs = rng.integers(1, 27, n)
    tn = rng.choice([0, 3600, 43200, 86400, 90000, 172800, 345600, 604800], n).astype(np.float64)
    kin = rng.integers(0, 2, n)
    return T.features(spot, rng.choice(np.asarray(vols, np.float64), n), tn, obs, kin)


def scaled_cfg(cfg3, vol):
    """The fixed-jump (v2-style) config whose sizes are v3's at this total vol."""
    js = vol / cfg3.vol_ref
    return T.TeacherConfig(name=f"{cfg3.name}@{vol:.4f}", r=cfg3.r, lambda_year=cfg3.lambda_year,
                           mu_j=cfg3.mu_j * js, sigma_j=cfg3.sigma_j * js)


def check_i(cuda_python):
    sha_ok = all(hashlib.sha256(open(os.path.join(K2_DIR, f), "rb").read()).hexdigest() == h
                 for f, h in K2_SHA256.items())
    k2 = load_k2()
    cfg_now, cfg_k2 = T.load_config(), k2.load_config(os.path.join(K2_DIR, "teacher_config.json"))
    F = v3_states(96, 800, [5000.0, 5500.0, 8000.0])
    same, parts = sha_ok, []
    for paths, seed in ((2**12, 801), (2**15, 802)):
        for smooth in (True, False):
            a = T.price_batch(F, paths, seed, smooth=smooth, cfg=cfg_now)
            b = k2.price_batch(F, paths, seed, smooth=smooth, cfg=cfg_k2)
            same &= np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])
    parts.append(f"numpy: 96 states x 2 path counts x smoothed/brute, pinned v2 config, "
                 f"{'bit-identical' if same else 'DIFFERENT'}")
    if cuda_python and os.path.exists(cuda_python):
        pa, sa, dev = torch_prices(HERE, F, 2**15, 803, None, cuda_python)
        pb, sb, _ = torch_prices(K2_DIR, F, 2**15, 803, None, cuda_python)
        tsame = np.array_equal(pa, pb) and np.array_equal(sa, sb)
        same &= tsame
        parts.append(f"torch ({dev}): 96 states, 2^15 paths, {'bit-identical' if tsame else 'DIFFERENT'}")
    else:
        parts.append("torch: skipped (no CUDA python)")
    report("(i) v2 path bit-identical to the frozen K2 teacher", same,
           f"frozen copies sha256 {'ok' if sha_ok else 'MISMATCH'}; " + "; ".join(parts))


def check_j(cfg3):
    worst, parts = 0.0, []
    for vol in (1500.0, 2000.0, 3500.0, 5500.0, 7500.0, 12000.0):
        F = v3_states(48, 900 + int(vol), [vol])
        a, sa = T.price_batch(F, 2**14, 901, cfg=cfg3)
        b, sb = T.price_batch(F, 2**14, 901, cfg=scaled_cfg(cfg3, vol / 1e4))
        d = float(np.max(np.abs(a - b)))
        worst = max(worst, d)
        parts.append(f"{vol / 100:.0f}%: {d:.1e}")
    report("(j) v3 at vol s == fixed jumps scaled to s, same seed", worst <= 0.01,
           f"48 states per vol, 2^14 paths; max |diff| bps " + ", ".join(parts)
           + " (float rounding of the scaled sizes only)")


def check_k(cfg3):
    ok_all, parts = True, []
    steps = [3600] + [604800] * 52
    Ty = sum(steps) / T.YEAR_SECS
    for i, vol in enumerate((0.20, 0.55, 0.90)):
        lx = T.simulate_log_spot(2**21, vol, steps, seed=1000 + i, cfg=cfg3)
        pm = 0.5 * (np.exp(lx[0]) + np.exp(lx[1]))
        mean, se = pm.mean(), pm.std(ddof=1) / math.sqrt(pm.size)
        z = (mean - math.exp(cfg3.r * Ty)) / se
        mu = lx.mean()
        sq = 0.5 * ((lx[0] - mu) ** 2 + (lx[1] - mu) ** 2)
        zv = (sq.mean() - vol**2 * Ty) / (sq.std(ddof=1) / math.sqrt(sq.size))
        ok_all &= abs(z) <= 3 and abs(zv) <= 3
        parts.append(f"vol {vol:.0%}: E[S_T] z {z:+.2f}, Var[logS] {sq.mean():.5f} vs {vol**2 * Ty:.5f} (z {zv:+.2f})")
    report("(k) v3 martingale and total variance", ok_all, f"T={Ty:.4f}y, 2^21 paths; " + "; ".join(parts))


def check_l(cfg3):
    ok_all, parts = True, []
    steps = [604800] * 26
    t = sum(steps) / T.YEAR_SECS
    for i, vol in enumerate((0.25, 0.85)):
        c = scaled_cfg(cfg3, vol)
        lx = T.simulate_log_spot(2**21, vol, steps, seed=1100 + i, cfg=cfg3)
        for K in (0.6, 1.0, 1.2):
            pm = 0.5 * (np.maximum(K - np.exp(lx[0]), 0) + np.maximum(K - np.exp(lx[1]), 0)) * math.exp(-cfg3.r * t)
            mc, se = pm.mean() * 1e4, pm.std(ddof=1) / math.sqrt(pm.size) * 1e4
            cf = merton_put(1.0, K, t, vol, c) * 1e4
            z = (mc - cf) / se
            ok_all &= abs(z) <= 3
            parts.append(f"vol {vol:.0%} put K={K}: {mc:.2f}+-{se:.2f} vs {cf:.2f} (z {z:+.2f})")
        F = T.features([7000], vol * 1e4, 0, 26, 1, ac=1e9)
        p, se = T.price_batch(F, 2**18, seed=1110 + i, cfg=cfg3)
        want = 1e4 * (0.7 - merton_call(0.7, 1.0, t, vol, c)) + math.exp(-cfg3.r * t) * 25 * 26
        z = (p[0] - want) / se[0]
        ok_all &= abs(z) <= 3
        parts.append(f"vol {vol:.0%} knocked-in no-autocall note: {p[0]:.2f}+-{se[0]:.2f} vs {want:.2f} (z {z:+.2f})")
    report("(l) v3 European puts and knocked-in note vs Merton closed forms", ok_all, "; ".join(parts))


def check_m(cfg3):
    rows = []
    for vol in (2000, 5500, 9000):
        for s in (5500, 7000, 9000):
            for kin in (0, 1):
                for n, tn in ((1, 0), (1, 302400), (4, 86400), (26, 3600)):
                    rows.append((s, n, tn, kin, vol))
    rows = np.array(rows, dtype=np.float64)
    F = T.features(rows[:, 0], rows[:, 4], rows[:, 2], rows[:, 1].astype(np.int64), rows[:, 3].astype(np.int64))
    p1, s1 = T.price_batch(F, 2**17, seed=1201, smooth=True, cfg=cfg3)
    p2, s2 = T.price_batch(F, 2**17, seed=1202, smooth=False, cfg=cfg3)
    z = (p1 - p2) / np.hypot(s1, s2)
    w = int(np.argmax(np.abs(z)))
    report("(m) v3 smoothing vs brute-force last step, mixed vols", bool(np.all(np.abs(z) <= 3.5)),
           f"{len(rows)} states (vol 20/55/90% in one batch), 2^17 paths; max |z| {np.max(np.abs(z)):.2f} at "
           f"vol {rows[w, 4]:.0f} spot {rows[w, 0]:.0f} obs {rows[w, 1]:.0f} tNext {rows[w, 2]:.0f}; "
           f"mean z^2 {np.mean(z**2):.2f}")


def check_n(cfg3, cuda_python):
    if not (cuda_python and os.path.exists(cuda_python)):
        report("(n) v3 torch backend vs numpy teacher", False, "no CUDA python")
        return
    F = v3_states(64, 1300, [1500.0, 2500.0, 4000.0, 5500.0, 7500.0, 9000.0, 12000.0])
    p_np, s_np = T.price_batch(F, 2**18, seed=1302, cfg=cfg3)
    p_t, s_t, dev = torch_prices(HERE, F, 2**18, 1301, T.CONFIG_V3_PATH, cuda_python)
    d, e = p_t - p_np, np.hypot(s_t, s_np)
    det = e < 1e-9                   # deterministic states: numpy's stderr can be 5e-15 of rounding, not 0
    z = d[~det] / e[~det]
    ok = bool(np.all(np.abs(z) <= 3.5)) and bool(np.all(np.abs(d[det]) <= 1e-6))
    report("(n) v3 torch backend vs numpy teacher, mixed vols", ok,
           f"64 states, vols 15-120%, 2^18 paths, cuda ({dev}); {int(det.sum())} deterministic; "
           f"max |z| {np.max(np.abs(z)):.2f}, mean z^2 {np.mean(z**2):.2f}, max |diff| {np.max(np.abs(d)):.2f} bps")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuda-python", default=os.environ.get("TEACHER_CUDA_PYTHON", DEFAULT_CUDA_PY))
    ap.add_argument("--skip-g", action="store_true")
    ap.add_argument("--only", default="", help="run only these checks, e.g. --only gh")
    args = ap.parse_args()
    cfg = T.load_config()
    print(f"jump teacher: {cfg.name} lambdaYear {cfg.lambda_year} muJ {cfg.mu_j} sigmaJ {cfg.sigma_j} "
          f"jumpVol {math.sqrt(cfg.jump_var_year):.4f} kappa {cfg.kappa:.6f} r {cfg.r}", flush=True)
    t0 = time.time()
    run = lambda c: not args.only or c in args.only
    if run("a"):
        check_a()
    if run("b"):
        check_b(cfg)
    if run("c"):
        check_c(cfg)
    if run("d"):
        check_d(cfg)
    if run("e"):
        check_e(cfg)
    if run("f"):
        check_f(cfg)
    if run("g") and not args.skip_g:
        check_g(cfg, args.cuda_python)
    if run("h"):
        check_h(cfg)
    cfg3 = T.load_config(T.CONFIG_V3_PATH)
    if any(run(c) for c in "ijklmno"):
        print(f"v3 teacher: {cfg3.name} lambdaYear {cfg3.lambda_year} muJ {cfg3.mu_j} sigmaJ {cfg3.sigma_j} at "
              f"volRef {cfg3.vol_ref}; jump share {cfg3.jump_share:.6f}", flush=True)
    if run("i"):
        check_i(args.cuda_python)
    if run("j"):
        check_j(cfg3)
    if run("k"):
        check_k(cfg3)
    if run("l"):
        check_l(cfg3)
    if run("m"):
        check_m(cfg3)
    if run("n") and not args.skip_g:
        check_n(cfg3, args.cuda_python)
    if run("o"):
        check_h(cfg3, "(o) v3 barrier equality at tNext=0")
    n_fail = sum(1 for _, ok in RESULTS if not ok)
    print(f"{len(RESULTS) - n_fail}/{len(RESULTS)} checks passed in {time.time() - t0:.0f}s")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
