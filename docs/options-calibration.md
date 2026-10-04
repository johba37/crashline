# Roadmap: a teacher calibrated to listed options

Status: design, 2026-10-01; the vol feed's requirements added 2026-10-04 (reason 4,
[Who writes the vol](#who-writes-the-vol), step 7). Not for the Oct 4 submission. Step 1 is done (option smiles for
13 stocks, a jump fit to them, the note-price impact); daily snapshots are running; the
decisions in [Open decisions](#open-decisions) come before any model is retrained. Code:
`ml/options_calibrate.py`, log `ml/options_calibrate.log`, outputs `ml/options_calibrate.json`,
`ml/options_note_prices.json`, data `ml/data/options/`. Every number below is from that log
unless marked otherwise.

## Why

The teacher learns its jumps from how the stock **moved** (ten years of daily closes,
[teacher-v2.md](teacher-v2.md)). A price should reflect what the market **charges** for the
same risk, and listed options are that price list: insurance on the stock at every level.
Calibrating means choosing the teacher's jump settings so that its own option prices match
the market's.

Four reasons it matters here:

1. **The note is crash insurance.** The NOTE holder is short a put at the 60% knock-in
   barrier, and the Desk now sells the other side outright as cover (`IDeskCover`). The
   market's price for that put is the left side of the volatility smile.
2. **The teacher has almost no smile.** Teacher v3's jumps are symmetric (mean jump ≈ 0),
   so its skew is at most 1.7 vol points across the 13 stocks and four expiries; the
   market's reaches 27.4 (SPY, 7 weeks).
3. **The vol input now comes from the market.** With `model/k3` the Desk lists at any vol
   in 20–90%, so a curator will list at the market's vol. Listing teacher v3 at the market's
   at-the-money vol misprices the note by 30–140 bps in the worst of 24 states, with a sign
   that depends on the stock and the state (below), more than the student's own error
   (37.9 bps, [k3-vol-input.md](k3-vol-input.md)).
4. **The price has to move with a crash.** Sellers of crash protection (NOTE buyers, LPs)
   are plentiful in quiet times and scarce in a crash, when everyone wants protection.
   Insurance markets clear that by price: premiums rise after a catastrophe and the higher
   price brings capital back. The Desk can only do the same if its vol follows the options
   market within a day. A vol from price history, or one that is updated slowly, sells cover
   too cheaply exactly then, and the LPs pay the difference. Protection already sold is not
   at risk (each series holds its maximum payout in escrow); new protection is. So the vol
   source is a question of the Desk surviving a crash, not only of accuracy.

**Words used below.** *Implied vol*: an option's price turned back into the bumpiness it
implies. *ATM vol*: the implied vol of the option whose strike is today's price (the most
traded, most reliable one). *Smile*: implied vol plotted against strike; for stocks it rises
on the crash side. *Skew*: that tilt, here implied vol at 70% of the forward minus ATM vol.

## Step 1: what the market says (done)

**Data.** Nasdaq's public option chains, four expiries per stock (2026-11-20, 2026-12-18,
2027-03-19, 2027-06-17: about 7 weeks to 9 months, around the note's 26-week life), the 13
stocks of [multi-stock-jumps.md](multi-stock-jumps.md), as of the 2026-09-30 close.

**Smiles.** Per expiry: the forward from put-call parity near the money (this absorbs
dividends without assuming a yield), implied vols from out-of-the-money mids (Black-76,
r = 4%), quotes with a wide spread or a tiny price dropped.

**Fit.** The teacher's own model (Merton jump-diffusion under the pricing measure, closed
form as a Poisson sum of Black-76 prices), one parameter set per stock across its four
expiries, least squares on vega-scaled price errors.

TSLA, as an example (implied vol in %, strike as a fraction of the forward):

| expiry | | 0.6 | 0.7 | 0.8 | 1.0 (ATM) | 1.2 |
|---|---|---:|---:|---:|---:|---:|
| 2026-11-20 (7 weeks) | market | 63.8 | 52.1 | 45.6 | 43.5 | 45.2 |
| | options fit | 64.6 | 52.5 | 45.7 | 42.9 | 43.9 |
| | teacher v3 at ATM vol | 44.7 | 44.0 | 43.6 | 43.3 | 43.6 |
| 2027-03-19 (24 weeks) | market | 49.0 | 45.6 | 44.1 | 43.7 | 44.0 |
| | options fit | 48.7 | 46.1 | 44.8 | 44.0 | 44.1 |
| | teacher v3 at ATM vol | 43.8 | 43.7 | 43.7 | 43.7 | 43.7 |

