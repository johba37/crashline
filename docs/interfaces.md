# Interfaces v1: guide for the frontend

**Frozen 2026-09-29; `IDeskCover` and `IDeskQueue` added 2026-09-30** (`IDeskCover`
extends `IDesk`, whose ABI is unchanged, see "Cover and the two prices"; `IDeskQueue` is
the LP redemption queue, see "LP"). The Solidity interfaces in
[`contracts/src/interfaces/`](../contracts/src/interfaces/) are the contract between
the contracts and the frontend. ABIs for viem/wagmi are in [`abi/`](../abi/)
(regenerate with `contracts/script/export-abi.sh`). Any change goes through a PR that
updates the interface, the ABI and this file together. Layer picture:
[architecture.md](architecture.md).

## Status

| Contract | Interface | Implementation |
|---|---|---|
| SurrogatePricer (Stylus) | `ISurrogatePricer` | done (`stylus/pricer-model`, default weights `model/k3`; `model/k2` selectable) |
| FixingsRecorder | `IFixingsRecorder` | done; records only observation times strictly in the past |
| SeriesFactory, NoteSeries, SeriesToken | `ISeriesFactory`, `INoteSeries`, `ISeriesToken` | done (`contracts/src/`), payout via the `AutocallPayout` library |
| NoteQuoter | `INoteQuoter` | done, series-based; one quoter for every model |
| Desk | `IDesk` (ERC-4626) + `IDeskCover` | done; `MAX_FEE_BPS` = 200 (of notional, NOTE), `MAX_COVER_FEE_BPS` = 1000 (of the premium, cover), `BACKSTOP_SHARE_BPS` = 5000, `MAX_SPREAD_BPS` = 1000. Use `abi/IDeskCover.json` (it contains all of `IDesk`) merged with `abi/IDeskQueue.json` |

All of the above pass `forge test` and the dev node end-to-end run
(`contracts/script/e2e-devnode.sh`); see [contracts-review.md](contracts-review.md).
No frozen interface or ABI changed since the freeze; `IDeskCover` and its ABI are new.

Robinhood Chain testnet: chain ID 46630, RPC `https://rpc.testnet.chain.robinhood.com`,
USDG `0x7E955252E15c84f5768B83c41a71F9eba181802F` (6 decimals). `contracts/script/Deploy.s.sol`
simulates cleanly against it; not broadcast yet, so no deployed addresses. They will be
listed here.

Implementation notes beyond the interfaces:
- `desk.listedSeries()` also returns delisted series; check `desk.listing(s)`.
- `listing.capNotional` is the most WRITER the Desk may hold in the series, and
  `listing.soldNotional` the WRITER it holds now (read live). NOTE available to buy =
  the Desk's NOTE balance + `capNotional − soldNotional`.
- The Desk has one extra view not in `IDesk`: `heldSeries()`.
- The integrator fee on a sell is capped at the gross proceeds.
- `FixingPending(obsTime)` starts once `obsTime < block.timestamp`.

> **Check `desk.risk(feed)` before offering a trade.** The quote functions do not apply
> the Desk's risk budget. A trade that adds to the Desk's position reverts
> `RiskBudgetExceeded` even right after a successful quote: `buyCover`, a `sell` of NOTE
> the Desk can't pair with WRITER it holds, and a `buy` or `sellCover` that leave it with
> WRITER. `desk.risk(feed)` → `(atRisk, limit)`, in USDG base units; the room left is
> `limit − atRisk`. Show it as "cover available", disable the button when the trade
> doesn't fit, and handle the revert anyway (another trade can land first). A feed whose
> budget was never set has `limit` = 0.
>
> The same trades also revert `QueuePending` while LP redemptions are queued and the
> Desk's idle USDG can't pay them all: read `desk.queuedShares()` too (see "LP").

## Units

