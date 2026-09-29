"""Torch (CUDA or CPU) backend of the jump teacher: same model, same payoff,
same config, its own random stream.

    from teacher_torch import price_batch
    price, se = price_batch(F, total_paths=2**18, seed=7, device="cuda")

F is the same feature dict as `teacher.price_batch` (keys teacher.FEATURE_KEYS,
equal-length arrays; `teacher.features(...)` builds one on the consistency
manifold). Returns numpy float64 (priceBps, mcStdErrBps). cfg None = the pinned
jump config (ml/teacher_config.json); teacher.GBM for lambda = 0.

Semantics are identical to ml/teacher.py (docstring there): total-vol split,
risk-neutral drift r - lambda*kappa, antithetic pairs sharing the jump count,
exact tNext = 0 barrier comparisons, maturity strike = initial, exact
Poisson-lognormal smoothing of the last step. Only the random numbers differ
(torch Philox/MT stream instead of numpy PCG64), so prices agree with the
numpy teacher within Monte Carlo error, not bit for bit (check (g) in
ml/test_teacher.py). Deterministic for a given (features, seed, config,
device type, max_elems).

Precision: log-spot paths in float32 (as the numpy teacher), payoffs,
discounting, smoothing and the pair statistics in float64.

Run it from a CUDA torch venv, e.g. /opt/ai/cache/venv-cuda/bin/python
(torch 2.11.0+cu128, numpy, scipy); on the CPU venv it runs on device="cpu".
"""

from __future__ import annotations

import math

import numpy as np
import torch

import teacher as T


def _dev(device):
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    return torch.device(device)


def _log_increment(gen, dt_y, sig, sd_d, cfg, P):
    """(G, 2P) float32 log increments; sig total vol, sd_d diffusion vol (float64 tensors)."""
    G = dt_y.shape[0]
    dev = dt_y.device
    if cfg.jumps:
        mu = (cfg.r - cfg.lambda_year * cfg.kappa - 0.5 * sd_d**2) * dt_y
    else:
        mu = (cfg.r - 0.5 * sig**2) * dt_y
    sd = sd_d * torch.sqrt(dt_y)
    Z = torch.randn((G, P), generator=gen, device=dev, dtype=torch.float32)
    inc = (mu[:, None] + sd[:, None] * torch.cat([Z, -Z], dim=1)).to(torch.float32)
    if cfg.jumps:
        rate = (cfg.lambda_year * dt_y).to(torch.float32)[:, None].expand(G, P).contiguous()
        n = torch.poisson(rate, generator=gen)
        zj = torch.randn((G, P), generator=gen, device=dev, dtype=torch.float32)
        base = n * cfg.mu_j
        spread = torch.sqrt(n) * cfg.sigma_j * zj
        inc[:, :P] += base + spread
        inc[:, P:] += base - spread
    return inc


def _smoothed_capped_spot(s_last, sd_d, dY, cfg):
    """E[min(S_T,1) | s_last] over dY years, exact Poisson-lognormal sum (float64)."""
    lam_t = cfg.lambda_year * dY
    ln_s = torch.log(s_last)
    drift = (cfg.r - cfg.lambda_year * cfg.kappa - 0.5 * sd_d**2) * dY
    out = torch.zeros_like(s_last)
    wsum = torch.zeros_like(s_last)
    for n in range(T.SMOOTH_TERMS):
        w = torch.exp(-lam_t + (n * torch.log(lam_t) if n else 0.0) - math.lgamma(n + 1))
        wsum += w
        if float(w.max()) < 1e-17:
            if n > float(lam_t.max()):
                break
            continue
        m = ln_s + drift + n * cfg.mu_j
        v = sd_d**2 * dY + n * cfg.sigma_j**2
        sv = torch.sqrt(v)
        out += w * (torch.exp(m + 0.5 * v) * torch.special.ndtr(-(m + v) / sv)
                    + torch.special.ndtr(m / sv))
    tail = 1.0 - float(wsum.min())
    assert tail < 1e-12, f"smoothing Poisson tail {tail:.3e}"
    return out


