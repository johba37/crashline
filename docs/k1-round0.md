# K1 round 0 — boundary fidelity of a small MLP surrogate

Status: 2026-09-29, round-0 go/no-go experiment. Code: `ml/teacher.py`,
`ml/train_student.py`, `ml/run_k1.py`. Plots: `docs/k1-round0.png` (full-range
student), `docs/k1-round0b.png` (fixed-terms student), `docs/k1-round0b-w28.png`
(28-wide sensitivity probe).

**Verdict up front: FAIL vs the 50 bps worst-case gate — and the failure is
instructive.** Three separate findings, in decreasing importance:

1. **The autocall barrier at an observation instant (`timeToNextObsSecs = 0`) is
   a true price discontinuity of ~229 bps of notional** (teacher: 9771 bps one
   5-bps step below AC, 10000 bps at it). No continuous student can represent
   it; ~half the jump shows up as error no matter the capacity. **Remedy (a)
   (ε-band fail-closed) is structurally required there** — it is not a capacity
   question.
2. **The knock-in barrier is benign.** With weekly discrete monitoring +
   continuously-accrued coupon (the §5 smoothness choices), the teacher has *no
   measurable jump* at KI (~5 bps per 5-bps spot step at `tNext=0`), because
   future knock-in probability smooths the latch. The 24-wide student hits
   **max 49.8 bps near KI — passes the gate**. Remedy (c) (smoother payoff) is
   already doing its work; nothing more needed at KI.
3. **A 1489-param student cannot simultaneously generalize over the full
   contract term space** (ki/ac/coupon/obsRemaining 1–104 randomized as
   briefed): worst case 915 bps, mean 173 bps, and the failure is global, not
   boundary-localized. With note terms pinned (per-series weightsHash;
   `obsRemaining`, `tNext`, `spot`, `knockedIn` still vary), the same
   architecture is 5–10× better: mean 19 bps, and outside the AC-observation
   band the worst case is ~91 bps.

## Setup

- Teacher: vectorized numpy GBM Monte Carlo, antithetic variates,
  conditional-expectation smoothing at the final fixing (analytic one-step GBM
  expectation replaces the last simulation step; cross-checked against the
  brute-force final step within 3·stderr). Payoff per brief: AC at 10000 bps,
  KI at 6000, coupon 25 bps/period accrued linearly in calendar time, 26 weekly
  observations, r = 4%.
- Eval grid: `spotBpsOfInitial` 5000–12000 step 5 (1401 pts) × `timeToNextObs`
  {0, 3600, 86400, 302400} s × `knockedIn` {0,1} = 11 208 labels; vol 5500,
  ttm = 26·604800 + tNext. Teacher labels at 2^18 paths (2^17 antithetic
  pairs), seed 777.
- **Eval label MC stderr: mean 1.47 bps, p95 3.13, max 3.22 bps** — label
  noise cannot contaminate a 50 bps measurement.
- Students: 10→24→24→24→1 ReLU MLP, linear output, **1489 params** (exact;
  10·24+24 + 2·(24·24+24) + 24+1). Sensitivity probe 10→28→28→28→1 = **1961
  params**. Inputs min-max normalized to [−1,1] from the NoteQuoter ranges;
  targets standardized (affine stored in checkpoint, inverted at inference).
- Training: 200k labels (50% uniform over ranges, 50% boundary-oversampled
  ±500 bps of KI/AC with 70% at tNext ≤ 1 day), 2^12 paths per label, Adam
  3e-3, cosine annealing, early stop on 10k holdout.

## Results (worst-case / p99 / mean |student − teacher|, bps)

### Student A — as-briefed, terms randomized over full contract ranges

Validation RMSE 427 bps (label-noise floor 77 bps at 2^12 paths). Fails
everywhere, worst in the knocked-in/low-spot region:

