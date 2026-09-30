"""K3 gate: the certified integer student vs teacher v3 on the held-out set T.

  python ml/round3_eval.py --model model/k3            # the gate, on T
  python ml/round3_eval.py --model model/k3 --set T2   # confirmation set T2 (labelled once, at the end)
  python ml/round3_eval.py --model model/k3 --set T3   # confirmation at the vol endpoints (after the first T)
  python ml/round3_eval.py --model model/k3 --set S    # vol-band spread (reported, not gated)

Checks, in order, as round2_eval (any failure ends with GATE FAIL):
  1. the labels were produced by the current v3 teacher and domain (sha256 at
     labelling time) and their points are exactly round3_sets' construction;
  2. max label stderr <= 4 bps and every label has >= 2^18 paths;
  3. every point outside the exclusions is priced by pq.forward (the
     contract's bit-exact twin, domain check included) and max |student -
     teacher| <= 50 bps.
S reports, for the Desk's vol-band quote (bid = P(vol + 2 pts), ask =
P(vol - 2 pts)), the student's error on that spread against the teacher's.
"""

from __future__ import annotations

import argparse
import hashlib
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
import round3_sets as sets  # noqa: E402
from round2 import forward_int, print_table  # noqa: E402
import round3  # noqa: E402,F401  (installs the K3 sets into round2's helpers)
from round2 import gate_table  # noqa: E402

GATE_BPS = 50.0
MAX_SE_BPS = 4.0
MIN_PATHS = 2**18
LABELS = {"T": "k3_test_labels.npz", "T2": "k3_test2_labels.npz", "T3": "k3_test3_labels.npz",
          "S": "k3_spread_labels.npz"}
POINTS = {"T": sets.test_points, "T2": sets.test2_points, "T3": sets.test3_points, "S": sets.spread_points}

_EXPORT = None


def _init(export):
    global _EXPORT
    _EXPORT = export


def _price(row):
    try:
        return pq.forward(_EXPORT, [int(v) for v in row])
    except pq.Uncertified:
        return None


