# Interfaces v1: guide for the frontend

**Frozen 2026-09-29.** The Solidity interfaces in
[`contracts/src/interfaces/`](../contracts/src/interfaces/) are the contract between
the contracts and the frontend. ABIs for viem/wagmi are in [`abi/`](../abi/)
(regenerate with `contracts/script/export-abi.sh`). Any change goes through a PR that
updates the interface, the ABI and this file together. Layer picture:
[architecture.md](architecture.md).

## Status

| Contract | Interface | Implementation |
|---|---|---|
| SurrogatePricer (Stylus) | `ISurrogatePricer` | done (`stylus/pricer-model`, default weights `model/k2`) |
| FixingsRecorder | `IFixingsRecorder` | done; records only observation times strictly in the past |
| SeriesFactory, NoteSeries, SeriesToken | `ISeriesFactory`, `INoteSeries`, `ISeriesToken` | done (`contracts/src/`), payout via the `AutocallPayout` library |
| NoteQuoter | `INoteQuoter` | done, series-based; one quoter for every model |
| Desk | `IDesk` (ERC-4626) | done; `MAX_FEE_BPS` = 200, `BACKSTOP_SHARE_BPS` = 2000 |

All of the above pass `forge test` and the dev node end-to-end run
(`contracts/script/e2e-devnode.sh`); see [contracts-review.md](contracts-review.md).
No interface or ABI changed since the freeze.

Robinhood Chain testnet: chain ID 46630, RPC `https://rpc.testnet.chain.robinhood.com`,
USDG `0x7E955252E15c84f5768B83c41a71F9eba181802F` (6 decimals). `contracts/script/Deploy.s.sol`
simulates cleanly against it; not broadcast yet, so no deployed addresses. They will be
listed here.

Implementation notes beyond the interfaces:
- `desk.listedSeries()` also returns delisted series; check `desk.listing(s)`.
- The Desk has one extra view not in `IDesk`: `heldSeries()`.
- The integrator fee on a sell is capped at the gross proceeds.
- `FixingPending(obsTime)` starts once `obsTime < block.timestamp`.

## Units

| Quantity | Unit |
|---|---|
| NOTE, WRITER, USDG amounts | base units, 6 decimals. 1 NOTE = 1 USDG notional |
| `priceBps`, `feeBps`, barriers, coupon | bps: of notional (price, fee, coupon) or of the initial fixing (barriers). Quoted prices include coupon accrued since strike; the model's own `priceBps` is clean |
| `payoutPerNote`, `maxPayoutPerNote` | USDG base units per 1 NOTE (1e6 base units) |
| feed prices, `initialFixing`, fixings | feed decimals (8) |
| times | unix seconds (`uint40`) |
| `volBpsAnnual` | annualized implied vol in bps (e.g. 5000 = 50%) |

## Models, products and stocks

A pricer (Stylus model) is certified for one **product**: fixed knock-in, autocall and
coupon, a maximum tenor, and currently a fixed vol. It never sees which stock it prices;
every input is relative to the strike. So one model serves every series with those
terms, on any stock with that vol. The Desk listing says which model prices which
series: `Listing{pricer, volBpsAnnual, capNotional, …}`. One stateless quoter serves all
models. Today: `model/k2` (60% / 100% / 25 bps per week, 26 weekly observations, 55%
total vol), distilled from a jump-diffusion teacher calibrated to TSLA
([teacher-v2.md](teacher-v2.md), [k2-round2.md](k2-round2.md)). `model/k1-r1` (first
week only) remains as a second certified model. `volBpsAnnual` is total vol; the
current teacher needs it above 44.7% (its pinned jump vol).

## Screens and the calls behind them

**Market list.** `desk.listedSeries()` → for each series:
`desk.listing(s)` (pricer, vol, cap, sold), `series.terms()`, `series.state()`,
`quoter.notePriceBps(s, listing.pricer, listing.volBpsAnnual)`. When a quote reverts, show why (see Errors)
rather than hiding the series.

**Lifecycle to show.** Barrier observations every interval after strike; the
maturity fixing is **one interval after the last observation** (see
`INoteSeries`). No quotes in that final period, or anywhere outside the model's
certified domain: for `model/k2` that is the whole life of the note (1–26
observations remaining, any time within the week), spot 50–120% of initial, except
two observation-day bands (next observation within 1 day): spot 95–105% of initial
(autocall, region 0) and, for notes not yet knocked in, spot 50–70% (knock-in,
region 1).