| region | max | p99 | mean |
|---|---|---|---|
| overall | **914.9** | 860.6 | 173.0 |
| near AC (9500–10500) | 557.0 | 427.7 | 92.4 |
| near KI (5500–6500) | 723.3 | 713.4 | 278.1 |
| elsewhere | 914.9 | 875.7 | 168.0 |
| tNext = 0 (at obs) | 914.0 | 861.5 | 165.5 |
| tNext > 0 (away) | 914.9 | 860.2 | 175.4 |

Worst point: spot 5000, tNext 3600, knockedIn 0 → teacher 5610.0 (se 1.1),
student 6524.9, Δ +914.9.

### Student B — fixed terms (ki 6000, ac 10000, coupon 25, vol 5500), 1489 params

Validation RMSE 21.3 bps (label-noise floor 5.9 bps).

| region | max | p99 | mean |
|---|---|---|---|
| overall | **171.0** | 84.8 | 19.5 |
| near AC (9500–10500) | 171.0 | 132.8 | 20.3 |
| near KI (5500–6500) | **49.8** ✅ | 34.7 | 12.9 |
| elsewhere | 91.3 | 81.2 | 20.6 |
| tNext = 0 (at obs) | 171.0 | 112.9 | 23.6 |
| tNext > 0 (away) | 101.6 | 77.7 | 18.1 |
| near AC & at-obs | 171.0 | 168.3 | 34.3 |
| near AC & away | 101.6 | 91.7 | 15.7 |
| near KI & at-obs | **49.8** ✅ | 41.1 | 11.5 |
| near KI & away | **39.6** ✅ | 34.0 | 13.4 |

Worst point: spot 9995, tNext 0, knockedIn 1 → teacher 9656.0 (se 2.2),
student 9827.0, Δ +171.0 (the AC jump, smoothed by the net).

Error vs tNext near AC: tNext=0 → max 171; 3600 s → 102; 86400 s → 38–47;
302400 s → 21–26. The discontinuity's influence decays over ~1 day.

### Sensitivity probe — fixed terms, 1961 params (28-wide)

Val RMSE 16.2 bps, but eval tail does **not** improve: max 203.8 (same AC
jump), near-KI max 72.2, elsewhere max 76.0. Conclusion: the tail is dominated
by the structural AC discontinuity plus a stochastic ripple floor (~70–90 bps)
that +33% capacity does not remove within this training budget.

## Remedy analysis (per teacher-spec §8, options in order)

- **(a) ε-band fail-closed — required, and sufficient for the structural part.**
  Sized from the grid: fail closed when `timeToNextObsSecs ≤ 86400` AND spot
  within ±500 bps of the AC barrier (at tNext=0: errors 153–171 bps within
  ±250 bps of AC; at 3600 s: 62–102 bps; at 86400 s and beyond: ≤47 bps even
  without the band). The band covers ~10.8% of this adversarial grid (which
  oversamples boundaries by design; the fraction of real (spot, time) space is
  far smaller). On-chain this is two comparisons in `NoteQuoter.checkRanges`.
- **(b) p99 + bounded worst case — needed as well at this capacity.** Outside
  the (a) band, student B: p99 ≈ 80 bps, worst ≈ 91 bps (mid-range ripple at
  spot ~8500, tNext ≤ 1 h). 50 bps worst-case outside the band is **not**
  achieved at ≤2K params. Either restate fidelity as p99 ≈ 80 / worst ≈ 91 bps
  outside the band, or cut label/training noise and re-test (see follow-ups).
- **(c) smoother payoff — not needed.** The continuous-coupon + weekly-monitoring
  choices already removed the KI discontinuity (KI passes: 49.8 bps). The AC
  jump is intrinsic to autocall redemption — no payoff smoothing short of
  removing autocall eliminates it.

**Data-favored resolution: (a) for the AC observation-day band (structural,
non-negotiable), then (b) restated as p99 ≈ 80 bps + bounded worst ≈ 91 bps
outside the band.** And a deployment-level correction: ship per-note-series
weights (terms pinned per `weightsHash`); a single 1489-param net over the
full term space misses the gate by ~18×.

