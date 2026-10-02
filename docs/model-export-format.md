# Model export format — `student_export.json` v2 (pricer)

The boundary between the distillation lane (Monte Carlo teacher → float student)
and the contracts lane (Stylus). If it's not in this document, it doesn't cross
the boundary. Reference implementation: [`tools/pricer_quant.py`](../tools/pricer_quant.py)
(`quantize()` produces this file from a float MLP; `certify()` attaches the
certified domain; `forward()` is the bit-exact twin of the contract).

**v2 (2026-09-29): every export carries a `certifiedDomain`**, the region where
fidelity was measured. The contract refuses everything else. Reason: the K1
round-1 student was trained with note terms pinned, but a v1 export accepted
any terms and extrapolated silently (e.g. coupon 10%/week → 7445 bps, far
below par).

Derived from GapGuard's `student-export-format.md`, with the changes listed at
the end. Those changes are deliberate and backed by measurements.

## Workflow for the distillation lane

1. Train the float student on **`normalized_float_inputs(raws)`**, i.e. the
   integer normalization divided by `qmax`, not on your own scaling. Then
   training and the chain see identical inputs (no train/serve skew).
   Target: `(priceBps − offsetBps) / priceScaleBps`.
2. Relu hidden layers, one linear output. Weights as numpy arrays with shape `(out, in)`.
3. `pq.quantize(weights, biases, calib_x, price_scale_bps, offset_bps)` → export dict.
4. Report fidelity for the **integer** student (`pq.forward`) against the teacher.
   That's the number the chain reproduces, not the float model.
5. Write the certified domain as JSON (example: `tools/domains/k1-r1.json`):
   exactly the region your adversarial eval covered. Then
   `python tools/certify.py --export <export> --domain <domain> --out model/<name>`
   writes the v2 export (keccak hash recomputed) and its golden + reject vectors.
6. Build the contract with `PRICER_MODEL_DIR=model/<name> cargo test`. The build
   recomputes the hash, checks the domain against the normalization ranges and
   proves no overflow is possible, or it fails.

## Inputs — featureSpecVersion 1

The field order is the `PricerInputs` struct order. The ranges below are the
**normalization convention** (teacher spec §4), fixed for featureSpecVersion 1.
What a model accepts is its certified domain, always inside these.

The price is **clean**: it covers coupon accruing from now on. The quoter adds
coupon accrued since strike (`INoteQuoter`).

| # | Field | Min | Max |
|---|---|---|---|
| 0 | spotBpsOfInitial | 2,000 | 30,000 |
| 1 | distToKnockInBps | −8,000 | 20,000 |
| 2 | volBpsAnnual | 1,500 | 15,000 |
| 3 | kiBarrierBps | 4,000 | 9,000 |
| 4 | acBarrierBps | 9,000 | 11,000 |
| 5 | couponBpsPerPeriod | 0 | 1,500 |
| 6 | timeToMaturitySecs | 604,800 | 63,072,000 |
| 7 | timeToNextObsSecs | 0 | 604,800 |
| 8 | observationsRemaining | 1 | 104 |
| 9 | flags (bit0 knockedIn) | 0 | 1 |

Normalization (binding, integer floor division, non-negative operands):
`x = ((v − min)·2·qmax + range//2) // range − qmax`, with `qmax = 2^(activationBits−1) − 1`.

## Certified domain (binding)

```jsonc
"certifiedDomain": {
  "ranges": [ {"name": "spotBpsOfInitial", "min": 5000, "max": 12000}, … ],  // all 10, in order;
                                          // min == max pins a note term
  "consistency": { "distToKnockIn": true,              // dist == spot − ki exactly
                   "observationIntervalSecs": 604800 }, // ttm == tNext + obsRemaining·interval (0 = off)
  "exclusions": [ { "name": "acObservationDay",        // refused if ALL bounds hold
                    "bounds": [ {"field": "timeToNextObsSecs", "min": 0, "max": 86400},
                                {"field": "spotBpsOfInitial", "min": 9500, "max": 10500} ] } ]
}
```

