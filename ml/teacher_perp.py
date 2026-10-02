"""Teacher for the v2 perpetual note (docs/v2-spec.md sections 2 and 6).

Prices the PRINCIPAL part of the NOTE per unit of live notional for a state

    x     = spot / H (H = the reference, the last high at a fixing)
    vol   = total annualized vol
    flag  = knocked in
    tau   = time to the next fixing, 0 .. dt
    tE    = time to the next earnings release

The coupon part is exact and separate (perp_formula.coupon_value; R at rho = 0),
so nothing here depends on the coupon.

The note (normative: contracts/src/interfaces/IPerpSeries.sol). At each fixing,
with y = fixing / H:
    y >= 1          H = fixing, clean            the slice a pays 1
    else y < k      knocked in                   the slice a pays y
    else            state unchanged              pays 1 if clean, y if knocked in
then the live notional shrinks by (1 - a).

World: teacher v3's risk-neutral Merton jump-diffusion in calendar time (jump
sizes scale with vol / volRef, drift r, payouts discounted at rDiscount) plus a
scheduled earnings move N(-sE^2/2, sE^2) in log price, sE = sigmaE * vol /
volRef, every `cycle_fixings` fixings (91 days). An earnings release exactly at
a fixing time counts as before it. Config: ml/teacher_perp_config.json.

Method (deterministic, no sampling). Right after a fixing the state is
(x <= 1, flag, j), j = the number of fixings before the week that contains the
next release. Its value W_j solves

    W_j = disc * E[ a * p' + (1 - a) * W_{j'}(x') ]      j' = j - 1, or 12 after the earnings week

over the one-week log return, a Poisson mixture of normals. W is piecewise
linear in ln x on a uniform grid that has ln k and 0 as nodes, so every
expectation is a closed form (normal cdf / pdf per segment) and the two
discontinuities sit exactly on segment ends. On that grid the weights depend
on the distance between nodes only, so one week is a few FFT convolutions. The
13 phases close into one linear system for W_0, solved by GMRES on the cycle map. A mid-week state is
one more expectation over the time to the next fixing. At tau = 0 the fixing
happens now at the exact current spot (cum-release).

The time to earnings enters only through n = the number of fixings before the
release (0 = it comes before or at the next fixing): where in a week the
release falls doesn't change that week's return law. `value_n` takes n
directly (the student's input, docs/p1-perp-student.md); `value` derives it
from tE. Across n = 0 / 1 (release just before vs just after the next fixing)
the price steps by up to ~200 bps near the knock-in barrier (ml/perp_measure.py).

The interpolation error is second order in the grid step (ratio 4.0 per
halving at every vol), so `price()` runs two grids (n_k / 2 and n_k nodes
between k and 1) and extrapolates: (4 * fine - coarse) / 3. With n_k = 256
that is within 0.02 bps of the next refinement (ml/test_teacher_perp.py (d)).

`mc()` is an independent brute-force Monte Carlo of the same rules, used by
ml/test_teacher_perp.py as the cross-check.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass

import numpy as np
from scipy import fft as sfft
from scipy.sparse.linalg import LinearOperator, gmres
from scipy.special import ndtr

WEEK_SECS = 604_800
YEAR_SECS = 31_536_000
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teacher_perp_config.json")
POISSON_TERMS = 40           # terms kept per expectation; the dropped mass is asserted < 1e-12
SQRT_2PI = math.sqrt(2.0 * math.pi)


@dataclass(frozen=True)
class Product:
    """What the note pins (spec section 4). dt in years."""
    k: float = 0.60
    a: float = 18_995_352_771_274_247 / 1e18
    dt: float = WEEK_SECS / YEAR_SECS

    @property
    def phi(self) -> float:
        return -math.log1p(-self.a) / self.dt


P1 = Product()


@dataclass(frozen=True)
class PerpConfig:
    """Teacher constants, per calendar year. Sizes are those at vol_ref."""
    name: str
    r: float = 0.04              # risk-neutral drift
    r_disc: float = 0.0          # discount rate of the payouts
    lambda_year: float = 0.0
    mu_j: float = 0.0
    sigma_j: float = 0.0
    sigma_e: float = 0.0         # std of the earnings move in log price
    vol_ref: float = 0.0         # 0: no jumps, no earnings (GBM)
    cycle_fixings: int = 13      # fixings between earnings releases
    max_fixings_before: int = 18  # largest n the solver keeps (a first release up to 18 fixings away)

    def components(self, vol: float, dt: float):
        """(diffusion vol, jump rate, muJ, sigmaJ, kappa, sigmaE) at total vol `vol`."""
        if self.vol_ref <= 0.0:
            return vol, 0.0, 0.0, 0.0, 0.0, 0.0
        s = vol / self.vol_ref
        mu, sj, se = self.mu_j * s, self.sigma_j * s, self.sigma_e * s
        jump_var = self.lambda_year * (mu * mu + sj * sj)
        earn_var = se * se / (self.cycle_fixings * dt)
        dvar = vol * vol - jump_var - earn_var
        if dvar <= 0.0:
            raise ValueError(f"no diffusion variance left at vol {vol} ({self.name})")
        return math.sqrt(dvar), self.lambda_year, mu, sj, math.expm1(mu + 0.5 * sj * sj), se


GBM = PerpConfig(name="gbm")


def load_config(path: str = CONFIG_PATH) -> PerpConfig:
    with open(path) as f:
        c = json.load(f)
    assert c["yearSecs"] == YEAR_SECS and c["weekSecs"] == WEEK_SECS
    assert c["earnings"]["cycleSecs"] % WEEK_SECS == 0
    return PerpConfig(name=c["name"], r=c["rFree"], r_disc=c["rDiscount"],
                      lambda_year=c["jumps"]["lambdaYear"], mu_j=c["jumps"]["muJ"],
                      sigma_j=c["jumps"]["sigmaJ"], sigma_e=c["earnings"]["sigmaE"],
                      vol_ref=c["jumps"]["volRef"], cycle_fixings=c["earnings"]["cycleSecs"] // WEEK_SECS)


# ---------------------------------------------------------------------------
# the log-return law over a step
# ---------------------------------------------------------------------------

def mixture(cfg: PerpConfig, vol: float, dt: float, step, earnings):
    """Log return over `step` years (array S), with an earnings move where
    `earnings` (bool array S): weights w (S, N), means m (S, N), stds sd (S, N)."""
    step = np.atleast_1d(np.asarray(step, dtype=np.float64))
    earnings = np.broadcast_to(np.asarray(earnings, dtype=bool), step.shape)
    sd_d, lam, mu, sj, kappa, se = cfg.components(vol, dt)
    lam_t = lam * step
    n_max = 1
    if lam > 0.0:
        top = float(lam_t.max())
        n_max = min(POISSON_TERMS, int(top + 12.0 * math.sqrt(top + 1.0) + 12))
    n = np.arange(n_max, dtype=np.float64)[None, :]
    lt = lam_t[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        logw = -lt + np.where(n > 0, n * np.log(np.where(lt > 0, lt, 1.0)), 0.0) \
            - np.array([math.lgamma(i + 1.0) for i in range(n_max)])[None, :]
    w = np.exp(logw)
    w = np.where((lt == 0.0) & (n > 0), 0.0, w)
    assert float((1.0 - w.sum(axis=1)).max()) < 1e-12, "Poisson tail: raise POISSON_TERMS"
    e = earnings[:, None].astype(np.float64)
    m = (cfg.r - lam * kappa - 0.5 * sd_d * sd_d) * step[:, None] + n * mu - 0.5 * se * se * e
    v = sd_d * sd_d * step[:, None] + n * sj * sj + se * se * e
    return w, m, np.sqrt(v)


def _pdf(u):
    return np.exp(-0.5 * u * u) / SQRT_2PI


# ---------------------------------------------------------------------------
# the fixed point right after a fixing
# ---------------------------------------------------------------------------

class Solver:
    """All post-fixing value functions of one vol: W0[j] on [ln k, 0] (clean),
    W1[j] on [z_min, 0] (knocked in), j = 0 .. max_fixings_before - 1 (j = the
    number of fixings before the week that contains the next release)."""

    def __init__(self, vol: float, cfg: PerpConfig, prod: Product = P1, n_k: int = 256,
                 x_min: float = 0.01, tol: float = 1e-12):
        self.vol, self.cfg, self.prod = float(vol), cfg, prod
        self.h = -math.log(prod.k) / n_k
        self.n_k = n_k
        self.M = int(math.ceil(-math.log(x_min) / self.h))
        self.z = -self.h * np.arange(self.M + 1)
        self.disc = math.exp(-cfg.r_disc * prod.dt)
        self.has_earnings = cfg.vol_ref > 0.0 and cfg.sigma_e > 0.0
        C = cfg.cycle_fixings
        J = max(cfg.max_fixings_before, C + 1)      # phases 0 .. J - 1

        ord_ = self._week(False)
        if not self.has_earnings:
            # one phase: W = b + G W
            W = self._solve_cycle([ord_], tol)
            self.W0 = np.repeat(W[0][None, :], J, axis=0)
            self.W1 = np.repeat(W[1][None, :], J, axis=0)
            return
        earn = self._week(True)
        # W_0 = b_E + G_E W_{C-1};  W_j = b_O + G_O W_{j-1}, j >= 1
        w0 = self._solve_cycle([ord_] * (C - 1) + [earn], tol)
        W0, W1 = [w0[0]], [w0[1]]
        for _ in range(J - 1):
            nxt = self._apply(ord_, W0[-1], W1[-1])
            W0.append(nxt[0])
            W1.append(nxt[1])
        self.W0, self.W1 = np.array(W0), np.array(W1)
        back = self._apply(earn, self.W0[C - 1], self.W1[C - 1])
        self.residual = float(max(np.abs(back[0] - self.W0[0]).max(), np.abs(back[1] - self.W1[0]).max()))

    def _week_parts(self, earnings: bool):
        """One week's weights. U[e + M + 1] / L[e + M + 1]: weight of segment
        e = i - s (source node i, segment between nodes s and s + 1) on its
        upper / lower node; plus the vectors over source nodes."""
        p, cfg, h, M, nk, z = self.prod, self.cfg, self.h, self.M, self.n_k, self.z
        w, m, sd = mixture(cfg, self.vol, p.dt, p.dt, earnings)
        w, m, sd = w[0], m[0], sd[0]
        keep = w > 1e-18
        w, m, sd = w[keep], m[keep], sd[keep]
        t = np.arange(-(M + 1), M + 2) * h          # relative edges t_q = q h
        P0 = np.zeros(2 * M + 3)                    # index q + M + 1 <-> segment e = q: [t_{e-1}, t_e]
        P1 = np.zeros(2 * M + 3)
        for wn, mn, sn in zip(w, m, sd):
            u = (t - mn) / sn
            cdf, pdf = ndtr(u), _pdf(u)
            i0 = np.zeros_like(t)
            i0[1:] = cdf[1:] - cdf[:-1]
            i1 = np.zeros_like(t)
            i1[1:] = ((mn - t[:-1]) * i0[1:] - sn * (pdf[1:] - pdf[:-1])) / h
            P0 += wn * i0
            P1 += wn * i1
        zi = z[:, None]

        def p_ge(c):
            return (w * ndtr((zi + m - c) / sd)).sum(axis=1)

        def e_below(c):
            return (w * np.exp(zi + m + 0.5 * sd * sd) * ndtr((c - zi - m - sd * sd) / sd)).sum(axis=1)

        ln_k = z[nk]
        p_up = p_ge(0.0)
        tail = e_below(z[M]) * math.exp(-z[M])
        b0 = self.disc * p.a * (p_ge(ln_k) + e_below(ln_k))[: nk + 1]
        b1 = self.disc * p.a * (p_up + e_below(0.0))
        return P1, P0 - P1, p_up, tail, b0, b1

    def _week(self, earnings: bool):
        """One week's operator for `_apply` (FFT form)."""
        U, L, p_up, tail, b0, b1 = self._week_parts(earnings)
        M = self.M
        Ls = np.concatenate([L[1:], [0.0]])         # node l as the lower end of segment l - 1: L[i - l + 1 + M + 1]
        n = sfft.next_fast_len(3 * M + 4, real=True)
        return b0, b1, sfft.rfft(U, n), sfft.rfft(Ls, n), p_up, tail, n

    def _apply(self, wk, W0, W1, affine: bool = True):
        """One week backwards: (W0, W1) of the next phase -> this phase."""
        b0, b1, fU, fL, p_up, tail, n = wk
        M, nk = self.M, self.n_k
        g = self.disc * (1.0 - self.prod.a)
        # knocked-in sources: W1 over every segment below 0
        ua = W1.copy(); ua[M] = 0.0                 # as the upper node of segment l (l <= M - 1)
        la = W1.copy(); la[0] = 0.0                 # as the lower node of segment l - 1 (l >= 1)
        c1 = sfft.irfft(fU * sfft.rfft(ua, n) + fL * sfft.rfft(la, n), n)[M + 1: 2 * M + 2]
        n1 = g * (c1 + p_up * W0[0] + tail * W1[M])
        # clean sources: W0 on [ln k, 0), W1 below ln k
        ub = ua.copy(); ub[:nk] = W0[:nk]           # node nk as the upper node of segment nk is W1's
        lb = la.copy(); lb[1: nk + 1] = W0[1:]      # node nk as the lower node of segment nk - 1 is W0's
        c0 = sfft.irfft(fU * sfft.rfft(ub, n) + fL * sfft.rfft(lb, n), n)[M + 1: M + nk + 2]
        n0 = g * (c0 + p_up[: nk + 1] * W0[0] + tail[: nk + 1] * W1[M])
        return (n0 + b0, n1 + b1) if affine else (n0, n1)

    def dense_week(self, earnings: bool):
        """The same operator as explicit matrices (b0, b1, G00, G01, gup, G11):
        only for the cross-check in ml/test_teacher_perp.py."""
        U, L, p_up, tail, b0, b1 = self._week_parts(earnings)
        M, nk = self.M, self.n_k
        i = np.arange(M + 1)[:, None]
        l = np.arange(M + 1)[None, :]
        TU = U[i - l + M + 1]
        TL = L[i - l + 1 + M + 1]
        A11 = TU * (l <= M - 1) + TL * (l >= 1)
        A11[:, M] += tail
        A00 = (TU * (l <= nk - 1) + TL * (l >= 1))[: nk + 1, : nk + 1].copy()
        A00[:, 0] += p_up[: nk + 1]
        A01 = (TU * (l <= M - 1) + TL * (l >= nk + 1))[: nk + 1, nk:].copy()
        A01[:, -1] += tail[: nk + 1]
        g = self.disc * (1.0 - self.prod.a)
        return b0, b1, g * A00, g * A01, g * p_up, g * A11

    def apply_dense(self, wk, W0, W1):
        b0, b1, G00, G01, gup, G11 = wk
        return G00 @ W0 + G01 @ W1[self.n_k:] + b0, gup * W0[0] + G11 @ W1 + b1

    def _solve_cycle(self, weeks, tol):
        """The fixed point of applying `weeks` in order (the last one is applied last)."""
        n0, n1 = self.n_k + 1, self.M + 1

        def cyc(v, affine):
            W0, W1 = v[:n0], v[n0:]
            for wk in weeks:
                W0, W1 = self._apply(wk, W0, W1, affine)
            return np.concatenate([W0, W1])

        d = cyc(np.zeros(n0 + n1), True)
        op = LinearOperator((n0 + n1, n0 + n1), matvec=lambda v: v - cyc(v, False), dtype=np.float64)
        x, info = gmres(op, d, x0=d, rtol=tol, atol=0.0, restart=60, maxiter=50)
        assert info == 0, f"GMRES did not converge ({info})"
        self.residual = float(np.abs(cyc(x, True) - x).max())
        return x[:n0], x[n0:]

    # --- evaluation ---------------------------------------------------------------

    def after_fixing(self, x, knocked_in, j):
        """Value right after a fixing left the state (x <= 1, flag, j)."""
        zq = np.log(np.asarray(x, dtype=np.float64))
        return self._interp(zq, np.asarray(knocked_in, dtype=bool), np.asarray(j, dtype=int))

    def _interp(self, zq, flag, j):
        """W_{flag, j}(z) by linear interpolation in z; below the grid, W1 ~ x."""
        pos = np.clip(-zq / self.h, 0.0, None)
        lo = np.minimum(pos.astype(int), self.M - 1)
        fr = pos - lo
        v1 = np.where(pos <= self.M, self.W1[j, lo] * (1 - fr) + self.W1[j, lo + 1] * fr,
                      self.W1[j, self.M] * np.exp(zq - self.z[self.M]))
        lo0 = np.minimum(lo, self.n_k - 1)
        fr0 = np.clip(pos - lo0, 0.0, 1.0)
        v0 = self.W0[j, lo0] * (1 - fr0) + self.W0[j, lo0 + 1] * fr0
        return np.where(flag, v1, v0)

    def fixings_before(self, tau, tE):
        """n: the fixings before the next release, from the time to it (years).
        0 = the release comes before or at the next fixing."""
        n = np.ceil((np.asarray(tE) - np.asarray(tau)) / self.prod.dt - 1e-12).astype(int)
        return np.maximum(n, 0)

    def value(self, x, knocked_in, tau, tE, chunk: int = 96):
        """Principal value of mid-week states. tau, tE in years; arrays broadcast."""
        tau, tE = np.broadcast_arrays(np.asarray(tau, dtype=np.float64), np.asarray(tE, dtype=np.float64))
        return self.value_n(x, knocked_in, tau, self.fixings_before(tau, tE), chunk)

    def value_n(self, x, knocked_in, tau, n, chunk: int = 96):
        """Principal value of mid-week states, by n = fixings before the next
        release. tau in years; arrays broadcast."""
        x, flag, tau, n = np.broadcast_arrays(np.asarray(x, dtype=np.float64), np.asarray(knocked_in, dtype=bool),
                                              np.asarray(tau, dtype=np.float64), np.asarray(n, dtype=int))
        x, flag, tau, n = x.ravel(), flag.ravel(), tau.ravel(), n.ravel()
        out = np.empty(x.size)
        if self.has_earnings:
            assert int(n.min(initial=0)) >= 0 and int(n.max(initial=0)) <= self.W0.shape[0], "n beyond the kept phases"
            before = n == 0
            j = np.where(before, self.cfg.cycle_fixings - 1, n - 1)
        else:
            before, j = np.zeros(x.size, dtype=bool), np.zeros(x.size, dtype=int)
        det = (tau == 0.0) & ~before
        if det.any():
            out[det] = self._at_fixing(x[det], flag[det], j[det])
        idx = np.flatnonzero(~det)
        idx = idx[np.argsort(x[idx], kind="stable")]       # neighbours in spot share a window of the grid
        for lo in range(0, idx.size, chunk):
            s = idx[lo:lo + chunk]
            out[s] = self._expect(np.log(x[s]), flag[s], tau[s], before[s], j[s])
        return out

    def _at_fixing(self, x, flag, j):
        """The fixing happens now at the exact spot x (the fixing rule, then W)."""
        a, k = self.prod.a, self.prod.k
        heal = x >= 1.0
        f2 = np.where(heal, False, flag | (x < k))
        x2 = np.where(heal, 1.0, x)
        pay = np.where(f2, x2, 1.0)
        return a * pay + (1.0 - a) * self._interp(np.log(x2), f2, j)

    def _expect(self, zs, flag, tau, before, j):
        """E[a p' + (1 - a) W(x')] over the step to the next fixing, per state.
        Per Poisson term only the grid segments within `reach` standard
        deviations of the states' means are summed, with `reach` set so that
        what is left out is below 1e-15 of the value (8.3 for a term of weight
        1, less for the rare many-jump terms)."""
        p, M, nk, h, z = self.prod, self.M, self.n_k, self.h, self.z
        w, m, sd = mixture(self.cfg, self.vol, p.dt, tau, before)
        clean = ~flag
        total = np.zeros(zs.size)
        ln_k = z[nk]
        W0_0 = self.W0[j, 0]
        W1_M = self.W1[j, M]
        c1 = p.a + (1.0 - p.a) * W0_0
        for n in range(w.shape[1]):
            wn = w[:, n]
            live = wn > 1e-15
            if not live.any():
                continue
            # the normal tail beyond `reach` sd, times the weight, stays below 1e-15
            reach = math.sqrt(2.0 * math.log(max(float(wn.max()), 1e-15) / 1e-15)) + 0.5
            mc, sc = m[:, n], sd[:, n]
            c = zs + mc
            p_up = ndtr(c / sc)
            ex = np.exp(c + 0.5 * sc * sc)
            e_b0 = ex * ndtr((-c - sc * sc) / sc)
            p_gek = ndtr((c - ln_k) / sc)
            e_bk = ex * ndtr((ln_k - c - sc * sc) / sc)
            tail = ex * ndtr((z[M] - c - sc * sc) / sc) * math.exp(-z[M])
            # nodes lo .. hi cover every live state's window
            top = float((c + reach * sc)[live].max())
            bot = float((c - reach * sc)[live].min())
            lo = max(0, int(math.floor(-top / h)))
            hi = min(M, int(math.ceil(-bot / h)))
            all1 = np.zeros(zs.size)
            low1 = np.zeros(zs.size)
            seg0 = np.zeros(zs.size)
            if hi > lo:
                rel = z[None, lo:hi + 1] - zs[:, None]        # node positions relative to the source
                u = (rel - mc[:, None]) / sc[:, None]
                cdf, pdf = ndtr(u), _pdf(u)
                # segment s between node s (upper) and s + 1 (lower)
                i0 = cdf[:, :-1] - cdf[:, 1:]
                up = ((mc[:, None] - rel[:, 1:]) * i0 - sc[:, None] * (pdf[:, :-1] - pdf[:, 1:])) / h
                lw = i0 - up
                W1 = self.W1[:, lo:hi + 1][j]
                seg1 = up * W1[:, :-1] + lw * W1[:, 1:]       # knocked-in target, segments lo .. hi - 1
                all1 = seg1.sum(axis=1)
                if hi > nk:                                    # z' < ln k only
                    low1 = seg1[:, max(nk - lo, 0):].sum(axis=1)
                if lo < nk:                                    # clean target, ln k <= z' < 0
                    e0 = min(hi, nk)
                    W0 = self.W0[:, lo:e0 + 1][j]
                    seg0 = (up[:, :e0 - lo] * W0[:, :-1] + lw[:, :e0 - lo] * W0[:, 1:]).sum(axis=1)
            v_ki = p_up * c1 + p.a * e_b0 + (1.0 - p.a) * (all1 + tail * W1_M)
            v_cl = p_up * c1 + p.a * (p_gek - p_up) + (1.0 - p.a) * seg0 \
                + p.a * e_bk + (1.0 - p.a) * (low1 + tail * W1_M)
            total += wn * np.where(clean, v_cl, v_ki)
        return np.exp(-self.cfg.r_disc * tau) * total