All 13 (6-month expiry for ATM vol and skew; the fit is across all four expiries):

| stock | ATM vol | skew | jumps / yr | mean jump | jump spread | jump share: options / history | fit RMSE (vol pts) |
|---|---:|---:|---:|---:|---:|---:|---:|
| TSLA | 43.7 | +1.8 | 0.08 | −37.0% | 78.6% | 25% / 57% | 0.85 |
| AAPL | 26.3 | +7.1 | 0.29 | −20.8% | 18.9% | 30% / 72% | 1.12 |
| NVDA | 37.1 | +5.9 | 0.26 | −28.7% | 35.4% | 33% / 58% | 0.76 |
| MSFT | 31.4 | +4.7 | 0.17 | −27.6% | 26.4% | 22% / 64% | 0.78 |
| AMZN | 35.6 | +4.1 | 0.22 | −20.0% | 33.4% | 23% / 61% | 1.09 |
| META | 41.4 | +1.6 | 0.76 | −4.2% | 24.2% | 24% / 63% | 1.14 |
| GOOGL | 35.1 | +3.2 | 0.45 | −7.4% | 25.7% | 24% / 66% | 0.80 |
| AMD | 55.2 | +0.1 | 0.15 | −24.4% | 63.2% | 21% / 63% | 0.95 |
| NFLX | 39.0 | +1.1 | 0.46 | −5.8% | 30.1% | 26% / 61% | 1.02 |
| COIN | 65.6 | −1.3 | 1.70 | −1.6% | 34.0% | 44% / 52% | 1.36 |
| PLTR | 55.1 | +3.0 | 0.30 | −33.1% | 51.1% | 30% / 52% | 1.03 |
| MSTR | 69.5 | +1.1 | 0.10 | −89.4% | 150% (bound) | 43% / 94% | 1.02 |
| SPY | 15.3 | +16.3 | 0.14 | −31.5% | 23.9% | 60% / 69% | 1.95 |

What it says:

- **History and options tell opposite stories.** History: 50–230 small, symmetric jumps a
  year carrying 52–94% of the variance. Options: rare, large, downward jumps (TSLA one every
  ~13 years, mean −37%) carrying 21–44% of it (SPY 60%). The market charges for crashes that
  ten years of TSLA closes barely contain.
- **The jump share and its direction are well determined; the split into how often and
  how big is not.** For TSLA a fit capped at a 60% jump spread (0.13 jumps/yr, mean −24%)
  matches the options almost as well (RMSE 0.90 vs 0.85). The two fits price the note within
  14.9 bps of each other at worst, 2.3 on average (a one-off check during the analysis, not
  in the log). MSTR's fit runs to the bound: the market prices a near-wipeout, which a
  lognormal jump can only approximate.
- **Shapes differ by stock far more than history's did.** Mean crash size runs from −2%
  (COIN) to −37% (TSLA), and −89% for MSTR; history put the large caps within 8 bps of one
  shape.

## What it does to the note

The K3 note (ki 60%, ac 100%, 25 bps/week) at 24 states (spot 55/70/85/95% × knocked in or
not × 26/13/2 observations left, a day before an observation), torch CUDA teacher, 2^20 paths
(`--prices`):

| stock | options fit vs teacher v3, both at the fit's total vol (worst state) | options fit vs teacher v3 listed at ATM vol (worst state) | the latter, mean over not knocked in / knocked in |
|---|---:|---:|---:|
| TSLA | +216.9 | **+48.7** | +9.6 / −6.5 |
| AAPL | +219.3 | +88.8 | −11.4 / +6.0 |
| NVDA | +298.2 | +132.1 | +11.5 / +7.3 |
| MSFT | +177.6 | +52.7 | −6.9 / +3.9 |
| AMZN | +180.3 | −43.2 | −7.1 / −0.6 |
| META | +119.6 | −29.7 | −7.3 / −9.6 |
| GOOGL | +145.4 | +77.6 | +4.4 / −1.9 |
| AMD | +140.8 | +46.2 | +10.7 / −4.8 |
| NFLX | +164.2 | +80.6 | +7.4 / −7.7 |
| COIN | +179.9 | +137.9 | +31.0 / −37.3 |
| PLTR | +249.8 | +79.9 | +15.3 / +2.5 |
| MSTR | +426.2 | −103.1 | +6.6 / −30.5 |
| SPY | +380.5 | −131.4 | −26.2 / +8.3 |