Checks run in this order, and the first failure is the revert:
1. ranges by field index → `OutOfRange(uint8 field, int64 value)`
2. derived fields, dist then ttm → `Inconsistent(uint8 field)` (1 or 6)
3. exclusions in order → `Uncertified(uint8 region)`

The consistency rules keep inputs on the manifold the teacher was trained on:
off it, a network returns numbers nobody measured. `certifiedRange(field)`
exposes the ranges on-chain. The domain is inside the hash, so `weightsHash`
pins both what the model computes and where it may answer.

## Schema

```jsonc
{
  "exportFormatVersion": 2,
  "featureSpecVersion": 1,
  "weightsHash": "0x…",                  // keccak256 of canonical form, see below
  "architecture": {
    "inputDim": 10,
    "layers": [ {"in": 10, "out": 64, "activation": "relu"}, …,
                {"in": 48, "out": 1, "activation": "linear"} ],
    "parameterCount": 3873
  },
  "quantization": { "weightBits": 16, "activationBits": 16 },
  "featureNormalization": [ {"name": "spotBpsOfInitial", "min": 2000, "max": 30000}, … ],
  "certifiedDomain": { … },            // see above
  "layers": [
    { "weightsHex": "0x…",               // int8 or int16 LE two's complement, row-major (out, in)
      "bias": [ … ],                     // int64, scale = s_in·s_w[i]; |b| < 2^53
      "requantMultiplierQ16": [ … ],     // per output channel, in [2^15, 2^16)
      "requantShift": [ … ] },           // per output channel; total shift S = 16 + shift, 1 ≤ S ≤ 62
    { "weightsHex": "0x…", "bias": [ … ] }   // head: no requant fields
  ],
  "output": { "multiplierQ16": …, "shift": …, "offsetBps": 10000 }
}
```

## Arithmetic (binding)

All in i64. Per hidden layer, channel `i`:

1. `acc = b[i] + Σ_j w[i,j]·x[j]`
2. `acc = max(acc, 0)` (relu)
3. `y = round_shift(acc · M[i], 16 + shift[i])`, saturated to `[−qmax−1, qmax]`

Head: `price = round_shift(acc · M, 16 + shift) + offsetBps`, clamped to `[0, 65535]`, returned as `uint16`.

`round_shift(p, S)`: rounds half away from zero, **sign-magnitude**:
`p ≥ 0 ? (p + 2^(S−1)) >> S : −((−p + 2^(S−1)) >> S)`.

## Canonical form and hash

`weightsHash = keccak256(json.dumps(export_with_weightsHash="0x", sort_keys=True,
separators=(",", ":"), ensure_ascii=False).encode())`. **No floats anywhere in
the file.** The build script re-serializes and re-hashes it in Rust, and
integer-only JSON has exactly one canonical spelling in both languages. The
contract returns the recomputed hash from `weightsHash()`.

## Golden vectors (`golden_vectors.json`)

- `weightsHash`: must equal the compiled model's hash (CI checks this).
- `modelVectors`: exactly 100 rows of `{features, expectedPriceBps}` from
  `pq.forward()`, all inside the domain: domain corners, every range bound,
  points just outside each exclusion, the rest sampled. CI requires **exact**
  equality.
- `rejectVectors`: `{features, error, index}` with `error` one of
  `OutOfRange` / `Inconsistent` / `Uncertified`: every range bound the ABI type
  can express, each consistency rule, each exclusion. CI requires exactly that
  revert.

## featureSpecVersion 2: the v2 perpetual note

Same export format (`exportFormatVersion` 2), same arithmetic, same hash rule. A spec-2
export differs in four places; `tools/pricer_quant.py` and the Stylus build read the spec
from the export and handle both ([v2-spec.md](v2-spec.md) §5).