class Teacher:
    """The teacher at one vol: two grids, Richardson-extrapolated."""

    def __init__(self, vol: float, cfg: PerpConfig, prod: Product = P1, n_k: int = 256):
        assert n_k % 2 == 0
        self.coarse = Solver(vol, cfg, prod, n_k=n_k // 2)
        self.fine = Solver(vol, cfg, prod, n_k=n_k)

    def value(self, x, knocked_in, tau, tE):
        """Principal value; tau, tE in years."""
        fine = self.fine.value(x, knocked_in, tau, tE)
        return fine + (fine - self.coarse.value(x, knocked_in, tau, tE)) / 3.0

    def value_n(self, x, knocked_in, tau, n):
        """Principal value by n = fixings before the next release; tau in years."""
        fine = self.fine.value_n(x, knocked_in, tau, n)
        return fine + (fine - self.coarse.value_n(x, knocked_in, tau, n)) / 3.0


def price(x, vol, knocked_in, tau_secs, tE_secs, cfg: PerpConfig | None = None, prod: Product = P1,
          n_k: int = 256, cache: dict | None = None):
    """Principal value per unit of live notional for arrays of states (times in
    seconds, vol as a fraction). One fixed point (on two grids) per distinct vol."""
    cfg = load_config() if cfg is None else cfg
    x, vol, flag, tau, tE = np.broadcast_arrays(*(np.asarray(v) for v in (x, vol, knocked_in, tau_secs, tE_secs)))
    shape = x.shape
    x, vol, flag = x.ravel().astype(np.float64), vol.ravel().astype(np.float64), flag.ravel().astype(bool)
    tau, tE = tau.ravel() / YEAR_SECS, tE.ravel() / YEAR_SECS
    out = np.empty(x.size)
    for v in np.unique(vol):
        key = (float(v), cfg.name, prod, n_k)
        t = cache.get(key) if cache is not None else None
        if t is None:
            t = Teacher(float(v), cfg, prod, n_k=n_k)
            if cache is not None:
                cache[key] = t
        sel = vol == v
        out[sel] = t.value(x[sel], flag[sel], tau[sel], tE[sel])
    return out.reshape(shape)


# ---------------------------------------------------------------------------
# independent brute-force Monte Carlo of the same rules
# ---------------------------------------------------------------------------

def fixing_step(x, flag, k):
    """The fixing rule on float arrays: (x', flag', pay) with pay the NOTE's
    principal per unit of the melting slice. x is fixing / H."""
    heal = x >= 1.0
    flag2 = np.where(heal, False, flag | (x < k))
    x2 = np.where(heal, 1.0, x)
    return x2, flag2, np.where(flag2, x2, 1.0)


def mc(x0: float, vol: float, knocked_in: bool, tau: float, tE: float, cfg: PerpConfig, prod: Product = P1,
       years: float = 16.0, paths: int = 2**17, seed: int = 1, writer: bool = False):
    """Principal PV per unit of live notional and its stderr (antithetic pairs).
    tau, tE in years. Simulates every fixing for `years`; what is still alive
    then (e^(-phi * years) of the notional) is dropped. writer=True returns the
    WRITER leg's PV (the slice pays 1 - p) instead."""
    rng = np.random.default_rng(seed)
    half = paths // 2
    sd_d, lam, mu, sj, kappa, se = cfg.components(vol, prod.dt)
    has_e = cfg.vol_ref > 0.0 and cfg.sigma_e > 0.0
    cyc = cfg.cycle_fixings * prod.dt
    x = np.full(2 * half, float(x0))
    flag = np.full(2 * half, bool(knocked_in))
    live, pv = 1.0, np.zeros(2 * half)
    t, t_e = 0.0, (tE if has_e else math.inf)
    n_fix = int(round(years / prod.dt))
    for i in range(n_fix):
        step = tau if i == 0 else prod.dt
        t_next = t + step
        z = rng.standard_normal(half)
        inc = (cfg.r - lam * kappa - 0.5 * sd_d * sd_d) * step + sd_d * math.sqrt(step) * np.concatenate([z, -z])
        if lam > 0.0:
            n = rng.poisson(lam * step, size=half).astype(np.float64)
            zj = rng.standard_normal(half)
            base, spread = n * mu, np.sqrt(n) * sj * zj
            inc += np.concatenate([base + spread, base - spread])
        while t_e <= t_next + 1e-15:              # a release at the fixing time counts as before it
            ze = rng.standard_normal(half)
            inc += -0.5 * se * se + se * np.concatenate([ze, -ze])
            t_e += cyc
        t = t_next
        if step > 0.0 or True:
            x = x * np.exp(inc)
        x, flag, pay = fixing_step(x, flag, prod.k)
        pv += math.exp(-cfg.r_disc * t) * live * prod.a * ((1.0 - pay) if writer else pay)
        live *= 1.0 - prod.a
    pm = 0.5 * (pv[:half] + pv[half:])
    return float(pm.mean()), float(pm.std(ddof=1) / math.sqrt(half))