## Process notes (two training bugs found by this experiment)

1. **Target standardization is mandatory.** With raw ~1e4-bps targets, Adam's
   ~lr-sized per-step updates cannot grow the output layer to scale; training
   plateaued at RMSE 6 779 bps (full ranges) / 1 364 bps (fixed terms) — the
   first cut of this experiment's numbers was 10–40× worse than achievable and
   would have produced a false "capacity" verdict. Fixed by standardizing
   targets (affine stored in checkpoint).
2. Early-stopping tolerance must be in standardized-loss units, not raw bps
   (a 1e-3 raw-MSE tolerance froze "best" checkpoints ~30% above optimum).

## v0 deviations from teacher-spec v0.1

1. **Calendar-time diffusion**, weekly grid — spec §2 wants market-hours-only
   XNYS diffusion; `exchange_calendars` unavailable in this environment.
   Boundary fidelity is insensitive to this; revisit before any mainnet claim.
2. **Training labels at 2^12 paths**, not spec 2^20: mean label stderr 77 bps
   (full ranges) / 5.9 bps (fixed terms). MSE training is unbiased under
   zero-mean label noise; eval labels at 2^18 paths with stderr ≤ 3.2 bps keep
   the measurement clean.
3. Textbook choices where the brief was silent: year = 365 d = 31 536 000 s
   (matches `MATURITY_MAX_SECS`); final redemption strike = `acBarrierBps`
   (== par on this grid); KI/AC latch at the N observation times only (no
   barrier check at maturity itself); coupon accrues linearly in calendar time.
4. r fixed at 4% (spec config constant); no ±200 bps sensitivity run yet.
5. Training-distribution note: `obsRemaining` uniform 1–104 (full) / 1–26
   (fixed terms); `timeToMaturity = obs·WEEK + tNext` by construction.

## Runtime (CPU, this machine)

- Teacher smoke checks: 4 s. Eval grid labels (11 208 × 2^18 paths): 28 min
  (cached in `ml/k1_eval_cache.npz`). Training labels: 14 min (full) / 4.5 min
  (fixed). Training run: ~2 min each. Eval + plot against cache: ~3 s.

## Artifacts

- `ml/teacher.py` — MC teacher (smoke-tested: autocall-at-par, martingale
  floor, smoothing consistency, determinism).
- `ml/train_student.py`, `ml/run_k1.py` — training / adversarial eval.
- `ml/student_k1.pt` (full-range), `ml/student_k1_fixed.pt` (1489 params),
  `ml/student_k1_fixed28.pt` (1961 params).
- `ml/k1_eval_cache.npz`, `ml/data_full.npz`, `ml/data_fixed.npz` — cached
  labels; `ml/*.log` — full logs.
- `docs/k1-round0.png`, `docs/k1-round0b.png`, `docs/k1-round0b-w28.png`.

## Follow-ups for round 1

1. Implement remedy (a) in `NoteQuoter.checkRanges` (revert when
   `tNext ≤ 86400 && |spot − acBarrier| ≤ 500`) and re-certify student B
   outside the band.
2. XNYS market-hours diffusion once `exchange_calendars` is available; r ±200
   bps sensitivity.
3. Probe whether 400k labels / lower label noise moves the ~80 bps ripple
   floor (capacity probe says no; data/noise probe untested).
4. Decide per-series `weightsHash` deployment (this data says: required).

---

## Round 1 — 16-bit export format (2026-09-29, increment on top of round 0)

Scope: the fixed-terms student line only (round 0 showed full term-space
generalization misses the gate globally by ~18×; per-series `weightsHash`
weights are mandatory). Conforms to the normative export format v1
(`docs/model-export-format.md` in the parallel repo; reference implementation
`tools/pricer_quant.py`, imported as `pq`). Script: `ml/round1_16bit.py`;
artifacts: `ml/student_k1_r1.pt`, `ml/student_export.json`
(exportFormatVersion 1, 1489 params, 16-bit weights+activations).