def spread_report(X, y, se, pred, ok):
    """Triplets (vol - d, vol, vol + d): teacher vs student ask - bid."""
    t = y.reshape(-1, 3)
    s = pred.reshape(-1, 3)
    good = ok.reshape(-1, 3).all(axis=1)
    sp_t = t[good, 0] - t[good, 2]
    sp_s = s[good, 0] - s[good, 2]
    noise = np.hypot(se.reshape(-1, 3)[good, 0], se.reshape(-1, 3)[good, 2])
    err = sp_s - sp_t
    a = np.abs(err)
    print(f"\n== S: vol-band spread ask - bid = P(vol - {sets.SPREAD_DVOL}) - P(vol + {sets.SPREAD_DVOL}), bps ==")
    print(f"triplets {good.sum()} (all three certified); teacher spread mean {sp_t.mean():.1f}, p99 "
          f"{np.percentile(np.abs(sp_t), 99):.1f}, max {np.abs(sp_t).max():.1f}; label noise on it mean "
          f"{noise.mean():.2f}, max {noise.max():.2f}")
    print(f"student spread error: max {a.max():.1f}, p99 {np.percentile(a, 99):.1f}, mean {a.mean():.2f}")
    print(f"student spread < 0 (bid above ask before the Desk's min/max): {int((sp_s < 0).sum())}; "
          f"teacher spread < -3 noise: {int((sp_t < -3 * noise).sum())}")
    mid = np.abs(s[good, 1] - t[good, 1])
    print(f"mid (vol) error on S: max {mid.max():.1f}, p99 {np.percentile(mid, 99):.1f}, mean {mid.mean():.2f}")
    Xm = X.reshape(-1, 3, 10)[good, 1]
    for i in np.argsort(-a)[:8]:
        print(f"  spot {Xm[i, 0]:5d} vol {Xm[i, 2]:5d} tNext {Xm[i, 7]:6d} obs {Xm[i, 8]:2d} knockedIn {Xm[i, 9]}  "
              f"teacher spread {sp_t[i]:+7.1f} student {sp_s[i]:+7.1f} err {err[i]:+6.1f}")
    return float(a.max())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="dir holding the certified student_export.json")
    ap.add_argument("--set", choices=("T", "T2", "T3", "S"), default="T")
    ap.add_argument("--labels", default=None)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    assert tuple(pq.FIELD_NAMES) == sets.FIELD_NAMES, "round3_sets.FIELD_NAMES out of date"
    labels = args.labels or str(HERE / LABELS[args.set])
    export = json.loads((pathlib.Path(args.model) / "student_export.json").read_text())
    pq.validate_domain(export["certifiedDomain"])
    assert export["weightsHash"] == pq.weights_hash(export), "weightsHash mismatch"
    z = np.load(labels)
    X, y, se, paths = z["X"].astype(np.int64), z["y"], z["se"], z["paths"]
    meta = json.loads(str(z["meta"]))
    fails = []
    print(f"model {args.model}: weightsHash {export['weightsHash']}, "
          f"{export['architecture']['parameterCount']} params, layers {[l['out'] for l in export['architecture']['layers']]}")
    print(f"labels {labels}: set {meta['set']}, {len(X)} points, label seed {meta['label_seed']}, "
          f"base paths {meta['paths']}, top-up rounds {meta['topup_rounds']}")
    fp = sets.teacher_fingerprint()
    dom_sha = hashlib.sha256(sets.DOMAIN_PATH.read_bytes()).hexdigest()
    print(f"teacher at labelling: {meta['teacher']}\nteacher now:          {fp}")
    if meta["teacher"] != fp:
        fails.append("labels were not produced by the current teacher")
    if meta["domain_sha256"] != dom_sha:
        fails.append("labels were drawn for another domain file")
    if export["certifiedDomain"] != sets.load_domain():
        fails.append("the export's certified domain is not tools/domains/k3.json")
    if meta["set"] != args.set or not np.array_equal(X, POINTS[args.set]()):
        fails.append(f"label points are not round3_sets.{POINTS[args.set].__name__}()")
    print(f"label stderr bps: mean {se.mean():.2f}, p99 {np.percentile(se, 99):.2f}, MAX {se.max():.2f} "
          f"(limit {MAX_SE_BPS}); paths min {paths.min()} (limit {MIN_PATHS})")
    if se.max() > MAX_SE_BPS or paths.min() < MIN_PATHS:
        fails.append("label noise above the limit")

    with mp.get_context("fork").Pool(args.workers, initializer=_init, initargs=(export,)) as pool:
        priced = pool.map(_price, X.tolist(), chunksize=256)
    ok = np.array([p is not None for p in priced])
    pred = np.where(ok, np.array([p if p is not None else 0 for p in priced], np.float64),
                    forward_int(export, X).astype(np.float64))
    err = pred - y
    print(f"\ndomain: {ok.sum()} points certified, {(~ok).sum()} refused (Uncertified) by "
          f"{[e['name'] for e in export['certifiedDomain']['exclusions']]}")
    print(f"\n== {args.set}: integer student (pq.forward) - teacher, bps ==")
    print_table(gate_table(X, err, ok))
    print("\nworst points outside the exclusions:")
    for r in sets.worst(X, err, ok, 10):
        print(f"  spot {r['spot']:5d} vol {r['vol']:5d} tNext {r['tNext']:6d} obs {r['obs']:2d} "
              f"knockedIn {r['knockedIn']}  err {r['err']:+7.1f}")
    mx = float(np.abs(err[ok]).max())
    print(f"\nmax |student - teacher| outside exclusions: {mx:.1f} bps (gate {GATE_BPS:.0f}); "
          f"max label stderr {se.max():.2f} bps")
    if args.set == "S":
        spread_report(X, y, se, pred, ok)
        for f in fails:
            print(f"FAIL: {f}")
        print("S REPORT (not the gate) " + ("OK" if not fails else "INVALID"))
        return 0 if not fails else 1
    if mx > GATE_BPS:
        fails.append(f"max error {mx:.1f} > {GATE_BPS:.0f} bps")
    for f in fails:
        print(f"FAIL: {f}")
    word = "GATE" if args.set == "T" else f"{args.set} CONFIRMATION (not the gate)"
    print(f"{word} PASS" if not fails else f"{word} FAIL (measured max {mx:.1f} bps)")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
