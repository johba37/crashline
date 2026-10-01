# Multi-stock jump check: is TSLA's jump share typical?

Status: 2026-09-30, a measurement for v2. The v1 teacher is unchanged (`ml/teacher_config.json`
is not touched). Code: `ml/multi_stock_jumps.py` (fetch + fit, log `ml/multi_stock_jumps.log`,
output `ml/multi_stock_jumps.json`), `ml/multi_stock_prices.py` (note prices, log
`ml/multi_stock_prices.log`, output `ml/multi_stock_prices.json`). Every number below is
copied from those two logs.

## Question

[teacher-v2.md](teacher-v2.md) pins TSLA's jump variance (jump vol 44.7%/yr) and carves it
out of the listing's total vol. Two things follow for more stocks:

1. Any listing with total vol ≤ 44.7% is refused, and most large caps sit below that.
2. The proposed fix ("Listed options", item 2) is a fixed jump *share* of total variance
   (TSLA's 57%), scaled to each listing's vol. That fix works only if the share, and the
   jump pattern behind it, is roughly the same across stocks.

So: fit the same model to a spread of stocks, then price the note under each fit, and see
how far a single TSLA-shaped rule is from each stock's own fit, in bps of notional.

## Verdict up front

- **Today's teacher refuses 7 of 12 stocks outright.** AAPL, MSFT, AMZN, META, GOOGL, NFLX
  and SPY have 10-year sample vol below TSLA's pinned jump vol (44.7%).
- **The jump share is not one number:** 52–72% over 10 years (MSTR 94%, a regime change),
  39–73% over 5 years. One common share is rejected on both windows (χ² 412 and 27.7 on
  12 dof).
- **In prices, the fixed-share rule is close for large caps.** Each stock is priced at its own
  vol under the rule (TSLA's jump pattern, 57% share) and under its own fit. Worst case
  over 24 note states: AAPL, MSFT, AMZN, GOOGL, NVDA ≤ 7.6 bps on both windows. Median over
  all 26 fits: 7.5 bps. The K2 student's error against its teacher is 18.7 bps max.
- **It misses by 16–27 bps on stocks with rare large drops** (META, NFLX, PLTR: earnings
  days of −21% to −35%) and on COIN (19 bps). TSLA's pattern is many small jumps
  (72/yr); these stocks have few big ones.
- **The window moves the answer as much as the stock does.** TSLA's share is 57% (10 y) and
  42% (5 y). At the same vol, the 5-year fit prices the note up to 8.5 bps away from the
  10-year shape, and up to 11.8 bps away from the pinned teacher at 55%. What the fit calls jumps is
  partly volatility that changes over time: weekly returns have fatter tails than the model
  predicts in 25 of 26 fits, and large moves bunch together (|return| autocorrelation at lags
  20–60 is positive in 26 of 26). More history of the same kind will not converge on one
  jump share; the model is missing a component.

## Data

| | |
|---|---|
| Symbols | TSLA, AAPL, NVDA, MSFT, AMZN, META, GOOGL, AMD, NFLX, COIN, PLTR, MSTR, SPY |
| Source | Yahoo Finance chart API v8 (`ml/data/fetch_tsla.py`, now with a symbol argument; TSLA's default unchanged), `events=split,div` |
| Files | `ml/data/multi/<symbol>_daily.csv` and `_daily_nasdaq.csv`, URLs and fetch times in `ml/data/multi/sources.json`; TSLA is the committed `ml/data/tsla_daily.csv` |
| Series fitted | `adjclose` (split- and dividend-adjusted, i.e. total return, as the feed series is assumed to be; teacher-spec A1). TSLA pays no dividends, so it's the same as the pinned fit |
| Cross-check | Nasdaq historical closes against Yahoo `close` on common dates: every symbol's largest daily log-return difference is ≤ 7.9e-4, except SPY, where a stale Nasdaq close on 2026-04-20 (repeats 04-17) gives two differences of 2.0e-3. Yahoo is right there |
| Windows (fixed before fitting) | 10 y: 2016-09-28..2026-09-28, the pinned TSLA window (COIN starts 2021-04-14, PLTR 2020-09-30); 5 y: 2021-09-28..2026-09-28, which every symbol covers |

The fit is `ml/calibrate_jumps.py`'s own `fit()` (imported), same likelihood, starts and
stderrs. TSLA's 10-year row reproduces `ml/jump_fit.json` exactly (λ 72.2299/yr, share
0.572811).

## Fits

Annualized per calendar year. σJ / daily vol is the jump size in units of the stock's
daily total vol: about 1 means a "jump" is an ordinary busy day, about 2 is a real outlier.

| Symbol | Vol 10y / 5y | λ/yr 10y / 5y | σJ / daily vol 10y / 5y | Jump share 10y | Jump share 5y |
|---|---:|---:|---:|---:|---:|
| TSLA | 59.3% / 59.6% | 72 / 56 | 1.41 / 1.37 | 57% ± 4% | 42% ± 8% |
| AAPL | 29.0% / 27.9% | 128 / 171 | 1.18 / 1.03 | 72% ± 4% | 73% ± 8% |
| NVDA | 49.7% / 51.4% | 100 / 72 | 1.20 / 1.25 | 58% ± 7% | 45% ± 12% |
| MSFT | 27.7% / 28.0% | 93 / 91 | 1.31 / 1.22 | 64% ± 5% | 55% ± 12% |
| AMZN | 33.0% / 36.3% | 86 / 22 | 1.33 / 2.09 | 61% ± 5% | 39% ± 6% |
| META | 39.8% / 45.6% | 57 / 26 | 1.66 / 2.25 | 63% ± 3% | 53% ± 5% |
| GOOGL | 29.5% / 32.0% | 106 / 60 | 1.25 / 1.42 | 66% ± 4% | 48% ± 7% |
| AMD | 56.7% / 56.8% | 110 / 128 | 1.20 / 1.07 | 63% ± 4% | 59% ± 9% |
| NFLX | 42.1% / 45.2% | 59 / 38 | 1.61 / 1.97 | 61% ± 3% | 58% ± 4% |
| COIN | 84.0% / 86.0% | 112 / 77 | 1.06 / 1.18 | 52% ± 18% | 44% ± 12% |
| PLTR | 68.4% / 65.6% | 50 / 28 | 1.60 / 1.96 | 52% ± 7% | 43% ± 6% |
| MSTR | 74.8% / 90.6% | 231 / 38 | 1.01 / 1.59 | 94% ± 1% | 39% ± 7% |
| SPY | 18.0% / 17.1% | 74 / 168 | 1.51 / 1.02 | 69% ± 3% | 70% ± 9% |

Vol is the sample vol. The Merton fit beats GBM by a likelihood-ratio statistic of 82 to 876
(3 extra parameters) for every fit. Jump share over 10 y: median 63.0%, χ² 412.4 against one
common share (MSTR's 94% ± 1% dominates). Over 5 y: median 48.0%, inverse-variance mean
51.3%, χ² 27.7 on 12 dof (p 0.006).

MSTR's 10-year fit mixes two stocks: a low-vol software company until 2020 and a bitcoin
proxy after. The fit reads that change as 231 small jumps a year.

### Volatility clustering

If jumps were independent from day to day, as the model assumes, a week's excess kurtosis
would be the daily one divided by 5, and the size of today's move would say nothing about
next month's.

| Symbol | Weekly excess kurtosis, data / model, 10y | 5y | acf \|r\| lags 20–60, 10y / 5y |
|---|---:|---:|---:|
| TSLA | 1.31 / 0.68 | 1.30 / 0.48 | 0.050 / 0.023 |
| AAPL | 0.93 / 0.61 | 6.23 / 0.47 | 0.054 / 0.042 |
| NVDA | 1.43 / 0.51 | 0.72 / 0.42 | 0.039 / 0.056 |
| MSFT | 4.72 / 0.67 | 4.43 / 0.49 | 0.059 / 0.051 |
| AMZN | 3.30 / 0.65 | 1.38 / 1.02 | 0.065 / 0.042 |
| META | 9.30 / 1.05 | 4.20 / 1.63 | 0.055 / 0.038 |
| GOOGL | 1.00 / 0.62 | 0.14 / 0.58 | 0.045 / 0.023 |
| AMD | 2.53 / 0.55 | 0.67 / 0.41 | 0.026 / 0.027 |
| NFLX | 9.92 / 0.94 | 18.78 / 1.34 | 0.056 / 0.063 |
| COIN | 1.39 / 0.36 | 2.67 / 0.38 | 0.057 / 0.050 |
| PLTR | 3.29 / 0.81 | 2.59 / 1.00 | 0.038 / 0.021 |
| MSTR | 4.18 / 0.57 | 1.40 / 0.59 | 0.194 / 0.036 |
| SPY | 8.36 / 0.96 | 3.59 / 0.44 | 0.090 / 0.079 |

Data exceed the model's weekly kurtosis in 25 of 26 fits (GOOGL 5 y is the exception) and
the autocorrelation is positive in all 26. This is why the two windows disagree: the
10-year window includes the 2020 crash period, which the model can only explain as extra
jumps. META's and NFLX's weekly kurtosis come from single earnings days; the largest 5-year
drops are META −26.4% (2022-02-03) and −24.6% (2022-10-27), NFLX −35.1% (2022-04-20) and
−21.8% (2022-01-21), PLTR −21.3% (2022-05-09) and −15.7% (2022-02-17).

## Prices

`ml/multi_stock_prices.py`. Product and states as `ml/teacher_v2_table.py`: ki 6000, ac 10000,
coupon 25 bps/week; spot {5500, 7000, 8500, 9500} × knockedIn {0, 1} × observationsRemaining
{26, 13, 2}, tNext 86,400 s. torch CUDA teacher (RTX 3090), 2^20 paths, one seed for all
configs. A fit's jump *shape* (λ, and jump sizes relative to its total vol) is scaled to a
target total vol V: μJ, σJ × V / V_fit, λ unchanged, so the jump share stays the fit's. The
note terms are the K2 product for every stock; this compares teachers, not term sheets.
Differences are worst case over the 24 states, with stderr as the hypot of the two labels
(conservative under common random numbers).

### A. The K2 product at 55% vol: each stock's jump shape vs the pinned teacher

The pinned teacher has a fixed jump variance, which is a 66% share at 55% vol. Pinned − GBM
is at most 30.0 bps (obs 13, clean, spot 7000).

| Symbol | 10y: max Δ vs pinned (bps) | 5y: max Δ vs pinned (bps) |
|---|---:|---:|
| TSLA | −6.4 ± 1.6 | −11.8 ± 2.0 |
| AAPL | +11.7 ± 1.8 | +13.6 ± 1.8 |
| NVDA | +12.1 ± 1.8 | −14.1 ± 1.6 |
| MSFT | +12.0 ± 1.5 | +10.9 ± 1.8 |
| AMZN | +8.3 ± 1.9 | −8.6 ± 0.4 |
| META | −11.9 ± 1.1 | +23.5 ± 1.7 |
| GOOGL | +12.4 ± 1.8 | −9.5 ± 1.6 |
| AMD | −13.6 ± 1.6 | −18.6 ± 1.6 |
| NFLX | +1.8 ± 1.6 | +11.4 ± 1.7 |
| COIN | −26.5 ± 1.6 | −24.7 ± 1.6 |
| PLTR | +23.7 ± 1.0 | +14.8 ± 1.0 |
| MSTR | −11.3 ± 1.6 | −17.4 ± 1.6 |
| SPY | −22.0 ± 1.1 | +16.7 ± 1.8 |

Median 12.3, max 26.5 bps. The TSLA 10y row is the fixed-share rule itself: switching K2's
teacher from fixed jump variance to a fixed 57% share moves its prices by up to 6.4 bps.
The worst state is most often mid-life, clean, spot 7000 (10% above the knock-in barrier).

### B. Each stock at its own vol: own fit vs the fixed-share rule

Rule = TSLA's 10-year shape (72 jumps/yr, 57% share) scaled to the stock's vol. The GBM
column is the whole jump effect, for scale.

| Symbol | Vol 10y / 5y | Own − rule, 10y | Own − rule, 5y | Own − GBM, 10y | Own − GBM, 5y |
|---|---:|---:|---:|---:|---:|
| TSLA | 59.1% / 59.6% | +0.0 ± 0.8 | +8.5 ± 1.6 | +22.9 ± 1.5 | +19.6 ± 1.5 |
| AAPL | 28.4% / 27.4% | −3.7 ± 1.4 | −6.0 ± 1.9 | +11.9 ± 1.9 | +10.2 ± 1.9 |
| NVDA | 48.7% / 51.0% | +6.8 ± 1.3 | −7.6 ± 1.7 | +20.3 ± 1.8 | +15.4 ± 1.7 |
| MSFT | 27.0% / 27.6% | −4.0 ± 1.2 | −3.1 ± 1.9 | +13.9 ± 2.0 | +10.3 ± 1.9 |
| AMZN | 32.6% / 36.3% | −3.2 ± 1.7 | +7.3 ± 2.0 | +16.3 ± 2.0 | +26.8 ± 2.0 |
| META | 37.9% / 44.2% | +11.3 ± 1.6 | **+27.1 ± 1.9** | +30.6 ± 2.0 | +49.0 ± 1.9 |
| GOOGL | 29.3% / 32.0% | −4.6 ± 1.4 | −2.0 ± 2.0 | +12.6 ± 1.9 | +15.2 ± 2.0 |
| AMD | 56.1% / 56.3% | −7.4 ± 1.6 | −12.1 ± 1.6 | +18.8 ± 0.3 | +15.1 ± 0.3 |
| NFLX | 40.2% / 42.3% | +7.4 ± 2.0 | **+18.1 ± 2.0** | +28.7 ± 2.0 | +39.6 ± 2.0 |
| COIN | 83.6% / 85.8% | **−18.8 ± 1.3** | **−18.5 ± 1.3** | −37.5 ± 2.0 | −38.0 ± 2.1 |
| PLTR | 68.1% / 65.5% | **+24.1 ± 1.4** | **+16.0 ± 1.3** | −41.7 ± 1.7 | −33.7 ± 1.7 |
| MSTR | 71.9% / 90.6% | +7.7 ± 0.6 | −12.8 ± 1.3 | +29.7 ± 0.5 | −42.2 ± 2.1 |
| SPY | 17.2% / 16.7% | −12.3 ± 1.1 | −5.4 ± 1.7 | −11.5 ± 1.1 | −4.7 ± 1.0 |

Vol here is the fit's model total vol. Own − rule: median 7.5, max 27.1 bps. Own − GBM:
median 20.0, max 49.0 bps. The rule captures most of the jump effect for large caps; it
misses where jumps are few and large (σJ ≈ 2 daily vols: META, NFLX, PLTR over 5 y).

## What this means

- **For v1: nothing changes.** K2's teacher and gate stand; this is a v2 input.
- **A fixed jump share is workable for a large-cap basket**, and it removes the ≤ 44.7%
  refusal. It isn't universal: stocks with earnings-driven jumps need their own shape,
  or a teacher that knows about earnings.
- **Teacher uncertainty is now the same size as student error.** Plausible teachers
  (window, stock shape) disagree by 7–27 bps; the K2 student is 18.7 bps max from its
  teacher. Tightening the student further buys little until the teacher is pinned down.
- **More history of the same kind won't settle the teacher.** The next teacher needs the
  missing components: scheduled earnings jumps (the v2 roadmap's "time to next earnings"
  input) and time-varying vol. This 12-stock set is the test bed to check them against.

## Caveats

1. Q = P for jumps, as in teacher v2: no jump risk premium, symmetric jumps.
2. 24 states, all one day before an observation; mid-week states aren't tested.
3. The note terms (coupon 25 bps/week) are fixed across stocks; at 17% vol the note is
   worth far more than par. The comparisons are teacher vs teacher on the same terms.
4. MSTR's 10-year fit (231 jumps/yr, 4.4 per week) needs more than the pinned 24 Poisson
   terms in the smoothed last step (dropped mass 7.8e-11 against the 1e-12 assert).
   `ml/multi_stock_prices.py` raises `teacher.SMOOTH_TERMS` to 40 in-process, after the
   pinned config is loaded; terms below 1e-17 weight are skipped, so the pinned teacher's
   prices are unchanged.
5. Yahoo can revise history; a refetch (`--refetch`) may move the fits slightly.

## Reproduce

```sh
tools/.venv/bin/python ml/multi_stock_jumps.py > ml/multi_stock_jumps.log            # ~7 min, 12 CPU; --refetch to refetch
/opt/ai/cache/venv-cuda/bin/python ml/multi_stock_prices.py > ml/multi_stock_prices.log   # ~1.5 min on an RTX 3090
```
