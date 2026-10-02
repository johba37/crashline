"""Quoter conformity vectors for the v2 perpetual note: note state -> PerpPricerInputs
-> closed form + model correction + coupon -> priceBps.

For a set of note states of one product this computes, in plain integers,
exactly what PerpQuoter must hand the model (featureSpecVersion 2, the
derivation in contracts/src/PerpQuoter.sol), asks the integer reference
(`pricer_quant.forward`, the bit-exact twin of the Stylus contract) for the
model's correction or the refusal it raises, computes the closed form with the
integer twin of PerpFormula.sol (tools/perp_formula.py), and assembles the
price the way the quoter does. contracts/test/PerpQuoter.t.sol rebuilds each
state on a live series and requires the quoter's inputs to match field by
field and the quote to match part by part.

Without --model the vectors are for the formula-only pricer
(contracts/src/PerpFormulaPricer.sol, twin below): its correction is the
fixing accrual, and it refuses a vol outside its range and the two bands next
to the barriers before a fixing.

Usage: python tools/perp_quoter_vectors.py [--model model/synthetic-p]
           [--out contracts/test/vectors/perp_quoter_vectors.json]
       python tools/perp_quoter_vectors.py --model model/p1 --inputs 9000,5500,300000,5,0 --reserve 235000
           one quote for inputs read from a chain (contracts/script/e2e-perp-devnode.sh): prints
           "<formulaBps> <correctionBps> <couponBps> <priceBps>", or the refusal
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import perp_formula as pf  # noqa: E402
import pricer_quant as pq  # noqa: E402

WEEK = 604_800
BPS = 10_000
UINT16_MAX = 2**16 - 1
UINT8_MAX = 2**8 - 1

# the P1 product (docs/v2-spec.md section 4); a model's own product section replaces it
PRODUCT = {"kiBarrierBps": 6000, "meltShareWad": pf.MELT_WEEKLY, "fixingIntervalSecs": WEEK, "driftBps": 400,
           "discountBps": 0}
# where the formula-only pricer answers: model/p1's two fixing bands and its vol range
BANDS = {"fixingBandSecs": 21_600, "knockInBandBps": 1000, "healBandBps": 500, "volMinBps": 2000, "volMaxBps": 9000}

# (label, first [8 dec], reference bps of first, spot bps of reference, fixingsDone, knockedIn,
#  secs since the last fixing, secs to the next earnings, couponReserve)
# spot None: a raw price that exercises the floor (FLOOR_SPOT below)
# With earnings in 40 days the model's input is 5 or 6 fixings before the release.
D = 86_400
STATES = [
    ("just_after_a_fixing_at_the_reference", 100 * 10**8, 10_000, 10_000, 3, 0, 1, 40 * D, 235_000),
    ("mid_week", 354_28_490_000, 10_000, 8_500, 3, 0, 302_400, 40 * D, 235_000),
    ("first_week", 100 * 10**8, 10_000, 9_200, 0, 0, 2 * D, 40 * D, 235_000),
    ("at_the_fixing", 100 * 10**8, 10_000, 9_000, 5, 0, WEEK, 40 * D, 235_000),  # timeToNextFixing = 0
    ("above_the_reference_mid_week", 100 * 10**8, 10_000, 10_800, 2, 0, 3 * D, 40 * D, 235_000),
    ("far_above_the_reference", 100 * 10**8, 10_000, 12_900, 2, 0, 3 * D, 40 * D, 235_000),
    ("clean_below_the_barrier_mid_week", 100 * 10**8, 10_000, 5_800, 2, 0, 3 * D, 40 * D, 235_000),
    ("clean_at_the_barrier_on_fixing_day", 100 * 10**8, 10_000, 6_000, 2, 0, WEEK - 3600, 40 * D, 235_000),
    ("clean_near_the_barrier_before_fixing_day", 100 * 10**8, 10_000, 6_000, 2, 0, WEEK - D - 1, 40 * D, 235_000),
    ("knocked_in", 100 * 10**8, 10_000, 7_000, 4, 1, 2 * D, 40 * D, 235_000),
    ("knocked_in_at_the_barrier_on_fixing_day", 100 * 10**8, 10_000, 6_000, 4, 1, WEEK - 3600, 40 * D, 235_000),
    ("knocked_in_deep", 100 * 10**8, 10_000, 2_500, 4, 1, 2 * D, 40 * D, 235_000),
    ("knocked_in_near_the_heal", 100 * 10**8, 10_000, 9_990, 4, 1, WEEK - 3600, 40 * D, 235_000),
    ("knocked_in_above_the_reference", 100 * 10**8, 10_000, 10_400, 4, 1, 5 * D, 40 * D, 235_000),
    ("after_a_ratchet", 100 * 10**8, 12_000, 9_166, 3, 0, 2 * D, 40 * D, 235_000),
    ("after_a_ratchet_knocked_in", 100 * 10**8, 12_000, 5_500, 4, 1, 2 * D, 40 * D, 235_000),
    ("spot_floor", 333_33_330_000, 10_000, None, 3, 0, 200_000, 40 * D, 235_000),
    ("spot_at_domain_min", 100 * 10**8, 10_000, 2_000, 4, 1, 2 * D, 40 * D, 235_000),
    ("spot_below_domain", 100 * 10**8, 10_000, 1_999, 4, 1, 2 * D, 40 * D, 235_000),
    ("spot_at_domain_max", 100 * 10**8, 10_000, 13_000, 3, 0, 2 * D, 40 * D, 235_000),
    ("spot_above_domain", 100 * 10**8, 10_000, 13_001, 3, 0, 2 * D, 40 * D, 235_000),
    ("spot_beyond_uint16", 1 * 10**8, 10_000, 70_000, 3, 0, 2 * D, 40 * D, 235_000),
    # the next fixing is 5 days away in these: what counts is how many fixings come before the release
    ("earnings_now", 100 * 10**8, 10_000, 9_000, 3, 0, 2 * D, 0, 235_000),  # n = 0
    ("earnings_before_the_fixing", 100 * 10**8, 10_000, 9_000, 3, 0, 2 * D, 3 * D, 235_000),  # 0
    ("earnings_at_the_fixing", 100 * 10**8, 10_000, 9_000, 3, 0, 2 * D, 5 * D, 235_000),  # 0: at counts as before
    ("earnings_just_after_the_fixing", 100 * 10**8, 10_000, 9_000, 3, 0, 2 * D, 5 * D + 1, 235_000),  # 1
    ("earnings_a_week_after_the_fixing", 100 * 10**8, 10_000, 9_000, 3, 0, 2 * D, 12 * D, 235_000),  # 1
    ("earnings_two_fixings_away", 100 * 10**8, 10_000, 9_000, 3, 0, 2 * D, 12 * D + 1, 235_000),  # 2
    ("earnings_at_domain_max", 100 * 10**8, 10_000, 9_000, 3, 0, 2 * D, 5 * D + 18 * WEEK, 235_000),  # 18
    ("earnings_beyond_domain", 100 * 10**8, 10_000, 9_000, 3, 0, 2 * D, 5 * D + 18 * WEEK + 1, 235_000),  # 19
    ("earnings_beyond_uint8", 100 * 10**8, 10_000, 9_000, 3, 0, 2 * D, 300 * WEEK, 235_000),  # capped at 255
    ("no_coupon_reserve", 100 * 10**8, 10_000, 8_500, 3, 0, 3 * D, 40 * D, 0),
    ("high_coupon_reserve", 100 * 10**8, 10_000, 8_500, 3, 0, 3 * D, 40 * D, 357_000),
    ("odd_coupon_reserve", 100 * 10**8, 10_000, 8_500, 3, 1, 3 * D, 40 * D, 123_400),
]
FLOOR_SPOT = 300_00_000_001  # vs reference 333.3333 -> 9000.0000x bps, floors to 9000
VOLS = (2000, 5500, 9000)
EXTRA_VOLS = (("vol_below_domain", 1999), ("vol_above_domain", 9001))


def fixings_before(t_next: int, t_earnings: int, interval: int) -> int:
    """Fixings before the next earnings release: 0 when it comes before or at the next
    fixing, else ceil((release - next fixing) / interval); capped at the uint8 maximum."""
    if t_earnings <= t_next:
        return 0
    return min(-(-(t_earnings - t_next) // interval), UINT8_MAX)


def inputs(reference: int, spot: int, knocked_in: int, secs_in: int, t_earnings: int, vol: int, interval: int):
    t_next = interval - secs_in
    return [spot * BPS // reference, vol, t_next, fixings_before(t_next, t_earnings, interval), knocked_in]


def formula_pricer(product: dict, bands: dict, v: list[int]) -> int:
    """PerpFormulaPricer.correctionBps: the refusals, then the fixing accrual
    a * elapsed / interval * (pay - formula), truncated towards zero."""
    x, vol, t_next, _n, flags = v
    interval, ki = product["fixingIntervalSecs"], product["kiBarrierBps"]
    if not (bands["volMinBps"] <= vol <= bands["volMaxBps"]):
        raise pq.OutOfRange(1, vol)
    if t_next > interval:
        raise pq.OutOfRange(2, t_next)
    if flags > 1:
        raise pq.OutOfRange(4, flags)
    if bands["fixingBandSecs"] and t_next <= bands["fixingBandSecs"]:
        within = lambda level, band: x + band >= level and x <= level + band  # noqa: E731
        if not flags and within(ki, bands["knockInBandBps"]):
            raise pq.Uncertified(0, "knockInFixingBand")
        if flags and within(BPS, bands["healBandBps"]):
            raise pq.Uncertified(1, "healFixingBand")
    w = pf.world_of(ki, product["meltShareWad"], interval, product["driftBps"], product["discountBps"], vol)
    formula_bps = pf.round_bps(pf.principal(pf.coefficients(*w), x * pf.WAD // BPS, bool(flags)))
    pay_bps = x if (flags or x < ki) and x < BPS else BPS  # what the melting slice would pay if the fixing were now
    return pf.sdiv(product["meltShareWad"] * (interval - t_next) * (pay_bps - formula_bps), pf.WAD * interval)


def quote(export: dict | None, product: dict, v: list[int], coupon_reserve: int, bands: dict = BANDS) -> dict:
    """The quoter's assembly. Returns result 0 with the quote's parts, or the refusal:
    1 OutOfRange(a, b), 3 Uncertified(a)."""
    if v[0] > UINT16_MAX:  # the quoter itself refuses what the struct can't hold
        return dict(result=1, a=0, b=v[0], formulaBps=0, correctionBps=0, couponBps=0, priceBps=0)
    try:
        correction = pq.forward(export, v) if export else formula_pricer(product, bands, v)
    except pq.OutOfRange as e:
        return dict(result=1, a=e.index, b=e.value, formulaBps=0, correctionBps=0, couponBps=0, priceBps=0)
    except pq.Uncertified as e:
        return dict(result=3, a=e.region, b=0, formulaBps=0, correctionBps=0, couponBps=0, priceBps=0)
    w = pf.world_of(product["kiBarrierBps"], product["meltShareWad"], product["fixingIntervalSecs"],
                    product["driftBps"], product["discountBps"], v[1])
    principal = pf.principal(pf.coefficients(*w), v[0] * pf.WAD // BPS, bool(v[4]))
    formula_bps = pf.round_bps(principal)
    tau = v[2] * pf.WAD // pf.YEAR
    coupon_bps = coupon_reserve * pf.coupon(product["meltShareWad"], w[2], w[5], tau) // (pf.WAD * 100)
    return dict(result=0, a=0, b=0, formulaBps=formula_bps, correctionBps=correction, couponBps=coupon_bps,
                priceBps=max(0, formula_bps + correction) + coupon_bps)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="model directory with a feature spec 2 export (default: formula only)")
    ap.add_argument("--out", default=str(ROOT / "contracts/test/vectors/perp_quoter_vectors.json"))
    ap.add_argument("--inputs", help="five comma-separated PerpPricerInputs fields: print that one quote and exit")
    ap.add_argument("--reserve", type=int, default=235_000, help="couponReserve of the series (with --inputs)")
    args = ap.parse_args()
    export = json.loads((Path(args.model) / "student_export.json").read_text()) if args.model else None
    product = {k: export["product"][k] for k in PRODUCT} if export else PRODUCT
    assert export is None or export["featureSpecVersion"] == 2
    if args.inputs:
        q = quote(export, product, [int(x) for x in args.inputs.split(",")], args.reserve)
        if q["result"] == 0:
            print(q["formulaBps"], q["correctionBps"], q["couponBps"], q["priceBps"])
        else:
            print({1: "OutOfRange", 3: "Uncertified"}[q["result"]], q["a"], q["b"])
        return

    rows = []
    cases = [(f"{label}@{vol}", st, vol) for vol in VOLS for (label, *st) in STATES]
    cases += [(label, list(STATES[1][1:]), vol) for label, vol in EXTRA_VOLS]
    for label, (first, ref_bps, spot_bps, done, ki, secs_in, t_earn, reserve), vol in cases:
        reference = first * ref_bps // BPS
        spot = FLOOR_SPOT if spot_bps is None else reference * spot_bps // BPS
        assert spot_bps is None or spot * BPS // reference == spot_bps, label
        v = inputs(reference, spot, ki, secs_in, t_earn, vol, product["fixingIntervalSecs"])
        row = {"label": label, "first": first, "reference": reference, "spot": spot, "fixingsDone": done,
               "knockedIn": ki, "secsSinceFixing": secs_in, "secsToEarnings": t_earn, "vol": vol,
               "couponReserve": reserve, "inputs": v if v[0] <= UINT16_MAX else []}
        row.update(quote(export, product, v, reserve))
        rows.append(row)

    out = {"model": Path(args.model).name if args.model else "formula",
           "weightsHash": export["weightsHash"] if export else "0x" + "00" * 32,
           "product": product, "bands": None if export else BANDS, "count": len(rows), "vectors": rows}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    names = {0: "price", 1: "OutOfRange", 3: "Uncertified"}
    for r in rows:
        detail = (f"{r['priceBps']} = max(0, {r['formulaBps']} {r['correctionBps']:+d}) + {r['couponBps']}"
                  if r["result"] == 0 else f"({r['a']}, {r['b']})")
        print(f"{r['label']:52s} {names[r['result']]:12s} {detail}")
    print(f"wrote {len(rows)} vectors for {out['model']} to {args.out}")


if __name__ == "__main__":
    main()
