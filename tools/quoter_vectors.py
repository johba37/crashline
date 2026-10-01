"""Quoter conformity vectors: note state -> PricerInputs -> model answer.

For a few note states of the K1 product this computes, in plain integers,
exactly what NoteQuoter must hand the model (featureSpecVersion 1, the
derivation in contracts/src/NoteQuoter.sol), then asks the integer reference
(`pricer_quant.forward`, the bit-exact twin of the Stylus contract) what the
certified model answers: a clean price or the refusal it raises.
contracts/test/NoteQuoter.t.sol rebuilds each state on a live series and
requires the quoter's inputs to match field by field, and notePriceBps to be
the model's answer plus the accrued coupon.

With --vols (e.g. model/k3, vol a live input 20-90%) every state is run at
each listing vol, and each vector carries its `vol`; the forge test passes it
to the quoter.

Usage: python tools/quoter_vectors.py [--model model/k1-r1] [--out contracts/test/vectors/quoter_vectors.json]
       python tools/quoter_vectors.py --model model/k3 --vols 2000,3500,5500,9000 \
           --out contracts/test/vectors/quoter_vectors_k3.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import pricer_quant as pq  # noqa: E402

WEEK = 604_800
BPS = 10_000
TERMS = {"ki": 6000, "ac": 10000, "coupon": 25, "n": 26, "interval": WEEK}
VOL = 5500

# (label, initial [8 dec], spot bps of initial, observationsDone, knockedIn, secs since the last fixing)
# spot is given in bps and converted with a price that makes floor(spot * 1e4 / initial) land exactly
# on it, except where a raw price is used to exercise the floor.
STATES = [
    ("first_week_at_par", 100 * 10**8, 10_000, 0, 0, 3 * 86400),
    ("first_week_mid", 354_28_490_000, 8_500, 0, 0, 302_400),
    ("first_week_just_after_strike", 100 * 10**8, 9_000, 0, 0, 1),
    ("first_week_at_obs", 100 * 10**8, 8_000, 0, 0, WEEK),  # tNext = 0
    ("first_week_ac_band", 100 * 10**8, 9_800, 0, 0, WEEK - 3600),  # inside the AC observation-day band
    ("first_week_ac_band_edge", 100 * 10**8, 9_499, 0, 0, WEEK - 3600),  # just below the band
    ("first_week_at_ki", 100 * 10**8, 6_000, 0, 0, 86400),
    ("first_week_spot_min", 100 * 10**8, 5_000, 0, 0, 86400),
    ("first_week_spot_below_domain", 100 * 10**8, 4_999, 0, 0, 86400),
    ("first_week_spot_max", 100 * 10**8, 12_000, 0, 0, 86400),
    ("first_week_spot_above_domain", 100 * 10**8, 12_001, 0, 0, 86400),
    ("first_week_floor", 333_33_330_000, None, 0, 0, 200_000),  # raw spot price below, floor matters
    ("first_week_ki_band", 100 * 10**8, 6_500, 0, 0, WEEK - 3600),  # k2: knock-in band, not knocked in
    ("mid_life", 100 * 10**8, 8_500, 10, 0, 172_800),
    ("mid_life_ki_band_knocked_in", 100 * 10**8, 6_500, 10, 1, WEEK - 3600),  # the k2 band excludes knockedIn = 0 only
    ("mid_life_knocked_in", 100 * 10**8, 7_000, 10, 1, 172_800),
    ("mid_life_at_obs", 100 * 10**8, 8_500, 13, 0, WEEK),
    ("last_observation_week", 100 * 10**8, 9_000, 25, 1, 3600),
    ("final_period", 100 * 10**8, 9_000, 26, 0, 3600),  # no barrier observation left
]
FLOOR_SPOT_PRICE = 300_00_000_001  # vs initial 333.3333 -> 9000.0000x bps, floors to 9000


def inputs(initial: int, spot: int, done: int, knocked_in: int, secs_in: int, vol: int = VOL) -> list[int]:
    spot_bps = spot * BPS // initial
    obs_remaining = TERMS["n"] - done
    t_next = TERMS["interval"] - secs_in
    ttm = t_next + obs_remaining * TERMS["interval"]
    return [spot_bps, spot_bps - TERMS["ki"], vol, TERMS["ki"], TERMS["ac"], TERMS["coupon"],
            ttm, t_next, obs_remaining, knocked_in]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "model/k1-r1"))
    ap.add_argument("--out", default=str(ROOT / "contracts/test/vectors/quoter_vectors.json"))
    ap.add_argument("--vols", help="comma-separated listing vols in bps (default: 5500 only, no per-vector vol)")
    args = ap.parse_args()
    vols = [int(v) for v in args.vols.split(",")] if args.vols else [VOL]
    export = json.loads((Path(args.model) / "student_export.json").read_text())

    rows = []
    for vol, (label, initial, spot_bps, done, ki, secs_in) in ((v, st) for v in vols for st in STATES):
        spot = FLOOR_SPOT_PRICE if spot_bps is None else initial * spot_bps // BPS
        assert spot_bps is None or spot * BPS // initial == spot_bps
        v = inputs(initial, spot, done, ki, secs_in, vol)
        secs_since_strike = done * TERMS["interval"] + secs_in
        accrued = TERMS["coupon"] * secs_since_strike // TERMS["interval"]
        row = {"label": label if not args.vols else f"{label}@{vol}", "initial": initial, "spot": spot,
               "observationsDone": done, "knockedIn": ki, "secsSinceStrike": secs_since_strike, "inputs": v,
               "accruedBps": accrued}
        if args.vols:
            row["vol"] = vol
        try:
            clean = pq.forward(export, v)
            row.update(result=0, a=clean, b=0, notePriceBps=clean + accrued)
        except pq.OutOfRange as e:
            row.update(result=1, a=e.index, b=e.value, notePriceBps=0)
        except pq.Inconsistent as e:
            row.update(result=2, a=e.index, b=0, notePriceBps=0)
        except pq.Uncertified as e:
            row.update(result=3, a=e.region, b=0, notePriceBps=0)
        rows.append(row)

    out = {"model": Path(args.model).name, "weightsHash": export["weightsHash"],
           "vol": vols if args.vols else VOL, "terms": TERMS, "count": len(rows), "vectors": rows}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    names = {0: "price", 1: "OutOfRange", 2: "Inconsistent", 3: "Uncertified"}
    for r in rows:
        detail = f"{r['a']} (+{r['accruedBps']} accrued)" if r["result"] == 0 else f"({r['a']}, {r['b']})"
        print(f"{r['label']:40s} {names[r['result']]:12s} {detail}")
    print(f"wrote {len(rows)} vectors for {out['model']} to {args.out}")


if __name__ == "__main__":
    main()