**Note detail.** Everything from the market list, plus:
- `quoter.inputs(s, vol)`: exactly what the model saw. Showing it is the transparency pitch.
- `series.pendingObservation()`.
- Fixing history: `FixingRecorded` events on `series.recorder()`, and `ObservationProcessed` on the series.
- `weightsHash`: from `notePriceBps`, or from `NoteBought` for past trades.
- The model card: `listing.pricer`, its `weightsHash()` and `certifiedRange(0..9)`,
  i.e. which product it was certified for and where it will answer.

**Buy NOTE.**
1. `desk.quoteBuy(s, amount, feeBps)` → `(cost, priceBps)`.
2. `USDG.approve(desk, maxCost)` with `maxCost = cost + slippage`.
3. `desk.buy(s, amount, maxCost, feeBps, feeReceiver, to)`.

`feeBps`/`feeReceiver` are the integrator fee: our frontend can charge one (≤ `MAX_FEE_BPS`) or pass 0.

**Sell NOTE (early exit).** `desk.quoteSell` → `NOTE.approve(desk, amount)` → `desk.sell(..., minProceeds, ...)`.

**Redeem after settlement.**
- If `state().phase == Settled`: `series.redeem(noteAmount, writerAmount, to)`. No approval
  needed; it burns the caller's tokens. `previewRedeem` gives the amount.
- If the phase is still Live but maturity or an autocall fixing has passed: call
  `series.advance()` first (anyone can), or just call `redeem`, which advances internally.

**Hedger: mint a pair.** `USDG.approve(series, previewMint(n))` → `series.mint(n, to)`.
You get `n` NOTE and `n` WRITER. Sell the NOTE to the Desk or keep it.
`series.redeemPair(n, to)` unwinds the pair at any time.

**LP.** Standard ERC-4626 on the Desk: `deposit`/`withdraw`/`redeem`. Check
`maxDeposit`/`maxWithdraw` first: they return 0 while any held series can't be quoted
(weekends, pending fixing), and in the final week of a knocked-in note, which the model
doesn't price. Say so in the UI; don't let the transaction revert.

**Keeper (anyone, could be a button).** For each observation time that has passed:
find the round (binary search over `feed.getRoundData`: the last round at or before
`obsTime`) → `recorder.recordFixing(obsTime, roundId)` → `series.advance()`.

## Events to index

| Contract | Event | Use |
|---|---|---|
| Factory | `SeriesCreated(seriesId, feed, series, note, writer, terms)` | series list |
| Recorder | `FixingRecorded(obsTime, roundId, price, timestamp)` | price path chart |
| Series | `Struck`, `ObservationProcessed`, `Settled` | lifecycle timeline |
| Series | `Minted`, `PairRedeemed`, `Redeemed` | positions |
| Desk | `NoteBought`, `NoteSold` (carry `priceBps`, `feeBps`, `weightsHash`) | trade history, "which model priced this" |
| Desk | `SeriesListed(series, pricer, weightsHash, vol, cap)` (also on updates), `SeriesDelisted`, ERC-4626 `Deposit`/`Withdraw` | admin + LP views, model changes |

## Errors worth a human message

When decoding, merge the ABIs: the Desk bubbles up errors from the quoter and the
pricer, so include `INoteQuoter` and `ISurrogatePricer` errors.

| Error | Show |
|---|---|
| `FeedStale(updatedAt)` | "Market closed: quotes resume when the feed updates" (weekends) |
| `FixingPending(obsTime)` | "Observation at … awaiting its fixing": offer the keeper button |
| `TooCloseToObservation(obsTime)` | "Trading pauses shortly before each observation" |
| `OutOfRange(field, value)` | "Outside the model's certified range (field …)": the model refuses rather than guesses. `pricer.certifiedRange(field)` gives the range to show |
| `Uncertified(region)` | region 0: "Too close to the autocall barrier on observation day"; region 1: "Too close to the knock-in barrier on observation day". The two places the value jumps at a fixing |
| `Inconsistent(field)` | a bug in whoever built the inputs; never expected from our quoter |
| `NotLive()` | not struck yet, or already settled |
| `CapExceeded`, `Slippage`, `FeeTooHigh` | self-explanatory |

## What is deliberately not here

No demo flags in production contracts: the demo uses staged feed history on the mock
feed. No NOTE collateral oracle yet (roadmap). No router or permit yet: plain approve + call.
