"""K2 round 2: measure the teacher's observation-day discontinuities, which
size the certified domain's exclusion bands (tools/domains/k2.json).

For each barrier (ki, ac), knockedIn state and observationsRemaining, prints
  jump@0     teacher(barrier + 1 bps) - teacher(barrier - 1 bps) at tNext = 0
             (the observation happens now: a step no continuous student fits;
             +-1 bps rather than the barrier itself, where the float32 path
             state may land on either side of the comparison)
  slope@1d+  max |teacher change| per 10 bps of spot within +-1000 bps of the
             barrier at tNext = 86401, just outside the band (what the student
             must fit at the band edge)
Teacher labels at --paths paths, seed 0xBA4D (used nowhere else).

  python round2_bands.py --backend cuda --paths 262144 > round2_bands.log
"""

from __future__ import annotations

import argparse
import sys

import numpy as np

import round2_sets as sets

OBS = (1, 2, 3, 5, 9, 13, 26)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", type=int, default=2**16)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--backend", choices=("numpy", "cuda"), default="numpy")
    args = ap.parse_args()
    print(f"teacher {sets.teacher_fingerprint()}, backend {args.backend}, paths {args.paths}")
    edge = np.arange(-1000, 1001, 10)
    blocks, X = [], []
    for name, b in (("ki", sets.KI), ("ac", sets.AC)):
        for flag in (0, 1):
            for obs in OBS:
                jump = sets.rows([b - 1, b + 1], [0, 0], [obs, obs], [flag, flag])
                spots = np.clip(b + edge, sets.SPOT_LO, sets.SPOT_HI)
                slope = sets.rows(spots, np.full(len(spots), sets.DAY + 1), np.full(len(spots), obs),
                                  np.full(len(spots), flag))
                blocks.append((name, flag, obs, len(X), len(jump), len(slope)))
                X += [jump, slope]
    X = np.vstack(X)
    y, se = sets.label(X, args.paths, 0xBA4D, args.workers, backend=args.backend)
    print(f"max label stderr {se.max():.2f} bps")
    print(f"{'barrier':7s} {'kIn':>3s} {'obs':>3s} {'below':>8s} {'above':>8s} {'jump@0':>8s} {'slope@1d+ /10bps':>17s}")
    off = 0
    for name, flag, obs, _, nj, ns in blocks:
        yj = y[off:off + nj]
        ys = y[off + nj:off + nj + ns]
        off += nj + ns
        print(f"{name:7s} {flag:3d} {obs:3d} {yj[0]:8.1f} {yj[1]:8.1f} {yj[1] - yj[0]:+8.1f} "
              f"{np.abs(np.diff(ys)).max():17.1f}")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
