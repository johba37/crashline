# Teacher v2: jump-diffusion calibrated to TSLA

Status: 2026-09-30, **frozen** (`ml/teacher_config.json`). Replaces "the stock never
jumps" (K1's GBM) with a Merton jump-diffusion whose jumps are fitted to ten years of
TSLA daily closes. No interface or input changes: `volBpsAnnual` is still the one
vol input, and it is the **total** vol. Code: `ml/teacher.py` (numpy, reference),
`ml/teacher_torch.py` (CUDA). Checks: `ml/test_teacher.py`, log `ml/test_teacher.log`.
Every number below is copied from a committed log, named in each section.

## Verdict up front

- Fitted (2016-09-28 to 2026-09-28): **λ = 72.2 jumps/yr (±17.4), μJ ≈ 0 (±0.27%),
  σJ = 5.26% (±0.52%)**. Jump vol 44.7%/yr, 57% (±4%) of TSLA's total variance.
  Against GBM the likelihood-ratio statistic is 373.9 for 3 extra parameters.
- At the product's 55% total vol the diffusion keeps **32.0%**, so the split is valid.
- On the 24 note states in the table below, the jump teacher prices the note
  **up to 26.4 bps away from GBM at the same total vol**, with a clear sign pattern:
  knocked-in notes are worth less (up to −16.7 bps), notes that haven't knocked in
  and sit mid-range are worth more (up to +26.4 bps). 19 of the 24 differences are
  beyond 3 stderr.
