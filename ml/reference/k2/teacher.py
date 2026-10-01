"""Monte Carlo teacher pricer, v2: Merton jump-diffusion calibrated to TSLA.

Weekly-observed autocallable on the feed series (payoff: docs/teacher-spec.md
section 5, normative in contracts/src/interfaces/INoteSeries.sol), priced under
the risk-neutral Merton jump-diffusion

    dS/S = (r - lam*kappa) dt + sigma_d dW + (e^Y - 1) dN,
    N ~ Poisson(lam), Y ~ N(muJ, sigJ^2), kappa = e^(muJ + sigJ^2/2) - 1,

in continuous calendar time (365-day year). Docs: docs/teacher-v2.md.

Vol convention (binding): the input `volBpsAnnual` is the TOTAL annualized vol.
The jump variance lam*(muJ^2 + sigJ^2) per year is pinned in
ml/teacher_config.json (fitted to TSLA daily returns, ml/calibrate_jumps.py);
the diffusion takes the rest: sigma_d^2 = vol^2 - jump variance, which must be
> 0 (ValueError otherwise). Q jump parameters = fitted P parameters.

Configs: `load_config()` (default, the pinned jump teacher) and `GBM` (lam = 0).
With lam = 0 the code takes the original K1 GBM path and is bit-identical to
ml/reference/teacher_gbm_k1.py for the same seed (checked by ml/test_teacher.py),
except where K1 was wrong: a spot exactly on a barrier at tNext = 0 (below).

Conventions (unchanged from K1, docs/k1-round0.md):
  - Year = 365 d = 31_536_000 s (matches NoteQuoter.MATURITY_MAX_SECS = 2 y).
  - Coupon accrues linearly in calendar time: accrued(t) = couponBps * t / WEEK.
  - KI/AC state updates at the `observationsRemaining` observation times only:
    t_k = timeToNextObsSecs + k*WEEK, k = 0..N-1. Maturity is one period after
    the last observation: timeToMaturitySecs = timeToNextObsSecs + N*WEEK.
    No knock-in check at maturity itself.
  - Maturity loss compares the fixing with the INITIAL fixing (10000 bps):
    knocked-in and s_T < 1 -> payout = notional * s_T. (K1 used acBarrierBps;
    identical on the K1 grid where ac = 10000.)
  - Autocall at an observation redeems notional + coupon accrued to that time.
  - An observation at tNext = 0 fixes at the exact current spot (float64
    comparison; K1 compared the float32 log round trip, which knocked in at
    spot == ki exactly). Later fixings are continuous random variables.
  - Clean price: coupon accruing from now on; the quoter adds accrued-since-strike.
  - Conditional-expectation smoothing: the last step (final observation ->
    maturity) is replaced by its exact conditional expectation; under jumps a
    Poisson-weighted sum of lognormal terms (SMOOTH_TERMS terms, the dropped
    Poisson tail is asserted < 1e-12).

Antithetic variates: P = total_paths//2 draws per step, used as +Z and -Z; the
jump count is shared by the pair and the jump-size normal is negated too. The
stderr is taken over the P pair means.

Deterministic given (features, seed, config): one PCG64 stream consumed in a
fixed order (buckets by observationsRemaining, fixed-size chunks; per step:
diffusion normals, then (jumps on) Poisson jump counts, then jump-size
normals, each (G, P)).
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass

import numpy as np
import torch
from scipy.special import ndtr as _ndtr

WEEK_SECS = 604_800
YEAR_SECS = 31_536_000
R_FREE = 0.04
SMOOTH_TERMS = 24          # Poisson terms in the smoothed last step (n = 0..23)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teacher_config.json")

FEATURE_KEYS = (
    "spot", "dist", "vol", "ki", "ac", "coupon", "ttm", "tNext", "obs", "knockedIn"
)


@dataclass(frozen=True)
class TeacherConfig:
    """Pinned teacher constants. Rates are per calendar year (365 d)."""
    name: str
    r: float = R_FREE
    lambda_year: float = 0.0   # jumps per year
    mu_j: float = 0.0          # mean log jump size
    sigma_j: float = 0.0       # std of log jump size

    @property
    def jumps(self) -> bool:
        return self.lambda_year > 0.0

    @property
    def kappa(self) -> float:
        return math.expm1(self.mu_j + 0.5 * self.sigma_j**2)

    @property
    def jump_var_year(self) -> float:
        return self.lambda_year * (self.mu_j**2 + self.sigma_j**2)

    def diffusion_vol(self, total_vol: np.ndarray) -> np.ndarray:
        """sigma_d = sqrt(total^2 - jump variance); raises if not > 0."""
        total_vol = np.asarray(total_vol, dtype=np.float64)
        if not self.jumps:
            return total_vol
        dv = total_vol**2 - self.jump_var_year
        if np.any(dv <= 0):
            bad = float(np.min(total_vol[dv <= 0]))
            raise ValueError(
                f"total vol {bad:.4f} <= jump vol {math.sqrt(self.jump_var_year):.4f}: "
                f"no diffusion variance left ({self.name})")
        return np.sqrt(dv)


GBM = TeacherConfig(name="gbm")
_PINNED: TeacherConfig | None = None


def load_config(path: str = CONFIG_PATH) -> TeacherConfig:
    """The pinned jump teacher (ml/teacher_config.json), cached."""
    global _PINNED
    if path == CONFIG_PATH and _PINNED is not None:
        return _PINNED
    with open(path) as f:
        c = json.load(f)
    cfg = TeacherConfig(name=c["name"], r=c["rFree"], lambda_year=c["jumps"]["lambdaYear"],
                        mu_j=c["jumps"]["muJ"], sigma_j=c["jumps"]["sigmaJ"])
    assert c["yearSecs"] == YEAR_SECS and c["weekSecs"] == WEEK_SECS
    assert c["smoothingPoissonTerms"] == SMOOTH_TERMS
    if path == CONFIG_PATH:
        _PINNED = cfg
    return cfg


def _phi(x: np.ndarray) -> np.ndarray:
    """Standard normal CDF via torch (numpy has no erf). Kept for the K1 GBM path."""
    t = torch.from_numpy(np.ascontiguousarray(x, dtype=np.float64))
    return torch.special.ndtr(t).numpy()


def _jump_increment(rng, lam_dt: np.ndarray, cfg: TeacherConfig, G: int, P: int):
    """Sum of log jumps per (label, pair): (plus, minus) float32 arrays (G, P).

    Given n jumps in the step, the sum is n*muJ + sqrt(n)*sigJ*Z (exact in law).
    The antithetic pair shares n; the jump-size normal is negated on the minus leg.
    """
    n = rng.poisson(lam_dt[:, None], size=(G, P)).astype(np.float32)
    zj = rng.standard_normal((G, P), dtype=np.float32)
    base = n * np.float32(cfg.mu_j)
    spread = np.sqrt(n) * np.float32(cfg.sigma_j) * zj
    return base + spread, base - spread


def log_increment(rng, dt_y: np.ndarray, sig: np.ndarray, cfg: TeacherConfig, P: int):
    """One step of log S for G labels x 2P antithetic paths, float32 (G, 2P).

    sig is the TOTAL vol per label; the diffusion part is cfg.diffusion_vol(sig).
    With cfg.jumps False this is exactly the K1 GBM step.
    """
    G = dt_y.size
    if not cfg.jumps:
        mu = (cfg.r - 0.5 * sig**2) * dt_y
        sd = sig * np.sqrt(dt_y)
        Z = rng.standard_normal((G, P), dtype=np.float32)
        return (mu[:, None] + sd[:, None] * np.concatenate([Z, -Z], axis=1)).astype(np.float32)
    sd_d = cfg.diffusion_vol(sig)
    mu = (cfg.r - cfg.lambda_year * cfg.kappa - 0.5 * sd_d**2) * dt_y
    sd = sd_d * np.sqrt(dt_y)
    Z = rng.standard_normal((G, P), dtype=np.float32)
    inc = (mu[:, None] + sd[:, None] * np.concatenate([Z, -Z], axis=1)).astype(np.float32)
    jp, jm = _jump_increment(rng, cfg.lambda_year * dt_y, cfg, G, P)
    inc[:, :P] += jp
    inc[:, P:] += jm
    return inc


def smoothed_capped_spot(s_last: np.ndarray, sig: np.ndarray, dY: np.ndarray, cfg: TeacherConfig):
    """E[min(S_T, 1) | S_t = s_last] over a step of dY years (undiscounted), exact.

    Given n jumps, log S_T ~ N(m_n, v_n) with m_n = ln s + (r - lam kappa -
    sd^2/2) dY + n muJ and v_n = sd^2 dY + n sigJ^2; then
    E[min(S,1) | n] = e^(m_n + v_n/2) Phi(-(m_n + v_n)/sqrt(v_n)) + Phi(m_n/sqrt(v_n)).
    Poisson weights with mean lam*dY; up to SMOOTH_TERMS terms, terms whose
    weight is < 1e-17 for every path are skipped, the dropped mass is asserted < 1e-12.
    Shapes: s_last (M,), sig/dY (M,) per path. Jumps only (GBM keeps its K1 code).
    """
    sd_d = cfg.diffusion_vol(sig)
    lam_t = cfg.lambda_year * dY
    ln_s = np.log(s_last)
    drift = (cfg.r - cfg.lambda_year * cfg.kappa - 0.5 * sd_d**2) * dY
    out = np.zeros_like(s_last, dtype=np.float64)
    wsum = np.zeros_like(out)
    for n in range(SMOOTH_TERMS):
        w = np.exp(-lam_t + (n * np.log(lam_t) if n else 0.0) - math.lgamma(n + 1))
        wsum += w
        if np.max(w) < 1e-17:          # every remaining term is below double precision
            if n > np.max(lam_t):
                break
            continue
        m = ln_s + drift + n * cfg.mu_j
        v = sd_d**2 * dY + n * cfg.sigma_j**2
        sv = np.sqrt(v)
        term = np.exp(m + 0.5 * v) * _ndtr(-(m + v) / sv) + _ndtr(m / sv)
        out += w * term
    tail = 1.0 - float(np.min(wsum))
    assert tail < 1e-12, f"smoothing Poisson tail {tail:.3e} >= 1e-12; raise SMOOTH_TERMS"
    return out


def _simulate(F, idx: np.ndarray, N: int, total_paths: int, rng, smooth: bool,
              cfg: TeacherConfig):
    """Price one group of labels sharing the same observation count N.

    Returns (priceBps (G,), mcStdErrBps (G,)).
    """
    G = idx.size
    P = total_paths // 2
    s0 = F["spot"][idx].astype(np.float64) / 1e4      # relative spot (1.0 = par = initial)
    sig = F["vol"][idx].astype(np.float64) / 1e4      # TOTAL annualized vol
    ac = F["ac"][idx].astype(np.float64) / 1e4
    ki = F["ki"][idx].astype(np.float64) / 1e4
    cpn = F["coupon"][idx].astype(np.float64)         # bps of notional / period
    t_next = F["tNext"][idx].astype(np.float64)
    T = F["ttm"][idx].astype(np.float64)
    ki0 = F["knockedIn"][idx].astype(bool)
    r = cfg.r

    x = np.repeat(np.log(s0)[:, None], 2 * P, axis=1).astype(np.float32)
    redeemed = np.zeros((G, 2 * P), dtype=bool)
    ki_latch = np.repeat(ki0[:, None], 2 * P, axis=1)
    pay = np.zeros((G, 2 * P), dtype=np.float64)

    t_prev = np.zeros(G)
    for k in range(N):
        tau = t_next + k * WEEK_SECS
        dt_y = (tau - t_prev) / YEAR_SECS             # per-label; 0 when tNext=0, k=0
        t_prev = tau
        x += log_increment(rng, dt_y, sig, cfg, P)
        s = np.exp(x)
        live = ~redeemed
        hit = live & (s >= ac[:, None])
        kin = live & (s < ki[:, None])
        if k == 0:
            # An observation right now (tNext = 0) fixes at the exact current
            # spot: compare in float64, not via the float32 log round trip
            # (exp(float32(log 0.6)) < 0.6 would knock in at spot == ki).
            now = dt_y == 0
            if now.any():
                hit[now] = (s0[now] >= ac[now])[:, None]
                kin[now] = (s0[now] < ki[now])[:, None]
        if hit.any():
            val = np.exp(-r * tau / YEAR_SECS) * (1e4 + cpn * tau / WEEK_SECS)
            pay[hit] = np.broadcast_to(val[:, None], pay.shape)[hit]
        redeemed |= hit
        ki_latch |= kin

    live = ~redeemed
    if live.any():
        tau_last = t_prev                              # t_next + (N-1)*WEEK
        dY = np.maximum((T - tau_last) / YEAR_SECS, 1e-9)
        s_last = np.exp(x).astype(np.float64)
        strike = np.ones(G)                            # initial fixing (teacher-spec s5)
        disc = np.exp(-r * dY[:, None])
        if smooth and not cfg.jumps:                   # K1 GBM closed form (bit-identical path)
            sq = sig[:, None] * np.sqrt(dY[:, None])
            d2 = (np.log(s_last / strike[:, None])
                  + (r - 0.5 * sig[:, None] ** 2) * dY[:, None]) / sq
            d1 = d2 + sq
            cont = np.where(
                ki_latch,
                1e4 * (disc * _phi(d2) + s_last * _phi(-d1)),
                1e4 * disc,
            )
        elif smooth:                                   # exact Poisson-lognormal sum
            cont = np.broadcast_to(1e4 * disc, (G, 2 * P)).copy()
            m = ki_latch & live
            if m.any():
                gi = np.nonzero(m)[0]
                ev = smoothed_capped_spot(s_last[m], sig[gi], dY[gi], cfg)
                cont[m] = 1e4 * disc[gi, 0] * ev
        else:  # brute-force final step, for cross-checking the analytic one
            # (K1's version left the principal undiscounted over this last step,
            # a ~8 bps inconsistency with its own smoothed branch; fixed here.)
            inc = log_increment(rng, dY, sig, cfg, P).astype(np.float64)
            sT = s_last * np.exp(inc)
            cont = disc * np.where(ki_latch & (sT < strike[:, None]), 1e4 * sT, 1e4)
        cont = cont + disc * (cpn[:, None] * T[:, None] / WEEK_SECS)
        fv = np.exp(-r * tau_last[:, None] / YEAR_SECS) * cont
        pay[live] = fv[live]

    pair_mean = 0.5 * (pay[:, :P] + pay[:, P:])
    price = pair_mean.mean(axis=1)
    se = pair_mean.std(axis=1, ddof=1) / np.sqrt(P)
    return price, se


def price_batch(F: dict, total_paths: int = 2**18, seed: int = 0, smooth: bool = True,
                cfg: TeacherConfig | None = None):
    """Price a batch of labels. F maps FEATURE_KEYS to equal-length arrays.

    cfg None = the pinned jump teacher (ml/teacher_config.json); pass
    teacher.GBM for the K1 GBM teacher. Returns (priceBps, mcStdErrBps).
    """
    if cfg is None:
        cfg = load_config()
    G = len(F["spot"])
    price = np.empty(G)
    se = np.empty(G)
    cfg.diffusion_vol(np.asarray(F["vol"], dtype=np.float64) / 1e4)   # fail fast
    rng = np.random.default_rng(seed)
    chunk = max(1, (1 << 25) // total_paths)
    for N in sorted(set(F["obs"].astype(int).tolist())):
        idx = np.flatnonzero(F["obs"].astype(int) == N)
        for lo in range(0, idx.size, chunk):
            sub = idx[lo:lo + chunk]
            p, s = _simulate(F, sub, N, total_paths, rng, smooth, cfg)
            price[sub] = p
            se[sub] = s
    return price, se


def features(spot, vol, tNext, obs, knockedIn, ki=6000, ac=10000, coupon=25):
    """Feature dict on the consistency manifold (ttm = tNext + obs*WEEK, dist = spot - ki)."""
    spot = np.atleast_1d(np.asarray(spot, dtype=np.float64))
    n = spot.size
    b = lambda v, dt=np.float64: np.broadcast_to(np.asarray(v, dtype=dt), (n,)).copy()
    tNext = b(tNext)
    obs = b(obs, np.int64)
    return {
        "spot": spot, "dist": spot - b(ki), "vol": b(vol), "ki": b(ki), "ac": b(ac),
        "coupon": b(coupon), "ttm": tNext + obs * WEEK_SECS, "tNext": tNext, "obs": obs,
        "knockedIn": b(knockedIn, np.int64),
    }


def price(spotBpsOfInitial, volBpsAnnual, timeToMaturitySecs, timeToNextObsSecs,
          observationsRemaining, knockedIn, kiBarrierBps=6000, acBarrierBps=10000,
          couponBpsPerPeriod=25, total_paths=2**18, seed=0, cfg: TeacherConfig | None = None):
    """Single-label convenience wrapper. Returns (priceBps, mcStdErrBps)."""
    F = {
        "spot": np.array([spotBpsOfInitial], dtype=np.float64),
        "dist": np.array([spotBpsOfInitial - kiBarrierBps], dtype=np.float64),
        "vol": np.array([volBpsAnnual], dtype=np.float64),
        "ki": np.array([kiBarrierBps], dtype=np.float64),
        "ac": np.array([acBarrierBps], dtype=np.float64),
        "coupon": np.array([couponBpsPerPeriod], dtype=np.float64),
        "ttm": np.array([timeToMaturitySecs], dtype=np.float64),
        "tNext": np.array([timeToNextObsSecs], dtype=np.float64),
        "obs": np.array([observationsRemaining], dtype=np.int64),
        "knockedIn": np.array([1 if knockedIn else 0], dtype=np.int64),
    }
    p, se = price_batch(F, total_paths=total_paths, seed=seed, cfg=cfg)
    return float(p[0]), float(se[0])


def simulate_log_spot(n_paths: int, vol: float, step_secs, seed: int,
                      cfg: TeacherConfig | None = None):
    """Antithetic log(S_T/S_0) after steps of the given lengths (seconds), using the
    teacher's own step (log_increment). Returns (2, n_paths//2) float64: the plus
    and minus legs of each pair. For the martingale / closed-form checks."""
    if cfg is None:
        cfg = load_config()
    rng = np.random.default_rng(seed)
    P = n_paths // 2
    x = np.zeros((1, 2 * P), dtype=np.float32)
    sig = np.array([vol], dtype=np.float64)
    for dt in step_secs:
        x += log_increment(rng, np.array([dt / YEAR_SECS]), sig, cfg, P)
    x = x.astype(np.float64)[0]
    return np.stack([x[:P], x[P:]])


if __name__ == "__main__":
    import subprocess
    import sys
    sys.exit(subprocess.call([sys.executable, os.path.join(os.path.dirname(__file__), "test_teacher.py")]
                             + sys.argv[1:]))
