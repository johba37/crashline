"""P1 gate: formula + the certified integer student vs the teacher on T (or T2).

  python ml/perp_eval.py --model model/p1              # the gate, on T
  python ml/perp_eval.py --model model/p1 --set T2     # confirmation set, labelled once at the end
  python ml/perp_eval.py --model model/p1 --set S      # vol-band spread (reported, not gated)

Checks, in order (any failure ends with GATE FAIL):
  1. the labels were produced by the current teacher (sha256 of ml/teacher_perp.py,
     ml/perp_formula.py and ml/teacher_perp_config.json at labelling time), on the
     extrapolated n_k >= 256 grid, and their points are exactly ml/perp_sets.py's
     construction;
  2. every point outside the exclusions is priced by pq.forward (the contract's
     bit-exact twin, domain check included), every point inside is refused;
  3. max |max(0, round(formula) + student) - teacher| <= 50 bps of live notional.
The formula alone is reported on the same points.
S reports, for the Desk's vol-band quote (IDeskCover: the model is asked at the
listing's vol - 2 and + 2 points), the error on that spread against the teacher's.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
# ml/ first: tools/ has a perp_formula.py of its own (the integer twin)
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))

import pricer_quant as pq  # noqa: E402
import perp_sets as sets  # noqa: E402
from perp_train import forward_int, gate_table, onchain, print_table, worst  # noqa: E402

GATE_BPS = 50.0
MIN_NK = 256
LABELS = {"T": "perp_p1_test_labels.npz", "T2": "perp_p1_test2_labels.npz", "S": "perp_p1_spread_labels.npz"}

_EXPORT = None


def _init(export):
    global _EXPORT
    sets.ensure_fields(pq)
    _EXPORT = export


def _price(row):
    try:
        return pq.forward(_EXPORT, [int(v) for v in row])
    except pq.Uncertified:
        return None


def spread_report(y: np.ndarray, pred: np.ndarray, pred0: np.ndarray, ok: np.ndarray) -> None:
    """Triplets (vol - d, vol, vol + d): teacher vs on-chain P(vol - d) - P(vol + d)."""
    t, s, f = y.reshape(-1, 3), pred.reshape(-1, 3), pred0.reshape(-1, 3)
    good = ok.reshape(-1, 3).all(axis=1)
    sp_t, sp_s, sp_f = t[good, 0] - t[good, 2], s[good, 0] - s[good, 2], f[good, 0] - f[good, 2]
    d = sets.SPREAD_DVOL
    print(f"\n== S: vol-band spread P(vol - {d}) - P(vol + {d}), bps ==")
    print(f"triplets {int(good.sum())} (all three certified); teacher spread mean {sp_t.mean():.1f}, p99 "
          f"{np.percentile(np.abs(sp_t), 99):.1f}, max {np.abs(sp_t).max():.1f}, negative in {int((sp_t < 0).sum())}")
    for name, sp in (("formula + student", sp_s), ("formula alone", sp_f)):
        a = np.abs(sp - sp_t)
        print(f"{name:18s} error on the spread: max {a.max():.1f}, p99 {np.percentile(a, 99):.1f}, mean {a.mean():.2f}; "
              f"spread < 0 in {int((sp < 0).sum())}")
    mid = np.abs(s[good, 1] - t[good, 1])
    print(f"formula + student  error on the mid: max {mid.max():.1f}, p99 {np.percentile(mid, 99):.1f}, mean {mid.mean():.2f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "model" / "p1"))
    ap.add_argument("--set", default="T", choices=list(LABELS))
    ap.add_argument("--labels", default=None)
    args = ap.parse_args()
    export = json.loads((pathlib.Path(args.model) / "student_export.json").read_text())
    assert pq.weights_hash(export) == export["weightsHash"], "export hash"
    dom = export["certifiedDomain"]
    z = np.load(args.labels or HERE / LABELS[args.set])
    X = sets.POINTS[args.set]()
    y = z["y"].astype(np.float64)
    fails = []

    ok_teacher = str(z["fingerprint"]) == sets.fingerprint()
    stored = sets.points_hash(z["X"]) if "X" in z else str(z["points"])
    ok_points = len(y) == len(X) and stored == sets.points_hash(X) and str(z["set"]) == args.set
    ok_grid = int(z["n_k"]) >= MIN_NK
    print(f"model {args.model}: weightsHash {export['weightsHash']}, "
          f"{export['architecture']['parameterCount']} parameters, featureSpecVersion {export['featureSpecVersion']}")
    print(f"set {args.set}: {len(X)} points; labels by the current teacher: {ok_teacher}; points equal the "
          f"construction: {ok_points}; teacher grid n_k {int(z['n_k'])} (extrapolated): {ok_grid}")
    fails += [n for n, ok in (("teacher fingerprint", ok_teacher), ("points", ok_points), ("grid", ok_grid)) if not ok]
    if not ok_points:
        print("GATE FAIL: " + ", ".join(fails))
        return 1

    fml = sets.formula_bps(X)
    inside = sets.in_ranges(X, dom)
    out = inside & ~sets.excluded(X, dom)
    with mp.Pool(min(12, mp.cpu_count()), initializer=_init, initargs=(export,)) as pool:
        ref = pool.map(_price, X[inside], chunksize=4096)
    refused = np.array([r is None for r in ref])
    corr = forward_int(export, X[inside]).astype(np.float64)
    ok_refuse = bool((refused == ~out[inside]).all())
    ok_exact = all(r == c for r, c, o in zip(ref, corr, out[inside]) if o)
    print(f"outside the exclusions {int(out.sum())}, refused {int(refused.sum())} (exactly the excluded points: "
          f"{ok_refuse}); pq.forward == vectorized integer forward on every priced point: {ok_exact}")
    fails += [n for n, ok in (("refusals", ok_refuse), ("integer forward", ok_exact)) if not ok]

    full = np.zeros(len(X))
    full[inside] = corr
    err = onchain(fml, full) - y
    err0 = onchain(fml, 0.0) - y
    print("\n== formula + integer student vs teacher, bps of live notional ==")
    tab = gate_table(X, err, out)
    print_table(tab)
    for r in worst(X, err, out, 8):
        print("  worst", r)
    print("\n== formula alone vs teacher, the same points ==")
    print_table(gate_table(X, err0, out))
    if args.set == "S":
        spread_report(y, onchain(fml, full), onchain(fml, 0.0), out)
        return 1 if fails else 0
    mx = float(np.abs(err[out]).max())
    c = full[out]
    print(f"\ncorrection range on these points: {c.min():.0f} .. {c.max():.0f} bps, mean |.| {np.abs(c).mean():.1f}")
    if mx > GATE_BPS:
        fails.append(f"max error {mx:.1f} > {GATE_BPS}")
    print(f"\n{'GATE PASS' if not fails else 'GATE FAIL: ' + ', '.join(fails)}: max {mx:.1f} bps (gate {GATE_BPS:.0f}), "
          f"formula alone {float(np.abs(err0[out]).max()):.1f}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
