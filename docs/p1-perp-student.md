# P1: teacher and student for the perpetual note

Status: 2026-10-02. **GATE PASS**: `model/p1` prices the principal of the v2 perpetual note
(k 60%, φ 1/yr, weekly fixings) at any vol from 20% to 90% within **20.9 bps** of the teacher
on T (p99 2.2, mean 0.49), confirmed on a set it never influenced: T2 **20.6**. The formula
alone is off by up to 270 bps on the same points (p99 77, mean 13.5). 7,145 parameters.
Spec: [v2-spec.md](v2-spec.md); product: [v2-perpetual-note.md](v2-perpetual-note.md).

On chain: `principalBps = max(0, round(formula · 1e4) + correctionBps)`, plus the coupon
part, which is exact. Every number below is for that sum, in bps of live notional.

## Deviation from the spec

**Input 3 is a count, not a time.** v2-spec.md section 5 has `timeToNextEarningsSecs`.
The model takes `fixingsBeforeEarnings` instead:

```
n = 0                                               if nextEarnings <= nextFixing
n = ceil((nextEarnings − nextFixing) / interval)    otherwise          (uint8)
```

- Why it is exact: where in a week the release falls doesn't change that week's return, so
  the price depends on the time to earnings only through n.
- Why it is needed: between n = 0 and n = 1 (release just before vs just after the next
  fixing) the price steps by up to 204 bps near the knock-in barrier (table below). With
  the time in seconds that step runs along the line tE = τ, and no box exclusion can cut
  it out; a small network would smear it. As a count there is nothing to smear.
- Range: n 0–18 certified (normalization 0–20). The spec's 98 days would refuse quotes
  for weeks after a release: TSLA's releases were 70–119 days apart.
- A release exactly at a fixing time counts as before it (n = 0 for that fixing).

The contract side followed (2026-10-02): v2-spec.md section 5, `IPerpPricer.sol`,
`PerpQuoter.sol`, the Stylus build and `tools/pricer_quant.py` carry the count
(`ml/perp_sets.ensure_fields` is now a no-op guard).

Everything else follows the spec: the formula, ρ = 0, the product section, the signed
int16 head, the coupon outside the model.

## Formula reference (`ml/perp_formula.py`)

`principal(x, sigma, knocked_in, r, rho, phi, k, dt)`, `coupon_value`, `note_value`,
`fair_coupon`. `python ml/perp_formula.py --check`: equal to `ml/perp_note_check.py`'s
closed form to 3e-16 over 72 points, and the roadmap's fair coupons 9.5 / 23.5 / 35.7 %/yr
(ρ = r = 4%, dt 1/52).

Those roadmap numbers are the coupon with V0(top) = 1, not V0(1) = 1 as its text says; at
x = 1 they are 9.54 / 23.62 / 35.92. Under P1's convention (ρ = 0, 365-day year) the fair
coupon is lower, because nothing is discounted:

| vol | 20% | 30% | 40% | 55% | 70% | 80% | 90% |
|---|---|---|---|---|---|---|---|
| fair coupon %/yr, formula | 1.36 | 5.83 | 11.59 | 20.22 | 27.99 | 32.65 | 36.93 |
| teacher, by weeks to earnings | 1.35–1.37 | 5.80–5.89 | 11.55–11.72 | 20.15–20.46 | 27.92–28.35 | 32.59–33.10 | 36.87–37.46 |

## Earnings calibration (`ml/calibrate_earnings.py`, log `ml/calibrate_earnings.log`)

- Dates: `ml/data/tsla_earnings.json`, from SEC EDGAR (8-K filings with item 2.02,
  `ml/data/fetch_tsla_earnings.py`). 40 releases in the fit window 2016-09-28 to
  2026-09-28, every one accepted after the New York close, so each moves the next session.
  Tesla's quarterly deliveries reports carry the same item; they are listed, not used.
