# Interfaces v2 (perpetual note): guide for the frontend

Status: 2026-10-02, first version, **not frozen**. The v1 interfaces
([interfaces.md](interfaces.md)) are untouched and stay frozen: every v2 contract is a new
contract next to v1, and both generations share the `FixingsRecorder` of a feed. Product
and reasons: [v2-perpetual-note.md](v2-perpetual-note.md). Exact rules and numbers:
[v2-spec.md](v2-spec.md). ABIs: [`abi/`](../abi/) (`contracts/script/export-abi.sh`).

## What is different from v1, in one table

| | v1 autocallable | v2 perpetual |
|---|---|---|
| A series ends | at an autocall or at maturity (`settle`, `redeem`) | never, unless its feed dies (`Closed`) |
| A token is | 1 USDG of notional until settlement | `notionalPerToken` of notional, 1.9% less after each weekly fixing |
| A holder is paid | once, by burning the token | at every fixing, by calling `series.claim(to)` |
| Price | the model | closed form + the model's correction + the coupon reserve |
| Desk parameters per series | vol | vol and the next earnings date |
| Contracts that can't claim | hold NOTE | hold the wrapper (`PerpWrapper`) |

## Contracts

| Contract | Interface | What it is |
|---|---|---|
| `PerpFactory` | `IPerpFactory` | creates series; same terms → same address |
| `PerpSeries` | `IPerpSeries` | one USDG escrow + NOTE + WRITER, no expiry |
| `PerpToken` | `ISeriesToken` (v1's) | NOTE or WRITER: an ERC-20 that tells its series before a transfer |
| `PerpQuoter` | `IPerpQuoter` | price = closed form + model + coupon reserve; view only |
| Stylus `PerpPricer` | `IPerpPricer` | the student `model/p1`: a signed correction in bps |
| `PerpFormulaPricer` | `IPerpPricer` | no student: the closed form plus the fixing accrual; refuses next to the barriers before a fixing |
| `PerpDesk` | `IPerpDesk` + `IDeskQueue` (v1's) | the market for both legs; ERC-4626 vault |
| `PerpWrapper` | (its own ABI) | auto-compounding wrapper of one leg |

## Units

| Quantity | Unit |
|---|---|
| NOTE, WRITER, USDG amounts | base units, 6 decimals |
| `notionalPerToken`, `noteIndex`, `writerIndex` | 1e27 fixed point |
| notional of `n` tokens | `n × notionalPerToken / 1e27` USDG (`series.notionalOf(n)`) |
| `priceBps`, spreads, fees | bps **of live notional**, not of a token |
| `couponReserve` | USDG base units per 1e6 of notional (235,000 = 23.5%); a multiple of 100 |
| `meltShare` | 1e18 fixed point (18,995,352,771,274,247 = 1.8995% per fixing) |
| wrapper shares, Desk shares | 12 decimals |

**Show prices per token.** Per-token price = `priceBps / 1e4 × notionalPerToken / 1e27`.
A NOTE bought at the start is not worth less after ten fixings because the price fell: it
stands for 17% less notional, and that 17% was paid out.

## The note in four lines

1. The first fixing sets the reference `H`. From then on, at every fixing `f`:
2. `f ≥ H` → `H = f`, the note is clean. Else `f < k × H` → the note is knocked in.
3. A share `a` of everything is paid out. Per unit of notional: NOTE gets `a × (p + R)`,
   WRITER gets `a × (1 − p)`, with `p` = 1 if clean and `f / H` if knocked in.
4. Nothing settles. A knocked-in note becomes clean again at the first fixing at or above `H`.

`series.state()` → `phase` (0 Pending, 1 Live, 2 Closed), `referenceFixing`, `lastFixing`,
`knockedIn`, `missedInARow`, `fixingsDone`, `nextFixing`, `notionalPerToken`, `noteIndex`,
`writerIndex`. It includes fixings that are recorded but not yet processed.

## Screens and the calls behind them

**Market list.** `desk.listedSeries()` → for each: `desk.listing(s)` (active, pricer, vol,
nextEarnings, capNotional), `series.terms()`, `series.state()`,
`quoter.quote(s, listing.pricer, listing.volBpsAnnual, listing.nextEarnings)`.
When a quote reverts, show why (see Errors) instead of hiding the series.

**Note detail.** Everything above, plus:
- `quoter.quote(...)` returns the parts: `formulaBps`, `correctionBps`, `couponBps`,
  `priceBps`, `weightsHash`. Show them: "formula 7,846 + model −12 + reserve 2,350".
- `quoter.inputs(s, vol, nextEarnings)`: exactly what the model saw (5 numbers: spot in
  bps of the reference, vol, seconds to the next fixing, fixings before the next earnings
  release, knocked in or not).
- `series.pendingFixing()`.
- History: `FixingProcessed(index, fixingTime, fixing, referenceFixing, knockedIn, missed,
  notionalPerToken, noteRelease, writerRelease)` on the series. `noteRelease` × a holder's
  balance at that fixing ÷ 1e27 is what that fixing paid it.
- The model card: `listing.pricer`, its `weightsHash()`, `product()` (knock-in, melt share,
  interval, drift, discount) and `certifiedRange(0..4)`. `pricer.correctionBps(inputs)` is
  the model alone; `pricer.answer(inputs)` is what the quoter calls (the correction, the
  product and the hash in one call).

**My position.** `note.balanceOf(me)`, `writer.balanceOf(me)`, `series.claimable(me)` (USDG
released to both legs and not yet claimed). **Claim:** `series.claim(to)`, no approval.

**Buy NOTE.**
1. `desk.quote(s, 0 /* BuyNote */, amount, feeBps)` → `(cost, priceBps)`.
2. `USDG.approve(desk, maxCost)`.
3. `desk.buy(s, amount, maxCost, feeBps, feeReceiver, to)`.

`amount` is in tokens. To buy for a USDG budget `X`: `amount = X × 1e27 × 1e4 /
(notionalPerToken × priceBps)`, rounded down. Sides for `desk.quote`: 0 BuyNote, 1 SellNote,
2 BuyCover, 3 SellCover.

**Sell NOTE.** `desk.quote(s, 1, amount, feeBps)` → `NOTE.approve(desk, amount)` →
`desk.sell(s, amount, minProceeds, feeBps, feeReceiver, to)`. Claim first or afterwards:
selling does not forfeit what earlier fixings released.

**Hedger: buy cover.** WRITER is crash cover: at every fixing of a knocked-in note it is paid
`a × (1 − f / H)`. `desk.quote(s, 2, amount, feeBps)` → approve → `desk.buyCover(...)`.
Exit: `desk.quote(s, 3, ...)` → `WRITER.approve` → `desk.sellCover(...)`. A cover holder
calls `series.claim(to)` like everybody else.

**The two prices.** `desk.spread(s)` → `(bidBps, askBps, volBandBps)`;
`pairBps = (1e6 + couponReserve) / 100`. Same rule as v1 (interfaces.md, "Cover and the two
prices") with `pairBps` in place of `maxBps`.

**Mint a pair without the Desk.** `series.previewMint(amount)` → `USDG.approve(series, …)` →
`series.mint(amount, to)`; `series.redeemPair(amount, to)` at any time.

**LP.** Unchanged from v1: ERC-4626 on the Desk plus the queue (`IDeskQueue`). Check
`maxDeposit` / `maxWithdraw` first; offer `requestRedeem` when they are 0.
`desk.navPriceBps(series)` is the mark behind the share price (the live feed only);
`desk.midPriceBps(series)` the one behind the risk budget (the weekend price included).

**Wrapper.** For a contract that will hold NOTE and never claim. `NOTE.approve(wrapper,
amount)` → `wrapper.wrap(amount, to)` → shares. `wrapper.unwrap(shares, to)` → `(tokens,
cash)`. `wrapper.previewUnwrap(shares)` shows what a share stands for; `wrapper.compound()`
is the keeper button. `wrap` reverts `CashPending` while the Desk can't reinvest.
`wrapper.exitTokens(shares, to)` returns the tokens alone: offer it when `unwrap` reverts
because USDG is paused or frozen.

**Weekends.** From Saturday 01:00 to Monday 00:00 UTC a feed that hasn't updated since the
window began is blind. `desk.spotOf(series)` → `(spot, weekendPrice)`: with a weekend
source set for the feed, trades go at its capped price with a wider spread
(`weekendPrice` = true: say so next to the quote); without one, quotes revert `FeedStale`.
LP deposits and withdrawals wait for Monday if the Desk holds a position on that feed
(`maxDeposit` / `maxWithdraw` are 0; the queue still takes requests).

**Keeper (anyone, could be a button).** For each fixing time that has passed: find the round
(the last round at or before the fixing time) → `recorder.recordFixing(time, roundId)` →
`series.advance()`. Then `wrapper.compound()` and `desk.collect(series)`.

**Curator.** `desk.listSeries(series, pricer, vol, nextEarnings, capNotional)`,
`setSpread`, `setRiskBudget(feed, bps)`, `setEarnings(series, nextEarnings)` after every
earnings release, `setWeekend(feed, source, capBps, spreadBps)`, `setMinSecsToFixing`.

> **The earnings date must be kept current.** Once `listing.nextEarnings` has passed, every
> quote of that series reverts `EarningsDatePassed` and LP flows pause until the curator
> calls `setEarnings`. Show the date, and a warning when it is less than a day away.

> **Check `desk.risk(feed)` and `desk.queuedShares()` before offering a trade**, as in v1:
> quotes apply neither.

## Events to index

| Contract | Event | Use |
|---|---|---|
| PerpFactory | `PerpSeriesCreated(seriesId, feed, series, note, writer, terms)` | series list |
| Recorder | `FixingRecorded(obsTime, roundId, price, timestamp)` | price path |
| Series | `Struck`, `FixingProcessed`, `Closed` | timeline, releases per token |
| Series | `Minted`, `PairRedeemed`, `Claimed(holder, to, amount)` | positions, cash received |
| NOTE, WRITER | ERC-20 `Transfer` | balances (needed to attribute releases to holders) |
| PerpDesk | `Traded(series, trader, side, to, amount, notional, priceBps, paid, feeBps, feeReceiver, weightsHash)` | trade history, one event for all four trades |
| PerpDesk | `SeriesListed`, `SeriesDelisted`, `EarningsSet`, `SpreadSet`, `RiskBudgetSet`, `WeekendSet`, `Collected` | listing state |
| PerpDesk | `RedeemRequested`, `RedeemFilled`, `RedeemCancelled`, `RedeemClaimed`, ERC-4626 `Deposit` / `Withdraw` | LP views |
| PerpWrapper | `Wrapped`, `Unwrapped`, `Compounded(cashIn, tokensBought)` | wrapper ratio over time |

## Errors worth a human message

Merge the ABIs when decoding: the Desk bubbles up errors from the quoter, the pricer and
the formula library.

| Error | Show |
|---|---|
| `FeedStale(updatedAt)` | "Market closed: quotes resume when the feed updates" |
| `FixingPending(fixingTime)` | "Fixing at … awaiting its price": offer the keeper button |
| `TooCloseToFixing(fixingTime)` | "Trading pauses shortly before each fixing" |
| `EarningsDatePassed(nextEarnings)` | "Waiting for the desk to set the next earnings date" |
| `OutOfRange(field, value)` | "Outside the model's certified range (field …)"; `pricer.certifiedRange(field)` gives the range |
| `Uncertified(region)` | `model/p1`: region 0 "too close to the knock-in barrier in the 6 hours before a fixing" (spot 50–70% of the reference, not knocked in), region 1 "too close to the reference in the 6 hours before a fixing" (spot 95–105%, knocked in): the two places the value jumps at a fixing. A Desk with `minSecsToFixing` ≥ 6 hours never shows them |
| `ProductMismatch(field)` | the listing names a model for other terms: a curator error |
| `VolOutOfRange(sigma)` | the closed form can't carry a vol this low |
| `NotLive()` | not struck yet, or closed |
| `RiskBudgetExceeded(atRisk, limit)`, `CapExceeded(requested, available)`, `QueuePending(queuedShares)`, `ReservedForClaims()` | as in v1 |
| `CashPending(cash)` (wrapper) | "The wrapper is waiting to reinvest: try again when the market is open" |
| `SeriesClosed()` | "This series has ended; claim what it released" |

## When a series closes

Four fixings in a row with no recorded price (a dead or deprecated feed, a delisted stock)
close the series: the fourth releases everything at the last good fixing under the normal
rule. `state().phase` = 2, `notionalPerToken` = 0. Show "ended" and the claim button:
`series.claimable(me)` is all that is left; the tokens themselves are worth nothing.

## What is deliberately not here

No merge of series that have reached the same state, no handling of stock splits (the
feed's adjustment policy is unverified), no pool adapter for the weekend price
(`IWeekendSource` is an interface and a test mock: pool depth on Robinhood Chain is
unchecked), no NOTE collateral oracle, no router or permit.