| Quantity | Unit |
|---|---|
| NOTE, WRITER, USDG amounts | base units, 6 decimals. 1 NOTE = 1 USDG notional |
| `priceBps`, `feeBps`, spreads, barriers, coupon | bps: of notional (price, fee, spread, coupon) or of the initial fixing (barriers). `quoter.notePriceBps` is the model's quote including coupon accrued since strike; the model's own `priceBps` is clean; the Desk's `priceBps` (quotes, trade events) is the price applied to that leg, spread included |
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
models. Today: `model/k3` (60% / 100% / 25 bps per week, 26 weekly observations, any
total vol 20–90%), distilled from teacher v3, a jump-diffusion calibrated to TSLA with
jump sizes scaled to the vol ([k3-vol-input.md](k3-vol-input.md)); the default build and
what the dev node deploys. `model/k2` (the same product at 55% only, teacher v2 needs vol
above 44.7%: [teacher-v2.md](teacher-v2.md), [k2-round2.md](k2-round2.md)) and
`model/k1-r1` (first week only) remain certified. `volBpsAnnual` is total vol.

## Screens and the calls behind them

**Market list.** `desk.listedSeries()` → for each series:
`desk.listing(s)` (pricer, vol, cap, sold), `series.terms()`, `series.state()`,
`quoter.notePriceBps(s, listing.pricer, listing.volBpsAnnual)`. When a quote reverts, show why (see Errors)
rather than hiding the series.

**Lifecycle to show.** Barrier observations every interval after strike; the
maturity fixing is **one interval after the last observation** (see
`INoteSeries`). No quotes in that final period, or anywhere outside the model's
certified domain: for `model/k2` and `model/k3` that is the whole life of the note (1–26
observations remaining, any time within the week), spot 50–120% of initial, except
two observation-day bands (next observation within 1 day): spot 95–105% of initial
(autocall, region 0) and, for notes not yet knocked in, spot 50–70% (knock-in,
region 1). `model/k3` adds three low-vol regions near the knock-in barrier late in
the note's life (regions 2–4, [k3-vol-input.md](k3-vol-input.md)).

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

`feeBps`/`feeReceiver` are the integrator fee: our frontend can charge one or pass 0. On NOTE
trades it is a share of the notional (≤ `MAX_FEE_BPS`, 2%); on cover trades a share of the
premium (≤ `MAX_COVER_FEE_BPS`, 10%). Half of it (`BACKSTOP_SHARE_BPS`) stays in the vault,
`feeReceiver` gets the other half.
`CapExceeded(requested, available)` now means: the Desk's NOTE inventory plus its WRITER
cap covers only `available`.

**Sell NOTE (early exit).** `desk.quoteSell` → `NOTE.approve(desk, amount)` → `desk.sell(..., minProceeds, ...)`.
NOTE the Desk can't pair with WRITER it holds becomes its position and needs room in the
risk budget (`RiskBudgetExceeded`).

**Redeem after settlement.**
- If `state().phase == Settled`: `series.redeem(noteAmount, writerAmount, to)`. No approval
  needed; it burns the caller's tokens. `previewRedeem` gives the amount.
- If the phase is still Live but maturity or an autocall fixing has passed: call
  `series.advance()` first (anyone can), or just call `redeem`, which advances internally.

**Hedger: buy cover.** WRITER is crash cover: it pays `maxPayout − NOTE payout`.
1. `desk.quoteBuyCover(s, amount, feeBps)` → `(cost, priceBps)`: the premium, not the pair's collateral.
   `feeBps` is of that premium: `fee = ceil(ceil(amount × priceBps / 1e4) × feeBps / 1e4)`.
2. `USDG.approve(desk, maxCost)`.
3. `desk.buyCover(s, amount, maxCost, feeBps, feeReceiver, to)`: `amount` WRITER arrives at `to`.

Exit: `desk.quoteSellCover` → `WRITER.approve(desk, amount)` → `desk.sellCover(..., minProceeds, ...)`.
After settlement: `series.redeem(0, writerAmount, to)`. `desk.risk(feed)` → `(atRisk, limit)`
shows how much more cover the Desk can sell on that stock (see the note above Units). Minting a pair directly
(`series.mint`, `series.redeemPair`) still works and needs no Desk.

**Cover and the two prices.** `desk.spread(s)` → `(bidBps, askBps, volBandBps)`,
`maxBps = series.maxPayoutPerNote() / 100`. The Desk asks the model at the listing's vol
minus and plus the band: `quoter.notePriceBps(s, pricer, vol − volBandBps)` and
`(…, vol + volBandBps)`. `lo` and `hi` are the lower and the higher of the two quotes,
capped at maxBps; with no band there is one quote and `lo = hi`.

