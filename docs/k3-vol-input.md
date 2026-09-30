# K3: vol as a live input

Status: 2026-09-30. **GATE PASS**: `model/k3` prices the K2 product at any vol from 20% to 90%
within **37.9 bps** of teacher v3 on T (p99 14.1, mean 2.65), confirmed on two sets it never
influenced: T2 **34.7** and T3 (the vol endpoints) **38.0**. The first model failed T at the vol
floor (71.2 bps); the fix and how it was measured are below. Stylus: 23,901 bytes, activation
check passes on Robinhood Chain testnet. Code: `ml/teacher.py` + `ml/teacher_config_v3.json`
(teacher v3), `ml/round3_bands.py` (band sizing), `ml/round3_sets.py` (points, labels),
`ml/round3.py` (training, K2's trainer), `ml/round3_eval.py` (gate, spread report),
`tools/domains/k3.json` (certified domain).

## Why

K2 prices one product at one vol (55%). A listing at another vol, another stock, or a
Desk quote at vol ± a band needs vol as an input. The interface already carries
`volBpsAnnual` and K2's network already reads it (10 inputs, normalized over 15–150%);
K2's certified domain pins it. K3 trains and certifies it over 20–90%, with no change to
the model format, the contract or the interfaces.

## Teacher v3

Two changes to the v2 jump teacher, both config-driven; with neither, the code is the v2
code path (check (j): bit-identical to the frozen K2 teacher on numpy and CUDA).

1. **Vol-scaled jumps** (`volRef` in the config). v2 pins the jump *variance*, so any
   listing below 44.7% vol is refused. v3 keeps the jump rate (72.2/yr) and scales the jump
   sizes with each label's vol, `muJ, sigmaJ × vol / volRef`, with `volRef` = TSLA's fitted
   total vol (0.590779). Jumps then carry a fixed **57.28%** of the variance at every vol,
   and nothing is refused. This is the "fixed-share rule" measured in
   [multi-stock-jumps.md](multi-stock-jumps.md): within 7.6 bps of each large cap's own
   fit, 16–27 bps off on stocks with rare large earnings drops.
2. **Separate discount rate** (`rDiscount`, the contracts thread's patch). Payouts discount
   at what the pair's escrow earns on chain, nothing, while the stock drifts at r = 4%. A
   pair redeems for maxPayout at any time, so NOTE + WRITER = maxPayout holds only with
   this discounting. It lifts K2-product prices by 1–110 bps (check (i)).

K2 keeps its teacher: `ml/reference/k2/` holds byte-identical copies of the three files
that labelled every K2 set, and `round2_sets` labels and fingerprints with them, so
`round2_eval.py --model model/k2` still accepts K2's caches (GATE PASS, 18.7 bps).

Checks (`ml/test_teacher.py`, log `ml/test_teacher.log`, **16/16 PASS**): (a)–(h) as before;
(i) the discount rate; (j) v2 path bit-identical to the frozen K2 teacher; (k) v3 at vol s
equals fixed jumps scaled to s, same seed, six vols 15–120% (max |diff| 9e-13 bps);
(l) martingale and total variance at 20 / 55 / 90%; (m) European puts and the knocked-in
note vs Merton closed forms at 25% and 85%; (n) smoothing vs brute force with vols mixed in
one batch; (o) torch vs numpy, 64 states at 15–120%; (p) barrier equality.

## Where the teacher is too steep: band sizing

`ml/round3_bands.py`, log `ml/round3_bands.log` (2^17 paths, max stderr 6.37 bps). K2 excluded
tNext ≤ 1 day near each barrier; just outside, its teacher moved up to 144 bps per 10 bps of
spot and K2's network fitted that to 18.7 bps. The observation-day step is smoothed over about
vol × √tNext of spot, so at low vol the same steepness sits further from the observation:

| Knock-in, not knocked in: max bps per 10 bps of spot | obs 1 | obs 2 | obs 3 | obs 5 |
|---|---:|---:|---:|---:|
| vol 20%, tNext 1 day + 1 s / 2 d / 4 d / 7 d | 345 / 232 / 156 / 115 | 235 / 171 / 125 / 98 | 185 / 139 / 108 / 90 | 142 / 108 / 91 / 74 |
| vol 30% | 237 / 158 / 109 / 79 | 150 / 119 / 89 / 69 | 125 / 94 / 73 / 66 | 93 / 73 / 59 / 52 |
| vol 40% | 178 / 121 / 84 / 71 | 118 / 90 / 72 / 53 | 100 / 73 / 59 / 51 | 73 / 60 / 51 / 42 |
| vol 55% | 131 / 89 / 74 / 50 | 85 / 69 / 53 / 44 | 70 / 53 / 45 / 40 | 48 / 43 / 39 / 34 |
| vol 90% | 86 / 59 / 44 / 38 | 54 / 42 / 38 / 35 | 44 / 38 / 32 / 33 | 30 / 31 / 29 / 27 |

At 15% vol and obs 1 the slope is 458 just past a day and still 146 a full week out, so
no band short of the whole week certifies it; the range starts at 20%. The autocall barrier stays under 40 bps per 10 bps outside K2's
band at every vol. Knocked-in notes have delta ≈ 1 (10 per 10 bps) and no step.

## Certified domain (`tools/domains/k3.json`)

K2's ranges with `volBpsAnnual` 2000–9000. Exclusions, each placed where the slope just
outside falls below 150 bps per 10 bps; disjoint, so a refusal names one region:

| # | name | refused |
|---|---|---|
| 0 | `acObservationDay` | tNext ≤ 1 day, spot 9500–10500 (K2) |
| 1 | `kiObservationDay` | tNext ≤ 1 day, spot 5000–7000, not knocked in (K2) |
| 2 | `kiLowVolLastWeek` | 1 day < tNext ≤ 5 days, vol 20–29.99%, obs 1 |
| 3 | `kiLowVolWeeks2to4` | 1 day < tNext ≤ 3 days, vol 20–29.99%, obs 2–4 |
| 4 | `kiMidVolLastWeeks` | 1 day < tNext ≤ 2.5 days, vol 30–49.99%, obs 1–2 |

(2–4 also require spot 5000–7000 and not knocked in.) Contract size with this domain and
K2's weights: **23,979 bytes** (K2's domain: 23,898; limit 24,576).

## Sets

All defined in `ml/round3_sets.py` and committed before any of them was labelled; seeds are
used nowhere else. Labels at 2^18 paths, topped up to max stderr ≤ 4 bps.

| set | points | construction |
|---|---:|---|
| T (gate) | 164,692 | 6 vols (20, 30, 40, 55, 70, 90%) × spot every 100 bps and every 20 within ±500 of ki and ac × 8 obs × 7 tNext values plus each band's first certified tNext and an hour after × both knockedIn, + 30,000 uniform |
| V (selection) | 237,408 | disjoint values (vols between T's, spots offset 50 / 10, other obs and tNext), 30,000 uniform, 40,000 steep-ki, 10,000 steep-ac |
| T2 (confirmation) | 182,872 | T's construction, vols +1 point, spots +30 / +6, new uniform seed; labelled once, at the end |
| S (spread) | 15,000 triplets | each state at vol − 2, vol, vol + 2 points, all certified; half uniform, half steep-ki |
| training | 1M + 1M mixture, 2 × 400k steep-ki, 200k steep-ac at 2^15 | K2's mixture with vol uniform and each band edge taken at the row's vol; steep sets skew vol low |

Labelling runs in shards of 8,192 rows (K2: 512): at 2^14–2^15 paths a 512-row shard splits
into 26 small observation buckets and the GPU idles (3.1M vs 26.7M path-labels/s at 2^14);
at 2^18 both run at 32M/s.

## Pilot (feasibility, not selection)

400k mixture labels at 2^14, a 40k selection set at 2^16 (stderr up to 7.7 bps), 1500 epochs,
64-48-40-40 (K2's architecture), integer student outside the bands:

| | max | p99 | mean |
|---|---:|---:|---:|
| K3 pilot, vol 20–90% | 65.2 | 29.4 | 6.49 |
| K2 at the same budget (b_s1_ema, one vol) | 44.6 | 14.3 | 2.86 |

Error by vol bucket: max 52–65, p99 27–33, so no part of the range breaks. The worst points
are K2's hardest region: not knocked in, spot ≈ 6000, tNext just past 1 day.

## Training, selection, gate

K2's trainer unchanged (`ml/round3.py`): 64-48-40-40 (7,465 params), weighted MSE, Adam
one-cycle, EMA of the weights, checkpoint with the lowest float max on the selection set,
quantized by `pq.quantize`. Logs: `ml/round3_runs/`.

### First round: selected on V, failed on T at the vol floor

Selection table (`ml/round3_select.log`, integer student, V outside the bands, bps):

| run | training labels | seed | V max | p99 | mean | max by vol 20-30 / 30-45 / 45-60 / 60-75 / 75-90% |
|---|---|---:|---:|---:|---:|---|
| a | train s1 + steep-ki s1 (1.4M) | 0 | 49.9 | 13.3 | 2.76 | 32.1 / 49.9 / 46.5 / 43.4 / 31.1 |
| b | s1 + s2 + steep-ki s1, s2 + steep-ac (3.0M) | 1 | 52.9 | 18.2 | 4.00 | 52.9 / 47.8 / 51.2 / 41.9 / 44.6 |
| c | s1 + s2 + steep-ki s1 (2.4M) | 2 | 39.4 | 11.8 | 2.47 | 39.4 / 33.7 / 39.1 / 25.6 / 26.2 |
| **d** | c + steep-ki s2 + edge-ki s1 (3.1M) | 2 | **37.1** | 12.8 | 2.62 | 37.1 / 34.8 / 36.9 / 23.8 / 23.2 |
| e | as c | 3 | 53.1 | 15.2 | 3.07 | 53.1 / 32.0 / 52.2 / 38.0 / 36.9 |

The edge-ki set (not knocked in, spot 5700–6400, obs 1–3, tNext within 12 hours past the band
edge at the row's vol) was added after run a's worst V points all landed there. Seeds move the
max by up to 14 bps on the same data (c vs e); K2's spread was 18.5–22.1.

**Run d's first and only clean evaluation on T: GATE FAIL, max 71.2 bps** (p99 14.1, mean
2.63; `ml/round3_eval_first.log`). Every worst point is at vol exactly 2000, the floor of the
range, not knocked in, spot 5920–5980, tNext just past a day, at obs 5 and obs 26 alike. Inside
the range T agrees with V:

| near ki, not knocked in: max \|error\| | T | V |
|---|---:|---:|
| vol exactly 2000 | **71.2** (1,116 grid points) | 15.1 (22 points) |
| vol 2001–2999 | 17.6–19.2 | 24.1–37.1 |
| vol exactly 3000 | 37.7 | 34.8 |
| vol 5000–9000 | 26.6 | 36.9 |

V's grid started at 2100, so selection never saw the floor: a hole in V, and a boundary
effect in the network (the one input where the domain ends inside the normalization range).

### The fix, and how it is measured

Defined and committed before any label or model for them ("K3 first gate: FAIL at the vol
floor"): **T3**, held out, T's grid at vol 2000 and 9000 plus near-end uniform points, new
seeds, disjoint from T, T2, V; **V_edge**, selection, the same ends with other offsets plus
steep-ki at vol 2000; and an **ends** training set (the mixture within 2 vol points of either
end, a third exactly at it). Runs f and g train on d's data plus 400k ends labels, selected on
V + V_edge. From here T is reported with the caveat that its failure shaped the fix (as K2's
T was); **T2 and T3 are the clean confirmations**.

### Second round and the gate

| run | training labels | seed | V max | p99 | mean | V_edge max | p99 |
|---|---|---:|---:|---:|---:|---:|---:|
| d (first round's pick) | 3.1M | 2 | 37.1 | 12.8 | 2.62 | 44.9 | 17.2 |
| **f** | d + ends s1 (3.5M) | 2 | **32.3** | 12.9 | 2.65 | **32.3** | 15.0 |
| g | d + ends s1 (3.5M) | 0 | 35.2 | 12.7 | 2.65 | 38.8 | 14.3 |

Selected on V + V_edge: **f** (max 32.3; by vol bucket 31.2 / 32.3 / 30.6 / 27.0 / 27.1).
Certified as `model/k3` (weightsHash `0x745cd5f8…1303523f`, `tools/domains/k3.json`).
Quantization: |int − float| ≤ 5.39 bps on the selection set.

| set | role | points certified | max | p99 | mean | log |
|---|---|---:|---:|---:|---:|---|
| T | **the gate** (its first result shaped the fix) | 130,752 | **37.9** | 14.1 | 2.65 | `ml/round3_eval.log` |
| T2 | confirmation, never used before | 143,484 | **34.7** | 14.0 | 2.63 | `ml/round3_eval_t2.log` |
| T3 | confirmation at the vol endpoints, defined after the first T | 43,698 | **38.0** | 15.9 | 2.85 | `ml/round3_eval_t3.log` |

Max label stderr 3.89 / 3.90 / 3.90 bps (limit 4). T by vol bucket: 33.7 / 28.7 / 29.8 / 24.5 /
37.9 (the floor bucket was 71.2). The worst points are now at the other end: vol 9000, knocked in,
spot ≈ 10,030, obs 26, tNext just past the autocall band (−34 to −38 bps on T, T2, T3).

For comparison, K2 at one vol: T 18.7, T2 17.8. Making vol live costs about 20 bps of worst case
at the same network size; p99 went from 7.9 to 14.1.

## Stylus

`model/k3`, 7,465 params, 10-64-48-40-40-1, 16-bit. `PRICER_MODEL_DIR=../../model/k3 cargo test`:
100 golden vectors exact, 35 reject vectors (every range, derived field and all five exclusions).
`cargo stylus check` against Robinhood Chain testnet: **23,901 bytes** (limit 24,576), activation
passes (data fee 0.000086 ETH). `model/k2` stays the default build: the contracts' quoter vectors
and e2e run use it.

## What this gives the Desk

**One model per product, any stock with vol 20–90%.** The listing's `volBpsAnnual` is now a live
input: AAPL-like 28%, TSLA-like 55%, COIN-like 85% all price through `model/k3`, where K2 refused
everything but 55% and teacher v2 refused everything under 44.7%.

**The vol-band spread** (`ml/round3_eval.py --set S`, `ml/round3_eval_s.log`). 15,000 states, each
priced at vol − 2, vol and vol + 2 points, all certified:

| | max | p99 | mean |
|---|---:|---:|---:|
| teacher spread P(vol − 2) − P(vol + 2) | 347.6 | 219.1 | 35.0 |
| student error on that spread | 38.5 | 13.7 | 2.70 |
| student error on the mid | 41.0 | 12.9 | 2.75 |

- Two vol points of uncertainty are worth 35 bps on average but up to 3.5% of notional in the
  steep states (mid-life, not knocked in, low vol near the barrier). A flat bps spread can't
  cover that without pricing out the calm states; a vol band follows it.
- **The price is not monotone in vol.** In 845 of 15,000 states the teacher's spread is negative
  beyond 3 × its noise (the note gains from vol); the student has 1,046. So the Desk's quote must be
  **bid = min(P(vol − δ), P(vol + δ)), ask = max(…)**, then IDeskCover's `bidBps` / `askBps` on top
  as a floor for student error.
- The listing vol must sit at least δ inside 20–90% for both prices to be certified, and a state
  refused at either vol is refused.
- On chain this is two pricer calls per quote. Gas per call for k3 isn't measured yet (~45,000 on
  the synthetic model).

**What the Desk still can't quote:** the five exclusion regions, all in the first day before an
observation (autocall or knock-in), plus, for a note not knocked in and near the knock-in barrier,
up to 5 days before its last observation at 20–30% vol, 3 days in weeks 2–4 at that vol, and 2.5
days in the last two weeks at 30–50%.

## Toward the other teacher improvements

- **Options calibration (step 2)** is a config change: teacher v3's jump sizes are ratios to vol,
  so a negative `muJ` (skew) or a jump premium fitted to option smiles drops into the same
  `teacher_config_v3.json` format, and the K3 pipeline (sets, trainer, gate) relabels and
  retrains unchanged.
- **Error bars per state (step 3)**: the 26 multi-stock fits are exactly v3 configs (λ, μJ, σJ at
  their volRef). Labelling S or T under each gives a per-state teacher-disagreement band for
  sizing `bidBps` / `askBps` from data.
- **Earnings (step 4)** needs a new input (time to next earnings), so `featureSpecVersion` 2 and
  a new certified domain; this pipeline carries over.
- **Time-varying vol (step 5)** is a new teacher; `volBpsAnnual` would become the current vol.

## Deviations and open items

- **T was used twice.** Its first evaluation (run d, FAIL 71.2) located the vol-floor hole and
  shaped V_edge, the ends training set and T3. T's final 37.9 therefore carries some optimism; T2
  (34.7) and T3 (38.0), neither used before the final model, are the clean numbers.
- The seed spread is wide (same data: 39.4 vs 53.1 on V); four of seven runs were above 50 on
  V + V_edge (the worse of the two). g, f's data with another seed, reached 38.8, so f's 32.3 is
  the good end of that spread.
- Gas for two k3 calls per Desk quote: not measured. Quoter vectors for k3
  (`tools/quoter_vectors.py --model model/k3`) not generated; that touches the contracts lane.
- Teacher v3 inherits v2's simplifications: Q = P jumps, symmetric jumps, flat vol, calendar clock.

## Reproduce

```sh
PY=/opt/ai/cache/venv-cuda/bin/python          # labels (CUDA teacher)
PYT=/opt/ai/cache/venv-student-cuda/bin/python # training (torch CUDA + keccak)
tools/.venv/bin/python ml/make_teacher_config.py --v3
tools/.venv/bin/python ml/test_teacher.py > ml/test_teacher.log
$PY ml/round3_bands.py --paths 131072 > ml/round3_bands.log
for s in V V_edge T T2 T3 S; do $PY ml/round3_sets.py --set $s --paths 262144 --max-se 4 --out ml/k3_<set>_labels.npz; done
$PY ml/round3_sets.py --set train --n 1000000 --seed {1,2} --paths 32768 --out .../train_s{1,2}.npz
$PY ml/round3_sets.py --set steep_ki --n 400000 --seed {1,2} --paths 32768 --out .../steep_ki_s{1,2}.npz
$PY ml/round3_sets.py --set edge_ki --n 300000 --seed 1 --paths 32768 --out .../edge_ki_s1.npz
$PY ml/round3_sets.py --set ends --n 400000 --seed 1 --paths 32768 --out .../ends_s1.npz
$PYT ml/round3.py --train <the six> --val <V + V_edge> --widths 64,48,40,40 --epochs 3000 --seed 2 --out-dir .../f
tools/.venv/bin/python ml/round3_select.py <runs>
cd tools && ../tools/.venv/bin/python certify.py --export ../ml/student_export_k3.json --domain domains/k3.json --out ../model/k3
tools/.venv/bin/python ml/round3_eval.py --model model/k3 [--set T2|T3|S]
cd stylus/pricer-model && PRICER_MODEL_DIR=../../model/k3 cargo test
```
