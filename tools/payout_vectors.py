"""Scalar reference of the normative note payout, and its conformity vectors.

The rule (contracts/src/interfaces/INoteSeries.sol, docs/teacher-spec.md §5):
  c = coupon per period (bps of notional), N = observationCount
  barrier observations i = 1..N, maturity fixing M one interval after the last
  knock-in:  any observation fixing_i < ki * initial      (latching; not checked at M)
  autocall:  first i with fixing_i >= ac * initial  ->  1 + c * i, settle at i
  maturity:  knocked in and fixing_M < initial      ->  fixing_M / initial + c * (N + 1)
             otherwise                              ->  1 + c * (N + 1)
  fallback:  a missing fixing reuses the previous one (the strike fixing for i = 1)

Plain integers only, written to be read next to ml/teacher.py (`_simulate`):
barriers are exact cross-multiplications in bps, payouts are USDG base units
per NOTE (1 NOTE = 1e6), and the one division (fixing_M / initial) rounds
down. contracts/test/PayoutVectors.t.sol replays every vector through the
AutocallPayout library and through a live NoteSeries and requires exact
equality.

Usage: python tools/payout_vectors.py [--out contracts/test/vectors/payout_vectors.json]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

UNIT = 10**6  # base units per NOTE of notional
BPS = 10_000
MISSING = 0  # a fixing that was never recorded (the feed never answers 0)


def payout(ki: int, ac: int, c: int, n: int, initial: int, fixings: list[int]) -> dict:
    """fixings[i - 1] is the fixing of observation i = 1..N, fixings[N] the
    maturity fixing; MISSING means unrecorded past the fallback deadline."""
    assert len(fixings) == n + 1
    last = initial
    knocked_in = False
    for i in range(1, n + 2):
        fixing = fixings[i - 1]
        if fixing == MISSING:
            fixing = last  # fallback: previous fixing
        last = fixing
        if i <= n:
            if fixing * BPS >= ac * initial:  # autocall, checked first
                return _result(UNIT + c * i * UNIT // BPS, i, i, True, knocked_in)
            if fixing * BPS < ki * initial:  # knock-in latches
                knocked_in = True
        else:  # maturity: no barrier check, compare with the initial fixing
            coupons = c * (n + 1) * UNIT // BPS
            if knocked_in and fixing < initial:
                pay = fixing * UNIT // initial + coupons
            else:
                pay = UNIT + coupons
            return _result(pay, n + 1, n, False, knocked_in)
    raise AssertionError("unreachable")


def _result(pay: int, settled_at: int, obs_done: int, autocalled: bool, knocked_in: bool) -> dict:
    return {
        "payoutPerNote": pay,
        "settledAt": settled_at,
        "observationsDone": obs_done,
        "autocalled": autocalled,
        "knockedIn": knocked_in,
    }


def max_payout(c: int, n: int) -> int:
    return UNIT + c * (n + 1) * UNIT // BPS


# ---------------------------------------------------------------------------
# vector generation
# ---------------------------------------------------------------------------

def at_bps(initial: int, bps: int) -> int:
    """Fixing at exactly `bps` of initial (initials are multiples of 1e4)."""
    assert initial % BPS == 0
    return initial * bps // BPS


def make_vectors(seed: int) -> list[dict]:
    rng = random.Random(seed)
    out: list[dict] = []

    def add(tag, ki, ac, c, n, initial, fixings):
        fixings = list(fixings)  # callers keep mutating their list
        v = {"tag": tag, "ki": ki, "ac": ac, "coupon": c, "n": n, "initial": initial, "fixings": fixings}
        v.update(payout(ki, ac, c, n, initial, fixings))
        v["maxPayoutPerNote"] = max_payout(c, n)
        out.append(v)

    k1 = (6000, 10000, 25, 26)  # the K1 product
    initials = [100 * 10**8, 354_28_490_000, 1 * 10**8, 99_990_000]  # 8 decimals, multiples of 1e4

    def flat(n, bps, initial):
        return [at_bps(initial, bps)] * (n + 1)

    for initial in initials:
        ki, ac, c, n = k1
        # autocall at the first observation
        f = flat(n, 9000, initial)
        f[0] = at_bps(initial, 10500)
        add("autocall_first", ki, ac, c, n, initial, f)
        # autocall at exactly the barrier / one unit below it at the first observation
        f = flat(n, 9000, initial)
        f[0] = at_bps(initial, ac)
        add("autocall_first_at_barrier", ki, ac, c, n, initial, f)
        f = flat(n, 9000, initial)
        f[0] = at_bps(initial, ac) - 1
        add("no_autocall_one_below_barrier", ki, ac, c, n, initial, f)
        # autocall at the last observation, with and without knock-in before
        f = flat(n, 9000, initial)
        f[n - 1] = at_bps(initial, 10001)
        add("autocall_last", ki, ac, c, n, initial, f)
        f[3] = at_bps(initial, 5000)
        add("knockin_then_autocall_last", ki, ac, c, n, initial, f)
        # knock-in exactly at / one unit below the barrier
        f = flat(n, 8000, initial)
        f[5] = at_bps(initial, ki)
        f[n] = at_bps(initial, 7000)
        add("at_ki_not_knocked_in", ki, ac, c, n, initial, f)
        f[5] = at_bps(initial, ki) - 1
        add("one_below_ki_knocked_in", ki, ac, c, n, initial, f)
        # knock-in, maturity below / at / above initial
        f = flat(n, 8000, initial)
        f[2] = at_bps(initial, 4000)
        for mat in (1, 3000, 9999, 10000, 10001, 15000):
            g = list(f)
            g[n] = at_bps(initial, mat) if mat > 1 else 1
            add(f"knockin_maturity_{mat}", ki, ac, c, n, initial, g)
        g = list(f)
        g[n] = initial - 1
        add("knockin_maturity_one_below_initial", ki, ac, c, n, initial, g)
        # knock-in then recovery: later observations above ki, maturity above initial
        f = flat(n, 9500, initial)
        f[0] = at_bps(initial, 5500)
        f[n] = at_bps(initial, 10200)
        add("knockin_recovery_maturity_above", ki, ac, c, n, initial, f)
        # no knock-in, maturity deep below initial: par + coupons
        f = flat(n, 7000, initial)
        f[n] = at_bps(initial, 2000)
        add("no_knockin_maturity_below", ki, ac, c, n, initial, f)
        # knock-in at the maturity fixing does not count
        f = flat(n, 9000, initial)
        f[n] = at_bps(initial, 1000)
        add("ki_not_checked_at_maturity", ki, ac, c, n, initial, f)
        # fallback fixings
        f = flat(n, 9000, initial)
        f[0] = MISSING  # obs 1 reuses the strike fixing (= initial >= ac): autocall
        add("fallback_first_uses_strike", ki, ac, c, n, initial, f)
        f = flat(n, 9000, initial)
        f[4] = at_bps(initial, 5000)
        f[5] = MISSING
        f[6] = MISSING
        f[n] = at_bps(initial, 8000)
        add("fallback_after_knockin", ki, ac, c, n, initial, f)
        f = flat(n, 9000, initial)
        f[7] = at_bps(initial, 5900)
        f[n] = MISSING  # maturity reuses fixing N
        add("fallback_maturity", ki, ac, c, n, initial, f)
        f = flat(n, 9000, initial)
        f[9] = at_bps(initial, 11000)
        f[10] = MISSING
        add("fallback_after_autocall_irrelevant", ki, ac, c, n, initial, f)
        f = [MISSING] * (n + 1)
        f[0] = at_bps(initial, 9000)
        add("fallback_all_after_first", ki, ac, c, n, initial, f)

    # other products: barriers, coupons, counts
    products = [
        (5000, 9000, 100, 4),
        (8000, 10000, 0, 1),
        (6500, 11000, 1500, 12),
        (9000, 9000, 10_000, 3),  # ki == ac
        (20000, 20000, 50, 2),  # ki == ac at the max
        (1, 10000, 25, 8),  # ki at the minimum
        (6000, 10000, 25, 104),  # longest schedule
    ]
    for ki, ac, c, n in products:
        initial = 250 * 10**8
        f = [at_bps(initial, max(ki, 1))] * (n + 1)
        add(f"product_{ki}_{ac}_{c}_{n}_flat_at_ki", ki, ac, c, n, initial, f)
        f = [at_bps(initial, ac)] + [at_bps(initial, 1)] * n
        add(f"product_{ki}_{ac}_{c}_{n}_autocall_first", ki, ac, c, n, initial, f)
        f = [at_bps(initial, max(ki - 1, 1))] * n + [at_bps(initial, 5000)]
        add(f"product_{ki}_{ac}_{c}_{n}_knockin_maturity_half", ki, ac, c, n, initial, f)
        f = [at_bps(initial, max(ki - 1, 1))] * (n - 1) + [at_bps(initial, ac)] + [1]
        add(f"product_{ki}_{ac}_{c}_{n}_autocall_last", ki, ac, c, n, initial, f)

    # random walks (weekly log-returns, 55% vol, occasional missing fixings)
    while len(out) < 260:
        ki, ac, c, n = rng.choice([k1, k1, k1, (5000, 9000, 100, 4), (6500, 11000, 1500, 12), (7000, 10000, 40, 52)])
        initial = rng.randrange(1, 10**7) * 10**4  # $0.0001 .. $1000 at 8 decimals
        s = float(initial)
        fixings = []
        for _ in range(n + 1):
            s *= 2.718281828459045 ** rng.gauss(-0.5 * 0.55**2 / 52, 0.55 / 52**0.5)
            fixing = min(max(int(s), 1), 10**13)
            fixings.append(MISSING if rng.random() < 0.05 else fixing)
        add("random_walk", ki, ac, c, n, initial, fixings)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--out", default=str(root / "contracts/test/vectors/payout_vectors.json"))
    ap.add_argument("--seed", type=int, default=20260929)
    args = ap.parse_args()

    vectors = make_vectors(args.seed)
    tags = {v["tag"] for v in vectors}
    for required in ("autocall_first", "autocall_last", "knockin_then_autocall_last",
                     "knockin_recovery_maturity_above", "knockin_maturity_3000", "knockin_maturity_15000",
                     "autocall_first_at_barrier", "at_ki_not_knocked_in", "knockin_maturity_10000",
                     "fallback_first_uses_strike", "fallback_maturity"):
        assert required in tags, required
    for v in vectors:  # the pair always covers both legs
        assert 0 < v["payoutPerNote"] <= v["maxPayoutPerNote"]
        assert all(0 <= f <= 10**13 for f in v["fixings"])

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump({"seed": args.seed, "count": len(vectors), "vectors": vectors}, fh, indent=1)
        fh.write("\n")
    n_ac = sum(v["autocalled"] for v in vectors)
    n_ki = sum(v["knockedIn"] for v in vectors)
    n_fb = sum(MISSING in v["fixings"][: v["settledAt"]] for v in vectors)
    print(f"wrote {len(vectors)} vectors to {args.out}: {n_ac} autocalled, {n_ki} knocked in, "
          f"{n_fb} use a fallback before settling, {len(tags)} tags")


if __name__ == "__main__":
    main()