| Call | The Desk | `priceBps` |
|---|---|---|
| `buy` | sells NOTE | `min(hi + askBps, maxBps)` |
| `sell` | buys NOTE | `lo − bidBps`, floored at 0 |
| `buyCover` | sells WRITER, ends up with NOTE | `maxBps − (lo − bidBps)` |
| `sellCover` | buys WRITER | `maxBps − min(hi + askBps, maxBps)` |

Cost and proceeds use `priceBps` exactly as in `IDesk`. The note is worth less at a
higher vol, so the band makes the spread widest where the price depends most on vol;
`bidBps`/`askBps` are a flat floor on top. To show the mid, quote at the listing's vol
itself; the Desk's marks use that. All three are 0 until the curator calls
`setSpread(series, bidBps, askBps, volBandBps)`. A band needs a model certified for a
range of vols (`pricer.certifiedRange(2)`): `model/k3` takes one (the dev node lists at
5500 ± 200), `model/k2` pins 55%, so its band is 0. The backend's `/series` has both legs'
bid and ask at 1 unit (`quotes`). The risk budget is 0 until the curator calls `setRiskBudget(feed, bps)`:
until then every trade that adds to the Desk's positions on that feed reverts.

**LP.** Standard ERC-4626 on the Desk: `deposit`/`withdraw`/`redeem`. Check
`maxDeposit`/`maxWithdraw` first: they return 0 while any held series can't be quoted
(weekends, pending fixing), and in the final week of a knocked-in note, which the model
doesn't price. Say so in the UI; don't let the transaction revert.

When `maxWithdraw` is 0 or smaller than the LP wants (the rest is locked in series), offer
the queue (`IDeskQueue`) instead. It works in every state:
1. `desk.requestRedeem(shares)` → `id`. No approval; the shares move into the Desk and keep
   earning until they are filled. Minimum `MIN_REQUEST_SHARES` (10 USDG worth at the start).
2. Anyone calls `desk.processQueue(n)` once every held series can be quoted (a keeper, or a
   button). Requests fill first in, first out at the share price of that moment; the last
   one partially if idle USDG runs out. Show `desk.redeemRequest(id)` → `(owner, unfilled
   shares)` and `desk.queue()` → `(head, length)` for the position in line.
3. `desk.claim(to)` pays `desk.claimableAssets(owner)`.
`desk.cancelRedeem(id)` returns the unfilled shares. While `desk.queuedShares() > 0`,
`maxWithdraw`/`maxRedeem` are 0 for everyone (no withdrawal ahead of the queue), and a
trade that adds to the Desk's position pays the queue first or reverts `QueuePending`.

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
| Desk | `NoteBought`, `NoteSold`, `CoverBought`, `CoverSold` (carry `priceBps`, `feeBps`, `weightsHash`) | trade history, "which model priced this" |
| Desk | `SpreadSet(series, bidBps, askBps, volBandBps)`, `RiskBudgetSet(feed, budgetBps)` | the two prices, cover capacity |
| Desk | `RedeemRequested(id, owner, shares)`, `RedeemFilled(id, owner, shares, assets)` (once per fill, a request can fill in parts), `RedeemCancelled`, `RedeemClaimed(owner, to, assets)` | the LP's queue position and payouts |
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
| `RiskBudgetExceeded(atRisk, limit)` | "The Desk has reached its risk budget for this stock": the trade would add to its position. Trades that shrink the position still work |
| `CapExceeded(requested, available)` | "Only `available` on offer": NOTE beyond the Desk's inventory and WRITER cap, or WRITER sold back beyond the cap |
| `QueuePending(queuedShares)` | "LP redemptions are waiting for USDG: the Desk takes no new position until they are paid." Trades that shrink its position still work |
| `ReservedForClaims()` | the trade would spend USDG already set aside for filled redemptions: the Desk's own idle USDG is too low |
| `RequestTooSmall`, `NothingToClaim`, `NotRequestOwner` | queue input errors |
| `Slippage`, `FeeTooHigh` | self-explanatory |

## What is deliberately not here

No demo flags in production contracts: the demo uses staged feed history on the mock
feed. No NOTE collateral oracle yet (roadmap). No router or permit yet: plain approve + call.