(bps of notional, label stderr ≤ 2.4.) For scale, teacher v3's own jumps move the same
states 9–35 bps away from no jumps at all.

- **The column that decides is the second one**: what the Desk's quotes miss if a curator
  lists teacher v3 at the market's ATM vol. 30–140 bps in the worst state.
- **The sign isn't the obvious one.** "The market prices crashes higher, so cover is too
  cheap" is wrong for most of these stocks. The options fit reads part of the ATM vol as
  crash premium, so the day-to-day vol it leaves is lower than the ATM vol; fewer gradual
  paths reach the barrier, and the crashes add some back. Which wins depends on the state.
  It has to be calibrated, not guessed.
- **The first column is why the meaning of the vol input matters**: at the same *total*
  vol the two models differ by 120–430 bps, because the same number means different things
  under them.

## Design

### Teacher v4 is a config, not new code

Teacher v3 already scales jump sizes with vol from a reference point (`volRef`) and
discounts at `rDiscount`. A v4 config is v3's format with the options-fitted shape: TSLA
would be 0.0753 jumps/yr, mean jump −0.370 and jump spread 0.786 at `volRef` 0.477 (the fit's
total vol, `ml/options_calibrate.json`), `rDiscount` 0. The simulator, its torch twin and the K3 pipeline (sets, trainer,
gate, certification) run unchanged. New teacher checks: the closed-form option prices of
the config reproduce the fitted smile, and the existing v3 checks (martingale, closed forms,
smoothing, torch vs numpy, barrier equality) run on each config.

### The vol input: ATM vol, not total vol (recommended)

Today `volBpsAnnual` is the model's total vol. Under an options-fitted shape the market's ATM
vol and the model's total vol differ (TSLA: 43.7% vs 47.7%), so a curator would have to
convert, off-chain, with the same shape the model was trained on.

**Proposal:** the input becomes the 6-month ATM vol. The teacher maps it to total vol with the
pinned shape (one closed-form inversion per label, monotone); the student learns the price as
a function of ATM vol; the curator, or a keeper, writes the number it reads off the market.
The contract, `PricerInputs` and the certified-domain format don't change, only what the
number means for a model trained this way. Open point: the note's remaining life runs from
1 to 26 weeks while the input is one tenor's ATM vol; the term structure (TSLA 43.5% at 7 weeks,
44.4% at 9 months) is then part of the pinned shape.

### One shape, groups, or one per stock

| | models | fit to each stock | cost |
|---|---|---|---|
| one shape for all | 1 | off by about 100 bps for some stocks (shapes above) | lowest |
| **groups** | 3–5 | to be measured | one training round (~5 GPU-hours) and one contract per group |
| one per stock | 13 | exact by construction | 13 rounds, 13 contracts, 13 recalibrations |

Candidate groups from the 2026-09-30 fits, to be tested:

| group | stocks | market's crash picture | ATM vol |
|---|---|---|---|
| mega-cap tech | AAPL, MSFT, NVDA, AMZN | mean crash −20% to −29%, steep skew | 26–37% |
| high-vol, crash-prone | TSLA, AMD, PLTR | mean crash −24% to −37% | 44–55% |
| mild skew | META, GOOGL, NFLX | mean crash −4% to −7% | 35–41% |
| own model or none | COIN (no skew), MSTR (wipeout tail), SPY (index, steep skew) | | |

