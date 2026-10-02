"""Integer twin of contracts/src/PerpMath.sol and PerpFormula.sol, and its vectors.

The closed-form price of the v2 perpetual note's principal (docs/v2-spec.md
section 3), computed exactly as the contract computes it: 1e18 fixed point,
every division truncating towards zero, the same series lengths. Plain Python
integers, so the result is bit-identical; contracts/test/PerpFormula.t.sol
replays the vectors and requires equality.

`--check` compares the integer result with a 50-digit reference (mpmath) over
a grid of worlds and states and prints the largest difference: this is the
"rounding of x^beta in fixed point" measurement of docs/v2-perpetual-note.md.

Usage: python tools/perp_formula.py [--out contracts/test/vectors/perp_formula_vectors.json] [--check]
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

WAD = 10**18
BPS = 10_000
YEAR = 31_536_000
LN2 = 693_147_180_559_945_309
EXP_MIN = -41_446_531_673_892_822_312
EXP_MAX = 80 * WAD
BGK = 582_600_000_000_000_000
SIGMA_MIN = 10**16
SIGMA_MAX = 3 * WAD
BETA_MAX = 25 * WAD

MELT_WEEKLY = 18_995_352_771_274_247  # 1 - exp(-7/365)
WEEK = 604_800


class Refused(ValueError):
    """The contract reverts (VolOutOfRange, BadWorld, ExpOverflow, LnNonPositive)."""


def sdiv(a: int, b: int) -> int:
    """Solidity's signed division: truncates towards zero."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


# --- PerpMath ----------------------------------------------------------------