def _simulate(F, idx, N, total_paths, gen, smooth, cfg, dev):
    f64 = dict(device=dev, dtype=torch.float64)
    G = idx.size
    P = total_paths // 2
    g = lambda k: torch.as_tensor(np.asarray(F[k])[idx].astype(np.float64), **f64)
    s0 = g("spot") / 1e4
    sig = g("vol") / 1e4
    ac = g("ac") / 1e4
    ki = g("ki") / 1e4
    cpn = g("coupon")
    t_next = g("tNext")
    Tm = g("ttm")
    ki0 = g("knockedIn") != 0
    r = cfg.r
    sd_d = torch.as_tensor(cfg.diffusion_vol(sig.cpu().numpy()), **f64)

    x = torch.log(s0).to(torch.float32)[:, None].repeat(1, 2 * P)
    redeemed = torch.zeros((G, 2 * P), dtype=torch.bool, device=dev)
    ki_latch = ki0[:, None].repeat(1, 2 * P)
    pay = torch.zeros((G, 2 * P), **f64)

    t_prev = torch.zeros(G, **f64)
    for k in range(N):
        tau = t_next + k * T.WEEK_SECS
        dt_y = (tau - t_prev) / T.YEAR_SECS
        t_prev = tau
        x += _log_increment(gen, dt_y, sig, sd_d, cfg, P)
        s = torch.exp(x)
        live = ~redeemed
        hit = live & (s >= ac[:, None])
        kin = live & (s < ki[:, None])
        if k == 0:
            now = dt_y == 0
            if bool(now.any()):
                hit[now] = (s0[now] >= ac[now])[:, None].expand(-1, 2 * P)
                kin[now] = (s0[now] < ki[now])[:, None].expand(-1, 2 * P)
        if bool(hit.any()):
            val = torch.exp(-r * tau / T.YEAR_SECS) * (1e4 + cpn * tau / T.WEEK_SECS)
            pay = torch.where(hit, val[:, None], pay)
        redeemed |= hit
        ki_latch |= kin

    live = ~redeemed
    if bool(live.any()):
        tau_last = t_prev
        dY = torch.clamp((Tm - tau_last) / T.YEAR_SECS, min=1e-9)
        s_last = torch.exp(x.to(torch.float64))
        disc = torch.exp(-r * dY)[:, None]
        if smooth and not cfg.jumps:
            sq = (sig * torch.sqrt(dY))[:, None]
            d2 = (torch.log(s_last) + ((r - 0.5 * sig**2) * dY)[:, None]) / sq
            d1 = d2 + sq
            cont = torch.where(ki_latch,
                               1e4 * (disc * torch.special.ndtr(d2) + s_last * torch.special.ndtr(-d1)),
                               1e4 * disc)
        elif smooth:
            cont = (1e4 * disc).expand(G, 2 * P).clone()
            m = ki_latch & live
            if bool(m.any()):
                gi = torch.nonzero(m, as_tuple=True)[0]
                ev = _smoothed_capped_spot(s_last[m], sd_d[gi], dY[gi], cfg)
                cont[m] = 1e4 * disc[gi, 0] * ev
        else:
            inc = _log_increment(gen, dY, sig, sd_d, cfg, P).to(torch.float64)
            sT = s_last * torch.exp(inc)
            cont = disc * torch.where(ki_latch & (sT < 1.0), 1e4 * sT, torch.full_like(sT, 1e4))
        cont = cont + disc * (cpn * Tm / T.WEEK_SECS)[:, None]
        fv = torch.exp(-r * tau_last / T.YEAR_SECS)[:, None] * cont
        pay = torch.where(live, fv, pay)

    pair_mean = 0.5 * (pay[:, :P] + pay[:, P:])
    price = pair_mean.mean(dim=1)
    se = pair_mean.std(dim=1, correction=1) / math.sqrt(P)
    return price.cpu().numpy(), se.cpu().numpy()


def price_batch(F: dict, total_paths: int = 2**18, seed: int = 0, smooth: bool = True,
                cfg: T.TeacherConfig | None = None, device=None, max_elems: int = 1 << 25):
    """Batch API for label generation (see module docstring).

    max_elems bounds labels x paths per chunk (GPU memory ~ 40 bytes x max_elems
    at peak; 2^25 ~ 1.3 GB). Labels are bucketed by observationsRemaining and
    chunked in input order, exactly like the numpy teacher.
    """
    if cfg is None:
        cfg = T.load_config()
    dev = _dev(device)
    cfg.diffusion_vol(np.asarray(F["vol"], dtype=np.float64) / 1e4)   # fail fast
    G = len(F["spot"])
    price = np.empty(G)
    se = np.empty(G)
    gen = torch.Generator(device=dev)
    gen.manual_seed(int(seed))
    chunk = max(1, max_elems // total_paths)
    obs = np.asarray(F["obs"]).astype(int)
    with torch.no_grad():
        for N in sorted(set(obs.tolist())):
            idx = np.flatnonzero(obs == N)
            for lo in range(0, idx.size, chunk):
                sub = idx[lo:lo + chunk]
                p, s = _simulate(F, sub, N, total_paths, gen, smooth, cfg, dev)
                price[sub] = p
                se[sub] = s
    return price, se


def simulate_log_spot(n_paths: int, vol: float, step_secs, seed: int, cfg=None, device=None):
    """Antithetic log S_T/S_0 after the given steps, (2, n_paths//2) float64 (plus, minus legs)."""
    if cfg is None:
        cfg = T.load_config()
    dev = _dev(device)
    gen = torch.Generator(device=dev)
    gen.manual_seed(int(seed))
    P = n_paths // 2
    sig = torch.tensor([vol], dtype=torch.float64, device=dev)
    sd_d = torch.as_tensor(cfg.diffusion_vol(np.array([vol])), dtype=torch.float64, device=dev)
    x = torch.zeros((1, 2 * P), dtype=torch.float32, device=dev)
    for dt in step_secs:
        x += _log_increment(gen, torch.tensor([dt / T.YEAR_SECS], dtype=torch.float64, device=dev),
                            sig, sd_d, cfg, P)
    x = x.to(torch.float64).cpu().numpy()[0]
    return np.stack([x[:P], x[P:]])