- Reaction days: std 8.97% against 3.60% on the other 2,472 days (variance ratio 6.2).
  Mean −1.3%, t = −0.92, so the earnings move is modelled without drift.
- Jumps refitted on the other days: 75.77 per year (72.23 with earnings days in), size std
  4.83% (5.26%).
- Earnings move: **σE = 8.32% ± 1.09%** (maximum likelihood; moments 8.23%).
- `ml/teacher_perp_config.json`: volRef 0.590315; of the variance, diffusion 41.3%, jumps
  50.7%, earnings 8.0% (one release per 91 days). Sizes scale with vol / volRef, so the
  input vol stays the total annualized vol.

## Teacher (`ml/teacher_perp.py`)

No sampling. Right after a fixing the state is (x, knocked in, weeks to the earnings
week); its value solves a linear fixed point over the 13-week cycle. The value is piecewise
linear in ln x on a grid with ln k and 0 as nodes, so each expectation is a closed form and
both discontinuities sit on node positions. One week is four FFTs; the cycle is solved by
GMRES. A mid-week state is one more expectation. Two grids are extrapolated,
(4 · fine − coarse) / 3.

- About a second per vol for the fixed point on both grids, 1–4 ms per state.
- `python ml/test_teacher_perp.py`, log `ml/test_teacher_perp.log`: **16/16 pass**.

| Check | Result |
|---|---|
| weekly return law | martingale to 4e-16; variance of a 13-week cycle = vol² × 13 weeks |
| fixing rule vs the spec's integer rule, 400 paths × 150 fixings | states equal at every fixing, payouts within 3e-15 |
| FFT operator vs explicit matrices | 4e-16 |
| grid | second order (ratio 3.8–3.9 per halving); extrapolated teacher within 0.019 bps of the next refinement |
| GBM limit vs `perp_note_check.mc`, 12 states, 2^18 paths | max \|z\| 1.32 |
| P1 world vs brute-force Monte Carlo, 40 states (both flags, mid-week, around earnings, x > 1, x < k, τ = 0) | max \|z\| 2.98; the two largest rechecked with 24 seeds: −0.01 and −0.27 bps (± 0.3) |
| a week ahead reproduces the fixed point; τ → 0 continuous; no earnings → no dependence on tE | 2e-14; 2e-11; 0 |

Monte Carlo and quadrature share the fixing rule function, so their agreement checks the
numerics; the rule itself is checked against the spec's integers in the second row.

## What the formula gets wrong (`ml/perp_measure.py`, log `ml/perp_measure.log`)

