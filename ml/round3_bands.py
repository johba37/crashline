"""K3: how steep teacher v3 gets near each barrier, by vol. Sizes the exclusion
bands of tools/domains/k3.json.

K2 excluded tNext <= 1 day near each barrier at 55% vol; just outside, the
teacher moved up to 144 bps per 10 bps of spot (round2_bands.log) and the K2
architecture fitted that to 18.7 bps. The step at an observation is smoothed
over roughly vol * sqrt(tNext) of spot, so at lower vol the same slope sits
further from the observation. For each vol, barrier, knockedIn and
observationsRemaining this prints
  jump@0            teacher(barrier + 1) - teacher(barrier - 1) at tNext = 0
  slope@t /10bps    max |teacher change| per 10 bps of spot within +-1000 bps
                    of the barrier, at tNext = t
Seed 0xBA4D3 (used nowhere else).

  python ml/round3_bands.py --paths 131072 > ml/round3_bands.log
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

import round3_sets as sets

VOLS = (1500, 2000, 3000, 4000, 5500, 7500, 9000, 12000)
OBS = (1, 2, 3, 5, 9, 13, 26)
TNEXTS = (sets.DAY + 1, 2 * sets.DAY + 1, 4 * sets.DAY + 1, sets.WEEK)
EDGE = np.arange(-1000, 1001, 10)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", type=int, default=2**17)
    ap.add_argument("--backend", choices=("numpy", "cuda"), default="cuda")
    ap.add_argument("--out", default=str(pathlib.Path(__file__).with_name("round3_bands.json")))
    args = ap.parse_args()
    print(f"teacher {sets.teacher_fingerprint()}, backend {args.backend}, paths {args.paths}", flush=True)
    blocks, X = [], []
    for vol in VOLS:
        for name, b in (("ki", sets.KI), ("ac", sets.AC)):
            for flag in (0, 1):
                for obs in OBS:
                    X.append(sets.rows([b - 1, b + 1], 0, obs, flag, vol))
                    spots = np.clip(b + EDGE, sets.SPOT_LO, sets.SPOT_HI)
                    for tn in TNEXTS:
                        X.append(sets.rows(spots, tn, obs, flag, vol))
                    blocks.append((vol, name, flag, obs))
    X = np.vstack(X)
    print(f"{len(X)} labels", flush=True)
    y, se = sets.label(X, args.paths, 0xBA4D3, backend=args.backend)
    print(f"max label stderr {se.max():.2f} bps")
    hdr = " ".join(f"{'slope@' + (str(t // 86400) + 'd+' if t != sets.WEEK else '7d'):>10s}" for t in TNEXTS)
    print(f"{'vol':>5s} {'barrier':7s} {'kIn':>3s} {'obs':>3s} {'below':>8s} {'above':>8s} {'jump@0':>8s} {hdr}")
    out, off, ns = [], 0, len(EDGE)
    for vol, name, flag, obs in blocks:
        yj = y[off:off + 2]
        off += 2
        slopes = []
        for _ in TNEXTS:
            slopes.append(float(np.abs(np.diff(y[off:off + ns])).max()))
            off += ns
        out.append({"vol": vol, "barrier": name, "knockedIn": flag, "obs": obs, "jump0": float(yj[1] - yj[0]),
                    "slopes": dict(zip(map(str, TNEXTS), slopes))})
        print(f"{vol:5d} {name:7s} {flag:3d} {obs:3d} {yj[0]:8.1f} {yj[1]:8.1f} {yj[1] - yj[0]:+8.1f} "
              + " ".join(f"{s:10.1f}" for s in slopes), flush=True)
    pathlib.Path(args.out).write_text(json.dumps({"paths": args.paths, "tnexts": TNEXTS, "rows": out}, indent=1) + "\n")


if __name__ == "__main__":
    main()