- Listed options (sanity check, not calibration) quote TSLA 6-month ATM implied vol
  at **~43%**, below the product's 55% and **below the fitted jump vol (44.7%)**.
  At 43% total vol this teacher refuses (no diffusion variance left). See
  [Listed options](#listed-options-sanity-check).

## Data

| | Primary | Cross-check |
|---|---|---|
| File | `ml/data/tsla_daily.csv` | `ml/data/tsla_daily_nasdaq.csv` |
| Source | Yahoo Finance chart API v8 | Nasdaq historical quotes API |
| URL | `https://query1.finance.yahoo.com/v8/finance/chart/TSLA?period1=1262304000&period2=1790640000&interval=1d&events=split` | `https://api.nasdaq.com/api/quote/TSLA/historical?assetclass=stocks&fromdate=2015-09-01&todate=2026-09-28&limit=9999` |
| Range | 2010-06-29 to 2026-09-28, 4,087 closes | 2016-09-28 to 2026-09-28, 2,513 closes |
| Adjustment | split-adjusted close (splits 5:1 2020-08-31, 3:1 2022-08-25); TSLA pays no dividends | split-adjusted |

Fetched 2026-09-29T20:46:28Z by `ml/data/fetch_tsla.py --end 2026-09-29` (the end is
exclusive, so no partial session). Metadata: `ml/data/tsla_daily.source.json`.
stooq's CSV endpoint was tried first and now serves a JavaScript proof-of-work page,
so it wasn't used.

**Cross-check** (`ml/calibrate_jumps.log`): over the fit window both sources have the
same 2,513 dates, and the largest difference between their daily log returns is
2.25e-05; no return differs by more than 1e-3.

**Window** (chosen before fitting): the last ten years, 2016-09-28 to 2026-09-28,
2,512 daily log returns. This is the span both sources cover, and it leaves out the
2010–2016 small-cap period. The full history and the last five years are reported as
sensitivity.

## Fit

`ml/calibrate_jumps.py`, log `ml/calibrate_jumps.log`, output `ml/jump_fit.json`.
Per trading day, r = m + σ_d·ε + Σ_{k≤N} Y_k with N ~ Poisson(λ) and Y ~ N(μJ, σJ²).
The likelihood is the Poisson mixture of normals, truncated at 30 jumps per day.
It is maximized from 12 starting points (Nelder–Mead, then BFGS). Standard errors
come from the inverse numerical Hessian (observed information), and the delta method
gives them for the derived quantities.

| Parameter (trading-day clock) | Estimate | Stderr |
|---|---:|---:|
| m (drift / day) | 0.001299 | 0.000724 |
| σ_d (/√day) | 0.024369 | 0.001231 |
| λ (jumps / trading day) | 0.287697 | 0.069345 |
| μJ (mean log jump) | −0.000006 | 0.002705 |
| σJ (std log jump) | 0.052610 | 0.005155 |

| Calendar-year quantity (D = 251.062 returns/yr) | Estimate | Stderr |
|---|---:|---:|
| λ_year | 72.229922 | 17.410045 |
| jump variance / yr | 0.199922 | 0.020136 |
| diffusion variance / yr | 0.149097 | 0.015065 |
| total vol / yr (model) | 0.590779 | 0.013809 |
| κ = E[e^Y] − 1 | 0.001379 | 0.002716 |
| jump share of variance | 0.572811 | 0.044209 |

**Reading the fit.** The MLE picks many small jumps (≈ 0.29 a day, σJ 5.3%), not rare
crashes. λ and σJ are strongly anti-correlated (−0.91), so the data pin down the jump
*variance* (±10%) much better than they pin down λ or σJ separately. μJ is zero within
its stderr: in this window TSLA's big moves are symmetric (sample skew −0.07).

### Goodness of fit (daily log returns)

| Statistic | Data | Merton | GBM |
|---|---:|---:|---:|
| std | 0.03744 | 0.03728 | 0.03743 |
| skew | −0.0662 | −0.0003 | 0 |
| excess kurtosis | 4.1335 | 3.4214 | 0 |
| 0.1% quantile | −0.18146 | −0.16735 | −0.11437 |
| 1% quantile | −0.09752 | −0.10600 | −0.08578 |
| 99% quantile | 0.10424 | 0.10858 | 0.08837 |
| 99.9% quantile | 0.18036 | 0.16993 | 0.11697 |

| Returns beyond k·std | Data | Merton expected | GBM expected |
|---|---:|---:|---:|
| k = 3 | 41 | 42.3 | 6.77 |
| k = 4 | 15 | 10.6 | 0.16 |
| k = 5 | 5 | 2.4 | 0.00 |
| k = 6 | 1 | 0.5 | 0.00 |

Merton gets the 3σ tail and most of the kurtosis; GBM misses both by an order of
magnitude. Beyond 4σ Merton still undercounts (15 vs 10.6, 5 vs 2.4): a
single-normal jump size can't reproduce the very largest days.

### Sensitivity (reported, not pinned)

| Window | Returns | λ_year | μJ | σJ | diffusion vol | jump vol | total vol |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2010-06-29 to 2026-09-28 (full) | 4,086 | 63.165 | +0.0002 | 0.0540 | 0.3711 | 0.4295 | 0.5677 |
| 2021-09-28 to 2026-09-28 (5 y) | 1,254 | 56.404 | −0.0038 | 0.0515 | 0.4518 | 0.3879 | 0.5955 |
| **2016-09-28 to 2026-09-28 (pinned)** | 2,512 | 72.230 | −0.0000 | 0.0526 | 0.3861 | 0.4471 | 0.5908 |

Jump vol ranges from 38.8% to 44.7% across windows. The pinned window has the highest.

## Clock

The fit uses trading-day time: each daily return is one step, and weekends and
holidays don't count. The teacher runs in calendar time (365-day year, uniform
diffusion, as in K1). The conversion keeps each component's variance per year equal
on both clocks. With D = 251.062 returns per calendar year in the window:
λ_year = D·λ, σ_d,year² = D·σ_d², and μJ, σJ (per jump) are unchanged. Jumps then
arrive uniformly in calendar time, weekends included. That matches the teacher's
calendar-time diffusion (teacher-spec §2 wants market-hours-only time; still open,
k1-round0.md deviation 1).

## Pinned parameters (`ml/teacher_config.json`)

Written by `ml/make_teacher_config.py` from `ml/jump_fit.json`, 6 significant digits:

| Constant | Value |
|---|---|
| `lambdaYear` | 72.2299 |
| `muJ` | −5.9616e-06 |
| `sigmaJ` | 0.0526104 |
| derived jump variance / yr | 0.199922 (jump vol 0.447126) |
| derived κ | 0.00137892 |
| `rFree` | 0.04 |
| year / week | 31,536,000 s / 604,800 s |
| smoothing Poisson terms | 24 |
| maturity strike | initial fixing (10000 bps) |

## Vol convention (binding)

`volBpsAnnual` is the **total** annualized vol. The teacher splits it:
σ_d² = (vol/1e4)² − λ(μJ² + σJ²), which must be > 0, or `ValueError`. At the pinned
5500: 0.3025 − 0.199922 = 0.102578, so **σ_d = 32.03%**. Any total vol ≤ **44.71%**
is refused (see listed options). Check (b) confirms Var[log S_T] = vol²·T within MC
error at 1 week, 26 weeks and 1 year.

Risk-neutral drift: r − λκ, with κ = e^(μJ + σJ²/2) − 1 = 0.00138. Check (b) confirms
E[S_T] = S0·e^(rT).

## Simplifications (stated, not hidden)

1. **Q jumps = fitted P jumps.** No jump risk premium, so the risk-neutral jump
   intensity and sizes are the historical ones.