Formula − teacher, principal, bps. "Smooth walk" is a random walk without jumps (the
roadmap's gap 1: contract rules only); "teacher's world" adds jumps and earnings (gap 2).

**Right after a fixing** (the roadmap's open item 1 covered one vol):

| vol | smooth walk, clean | smooth walk, knocked in | teacher's world, clean | teacher's world, knocked in |
|---|---|---|---|---|
| 20% | −37 … +39 | −2 … +16 | −31 … +19 | −2 … +13 |
| 30% | −21 … +42 | −2 … +27 | −17 … +25 | −1 … +20 |
| 40% | −9 … +45 | −1 … +36 | −6 … +29 | −1 … +24 |
| 55% | +6 … +50 | −1 … +45 | +6 … +35 | 0 … +30 |
| 70% | +18 … +56 | 0 … +52 | +16 … +40 | 0 … +36 |
| 90% | +31 … +64 | +1 … +60 | +27 … +47 | +2 … +42 |

The 55% smooth-walk row is the roadmap's "5–51 bps, always high". Below 55% the formula is
not always high.

**Mid-week**, teacher's world, largest error over x 0.20–1.30 (it sits next to a barrier):

| vol, state | τ = 0 | 1 h | 6 h | 1 d | 2 d | 3.5 d | 5 d | 7 d |
|---|---|---|---|---|---|---|---|---|
| 20% clean | 96 | 119 | 103 | 73 | 55 | 101 | 138 | 177 |
| 20% knocked in | 98 | 39 | 35 | 23 | 13 | 21 | 31 | 42 |
| 55% clean | 122 | 121 | 107 | 58 | 28 | 43 | 59 | 75 |
| 55% knocked in | 127 | 98 | 76 | 44 | 23 | 24 | 39 | 58 |
| 90% clean | 96 | 95 | 68 | 35 | 21 | 27 | 34 | 47 |
| 90% knocked in | 117 | 95 | 72 | 43 | 23 | 22 | 38 | 57 |

Mean error over that spot range: 1–22 bps.

**Jumps at a fixing** (open item 2), value at the barrier minus 1 bps of spot below:

| vol | knock-in at x = k, clean | heal at x = 1, knocked in | clean at x = 1 |
|---|---|---|---|
| 20% | 447 | 112 | 0.01 (kink only) |
| 30% | 369 | 134 | 0.04 |
| 55% | 260 | 118 | 0.07 |
| 90% | 192 | 88 | 0.07 |

**Slope just outside a band**, teacher's world, bps per 10 bps of spot, clean near k (the
heal side is 4–8 times flatter):

| vol | τ > 1 h | τ > 6 h | τ > 1 d |
|---|---|---|---|
| 20% | 199 | 106 | 62 |
| 30% | 124 | 62 | 38 |
| 55% | 54 | 28 | 18 |
| 90% | 26 | 14 | 10 |

K3 placed its bands where the slope fell below 150: here that is 6 hours at every vol.
K3's own knock-in slope was still 86–345 a day before its last observation
([k3-vol-input.md](k3-vol-input.md)).

**Earnings before vs after the next fixing**, value(n = 0) − value(n = 1), worst spot:

| vol | τ = 1 d | 2 d | 3.5 d | 5 d | 7 d |
|---|---|---|---|---|---|
| 20% | +204 | +158 | +128 | +108 | +89 |
| 55% | +103 | +81 | +63 | +52 | +43 |
| 90% | +63 | +48 | +47 | +46 | +44 |

Near k the release before the fixing can still lift the stock over the barrier; above the
reference it resets H before the move instead of after. Over n = 1–18 the price moves by
another 55–92 bps.

## Certified domain (`tools/domains/p1.json`)

| Input | Certified | Normalization |
|---|---|---|
| `spotBpsOfReference` | 2,000–13,000 | 1,000–15,000 |
| `volBpsAnnual` | 2,000–9,000 | 1,500–15,000 |
| `timeToNextFixingSecs` | 0–604,800 | same |
| `fixingsBeforeEarnings` | 0–18 | 0–20 |
| `flags` | 0–1 | same |

| # | Exclusion | Refused |
|---|---|---|
| 0 | `knockInFixingBand` | τ ≤ 6 h, spot 5,000–7,000, not knocked in |
| 1 | `healFixingBand` | τ ≤ 6 h, spot 9,500–10,500, knocked in |

**Band length was chosen on V**, by a rule fixed before training: train one model per
candidate (6 hours, 1 day) and take 6 hours if its V maximum outside the 6-hour bands is at
most 40 bps. It was 21.4. The 1-day model reached 11.4 outside 1-day bands and 40.1
outside 6-hour ones.

## Sets (`ml/perp_sets.py`)

Defined before any was labelled; seeds used nowhere else. Labels: extrapolated teacher,
n_k 256 (training: 128, within 0.02 bps).

| set | points | construction |
|---|---:|---|
| T (gate) | 350,880 | 6 vols (20, 30, 40, 55, 70, 90%) × spot every 100 bps and every 20 within ±500 of k and of 1 × 14 times to the fixing (both candidate band edges and one second after) × 10 values of n × both flags, + 30,000 uniform |
| V (selection) | 372,600 | other values (7 vols incl. 20.5% and 89.5%, spots offset 50 / 10, 11 times, 10 n), + 30,000 uniform, 40,000 clean near k and 10,000 knocked in near 1 just outside the bands |
| T2 (confirmation) | 382,800 | T's construction, vols + 1 point, spots + 30 / + 6, other n, new uniform seed; labelled once, after the model was certified |
| S (spread) | 15,000 triplets | each state at vol − 2, vol, vol + 2 points; half uniform, half clean near k |
| training | 1,000,000 | 55% uniform, 20% clean near k, 10% knocked in near 1, 10% n = 0 or 1, 5% vol ends; 1,385 distinct vols; times skewed towards the fixing in the barrier parts |

Compact label files are in `ml/perp_p1_*_labels.npz` (labels and a hash of the points;
the points are rebuilt from the construction). The training set is not committed:
10 minutes to relabel.

## Training, selection, gate

`ml/perp_train.py` is K3's trainer with five inputs and a signed head: 64-48-40-40
(**7,145 parameters**; K3 has 7,465), target (teacher − formula) / 100 bps, weighted MSE,
Adam one-cycle, EMA of the weights, 1,500 epochs, the checkpoint with the lowest float
maximum on V, quantized by `pq.quantize(spec=2)`. Logs: `ml/perp_runs/`.

| run | bands in training | V max outside its bands (integer) | p99 | mean |
|---|---|---:|---:|---:|
| a | 1 day | 11.4 | 2.0 | 0.46 |
| **b** (model/p1) | 6 hours | 21.4 | 2.3 | 0.49 |

**Gate** (`ml/perp_eval.py`, logs `ml/perp_eval_T.log`, `ml/perp_eval_T2.log`): 50 bps,
fixed before training. Every point outside the exclusions is priced through `pq.forward`
(domain check included), every point inside is refused.

| set | points priced | formula + student: max | p99 | mean | formula alone: max | p99 | mean |
|---|---:|---:|---:|---:|---:|---:|---:|
| V | 357,052 | 21.4 | 2.3 | 0.49 | 255.8 | 78.0 | 14.22 |
| **T** | 330,554 | **20.9** | 2.2 | 0.49 | 270.4 | 76.9 | 13.50 |
| **T2** | 359,266 | **20.6** | 2.2 | 0.50 | 253.8 | 79.2 | 13.92 |

T was evaluated once, after the model and the band were fixed; T2 was labelled after that.

By region on T (maximum / mean, formula + student): clean near k 20.9 / 0.77; clean
elsewhere 6.4 / 0.45; knocked in near 1 13.9 / 0.67; knocked in elsewhere 4.5 / 0.38;
vol ≤ 25% 20.9 / 0.52; vol ≥ 75% 6.7 / 0.55. The worst points are clean notes at spot
5,980–6,040, vol 20%, one second past the 6-hour band. The correction ranges from −195 to
+265 bps (mean size 13.5), far inside int16; quantization moves it by at most 1.67 bps.

**Vol-band spread** (`ml/perp_eval.py --set S`, log `ml/perp_eval_S.log`), the Desk's
P(vol − 2 points) − P(vol + 2 points):

| | max | p99 | mean |
|---|---:|---:|---:|
| teacher spread | 296.3 | 279.6 | 121.4 |
| formula + student, error on the spread | 11.6 | 2.5 | 0.56 |
| formula alone, error on the spread | 51.6 | 11.2 | 1.49 |

The teacher's spread is positive in all 14,965 triplets and so is the model's (the formula
alone has 22 negative ones): the perpetual note always loses from vol, where the K3 note
gained in 6% of states.

## Exported (`model/p1`)

`weightsHash` 0x650a942371b5608601728735d72df2e3b1687116f86f8ef15aad99cdac3ef807, 100
golden vectors (`expectedCorrectionBps`), 13 reject vectors.

```json
"product": {"kiBarrierBps": 6000, "meltShareWad": 18995352771274247, "fixingIntervalSecs": 604800,
            "driftBps": 400, "discountBps": 0,
            "teacher": {"lambdaYearE6": 75771200, "muJE12": 561804000, "sigmaJE9": 48283600,
                        "sigmaEE9": 83187100, "volRefE6": 590315, "earningsCycleSecs": 7862400}}
```

## On chain (done, 2026-10-02)

1. `PerpPricerInputs` field 3 is `uint8 fixingsBeforeEarnings`, computed by PerpQuoter as
   in "Deviation" from the listing's `nextEarnings`, the series' next fixing and its
   interval (capped at 255). It does not depend on `now`.
2. The quoter refuses a `nextEarnings` that has passed (`EarningsDatePassed`). Otherwise a
   stale date would read as n = 0, a release before the next fixing, which is worth up to
   ~200 bps.
3. `tools/pricer_quant.py`, `tools/certify.py`, the Stylus build, `model/synthetic-p` and
   the ABIs carry the field; `python tools/certify.py` reproduces `model/p1` byte for byte.
4. A listing's vol ± its band must stay inside 2,000–9,000: PerpDesk checks it against
   `certifiedRange(1)` when the listing or the spread is set (`ModelMismatch(3)`).
5. The two exclusions cover the last 6 hours before a fixing near the barriers. A Desk
   with `minSecsToFixing` ≥ 21,600 never meets them: `DeployPerp.s.sol` sets 6 hours.
6. The model knows nothing about the coupon; R comes from the series' terms.
7. The gate used half-to-even rounding of the formula where the contract rounds half up;
   they differ only on exact ties.
8. Stylus: `PRICER_MODEL_DIR=../../model/p1 cargo test` reproduces the 100 golden vectors
   and the 13 refusals exactly; the contract is **21,785 bytes** (limit 24,576). On a Nitro
   dev node every quote of `contracts/script/e2e-perp-devnode.sh` equals the Python integer
   twins (`contracts/logs/e2e-perp-devnode-p1.log`), and 104 quoter vectors pass in forge
   (`contracts/test/vectors/perp_quoter_vectors_p1.json`).

## Caveats

- One stock. The jump and earnings constants are TSLA's; other stocks need their own
  config and model, or a measured error bar ([multi-stock-jumps.md](multi-stock-jumps.md)).
- One seed per band. K3's seeds spread widely; here V, T and T2 agree (21.4 / 20.9 / 20.6).
- The teacher's simplifications are v3's: Q = P, symmetric jumps, flat vol, calendar clock
  (weekends diffuse). Earnings repeat every 91 days after the first.
- States a note can hardly reach are certified too (a clean note below k with a full week
  to go); the worst formula errors sit there.
- Training ran on the shared GPUs under a hard memory cap (432 MB); the CPU was too loaded.

## Reproduce

```sh
PY=/opt/ai/cache/venv-cuda/bin/python           # numpy, scipy: teacher, labels
PYT=/opt/ai/cache/venv-student-cuda/bin/python  # torch, keccak: training, gate
export PYTHONDONTWRITEBYTECODE=1
$PY ml/perp_formula.py --check
$PY ml/data/fetch_tsla_earnings.py && $PY ml/calibrate_earnings.py > ml/calibrate_earnings.log
$PY ml/test_teacher_perp.py > ml/test_teacher_perp.log      # ~8 min
$PY ml/perp_measure.py > ml/perp_measure.log                # ~3 min
for s in V T T2 S; do $PY ml/perp_sets.py --set $s --out <dir>/p1_$s.npz; done     # ~4 min each
$PY ml/perp_sets.py --set train --n 1000000 --seed 1 --n-k 128 --out <dir>/train_s1.npz
$PYT ml/perp_train.py --train <dir>/train_s1.npz --val <dir>/p1_V.npz --band 21600 --device cuda --out-dir <dir>/b
(cd tools && $PYT certify.py --export ../ml/student_export_p1.json --domain domains/p1.json --out ../model/p1)
$PYT ml/perp_eval.py --model model/p1 [--set T2|S]
```

Changing `ml/teacher_perp.py`, `ml/perp_formula.py` or the config invalidates every label
file (their hash is stored with the labels).