def exp_wad(x: int) -> int:
    if x <= EXP_MIN:
        return 0
    if x > EXP_MAX:
        raise Refused("ExpOverflow")
    k = sdiv(x + LN2 // 2 if x >= 0 else x - LN2 // 2, LN2)
    r = x - k * LN2
    term = total = WAD
    for n in range(1, 17):
        term = sdiv(term * r, n * WAD)
        total += term
    return total << k if k >= 0 else total >> -k


def ln_wad(x: int) -> int:
    if x <= 0:
        raise Refused("LnNonPositive")
    k = x.bit_length() - 1 - 59
    y = x >> k if k >= 0 else x << -k
    z = sdiv((y - WAD) * WAD, y + WAD)
    z2 = z * z // WAD
    power = total = z
    for n in range(3, 34, 2):
        power = sdiv(power * z2, WAD)
        total += sdiv(power, n)
    return 2 * total + k * LN2


def sqrt_wad(x: int) -> int:
    return math.isqrt(x * WAD)


def pow_wad(ln_x: int, y: int) -> int:
    return exp_wad(sdiv(y * ln_x, WAD))


# --- PerpFormula ---------------------------------------------------------------

def coefficients(sigma: int, r: int, rho: int, phi: int, k: int, dt: int) -> dict:
    if not (SIGMA_MIN <= sigma <= SIGMA_MAX):
        raise Refused("VolOutOfRange")
    if phi > 100 * WAD or dt == 0 or dt > WAD:
        raise Refused("BadWorld")
    melt = rho + phi
    if melt <= 0 or melt <= r or k == 0 or k > WAD:
        raise Refused("BadWorld")
    s2 = sigma * sigma // WAD
    b = r - s2 // 2
    root = sqrt_wad(b * b // WAD + 2 * s2 * melt // WAD)
    c = {"betaPlus": sdiv((root - b) * WAD, s2), "betaMinus": sdiv(-(root + b) * WAD, s2)}
    if c["betaPlus"] > BETA_MAX or c["betaMinus"] < -BETA_MAX:
        raise Refused("VolOutOfRange")
    c["alpha"] = sdiv(phi * WAD, melt - r)
    c["p0"] = sdiv(phi * WAD, melt)
    c["lnTop"] = BGK * sigma // WAD * sqrt_wad(dt) // WAD
    c["top"] = exp_wad(c["lnTop"])
    c["kPrime"] = k * WAD // c["top"]
    ln_kappa = ln_wad(c["kPrime"]) - c["lnTop"]
    kappa_plus = pow_wad(ln_kappa, c["betaPlus"])
    kappa_minus = pow_wad(ln_kappa, c["betaMinus"])
    g_top = sdiv(c["alpha"] * c["top"], WAD) - c["p0"]
    g_k = sdiv(c["alpha"] * c["kPrime"], WAD) - c["p0"]
    c["b"] = sdiv((g_k - sdiv(g_top * kappa_plus, WAD)) * WAD, kappa_minus - kappa_plus)
    c["a"] = sdiv(-c["b"] * c["betaMinus"], c["betaPlus"])
    c["c"] = c["a"] - (g_top - c["b"])
    return c


def principal(c: dict, x: int, knocked_in: bool) -> int:
    if x >= c["top"]:
        v = c["p0"] + c["a"] + c["b"]
    elif x == 0:
        v = 0
    else:
        ln_u = ln_wad(x) - c["lnTop"]
        if knocked_in or x < c["kPrime"]:
            v = sdiv(c["alpha"] * x, WAD) + sdiv(c["c"] * pow_wad(ln_u, c["betaPlus"]), WAD)
        else:
            v = (c["p0"] + sdiv(c["a"] * pow_wad(ln_u, c["betaPlus"]), WAD)
                 + sdiv(c["b"] * pow_wad(ln_u, c["betaMinus"]), WAD))
    return max(v, 0)


def melt_rate(a: int, dt: int) -> int:
    return -ln_wad(WAD - a) * WAD // dt


def coupon(a: int, rho: int, dt: int, tau: int) -> int:
    if rho == 0:
        return WAD
    keep = sdiv((WAD - a) * exp_wad(sdiv(-rho * dt, WAD)), WAD)
    return sdiv(a * exp_wad(sdiv(-rho * tau, WAD)), WAD - keep)


# --- the quoter's assembly (PerpQuoter.sol; used by tools/perp_quoter_vectors.py) ---

def world_of(ki_bps: int, melt_share: int, interval: int, drift_bps: int, discount_bps: int, vol_bps: int):
    """(sigma, r, rho, phi, k, dt) in WAD from the series terms, the model's rates and the listing vol."""
    dt = interval * WAD // YEAR
    return (vol_bps * WAD // BPS, drift_bps * WAD // BPS, discount_bps * WAD // BPS,
            melt_rate(melt_share, dt), ki_bps * WAD // BPS, dt)


def round_bps(wad: int) -> int:
    """A non-negative WAD value in bps, rounded half up."""
    return (wad * BPS + WAD // 2) // WAD


# --- 50-digit reference ----------------------------------------------------------

def reference(sigma, r, rho, phi, k, dt, x, knocked_in):
    """The same formula in mpmath at 50 digits; arguments and result are mp numbers / floats."""
    import mpmath as mp
    mp.mp.dps = 50
    sigma, r, rho, phi, k, dt, x = (mp.mpf(v) for v in (sigma, r, rho, phi, k, dt, x))
    s2 = sigma * sigma
    b = r - s2 / 2
    root = mp.sqrt(b * b + 2 * s2 * (rho + phi))
    bp, bm = (root - b) / s2, -(root + b) / s2
    alpha, p0 = phi / (rho + phi - r), phi / (rho + phi)
    top = mp.e ** (mp.mpf(BGK) / WAD * sigma * mp.sqrt(dt))
    kp = k / top
    kappa = kp / top
    g = lambda y: alpha * y - p0  # noqa: E731
    bq = (g(kp) - g(top) * kappa**bp) / (kappa**bm - kappa**bp)
    aq = -bq * bm / bp
    cq = aq - (g(top) - bq)
    if x >= top:
        v = p0 + aq + bq
    elif x == 0:
        v = mp.mpf(0)
    else:
        u = x / top
        v = alpha * x + cq * u**bp if knocked_in or x < kp else p0 + aq * u**bp + bq * u**bm
    return max(v, mp.mpf(0))


def check() -> None:
    import mpmath as mp
    mp.mp.dps = 50
    worst = (0, None)
    worst_fn = {"exp": 0, "ln": 0}
    for i in range(-4000, 801):
        x = i * WAD // 100
        err = abs(mp.mpf(exp_wad(x)) / WAD - mp.e ** (mp.mpf(x) / WAD)) / max(mp.e ** (mp.mpf(x) / WAD), 1)
        worst_fn["exp"] = max(worst_fn["exp"], float(err))
    for i in range(1, 3001):
        x = i * WAD // 1000
        err = abs(mp.mpf(ln_wad(x)) / WAD - mp.log(mp.mpf(x) / WAD))
        worst_fn["ln"] = max(worst_fn["ln"], float(err))
    print(f"exp_wad: max error {worst_fn['exp']:.2e} (relative above 1, absolute below), x in [-40, 8]")
    print(f"ln_wad:  max absolute error {worst_fn['ln']:.2e}, x in [0.001, 3]")
    n = refused = 0
    worst20 = 0
    lowest = {}
    for name, (r, rho, phi, dt) in WORLDS.items():
        for k_bps in (5000, 6000, 8000, 10_000):
            for vol in range(100, 15_001, 100):
                w = (vol * WAD // BPS, r, rho, phi, k_bps * WAD // BPS, dt)
                try:
                    c = coefficients(*w)
                except Refused:
                    refused += 1
                    continue
                lowest[name] = min(lowest.get(name, vol), vol)
                for x_bps in list(range(100, 13_001, 200)) + [1, k_bps - 1, k_bps, k_bps + 1, 9999, 10_000, 10_001]:
                    for ki in (False, True):
                        x = x_bps * WAD // BPS
                        got = principal(c, x, ki)
                        want = reference(*(mp.mpf(v) / WAD for v in w), mp.mpf(x) / WAD, ki)
                        err = abs(mp.mpf(got) / WAD - want)
                        n += 1
                        if err > worst[0]:
                            worst = (err, (name, k_bps, vol, x_bps, ki))
                        if vol >= 2000:
                            worst20 = max(worst20, err)
    print(f"principal: {n} states in {len(WORLDS)} worlds, k 50-100%, x 0.01%-130%, every vol 1-150% the "
          f"contract accepts ({refused} world/vol pairs refused; lowest accepted vol per world: "
          + ", ".join(f"{k} {v / 100:.0f}%" for k, v in lowest.items()) + ")")
    print(f"  max |fixed point - 50-digit| = {float(worst[0]) * 1e4:.2e} bps at {worst[1]}")
    print(f"  from 20% vol up: {float(worst20) * 1e4:.2e} bps")


# --- vectors -----------------------------------------------------------------------

WORLDS = {
    # name: (r, rho, phi, dt) in WAD
    "p1": (4 * WAD // 100, 0, None, WEEK * WAD // YEAR),  # phi from MELT_WEEKLY: model P1's world
    "doc": (4 * WAD // 100, 4 * WAD // 100, WAD, WAD // 52),  # docs/v2-perpetual-note.md's check table
    "zero_rates": (0, 0, WAD, WEEK * WAD // YEAR),
    "daily": (4 * WAD // 100, 0, WAD, 86_400 * WAD // YEAR),
    "fast_melt": (2 * WAD // 100, WAD // 100, 4 * WAD, WEEK * WAD // YEAR),
}
WORLDS["p1"] = (WORLDS["p1"][0], 0, melt_rate(MELT_WEEKLY, WEEK * WAD // YEAR), WORLDS["p1"][3])


def make_vectors() -> dict:
    """Columns, not rows: forge reads each column with one parse call."""
    exp_x = [EXP_MIN, EXP_MIN + 1, -40 * WAD, -10 * WAD, -WAD, -1, 0, 1, WAD // 3, WAD, LN2 // 2, LN2 // 2 + 1,
             -LN2 // 2, 5 * WAD, 12_345_678_901_234_567_890, EXP_MAX]
    ln_x = [1, 1000, WAD // 1000, WAD // 2, 6 * WAD // 10, WAD - 1, WAD, WAD + 1, 2**59, 2**60 - 1, 2**60,
            13 * WAD // 10, 2 * WAD, 10**24]
    cols = {key: [] for key in ("sigma", "r", "rho", "phi", "k", "dt", "x", "clean", "knockedIn")}
    refused = {key: [] for key in ("sigma", "r", "rho", "phi", "k", "dt")}
    xs = [0, 1, 100, 2000, 4000, 5000, 5500, 5800, 5999, 6000, 6001, 6500, 7000, 8000, 9000, 9500, 9999, 10_000,
          10_001, 10_200, 10_500, 11_000, 13_000, 15_000]
    for name, (r, rho, phi, dt) in WORLDS.items():
        for k_bps in ((6000,) if name != "doc" else (5000, 6000, 8000, 10_000)):
            for vol in (500, 900, 2000, 3000, 5500, 8000, 9000, 15_000, 30_000):
                w = dict(sigma=vol * WAD // BPS, r=r, rho=rho, phi=phi, k=k_bps * WAD // BPS, dt=dt)
                try:
                    c = coefficients(**w)
                except Refused:
                    for key, v in w.items():
                        refused[key].append(str(v))
                    continue
                edge = [c["kPrime"] - 1, c["kPrime"], c["top"] - 1, c["top"]]
                for x in [b * WAD // BPS for b in xs] + edge:
                    for key, v in w.items():
                        cols[key].append(str(v))
                    cols["x"].append(str(x))
                    cols["clean"].append(str(principal(c, x, False)))
                    cols["knockedIn"].append(str(principal(c, x, True)))
    coupons = [(MELT_WEEKLY, 0, WEEK * WAD // YEAR, 0),
               (MELT_WEEKLY, 4 * WAD // 100, WEEK * WAD // YEAR, 0),
               (MELT_WEEKLY, 4 * WAD // 100, WEEK * WAD // YEAR, WEEK * WAD // YEAR),
               (MELT_WEEKLY, 4 * WAD // 100, WEEK * WAD // YEAR, 3 * 86_400 * WAD // YEAR),
               (WAD // 2, 10 * WAD // 100, WAD // 12, WAD // 24),
               (1, 4 * WAD // 100, WAD // 52, 0)]
    melts = [(MELT_WEEKLY, WEEK * WAD // YEAR), (114_148_735_678_798, 3600 * WAD // YEAR), (WAD // 2, WAD // 12),
             (1, WAD), (WAD - 1, WAD)]
    strs = lambda values: [str(v) for v in values]  # noqa: E731
    return {
        "exp": {"x": strs(exp_x), "y": strs(exp_wad(x) for x in exp_x)},
        "ln": {"x": strs(ln_x), "y": strs(ln_wad(x) for x in ln_x)},
        "count": len(cols["x"]),
        "formula": cols,
        "refused": refused,
        "coupon": {"a": strs(c[0] for c in coupons), "rho": strs(c[1] for c in coupons),
                   "dt": strs(c[2] for c in coupons), "tau": strs(c[3] for c in coupons),
                   "value": strs(coupon(*c) for c in coupons)},
        "meltRate": {"a": strs(m[0] for m in melts), "dt": strs(m[1] for m in melts),
                     "phi": strs(melt_rate(*m) for m in melts)},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--out", default=str(root / "contracts/test/vectors/perp_formula_vectors.json"))
    ap.add_argument("--check", action="store_true", help="compare with a 50-digit reference (needs mpmath)")
    args = ap.parse_args()
    if args.check:
        check()
        return
    out = make_vectors()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {len(out['exp']['x'])} exp and {len(out['ln']['x'])} ln vectors, {out['count']} formula vectors, "
          f"{len(out['refused']['sigma'])} refused worlds, {len(out['coupon']['a'])} coupon vectors and "
          f"{len(out['meltRate']['a'])} melt rates to {args.out}")
    # the P1 world at the doc's reference state, for the eye
    r, rho, phi, dt = WORLDS["p1"]
    c = coefficients(55 * WAD // 100, r, rho, phi, 6 * WAD // 10, dt)
    print(f"P1 world, vol 55%: beta+ {c['betaPlus'] / WAD:.4f}, beta- {c['betaMinus'] / WAD:.4f}, top "
          f"{c['top'] / WAD:.5f}, k' {c['kPrime'] / WAD:.5f}; principal at x = 1 clean "
          f"{principal(c, WAD, False) / WAD:.6f}, at x = 0.8 knocked in {principal(c, 8 * WAD // 10, True) / WAD:.6f}")


if __name__ == "__main__":
    main()
