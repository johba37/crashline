"""K2 round 2: model selection on the validation set V (never on T).

  python ml/round2_select.py <run dir> [<run dir> ...]

Each run dir holds a student_export.json written by ml/round2.py. For each,
prints the INTEGER student's |error| on V outside the exclusion bands (max,
p99, mean), split into V's grid + uniform part and its steep knock-in block,
then names the run with the lowest max.
"""

from __future__ import annotations

import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tools"))

import round2  # noqa: E402
import round2_sets as sets  # noqa: E402

STEEP_BLOCK = 30_000  # the last rows of V (round2_sets.val_points)


def main() -> None:
    runs = [pathlib.Path(p) for p in sys.argv[1:]]
    z = np.load(HERE / "k2_val_labels.npz")
    X, y = z["X"].astype(np.int64), z["y"]
    assert np.array_equal(X, sets.val_points()), "V labels are not round2_sets.val_points()"
    out = ~sets.excluded(X)
    steep = np.zeros(len(X), bool)
    steep[-STEEP_BLOCK:] = True
    print(f"V: {len(X)} points, {out.sum()} outside the bands, max label stderr {z['se'].max():.2f} bps")
    print(f"{'run':28s} {'params':>6s} {'max':>6s} {'p99':>5s} {'mean':>5s} | {'grid+unif max':>13s} | {'steep max':>9s}")
    best = None
    for r in runs:
        ex = json.loads((r / "student_export.json").read_text())
        round2.check_forward_int(ex, X, n=200)
        a = np.abs(round2.forward_int(ex, X) - y)
        mx = float(a[out].max())
        print(f"{r.name:28s} {ex['architecture']['parameterCount']:6d} {mx:6.1f} {np.percentile(a[out], 99):5.1f} "
              f"{a[out].mean():5.2f} | {a[out & ~steep].max():13.1f} | {a[out & steep].max():9.1f}")
        if best is None or mx < best[0]:
            best = (mx, r.name)
    print(f"selected on V: {best[1]} (V max {best[0]:.1f} bps)")


if __name__ == "__main__":
    main()