2. **Symmetric jumps.** μJ ≈ 0, so the teacher has a smile, not a skew. It prices
   crash risk no worse than rally risk. The listed options show a put skew
   (below).
3. **Flat total vol** and flat r = 4% (unchanged from K1).
4. **Calendar-time clock**, uniform over weekends (above). The market-hours-only
   time of teacher-spec §2 is still open.
5. **No feed gaps, halts or fallback fixings.** The contracts' fallback fixing
   (an unrecorded fixing past MAX_ROLL + 1 day reuses the previous one) isn't
   simulated; see teacher-spec §3. Flagged by the contracts lane's payout review,
   which found no mismatch between `tools/payout_vectors.py` and this teacher.
6. **History-based calibration** only. Listed options are a sanity check, not a
   target.

## Teacher code

`ml/teacher.py`:
- `load_config()` returns the pinned jump teacher (the default of `price_batch` and
  `price`); `GBM` is λ = 0.
- λ = 0 takes the K1 code path: bit-identical to `ml/reference/teacher_gbm_k1.py`
  (K1's `ml/teacher.py`, verbatim, sha256 checked), check (a). The K1 scripts
  (`run_k1.py`, `train_student.py`) now import that frozen copy, so their cached
  labels stay reproducible.
- Jumps per step: a Poisson count per antithetic pair (shared), and the sum of log
  jumps n·μJ + √n·σJ·Z with Z negated on the antithetic leg.
- Last step (final observation → maturity) is smoothed exactly. Given n jumps,
  log S_T is normal, and E[min(S_T, 1)] = e^(m+v/2)Φ(−(m+v)/√v) + Φ(m/√v). These
  terms are summed with Poisson weights: up to 24 terms, skipping terms below 1e-17,
  and the dropped mass is asserted < 1e-12. Check (d).
- Two fixes that make the teacher follow the contracts' payout rule:
  - The maturity loss compares with the **initial** fixing, no longer
    `acBarrierBps` (teacher-spec §5). It's identical on the K1 grid.
  - An observation at `timeToNextObsSecs = 0` fixes at the exact float64 spot. K1
    compared exp(float32(log s)), which knocked in at spot == ki exactly (found by
    lane B). Check (h). Check (a) avoids exact-barrier states, since K1 gets those
    wrong.
- K1's brute-force last step (only used for cross-checks) left the principal
  undiscounted over the final period, an ~8 bps inconsistency with its own smoothed
  branch. Fixed here; the production smoothed path is unaffected.

## Checks (`ml/test_teacher.py`, log `ml/test_teacher.log`, 8/8 PASS)

| Check | Result |
|---|---|
| (a) λ = 0 bit-identical to the K1 GBM teacher | 440 states × 2^12 and 2^15 paths, max \|diff\| 0 |
| (b) martingale with jumps (+ total variance) | z −0.33 / −0.14 / −0.55 for E[S_T]; −0.33 / −0.12 / −0.35 for Var[log S_T] (1 w, 26 w, 1 y) |
| (c) European put vs Merton series, 26 weeks | K 0.6: 115.76 ± 0.27 vs 116.00; K 1.0: 1425.82 ± 0.70 vs 1425.99; K 1.2: 2698.08 ± 0.46 vs 2698.55 (bps of notional) |
| (d) smoothed vs brute-force last step | 24 states, 2^17 paths, max \|z\| 2.83, mean z² 1.27 |
| (e) determinism | identical for the same seed, different for another |
| (f) smoke | immediate autocall = 10000 exactly (GBM, jumps); K1 low-vol martingale; knocked-in never-autocall note vs closed form (z −0.52 GBM, +0.14 / −0.28 jumps) |
| (g) CUDA backend vs numpy | 64 states, 2^18 paths, max \|z\| 2.22, mean z² 0.97 |
| (h) barrier equality at tNext = 0 | spot == ki does not knock in, spot == ac autocalls |

Check (c) also shows how little European prices move: at 26 weeks and equal total
vol, the Merton put and the Black–Scholes put differ by ≤ 1.8 bps. Over a
half-year, ~35 small jumps (72.2/yr × 0.48 yr) add up to something close to a normal. The note's
path-dependence (a weekly knock-in, a one-week final period) is where jumps show.

## GBM vs jump prices (for the pitch)

`ml/teacher_v2_table.py`, log `ml/teacher_v2_table.log`. Product: ki 6000 / ac 10000 /
coupon 25 bps/week / total vol 5500. States are one day before an observation
(tNext 86,400 s), with ttm = tNext + obs·week. numpy teacher, 2^20 paths, seeds 9001
(GBM) and 9002 (jump). Clean prices in bps of notional ± stderr.

| obs left | knocked in | spot | GBM | jump | jump − GBM | z |
|---:|---:|---:|---:|---:|---:|---:|
| 26 | no | 5500 | 6089.8 ± 0.5 | 6089.3 ± 0.5 | −0.5 ± 0.8 | −0.6 |
| 26 | no | 7000 | 7752.9 ± 0.9 | 7766.7 ± 0.9 | +13.8 ± 1.2 | +11.3 |
| 26 | no | 8500 | 8997.7 ± 1.6 | 9008.6 ± 1.6 | +10.9 ± 2.3 | +4.8 |
| 26 | no | 9500 | 9572.1 ± 1.3 | 9571.3 ± 1.3 | −0.7 ± 1.9 | −0.4 |
| 26 | yes | 5500 | 6090.7 ± 0.5 | 6088.3 ± 0.5 | −2.4 ± 0.7 | −3.2 |
| 26 | yes | 7000 | 7447.3 ± 0.6 | 7440.4 ± 0.6 | −6.9 ± 0.9 | −7.8 |
| 26 | yes | 8500 | 8662.0 ± 1.3 | 8648.5 ± 1.3 | −13.5 ± 1.8 | −7.6 |
| 26 | yes | 9500 | 9377.9 ± 1.3 | 9361.3 ± 1.3 | −16.7 ± 1.8 | −9.0 |
| 13 | no | 5500 | 5817.6 ± 0.3 | 5821.0 ± 0.3 | +3.4 ± 0.5 | +7.0 |
| 13 | no | 7000 | 8154.2 ± 1.1 | 8180.6 ± 1.2 | +26.4 ± 1.6 | +16.4 |
| 13 | no | 8500 | 9496.0 ± 1.4 | 9508.1 ± 1.4 | +12.1 ± 2.0 | +6.0 |
| 13 | no | 9500 | 9854.7 ± 1.0 | 9856.6 ± 1.0 | +1.9 ± 1.4 | +1.4 |
| 13 | yes | 5500 | 5816.9 ± 0.3 | 5815.8 ± 0.3 | −1.2 ± 0.5 | −2.5 |
| 13 | yes | 7000 | 7253.9 ± 0.3 | 7249.7 ± 0.3 | −4.1 ± 0.4 | −9.3 |
| 13 | yes | 8500 | 8559.6 ± 0.8 | 8550.5 ± 0.8 | −9.1 ± 1.1 | −8.2 |
| 13 | yes | 9500 | 9332.0 ± 1.1 | 9315.7 ± 1.0 | −16.3 ± 1.5 | −11.0 |
| 2 | no | 5500 | 5555.8 ± 0.1 | 5578.9 ± 0.3 | +23.2 ± 0.3 | +72.5 |
| 2 | no | 7000 | 9905.5 ± 0.7 | 9893.3 ± 0.7 | −12.2 ± 1.0 | −12.0 |
| 2 | no | 8500 | 10036.7 ± 0.0 | 10035.6 ± 0.1 | −1.1 ± 0.1 | −16.3 |
| 2 | no | 9500 | 10031.7 ± 0.0 | 10032.2 ± 0.0 | +0.4 ± 0.0 | +25.2 |
| 2 | yes | 5500 | 5553.5 ± 0.0 | 5553.5 ± 0.0 | +0.1 ± 0.1 | +1.6 |
| 2 | yes | 7000 | 7053.2 ± 0.0 | 7052.9 ± 0.0 | −0.3 ± 0.1 | −5.1 |
| 2 | yes | 8500 | 8523.0 ± 0.0 | 8519.3 ± 0.0 | −3.8 ± 0.1 | −71.7 |
| 2 | yes | 9500 | 9363.9 ± 0.2 | 9360.9 ± 0.2 | −3.0 ± 0.3 | −10.4 |

Max |jump − GBM| is 26.4 bps; 19 of 24 states are beyond 3 stderr; max label stderr
is 1.61 bps.

**What the table says, honestly.** At the same total vol, fitted TSLA jumps move this
note by tens of bps, not hundreds. The pattern:
- **Knocked-in notes are worth less under jumps** in every state with 13 or 26
  observations left (−1.2 to −16.7 bps). The holder is short a put on the final
  fixing, and fatter tails make that put dearer.
- **Notes not yet knocked in** are mostly worth more with 13 or 26 observations left
  (up to +26.4 bps at spot 7000). Close to maturity the sign is mixed (−12.2 bps at
  spot 7000 with 2 left, +23.2 at 5500). A plausible reading, not separately measured:
  with part of the variance in jumps, the diffusion is slower, so a spot drifts
  through the knock-in barrier less often, while jumps sometimes carry it across.

For the pitch: fitted to ten years of TSLA, jumps move this note's fair value by up to
~26 bps at equal vol. The vol level matters more (see the listed options). The 50 bps
student gate is measured against this teacher.

## Listed options (sanity check)

`ml/option_sanity.py`, snapshot `ml/data/tsla_options_20260929.json` (Nasdaq
option-chain API, fetched 2026-09-29T21:23:45Z, expiry 2027-03-19, t = 0.4685 y,
spot 352.84), log `ml/option_sanity.log`. Black–Scholes IVs from OTM mids, r = 4%.
American puts are treated as European, so the put IVs are slightly overstated.

| K/S | Market IV (mean, min–max) | Jump teacher at 55% total |
|---|---|---|
| 0.55–0.70 | 48.8% (46.2–51.1) | 55.0% |
| 0.70–0.90 | 43.7% (42.7–45.4) | 54.9% |
| 0.95–1.05 | 43.1% (42.4–43.7) | 54.9% |
| 1.05–1.25 | 43.7% (43.5–44.0) | 55.0% |

1. **55% is ~12 vol points above the 6-month ATM market level.** A higher vol makes
   the note cheaper (the holder is short the knock-in put), so the pinned product's
   quotes are conservative for the Desk and cheap for sellers. The vol is a
   per-listing term (architecture.md), so this is a product choice, not a teacher bug.
2. **The fitted jump vol (44.7%) is above the market's ATM IV.** Under the binding
   convention, a listing at the market's 43% would be refused (no diffusion variance
   left). A lower-vol product would need one of: a jump variance scaled to the
   listing vol (e.g. a fixed jump share of variance, 57%), the 5-year window's jump
   vol (38.8%), or a jump risk-premium adjustment. Out of scope for this freeze; flagged
   for the orchestrator. The fixed share is measured across 12 more stocks in
   [multi-stock-jumps.md](multi-stock-jumps.md).