| | spec 1 (v1 autocallable) | spec 2 (v2 perpetual) |
|---|---|---|
| Inputs | 10, `PricerInputs` | 5, `PerpPricerInputs` (below) |
| Output | clean price, `uint16`, head clamp 0..65,535 | **correction** to the closed form's principal, `int16`, head clamp −32,768..32,767; `offsetBps` 0 |
| Derived fields | `distToKnockInBps`, `timeToMaturitySecs` | none: `"consistency": {}`, no `Inconsistent` error |
| Pinned product | certified ranges with min == max | a `product` section, inside the hash |

| # | Field | Min | Max |
|---|---|---|---|
| 0 | spotBpsOfReference | 1,000 | 15,000 |
| 1 | volBpsAnnual | 1,500 | 15,000 |
| 2 | timeToNextFixingSecs | 0 | 604,800 |
| 3 | fixingsBeforeEarnings (0: the release comes before or at the next fixing) | 0 | 20 |
| 4 | flags (bit0 knockedIn) | 0 | 1 |

```jsonc
"product": {
  "kiBarrierBps": 6000,
  "meltShareWad": 18995352771274247,   // a, 1e18 fixed point
  "fixingIntervalSecs": 604800,
  "driftBps": 400,                     // the closed form's r
  "discountBps": 0,                    // the closed form's rho
  "teacher": { … }                     // the teacher's constants as integers: pinned by the hash, not read on chain
}
```

The contract exposes the first five as `product()`, and `answer(inputs)` returns the
correction, the product and the hash in one call (the quoter's path: entering a Stylus
contract costs gas each time). PerpQuoter prices a series with a
model only if the series' knock-in, melt share and interval equal them, and computes the
closed form with the model's two rates. The coupon reserve is not pinned: the note's price
is linear in it and the model prices the principal only.

Training target: `teacher principal − closed form principal`, in bps, with the closed form
evaluated on the integer inputs (`x = spotBpsOfReference / 1e4`), so the student learns
what the formula gets wrong and nothing else. `pq.quantize(..., spec=2, product=…)`;
`golden_vectors.json` carries `expectedCorrectionBps`; at least one model vector must be
negative. `PRICER_MODEL_DIR=model/p1 cargo test` builds the `PerpPricer` contract
(`cfg(perp)`) and runs the spec-2 golden tests. `model/synthetic-p` is the toy spec-2
export (`tools/make_synthetic_perp.py`).

## Changes from GapGuard's format, and why

| Change | Why (measured on a 3,873-parameter synthetic student) |
|---|---|
| **16-bit weights and activations** (were int8) | Stylus arithmetic is i64 regardless, so width is free in gas; it only costs code size (15.3 KB of the 24 KB limit). Post-training int8 weights alone cost **p99 110 bps / max 343 bps** on a price head spanning ~16,000 bps. That breaks the 50 bps K1 budget before the teacher error is even counted. int16: **p99 1.2 / max 3.2 bps**. Inputs were also coarse at 8 bits: a 1.1% step in spot. |
| **Activation scale = calibration max × 2** (not a percentile) | At the 99.99th percentile, one held-out input saturated a channel: max error 190 bps. One bit of headroom removes it. |
| **Per-channel requant multiplier/shift** (was one per layer) | Per-channel weight scales require a per-channel multiplier, or channels get rescaled wrongly. |
| **Linear head with multiplier/offset** (was sigmoid LUT) | A price isn't a probability. An int8 output would have ~40 bps resolution. |
| **Sign-magnitude rounding** (was `(p + copysign(half, p)) >> S`) | With an arithmetic shift, the old formula floors −0.25 to −1. The head can go negative, so this matters. |
| **int64 bias, integer-only JSON, `exportFormatVersion`** | int32 overflows at 16×16-bit scales. Floats make the canonical hash library-dependent. |
| **Out-of-range → revert** (was clamp on the student side) | Callers other than NoteQuoter get the same fail-closed guarantee. In-range behavior is unchanged. |
| **v2: certified domain** (pinned terms, consistency, exclusions) | A model trained on one term sheet accepted any terms under v1. The autocall observation-day jump (~229 bps in the teacher itself) can't be fitted by any continuous student, so it's an exclusion rather than an error budget. |