**Test** (as in multi-stock-jumps.md): price each member's note with the group's shape and
with its own fit, at its own ATM vol; keep the group if the worst gap is under 15 bps (inside
the student's error budget). A group's vol range can be narrower than K3's 20–90%, which buys
accuracy back (the vol axis cost K3 about 20 bps of worst case).

The contracts already support this: each listing names its pricer, so a stock's series list
with its group's model. Nothing on chain changes.

### Recalibration

Shapes move, most in selloffs. Each recalibration is a new model and a new `weightsHash`.

- **What to track: note prices, not parameters.** The fitted parameters are not identified
  (two TSLA fits, same quality, different jumps), but the note price is (within 15 bps).
  Daily: refit each stock, price the 24 states under the new fit and under the pinned
  config at today's ATM vol, and record the worst gap.
- **When to recalibrate:** when the worst gap stays above a threshold (proposal: 15 bps)
  for several days. The snapshot history sets that threshold and shows how often it would
  trigger.
- **Provenance:** each model directory should carry its teacher config (and its sha256), so
  the backend's verify-quote ("the model's own teacher") and any audit can price with the
  exact teacher that labelled it. K3's label caches record the teacher hash; the model
  directory doesn't yet.

### Who writes the vol

The Desk's owner (the curator) sets vol per listing with `listSeries`; the contract can't
read option prices. **Proposal for the contracts side:** a vol feed, a keeper role that can
only update a listing's vol, fed by an off-chain job that reads ATM vol from the same
snapshots. A Desk buy with `model/k3` and a vol band (two model calls) is 775,042 L2
execution gas on the dev node ([README](../README.md)).

The feed has to keep up with a crash (reason 4). What that asks of it:

| requirement | why | today |
|---|---|---|
| follows a crash within a day | a lagging vol sells cover too cheaply when demand peaks | the owner relists by hand |
| bounds that stop a bad keeper but not a crash | a per-update or per-day cap that rejects a jump from 30% to 80% also keeps the crash out of the price | no keeper, no bounds |
| a certified vol range that covers crisis vols | `listSeries` rejects a vol outside the pricer's range, so the series stops quoting when demand peaks | K3: 20–90%; MSTR's ATM vol is 69.5% on a calm day |
| fails closed when stale | as the price feed does (`FeedStale`): no cover sold on an old vol | the vol band (`volBandBps`) covers small gaps only |

The vol band already prices every trade against the trader on both sides (`_sidePrice`): the
Desk sells NOTE at the higher of the two NOTE prices, and sells cover at the maximum payout
minus the lower one. It is the safety margin between updates, sized for a day's drift, not
for a crash.

**Open: how to bound the keeper without slowing a crash.** Candidates: wide bounds with a
band that widens after a large move and narrows again; rises allowed faster than falls; a
second signer for a move beyond the bound. The snapshot history (step 2) shows how far ATM
vol moves in a day; a sharp selloff in that history is the test case.

## Limits of this model

- **One constant jump model can't match every expiry.** TSLA's skew is +8.6 vol points at
  7 weeks and +1.8 at 6 months; one parameter set compromises (RMSE about 1 vol point). The
  note spans 1–26 weeks, so the short end matters. Stochastic vol with jumps (Bates) is the
  model that fixes this; it is a later step.
- **Earnings sit inside the expiries.** A short expiry that spans an earnings date prices that
  jump; the fit spreads it over the year. Earnings-aware jumps are a separate roadmap step.
- **American options priced as European.** The early-exercise premium on out-of-the-money
  options is small at 4% and ignored.
- **One snapshot day so far.** Every number above is one close.
- **Pricing measure.** The fitted jumps include the market's crash premium. That is the right
  measure for pricing what the Desk sells, because a hedger can buy the same protection on
  listed options; it is not a forecast of how often crashes happen.

## Open decisions

| decision | options | recommendation |
|---|---|---|
| meaning of the vol input | total vol (today) / 6-month ATM vol | ATM vol |
| shape granularity | one / groups / per stock | groups, if the test and the snapshot history hold |
| recalibration trigger | fixed schedule / note-price drift above a threshold | drift, threshold from the snapshot history |
| who writes the vol | owner by hand / a bounded keeper role | a keeper role whose bounds let a crash through ([Who writes the vol](#who-writes-the-vol)) |
| model richness | constant jumps / stochastic vol + jumps | constant jumps first |

## Plan

| step | what | status |
|---|---|---|
| 1 | snapshots, smiles, fits, note-price impact | done (`d712e15`) |
| 2 | daily snapshots for about a week; `--history`: fit stability measured in note bps | running (cron, weekdays after the US close) |
| 3 | grouping test; decide the open decisions | after step 2 |
| 4 | teacher v4 configs per group, new checks, band sizing per group | |
| 5 | per group: label, train, gate (K3 pipeline, ~5 GPU-hours), certify, Stylus size, quoter vectors, backend teacher mapping | |
| 6 | recalibration runbook: daily drift report, threshold, model switch per listing | |
| 7 | vol feed (contracts): keeper role, bounds that let a crash through, staleness, a certified vol range that covers crisis vols | before mainnet (README M2) |

## Reproduce

```sh
PY=tools/.venv/bin/python
$PY ml/options_calibrate.py --fetch        # today's snapshot (skips a day already on disk)
$PY ml/options_calibrate.py > ml/options_calibrate.log
/opt/ai/cache/venv-cuda/bin/python ml/options_calibrate.py --prices >> ml/options_calibrate.log
$PY ml/options_calibrate.py --history      # every snapshot day side by side
```