3. The market has a put skew (+3 to +8 vol points at 0.55–0.70 vs ATM). The fitted
   symmetric jumps produce a nearly flat smile (55.0 vs 54.9%). See simplification 2.

## Speed and the CUDA backend

`ml/bench_teacher.py`, log `ml/bench_teacher.log`. Random K2-domain states, total vol
5500. One path-step is one antithetic-leg path advanced one observation interval.
Measured with other lanes running (load average 18.9 and 20.5) and the 3090 shared with
another process:

| Backend | Throughput | Lane-B label budget (T 60k + V 60k at 2^18, 400k train at 2^14) |
|---|---:|---:|
| numpy, one process | 2.02e6 path-steps/s | 70.5 h |
| torch CUDA (RTX 3090) | 2.73e8 path-steps/s | 0.52 h |

At 2^18 paths on 1,024 random states the label stderr was mean 1.14 / max 3.23 bps,
inside the 4 bps gate.

**Batch API** (lane B):

```python
# /opt/ai/cache/venv-cuda/bin/python (torch 2.11.0+cu128, numpy, scipy); ml/ on sys.path
import teacher as T, teacher_torch as TT
F = T.features(spot, 5500, tNext, obs, knockedIn)       # arrays; ttm/dist derived on the manifold
price, se = TT.price_batch(F, total_paths=2**18, seed=SEED, device="cuda")   # numpy float64
# cfg=None -> pinned jump teacher; cfg=T.GBM for lambda = 0; max_elems bounds labels x paths per chunk
```

Same feature dict, bucketing and chunking as `teacher.price_batch`, and deterministic
per (features, seed, device type, `max_elems`). The random stream differs from numpy,
so labels agree within MC error, not bit for bit (check (g)). Build the CUDA venv with
`python3 -m venv /opt/ai/cache/venv-cuda && /opt/ai/cache/venv-cuda/bin/pip install
torch==2.11.0 numpy scipy --index-url https://download.pytorch.org/whl/cu128
--extra-index-url https://pypi.org/simple`.

## Reproduce

```sh
PY=tools/.venv/bin/python
$PY ml/data/fetch_tsla.py --end 2026-09-29          # refetch (vendor revisions possible)
$PY ml/calibrate_jumps.py --sensitivity > ml/calibrate_jumps.log
$PY ml/make_teacher_config.py
$PY ml/test_teacher.py > ml/test_teacher.log         # (g) uses /opt/ai/cache/venv-cuda if present
$PY ml/teacher_v2_table.py > ml/teacher_v2_table.log
$PY ml/option_sanity.py > ml/option_sanity.log
```
