"""K2 round 2 gate: the certified integer student vs the frozen teacher on the
held-out test set T.

  python ml/round2_eval.py --model model/k2            # the gate, on T
  python ml/round2_eval.py --model model/k2 --set T2   # confirmation set T2

Checks, in order (any failure ends with GATE FAIL):
  1. the label cache was produced by the current teacher (sha256 of
     ml/teacher.py and ml/teacher_config.json recorded at labelling time),
     and its points are exactly round2_sets.test_points();
  2. max label stderr <= 4 bps and every label has >= 2^18 paths;
  3. every point outside the export's exclusion bands is priced by
     pq.forward (the contract's bit-exact twin, domain check included) and
     max |student - teacher| <= 50 bps.
Prints the table by region (outside the exclusions unless stated), the worst
points, and a final `GATE PASS` or `GATE FAIL` line.
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
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

import pricer_quant as pq  # noqa: E402
import round2_sets as sets  # noqa: E402
from round2 import forward_int, gate_table, print_table, worst  # noqa: E402

GATE_BPS = 50.0
MAX_SE_BPS = 4.0
MIN_PATHS = 2**18

_EXPORT = None


def _init(export):
    global _EXPORT
    _EXPORT = export


def _price(row):
    """pq.forward, or None where the domain refuses with Uncertified."""
    try:
        return pq.forward(_EXPORT, [int(v) for v in row])
    except pq.Uncertified:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="dir holding the certified student_export.json")
    ap.add_argument("--set", choices=("T", "T2"), default="T",
                    help="T = the gate; T2 = the confirmation set (reported, not the gate)")
    ap.add_argument("--labels", default=None, help="default ml/k2_test_labels.npz (T) / ml/k2_test2_labels.npz (T2)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    points = {"T": sets.test_points, "T2": sets.test2_points}[args.set]
    args.labels = args.labels or str(HERE / {"T": "k2_test_labels.npz", "T2": "k2_test2_labels.npz"}[args.set])
    export = json.loads((pathlib.Path(args.model) / "student_export.json").read_text())
    pq.validate_domain(export["certifiedDomain"])
    assert export["weightsHash"] == pq.weights_hash(export), "weightsHash mismatch"
    z = np.load(args.labels)
    X, y, se, paths = z["X"].astype(np.int64), z["y"], z["se"], z["paths"]
    meta = json.loads(str(z["meta"]))
    fails = []

    print(f"model {args.model}: weightsHash {export['weightsHash']}, "
          f"{export['architecture']['parameterCount']} params, layers "
          f"{[l['out'] for l in export['architecture']['layers']]}")
    print(f"labels {args.labels}: set {meta['set']}, {len(X)} points, label seed {meta['label_seed']}, "
          f"base paths {meta['paths']}, top-up rounds {meta['topup_rounds']}")
    fp = sets.teacher_fingerprint()
    print(f"teacher at labelling: {meta['teacher']}")
    print(f"teacher now:          {fp}")
    if meta["teacher"] != fp:
        fails.append("labels were not produced by the current teacher")
    if meta["set"] != args.set or not np.array_equal(X, points()):
        fails.append(f"label points are not round2_sets.{points.__name__}()")
    print(f"label stderr bps: mean {se.mean():.2f}, p99 {np.percentile(se, 99):.2f}, MAX {se.max():.2f} "
          f"(limit {MAX_SE_BPS}); paths min {paths.min()} (limit {MIN_PATHS})")
    if se.max() > MAX_SE_BPS or paths.min() < MIN_PATHS:
        fails.append("label noise above the limit")

    # every T point must be inside the ranges + consistency; exclusions are the only refusals
    with mp.get_context("fork").Pool(args.workers, initializer=_init, initargs=(export,)) as pool:
        priced = pool.map(_price, X.tolist(), chunksize=256)
    out_band = np.array([p is not None for p in priced])
    pred = np.where(out_band, np.array([p if p is not None else 0 for p in priced], np.float64),
                    forward_int(export, X).astype(np.float64))  # in-band: informational only
    err = pred - y
    print(f"\ndomain: {out_band.sum()} points certified, {(~out_band).sum()} refused (Uncertified) by "
          f"{[e['name'] for e in export['certifiedDomain']['exclusions']]}")
    print(f"\n== {args.set}: integer student (pq.forward) - teacher, bps ==")
    tab = gate_table(X, err, out_band)
    print_table(tab)
    print("\nworst points outside the exclusions:")
    for r in worst(X, err, out_band, 10):
        print(f"  spot {r['spot']:5d} tNext {r['tNext']:6d} obs {r['obs']:2d} knockedIn {r['knockedIn']}  "
              f"err {r['err']:+7.1f}")
    mx = float(np.abs(err[out_band]).max())
    print(f"\nmax |student - teacher| outside exclusions: {mx:.1f} bps (gate {GATE_BPS:.0f}); "
          f"max label stderr {se.max():.2f} bps")
    if mx > GATE_BPS:
        fails.append(f"max error {mx:.1f} > {GATE_BPS:.0f} bps")
    for f in fails:
        print(f"FAIL: {f}")
    word = "GATE" if args.set == "T" else "T2 CONFIRMATION (not the gate)"
    print(f"{word} PASS" if not fails else f"{word} FAIL (measured max {mx:.1f} bps)")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
