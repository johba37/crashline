"""Attach a certified domain to a student export and generate its test vectors.

Usage (from tools/):
  ../tools/.venv/bin/python certify.py --export ../ml/student_export.json \\
      --domain domains/k1-r1.json --out ../model/k1-r1

Writes <out>/student_export.json (format v2, keccak weightsHash recomputed;
weights untouched) and <out>/golden_vectors.json:
  modelVectors  100 in-domain rows -> expected clean priceBps (pq.forward)
  rejectVectors one row per refusal the domain defines (range bounds, derived
                fields, excluded regions), each with its expected error.

The domain is a claim: "fidelity was measured here". Keep it to what the
adversarial eval actually covered.
"""

from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np

import pricer_quant as pq

TYPE_MIN = {1: -(2**31)}  # distToKnockInBps is int32; every other field is unsigned
TYPE_MAX = {0: 2**16 - 1, 1: 2**31 - 1, 2: 2**16 - 1, 3: 2**16 - 1, 4: 2**16 - 1, 5: 2**16 - 1,
            6: 2**32 - 1, 7: 2**32 - 1, 8: 2**8 - 1, 9: 2**8 - 1}


def _fix_derived(v: list[int], dom: dict) -> list[int]:
    c = dom["consistency"]
    if c["distToKnockIn"]:
        v[pq.DIST] = v[pq.SPOT] - v[pq.KI]
    if c["observationIntervalSecs"]:
        v[pq.TTM] = v[pq.TNEXT] + v[pq.OBS] * c["observationIntervalSecs"]
    return v


def _accepted(export: dict, v: list[int]) -> bool:
    try:
        pq.forward(export, v)
        return True
    except (pq.OutOfRange, pq.Inconsistent, pq.Uncertified):
        return False


def sample_in_domain(export: dict, rng: np.random.Generator, n: int) -> list[list[int]]:
    dom = export["certifiedDomain"]
    rows, tries = [], 0
    while len(rows) < n:
        tries += 1
        assert tries < 100 * n, "domain too narrow to sample"
        v = [int(rng.integers(r["min"], r["max"] + 1)) for r in dom["ranges"]]
        v = _fix_derived(v, dom)
        if _accepted(export, v):
            rows.append(v)
    return rows


def golden_rows(export: dict, rng: np.random.Generator) -> list[list[int]]:
    dom = export["certifiedDomain"]
    lo = [r["min"] for r in dom["ranges"]]
    hi = [r["max"] for r in dom["ranges"]]
    rows = []
    for base in (lo, hi):  # corners, made consistent; kept only if accepted
        v = _fix_derived(list(base), dom)
        if _accepted(export, v):
            rows.append(v)
    # every range bound with the other fields sampled
    for i in range(len(pq.FIELDS)):
        for bound in (lo[i], hi[i]):
            for _ in range(50):
                v = sample_in_domain(export, rng, 1)[0]
                v[i] = bound
                v = _fix_derived(v, dom)
                if v[i] == bound and _accepted(export, v):
                    rows.append(v)
                    break
    # just outside each exclusion, per bound
    for ex in dom["exclusions"]:
        for b in ex["bounds"]:
            f = pq.FIELD_NAMES.index(b["field"])
            for edge in (b["min"] - 1, b["max"] + 1):
                for _ in range(200):
                    v = sample_in_domain(export, rng, 1)[0]
                    for bb in ex["bounds"]:  # put the other bounds inside the region
                        g = pq.FIELD_NAMES.index(bb["field"])
                        v[g] = int(rng.integers(bb["min"], bb["max"] + 1))
                    v[f] = edge
                    v = _fix_derived(v, dom)
                    if _accepted(export, v):
                        rows.append(v)
                        break
    rows += sample_in_domain(export, rng, 100 - len(rows))
    return rows[:100]


def reject_rows(export: dict, rng: np.random.Generator) -> list[dict]:
    dom = export["certifiedDomain"]
    out = []

    def add(v, error, index):
        out.append({"features": dict(zip(pq.FIELD_NAMES, v)), "error": error, "index": index})

    for i, r in enumerate(dom["ranges"]):
        for v_i in (r["min"] - 1, r["max"] + 1):
            if not (TYPE_MIN.get(i, 0) <= v_i <= TYPE_MAX[i]):
                continue
            # ranges are checked first, in field order, and every other field of
            # an in-domain sample is in range: the first failure is field i
            v = sample_in_domain(export, rng, 1)[0]
            v[i] = v_i
            add(v, "OutOfRange", i)
    c = dom["consistency"]
    if c["distToKnockIn"]:
        v = sample_in_domain(export, rng, 1)[0]
        v[pq.DIST] += 1 if v[pq.DIST] < dom["ranges"][pq.DIST]["max"] else -1
        add(v, "Inconsistent", pq.DIST)
    if c["observationIntervalSecs"]:
        v = sample_in_domain(export, rng, 1)[0]
        v[pq.TTM] += 1 if v[pq.TTM] < dom["ranges"][pq.TTM]["max"] else -1
        add(v, "Inconsistent", pq.TTM)
    for k, ex in enumerate(dom["exclusions"]):
        for corner in ("min", "max", "mid"):
            v = sample_in_domain(export, rng, 1)[0]
            for b in ex["bounds"]:
                g = pq.FIELD_NAMES.index(b["field"])
                v[g] = b[corner] if corner != "mid" else (b["min"] + b["max"]) // 2
            v = _fix_derived(v, dom)
            add(v, "Uncertified", k)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=2)
    args = ap.parse_args()

    export = pq.certify(json.load(open(args.export)), json.load(open(args.domain)))
    rng = np.random.default_rng(args.seed)
    rows = golden_rows(export, rng)
    rejects = reject_rows(export, rng)
    pq.check_reject_vectors(export, rejects)
    vectors = {
        "featureSpecVersion": pq.FEATURE_SPEC_VERSION,
        "weightsHash": export["weightsHash"],
        "modelVectors": [
            {"features": dict(zip(pq.FIELD_NAMES, r)), "expectedPriceBps": pq.forward(export, r)} for r in rows
        ],
        "rejectVectors": rejects,
    }
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "student_export.json").write_text(json.dumps(export, indent=1) + "\n")
    (out / "golden_vectors.json").write_text(json.dumps(vectors, indent=1) + "\n")
    print(f"{out}: weightsHash {export['weightsHash']}, {len(rows)} model vectors, {len(rejects)} reject vectors")


if __name__ == "__main__":
    main()