**Train/serve conformity.** The float student was retrained on
`pq.normalized_float_inputs(raws, bits=16)` — the integer normalization
`((v−min)·2·qmax + range//2) // range − qmax` divided by `qmax = 2^15−1` — so
training and the chain see bit-identical inputs. Target affine:
`(priceBps − offsetBps)/priceScaleBps` with the format-mandated
`offsetBps = 10000` and `priceScaleBps = 2000`. Mapping to the round-0 fix:
round 0 standardized with measured `(y − 8494.6)/1981.8`; round 1 keeps the
same affine family with the offset moved to 10000 and a round scale of 2000
bps (label std was 1981.8). The scale value is not normative — `pq.quantize`
folds it into the head `multiplierQ16/shift`.

**Environment note:** pycryptodome is absent here, so the `weightsHash` in
`ml/student_export.json` is a sha3_256 **placeholder** (not keccak256). The
hash plays no role in the fidelity numbers; the normative hash is recomputed
by the Rust build script in the contracts lane.

### Quantization cost (integer vs float, same weights)

`|pq.forward − float model|` on the 11,208-point grid, both training seeds:
**max 2.49 bps, p99 1.45, mean 0.44** — matching the parallel thread's
synthetic-student measurement (p99 1.2 / max 3.2). At 16 bits, quantization is
a rounding error on top of the fit error, not a budget item. (int8 wording is
dead: round-0's "int8 budget" references now read 16-bit.)

### Integer student vs teacher (the on-chain number; seed 0 / seed 1)

| region | max | p99 | mean |
|---|---|---|---|
| overall | 218.0 / 196.0 | 121.1 / 110.0 | 25.1 / 22.0 |
| near AC (9500–10500) | 218.0 / 196.0 | 168.9 / 154.9 | 30.5 / 24.6 |
| near KI (5500–6500) | 73.3 / 56.6 | 48.9 / 47.4 | 10.9 / 10.7 |
| at-obs (tNext=0) | 218.0 / 196.0 | 151.0 / 138.8 | 27.8 / 28.6 |
| away (tNext>0) | 135.6 / 129.7 | 116.7 / 89.9 | 24.2 / 19.8 |
| **outside AC ε-band** (tNext>86400 OR \|spot−AC\|>500) | **135.6 / 132.1** | **114.0 / 96.8** | 23.9 / 21.2 |

Worst integer point (both seeds): spot exactly 10000, tNext=0, knockedIn=1 —
teacher 10000.00 (immediate autocall, deterministic), integer student
9782/9804, Δ ≈ −200 bps. Same structural AC discontinuity as round 0.

### What round 1 changes in the verdict

- **Quantization adds ≤ 2.5 bps** — the float→integer step is closed as a
  risk. The fidelity question is entirely the float student's.
- **Outside-band integer tail: p99 ≈ 97–114 bps, worst ≈ 132–136 bps** across
  two seeds (round-0 float student B measured p99 80 / worst 91). The run-to-run
  spread (±40 bps in the tail at val RMSE 21–25 bps) is itself a finding: the
  tail is stochastic fit ripple, so any fidelity statement must be made on the
  *exported integer artifact*, per the format doc — which is exactly what this
  table is.
- **Combined picture still supports remedies (a)+(b), unchanged:** (a) the
  AC-observation-day ε-band (tNext ≤ 86400, |spot−AC| ≤ 500) remains
  structurally required — the ~200 bps integer error at the AC jump is
  untouched by 16-bit quantization and by capacity; (b) outside the band,
  restate fidelity as p99 ≈ 100–115 + bounded worst ≈ 135 bps at this
  architecture/training budget (50 bps worst-case remains unmet; the round-0
  follow-up of more data / lower label noise is still the open lever).
