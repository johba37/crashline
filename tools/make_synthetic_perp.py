"""Synthetic feature-spec-2 student + golden vectors for the Stylus lane.

The v2 counterpart of make_synthetic.py: a TOY correction function, not the
teacher's residual. Its only job is to give the Stylus contract a spec-2
export (5 inputs, a signed correction, a product section, a certified domain
with an exclusion) so the integer port, the hash pin and the ABI can be tested
whatever the state of model/p1.

Usage (from tools/): ../tools/.venv/bin/python make_synthetic_perp.py [--out ../model/synthetic-p]
Deterministic given --seed.
"""

from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np
import torch

import certify
import pricer_quant as pq
from make_synthetic import fit, float_forward

WEEK = 604_800
EARNINGS_CYCLE = 13  # fixings between two earnings releases (91 days)

PRODUCT = {
    "kiBarrierBps": 6000,
    "meltShareWad": 18_995_352_771_274_247,  # 1 - exp(-7/365)
    "fixingIntervalSecs": WEEK,
    "driftBps": 400,
    "discountBps": 0,
    "teacher": {"name": "synthetic toy target, not a teacher"},
}

DOMAIN = {
    "ranges": [
        {"name": "spotBpsOfReference", "min": 2000, "max": 13000},
        {"name": "volBpsAnnual", "min": 2000, "max": 9000},
        {"name": "timeToNextFixingSecs", "min": 0, "max": WEEK},
        {"name": "fixingsBeforeEarnings", "min": 0, "max": 18},
        {"name": "flags", "min": 0, "max": 1},
    ],
    "consistency": {},
    "exclusions": [
        {"name": "kiFixingDay",
         "bounds": [{"field": "timeToNextFixingSecs", "min": 0, "max": 86_400},
                    {"field": "spotBpsOfReference", "min": 5500, "max": 6500},
                    {"field": "flags", "min": 0, "max": 0}]},
    ],
}


def sample_raw(rng: np.random.Generator, n: int) -> list[list[int]]:
    f = pq.FIELDS_V2
    return [[int(rng.integers(fi.lo, fi.hi + 1)) for fi in f] for _ in range(n)]


def toy_correction(r: list[int]) -> float:
    """Smooth and signed, roughly +-100 bps: a bump at the knock-in barrier that
    fades with vol, a tilt in the time to the fixing, a wave over the earnings cycle."""
    spot, vol, t_next, n_earn, flags = r
    x, sig = spot / 1e4, vol / 1e4
    bump = 0.0 if flags & 1 else 60 * np.exp(-((x - 0.6) / 0.12) ** 2) * (0.3 / max(sig, 0.15))
    tilt = -25 * (t_next / WEEK) * min(x, 1.0)
    wave = 12 * np.cos(2 * np.pi * n_earn / EARNINGS_CYCLE)
    return float(bump + tilt + wave)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../model/synthetic-p")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--widths", default="48,32")
    ap.add_argument("--train", type=int, default=60_000)
    ap.add_argument("--steps", type=int, default=6_000)
    args = ap.parse_args()

    torch.set_num_threads(1)  # tiny batches: more threads only contend, and one thread is reproducible
    rng = np.random.default_rng(args.seed)
    widths = [int(w) for w in args.widths.split(",")]
    raws = sample_raw(rng, args.train)
    x = pq.normalized_float_inputs(raws, fields=pq.FIELDS_V2)
    scale = 100.0
    y = np.array([toy_correction(r) for r in raws]) / scale

    ws, bs = fit(x, y, widths, args.seed, args.steps)
    export = pq.quantize(ws, bs, x[:20_000], price_scale_bps=scale, offset_bps=0, spec=2, product=PRODUCT)
    export = pq.certify(export, DOMAIN)

    held = [r for r in sample_raw(np.random.default_rng(args.seed + 1), 6_000) if certify._accepted(export, r)]
    f_bps = float_forward(ws, bs, pq.normalized_float_inputs(held, fields=pq.FIELDS_V2)) * scale
    q_bps = np.array([pq.forward(export, r) for r in held])
    t_bps = np.array([toy_correction(r) for r in held])
    qerr, ferr = np.abs(q_bps - f_bps), np.abs(f_bps - t_bps)

    vrng = np.random.default_rng(args.seed + 2)
    rows = certify.golden_rows(export, vrng)
    rejects = certify.reject_rows(export, vrng)
    pq.check_reject_vectors(export, rejects)
    names = [f.name for f in pq.FIELDS_V2]
    vectors = {
        "featureSpecVersion": 2,
        "weightsHash": export["weightsHash"],
        "modelVectors": [{"features": dict(zip(names, r)), "expectedCorrectionBps": pq.forward(export, r)}
                         for r in rows],
        "rejectVectors": rejects,
    }
    assert any(v["expectedCorrectionBps"] < 0 for v in vectors["modelVectors"]), "no negative correction in the vectors"

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "student_export.json").write_text(json.dumps(export, indent=1) + "\n")
    (out / "golden_vectors.json").write_text(json.dumps(vectors, indent=1) + "\n")
    report = {
        "note": "SYNTHETIC toy correction, not the teacher's residual. Quantization numbers only.",
        "featureSpecVersion": 2,
        "parameterCount": export["architecture"]["parameterCount"],
        "weightsHash": export["weightsHash"],
        "correctionRangeBps": [int(q_bps.min()), int(q_bps.max())],
        "quantizationErrorBps": {"p50": float(np.median(qerr)), "p99": float(np.percentile(qerr, 99)),
                                 "max": float(qerr.max())},
        "floatFitErrorBps": {"p50": float(np.median(ferr)), "p99": float(np.percentile(ferr, 99)),
                             "max": float(ferr.max())},
    }
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
