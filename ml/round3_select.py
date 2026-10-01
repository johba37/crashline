"""K3: model selection on the validation sets V and V_edge (never on T, T2, T3 or S).

  python ml/round3_select.py <run dir> [<run dir> ...]

For each run (a student_export.json written by ml/round3.py), prints the INTEGER
student's |error| on V outside the exclusions (max, p99, mean), split into V's
grid + uniform part and its steep blocks, and the max by vol bucket; then names
the run with the lowest max. With ml/k3_val_edge_labels.npz present (V_edge, the vol
endpoints V's grid missed) it also prints the max on V_edge and selects on the
larger of the two maxima.
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
import round3_sets as sets  # noqa: E402

STEEP_BLOCK = 50_000  # the last rows of V: 40,000 steep-ki + 10,000 steep-ac (round3_sets.val_points)


def main() -> None:
    runs = [pathlib.Path(p) for p in sys.argv[1:]]
    z = np.load(HERE / "k3_val_labels.npz")
    X, y = z["X"].astype(np.int64), z["y"]
    assert np.array_equal(X, sets.val_points()), "V labels are not round3_sets.val_points()"
    out = ~sets.excluded(X)
    steep = np.zeros(len(X), bool)
    steep[-STEEP_BLOCK:] = True
    vb = [(lo, hi) for lo, hi in sets.VOL_BUCKETS if ((X[:, 2] >= lo) & (X[:, 2] <= hi)).any()]
    print(f"V: {len(X)} points, {out.sum()} outside the bands, max label stderr {z['se'].max():.2f} bps")
    print(f"{'run':24s} {'max':>6s} {'p99':>5s} {'mean':>5s} | {'grid+unif':>9s} {'steep':>6s} | "
          + " ".join(f"{f'{lo // 100}-{hi // 100}%':>7s}" for lo, hi in vb))
    ve = HERE / "k3_val_edge_labels.npz"
    if ve.exists():
        ze = np.load(ve)
        Xe, ye = ze["X"].astype(np.int64), ze["y"]
        assert np.array_equal(Xe, sets.val_edge_points()), "V_edge labels are not round3_sets.val_edge_points()"
        oute = ~sets.excluded(Xe)
        print(f"V_edge: {len(Xe)} points, {oute.sum()} outside the bands, max label stderr {ze['se'].max():.2f} bps")
    best = None
    for r in runs:
        ex = json.loads((r / "student_export.json").read_text())
        round2.check_forward_int(ex, X, n=200)
        a = np.abs(round2.forward_int(ex, X) - y)
        mx = float(a[out].max())
        per_vol = [a[out & (X[:, 2] >= lo) & (X[:, 2] <= hi)].max() for lo, hi in vb]
        edge = ""
        if ve.exists():
            ae = np.abs(round2.forward_int(ex, Xe) - ye)[oute]
            edge = f" | V_edge max {ae.max():5.1f} p99 {np.percentile(ae, 99):5.1f}"
            mx = max(mx, float(ae.max()))
        print(f"{r.name:24s} {float(a[out].max()):6.1f} {np.percentile(a[out], 99):5.1f} {a[out].mean():5.2f} | "
              f"{a[out & ~steep].max():9.1f} {a[out & steep].max():6.1f} | " + " ".join(f"{v:7.1f}" for v in per_vol)
              + edge)
        if best is None or mx < best[0]:
            best = (mx, r.name)
    print(f"selected on V{' + V_edge' if ve.exists() else ''}: {best[1]} (max {best[0]:.1f} bps)")


if __name__ == "__main__":
    main()
