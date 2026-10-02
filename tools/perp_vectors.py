"""Scalar reference of the v2 perpetual note's fixing rule, and its conformity vectors.

The rule (contracts/src/interfaces/IPerpSeries.sol, docs/v2-spec.md section 2):
  first fixing:  H = f
  fixing f:      1. f >= H          ->  H = f, knockedIn = False
                 2. else f < k * H  ->  knockedIn = True
                 3. m = floor(s * a / 1e18) of the live notional s melts and pays
                    NOTE   floor(m * (1 + R))       if clean
                           floor(m * (f / H + R))   if knocked in
                    WRITER the rest of floor(m * (1 + R))
  missed:        a fixing that was never recorded reuses the last one;
                 CLOSE_AFTER_MISSED in a row melt everything (m = s) and close

Plain integers only: barriers are exact cross-multiplications in bps, a is
1e18 fixed point, R is USDG base units per 1e6 of notional, s and the releases
are per token base unit in 1e27 fixed point. contracts/test/PerpVectors.t.sol
replays every vector through the PerpPayout library and through a live
PerpSeries and requires exact equality at every fixing.

Usage: python tools/perp_vectors.py [--out contracts/test/vectors/perp_vectors.json]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

UNIT = 10**6
BPS = 10_000
WAD = 10**18
RAY = 10**27
MISSING = 0  # a fixing that was never recorded (the feed never answers 0)
CLOSE_AFTER_MISSED = 4
PRICE_MAX = 10**13  # FixingsRecorder.PRICE_MAX

MELT_WEEKLY = 18_995_352_771_274_247  # 1 - exp(-7/365): phi = 1/yr, weekly fixings
PENDING, LIVE, CLOSED = 0, 1, 2


def run(ki: int, a: int, coupon_reserve: int, first: int, fixings: list[int]) -> dict:
    """`first` is the fixing that sets H; fixings[n - 1] is fixing n = 1, 2, ...
    (MISSING = unrecorded past its fallback deadline). Fixings after the note
    closes are ignored. Returns the final state and the release of every
    processed fixing."""
    h = last = first
    knocked_in = False
    missed_in_a_row = 0
    s = RAY
    note_index = writer_index = 0
    phase = LIVE
    note_release, writer_release = [], []
    for fixing in fixings:
        if phase == CLOSED:
            break
        missed = fixing == MISSING
        if missed:
            fixing = last
        if fixing >= h:  # the ratchet, checked first
            h = fixing
            knocked_in = False
        elif fixing * BPS < ki * h:
            knocked_in = True
        last = fixing
        missed_in_a_row = missed_in_a_row + 1 if missed else 0
        close = missed_in_a_row >= CLOSE_AFTER_MISSED
        m = s if close else s * a // WAD
        total = m * (UNIT + coupon_reserve) // UNIT
        note = m * (fixing * UNIT + coupon_reserve * h) // (h * UNIT) if knocked_in else total
        assert 0 <= note <= total
        s -= m
        note_index += note
        writer_index += total - note
        note_release.append(note)
        writer_release.append(total - note)
        if close:
            phase = CLOSED
    return {
        "phase": phase,
        "reference": h,
        "lastFixing": last,
        "knockedIn": knocked_in,
        "missedInARow": missed_in_a_row,
        "fixingsDone": len(note_release),
        "notionalPerToken": s,
        "noteIndex": note_index,
        "writerIndex": writer_index,
        "noteRelease": note_release,
        "writerRelease": writer_release,
    }


# ---------------------------------------------------------------------------
# vector generation
# ---------------------------------------------------------------------------

def at_bps(price: int, bps: int) -> int:
    """`bps` of `price` (prices are multiples of 1e4, so this is exact)."""
    assert price % BPS == 0
    return price * bps // BPS


def make_vectors(seed: int) -> list[dict]:
    rng = random.Random(seed)
    out: list[dict] = []

    def add(tag, ki, a, r, first, fixings):
        fixings = list(fixings)
        res = run(ki, a, r, first, fixings)
        # the pair's release never exceeds the pair value it melts
        melted = RAY - res["notionalPerToken"]
        assert res["noteIndex"] + res["writerIndex"] <= melted * (UNIT + r) // UNIT, tag
        v = {"tag": tag, "ki": ki, "meltShare": a, "couponReserve": r, "first": first, "fixings": fixings}
        v.update(res)
        out.append(v)

    p1 = (6000, MELT_WEEKLY, 235_000)  # k 60%, phi 1/yr weekly, R = 23.5%
    firsts = [100 * 10**8, 354_28_490_000, 1 * 10**8, 99_990_000]  # 8 decimals, multiples of 1e4

    for first in firsts:
        ki, a, r = p1
        f = lambda bps, base=first: at_bps(base, bps)  # noqa: E731
        add("flat_at_reference", ki, a, r, first, [first] * 5)
        add("ratchet_up", ki, a, r, first, [f(10100), f(10500), f(10500), f(12000)])
        add("drift_down_clean", ki, a, r, first, [f(9000), f(8000), f(7000), f(6001)])
        add("at_ki_not_knocked_in", ki, a, r, first, [f(8000), f(6000), f(7000)])
        add("one_below_ki_knocked_in", ki, a, r, first, [f(8000), f(6000) - 1, f(7000)])
        add("knocked_in_pays_x", ki, a, r, first, [f(5000), f(5500), f(3000), f(9999)])
        add("knocked_in_deep", ki, a, r, first, [f(5999), 1, 1, f(100)])
        add("heal_at_reference", ki, a, r, first, [f(4000), first, f(9000)])
        add("heal_one_below_reference_stays", ki, a, r, first, [f(4000), first - 1, f(9000)])
        add("heal_above_then_new_barrier", ki, a, r, first, [f(4000), f(12000), f(7300), f(7100)])
        add("ratchet_then_knockin_on_new_reference", ki, a, r, first, [f(20000), f(11999), f(13000)])
        add("missed_single_reuses_last", ki, a, r, first, [f(9000), MISSING, f(9500)])
        add("missed_first_reuses_reference", ki, a, r, first, [MISSING, f(9000)])
        add("missed_while_knocked_in", ki, a, r, first, [f(5000), MISSING, MISSING, f(5500)])
        add("missed_three_then_recorded", ki, a, r, first, [f(9000), MISSING, MISSING, MISSING, f(9100), MISSING])
        add("close_clean", ki, a, r, first, [f(9000), MISSING, MISSING, MISSING, MISSING])
        add("close_knocked_in", ki, a, r, first, [f(5000), f(5200), MISSING, MISSING, MISSING, MISSING])
        add("close_from_first", ki, a, r, first, [MISSING] * 4)
        add("close_ignores_later_fixings", ki, a, r, first, [f(7000)] + [MISSING] * 4 + [f(3000), f(20000)])
        add("close_at_reference_heals", ki, a, r, first, [f(5000), f(11000)] + [MISSING] * 4)

    first = 250 * 10**8
    f = lambda bps: at_bps(first, bps)  # noqa: E731
    path = [f(9000), f(5000), f(4000), MISSING, f(10000), f(11000), f(6500), f(6700)]
    products = [
        (10_000, MELT_WEEKLY, 235_000),  # any fixing below the reference knocks in
        (1, MELT_WEEKLY, 235_000),  # ki at the minimum
        (6000, MELT_WEEKLY, 0),  # no coupon reserve
        (6000, MELT_WEEKLY, 5 * UNIT),  # the factory's maximum
        (6000, 1, 235_000),  # the smallest melt
        (6000, WAD - 1, 235_000),  # nearly everything melts at once
        (6000, WAD // 2, 95_000),  # half per fixing
        (8000, 114_148_735_678_798, 357_000),  # hourly fixings at phi = 1/yr
        (6500, 2_735_976_403_140_714, 120_000),  # daily fixings at phi = 1/yr
    ]
    for ki, a, r in products:
        add(f"product_{ki}_{a}_{r}_path", ki, a, r, first, path)
        add(f"product_{ki}_{a}_{r}_close", ki, a, r, first, [f(5999)] + [MISSING] * 4)
    add("price_bounds", 6000, MELT_WEEKLY, 235_000, 1, [PRICE_MAX, 1, PRICE_MAX - 1, PRICE_MAX])
    add("reference_at_price_max", 6000, MELT_WEEKLY, 5 * UNIT, PRICE_MAX, [PRICE_MAX - 1, 5 * 10**12, 1])

    # random walks: weekly log-returns at 55% vol, some unrecorded fixings (single and in runs)
    while len(out) < 170:
        ki, a, r = rng.choice([p1, p1, p1, (7000, MELT_WEEKLY, 95_000), (5000, 2 * MELT_WEEKLY, 357_000),
                               (8000, WAD // 10, 500_000)])
        n = rng.choice([8, 26, 26, 52, 104])
        first = rng.randrange(1, 10**7) * 10**4  # $0.0001 .. $1000 at 8 decimals
        s = float(first)
        fixings = []
        gap = 0
        for _ in range(n):
            s *= 2.718281828459045 ** rng.gauss(-0.5 * 0.55**2 / 52, 0.55 / 52**0.5)
            if gap == 0 and rng.random() < 0.04:
                gap = rng.choice([1, 1, 2, 3, 4])
            if gap:
                gap -= 1
                fixings.append(MISSING)
            else:
                fixings.append(min(max(int(s), 1), PRICE_MAX))
        add("random_walk", ki, a, r, first, fixings)
    # ten years of weekly fixings: the notional per token falls to ~0.005%
    for i in range(2):
        s = float(firsts[i])
        fixings = []
        for _ in range(520):
            s *= 2.718281828459045 ** rng.gauss(-0.5 * 0.55**2 / 52, 0.55 / 52**0.5)
            fixings.append(min(max(int(s), 1), PRICE_MAX))
        add("ten_years_weekly", *p1, firsts[i], fixings)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--out", default=str(root / "contracts/test/vectors/perp_vectors.json"))
    ap.add_argument("--seed", type=int, default=20261001)
    args = ap.parse_args()

    vectors = make_vectors(args.seed)
    tags = {v["tag"] for v in vectors}
    for required in ("ratchet_up", "at_ki_not_knocked_in", "one_below_ki_knocked_in", "heal_at_reference",
                     "missed_single_reuses_last", "close_clean", "close_knocked_in", "ten_years_weekly"):
        assert required in tags, required
    for v in vectors:
        assert all(0 <= x <= PRICE_MAX for x in v["fixings"])
        assert 0 < v["first"] <= PRICE_MAX

    # 1e27-scale values exceed what a JSON number holds exactly: write them as decimal strings
    big = ("notionalPerToken", "noteIndex", "writerIndex")
    rows = []
    for v in vectors:
        row = dict(v)
        for key in big:
            row[key] = str(v[key])
        row["noteRelease"] = [str(x) for x in v["noteRelease"]]
        row["writerRelease"] = [str(x) for x in v["writerRelease"]]
        rows.append(row)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump({"seed": args.seed, "closeAfterMissed": CLOSE_AFTER_MISSED, "count": len(rows), "vectors": rows},
                  fh, indent=1)
        fh.write("\n")
    n_closed = sum(v["phase"] == CLOSED for v in vectors)
    n_ki = sum(v["knockedIn"] for v in vectors)
    n_missed = sum(MISSING in v["fixings"] for v in vectors)
    n_fix = sum(v["fixingsDone"] for v in vectors)
    print(f"wrote {len(rows)} vectors ({n_fix} fixings) to {args.out}: {n_closed} closed, {n_ki} end knocked in, "
          f"{n_missed} with a missed fixing, {len(tags)} tags")


if __name__ == "__main__":
    main()
