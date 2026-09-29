"""Monte Carlo teacher pricer — K1 round 0.

Weekly-observed autocallable on the feed series, priced under risk-neutral GBM
dS/S = r dt + sigma dW with continuous calendar-time diffusion.

v0 deviations from docs/teacher-spec.md (v0.1):
  - Calendar-time diffusion, weekly observation grid. Spec section 2 wants
    market-hours-only diffusion on an XNYS calendar; exchange_calendars is not
    installed in this environment. Boundary fidelity is insensitive to this.
  - No numba/JAX: pure vectorized numpy.

Textbook conventions fixed where the brief was silent (all documented in
docs/k1-round0.md):
  - Year = 365 d = 31_536_000 s (matches NoteQuoter.MATURITY_MAX_SECS = 2 y).
  - Coupon accrues linearly in calendar time: accrued(t) = couponBps * t / WEEK.
  - KI/AC state updates at the `observationsRemaining` observation times only:
    t_k = timeToNextObsSecs + k*WEEK, k = 0..N-1. Maturity is one period after
    the last observation: timeToMaturitySecs = timeToNextObsSecs + N*WEEK.
    No knock-in check at maturity itself.
  - Final redemption strike = acBarrierBps (== par 10000 on the K1 grid):
    knocked-in and s_T < strike -> payout = notional * s_T.
  - Autocall at an observation redeems notional + coupon accrued to that time.
  - Conditional-expectation smoothing (teacher-spec section 6): the last step
    (final observation -> maturity) is replaced by its analytic GBM conditional
    expectation, removing label variance exactly at the final-fixing kink.

Deterministic given (features, seed): a single PCG64 stream is consumed in a
fixed order (buckets by observationsRemaining, then fixed-size chunks).
"""

from __future__ import annotations

import numpy as np
import torch

WEEK_SECS = 604_800
YEAR_SECS = 31_536_000
R_FREE = 0.04

FEATURE_KEYS = (
    "spot", "dist", "vol", "ki", "ac", "coupon", "ttm", "tNext", "obs", "knockedIn"
)


def _phi(x: np.ndarray) -> np.ndarray:
    """Standard normal CDF via torch (numpy has no erf)."""
    t = torch.from_numpy(np.ascontiguousarray(x, dtype=np.float64))
    return torch.special.ndtr(t).numpy()


def _simulate(F, idx: np.ndarray, N: int, total_paths: int, rng, smooth: bool):
    """Price one group of labels sharing the same observation count N.

    Returns (priceBps (G,), mcStdErrBps (G,)). Antithetic: P = total_paths//2
    standard-normal draws per step, each used as +Z and -Z; the stderr is taken
    over the P pair-means.
    """
    G = idx.size
    P = total_paths // 2
    s0 = F["spot"][idx].astype(np.float64) / 1e4      # relative spot (1.0 = par)
    sig = F["vol"][idx].astype(np.float64) / 1e4
    ki = F["ki"][idx].astype(np.float64) / 1e4
    ac = F["ac"][idx].astype(np.float64) / 1e4
    cpn = F["coupon"][idx].astype(np.float64)         # bps of notional / period
    t_next = F["tNext"][idx].astype(np.float64)
    T = F["ttm"][idx].astype(np.float64)
    ki0 = F["knockedIn"][idx].astype(bool)

    x = np.repeat(np.log(s0)[:, None], 2 * P, axis=1).astype(np.float32)
    redeemed = np.zeros((G, 2 * P), dtype=bool)
    ki_latch = np.repeat(ki0[:, None], 2 * P, axis=1)
    pay = np.zeros((G, 2 * P), dtype=np.float64)

    t_prev = np.zeros(G)
    for k in range(N):
        tau = t_next + k * WEEK_SECS
        dt_y = (tau - t_prev) / YEAR_SECS             # per-label; 0 when tNext=0, k=0
        t_prev = tau
        mu = (R_FREE - 0.5 * sig**2) * dt_y
        sd = sig * np.sqrt(dt_y)
        Z = rng.standard_normal((G, P), dtype=np.float32)
        x += (mu[:, None] + sd[:, None] * np.concatenate([Z, -Z], axis=1)).astype(np.float32)
        s = np.exp(x)
        live = ~redeemed
        hit = live & (s >= ac[:, None])
        if hit.any():
            val = np.exp(-R_FREE * tau / YEAR_SECS) * (1e4 + cpn * tau / WEEK_SECS)
            pay[hit] = np.broadcast_to(val[:, None], pay.shape)[hit]
        redeemed |= hit
        ki_latch |= live & (s < ki[:, None])

    live = ~redeemed
    if live.any():
        tau_last = t_prev                              # t_next + (N-1)*WEEK
        dY = np.maximum((T - tau_last) / YEAR_SECS, 1e-9)
        s_last = np.exp(x).astype(np.float64)
        strike = ac                                    # == par on the K1 grid
        disc = np.exp(-R_FREE * dY[:, None])
        if smooth:
            sq = sig[:, None] * np.sqrt(dY[:, None])
            d2 = (np.log(s_last / strike[:, None])
                  + (R_FREE - 0.5 * sig[:, None] ** 2) * dY[:, None]) / sq
            d1 = d2 + sq
            cont = np.where(
                ki_latch,
                1e4 * (disc * _phi(d2) + s_last * _phi(-d1)),
                1e4 * disc,
            )
        else:  # brute-force final step, for cross-checking the analytic one
            Z = rng.standard_normal((G, P), dtype=np.float32)
            Zf = np.concatenate([Z, -Z], axis=1)
            sT = s_last * np.exp((R_FREE - 0.5 * sig[:, None] ** 2) * dY[:, None]
                                 + sig[:, None] * np.sqrt(dY[:, None]) * Zf)
            cont = np.where(ki_latch & (sT < strike[:, None]), 1e4 * sT, 1e4)
        cont = cont + disc * (cpn[:, None] * T[:, None] / WEEK_SECS)
        fv = np.exp(-R_FREE * tau_last[:, None] / YEAR_SECS) * cont
        pay[live] = fv[live]

    pair_mean = 0.5 * (pay[:, :P] + pay[:, P:])
    price = pair_mean.mean(axis=1)
    se = pair_mean.std(axis=1, ddof=1) / np.sqrt(P)
    return price, se


def price_batch(F: dict, total_paths: int = 2**18, seed: int = 0, smooth: bool = True):
    """Price a batch of labels. F maps FEATURE_KEYS to equal-length arrays."""
    G = len(F["spot"])
    price = np.empty(G)
    se = np.empty(G)
    rng = np.random.default_rng(seed)
    chunk = max(1, (1 << 25) // total_paths)
    for N in sorted(set(F["obs"].astype(int).tolist())):
        idx = np.flatnonzero(F["obs"].astype(int) == N)
        for lo in range(0, idx.size, chunk):
            sub = idx[lo:lo + chunk]
            p, s = _simulate(F, sub, N, total_paths, rng, smooth)
            price[sub] = p
            se[sub] = s
    return price, se


def price(spotBpsOfInitial, volBpsAnnual, timeToMaturitySecs, timeToNextObsSecs,
          observationsRemaining, knockedIn, kiBarrierBps=6000, acBarrierBps=10000,
          couponBpsPerPeriod=25, total_paths=2**18, seed=0):
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
    p, se = price_batch(F, total_paths=total_paths, seed=seed)
    return float(p[0]), float(se[0])


if __name__ == "__main__":
    # Smoke checks (fast, low paths). Each prints price +- 3*se.
    def check(name, got, want, tol):
        ok = abs(got - want) <= tol
        print(f"{'OK ' if ok else 'FAIL'} {name}: got {got:.2f}, want {want:.2f} +- {tol:.2f}")
        return ok

    allok = True
    # 1. Deep above AC, at an observation: immediate autocall at par, no coupon.
    p, se = price(30000, 5500, 26 * WEEK_SECS, 0, 26, 0, total_paths=2**12)
    allok &= check("immediate autocall", p, 10000.0, 3 * se + 1.0)

    # 2. Knocked in, tiny vol, spot 0.5: martingale property -> 10000*0.5 = 5000
    #    plus discounted coupon e^{-rT} * 25*26.
    T = 26 * WEEK_SECS
    p, se = price(5000, 1500, T, 0, 26, 1, total_paths=2**12)
    want = 5000.0 + np.exp(-R_FREE * T / YEAR_SECS) * 25 * 26
    allok &= check("knocked-in low-vol martingale", p, want, 3 * se + 30.0)

    # 3. Smoothed final step agrees with brute-force final step within MC noise.
    F = {
        "spot": np.full(64, 5800.0), "dist": np.full(64, -200.0),
        "vol": np.full(64, 5500.0), "ki": np.full(64, 6000.0),
        "ac": np.full(64, 10000.0), "coupon": np.full(64, 25.0),
        "ttm": np.full(64, 26.0 * WEEK_SECS), "tNext": np.full(64, 86400.0),
        "obs": np.full(64, 26), "knockedIn": np.zeros(64, dtype=np.int64),
    }
    p1, se1 = price_batch(F, total_paths=2**15, seed=11, smooth=True)
    p2, se2 = price_batch(F, total_paths=2**15, seed=11, smooth=False)
    diff = abs(p1.mean() - p2.mean())
    tol = 3 * float(np.hypot(se1.mean(), se2.mean()))
    allok &= check("smoothing consistency", diff, 0.0, tol)

    # 4. Determinism.
    pa, _ = price_batch(F, total_paths=2**12, seed=5)
    pb, _ = price_batch(F, total_paths=2**12, seed=5)
    allok &= bool(np.array_equal(pa, pb))
    print("OK  determinism" if allok else "FAIL determinism")

    print("ALL OK" if allok else "SMOKE TESTS FAILED")
