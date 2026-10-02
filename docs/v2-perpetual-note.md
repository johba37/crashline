# v2 perpetual note

Status: design of 2026-09-29, **built 2026-10-02 next to v1** (v1's contracts, interfaces
and ABIs are untouched; not part of the Oct 4 submission). What was built, what was
decided on the way and what the open items turned out to be: ["Implementation"](#implementation-2026-10-02)
at the end. Exact rules: [v2-spec.md](v2-spec.md). The numbers in the design sections
below are the original ones: `ml/perp_note_check.py` (~1 min, numpy only).

## Why

v1's calendar (strike date, maturity, counted observations) causes: a new series every
week, a ~229 bps autocall jump no student can fit, 10 model inputs, and capital that
expires. Removing the calendar removes all four, and the price gets a formula.

The cost is the accounting: v1 settles once and redeems by burning; v2 pays out every
week to two transferable tokens (see "Holders"). The lifecycle gets simpler, the
contract doesn't.

## The note

Terms: `feed`, fixing interval (weekly), knock-in `k`, melt share per fixing
`a = 1 − e^(−φ·interval)`, coupon reserve `R = c/φ` (coupon rate `c`, melt rate `φ`).
`a` and `R` are stored as integers, so the contract needs no `exp`.
State: reference `H`, `knockedIn`. One NOTE and one WRITER per terms, no expiry.
The first fixing sets `H`, so a short Pending phase remains.

At each fixing, with `x = fixing / H`:

1. `x ≥ 1` → `H = fixing`, `knockedIn = false` (autocall moves the reference; nothing is redeemed)
2. else `x < k` → `knockedIn = true`
3. release: a share `a` of the whole pool pays out. Per unit of live notional, NOTE gets
   `a·(p + R)` and WRITER gets `a·(1 − p)`, with `p = 1` if clean, `x` if knocked in.

Pair mint locks `1 + R` per unit of live notional, and every fixing releases the same
share `a` of everything, so full collateralization is structural: no liquidations, no
top-ups. Paying the coupon as `c·interval` instead would not be: that is ~1% more than
`a·R` per fixing and leaves the last redeemers ~0.2% of the pool short (c 23.5%/yr, weekly).

The melt is the construction of Paradigm's
[Everlasting Options](https://www.paradigm.xyz/2021/05/everlasting-options) (White and
Bankman-Fried, 2021): a perpetual as a portfolio of expiries weighted ½, ¼, ⅛, …; here
the weights are `a·(1 − a)^n` per fixing. The maturity is memoryless, which is why time
drops out of the price below.

## Holders

- Released cash is pulled, not pushed: a cumulative index per token (NOTE and WRITER),
  checkpointed on every transfer, as in Pendle's
  [YT](https://docs.pendle.finance/pendle-v2/ProtocolMechanics/YieldTokenization/YT). v1 needs
  none of this.
- Composability cost: a contract that holds NOTE but never claims (an AMM pool, a
  lending market) strands its cash. Integrations need an auto-compounding wrapper, as
  wstETH is for stETH. v1 NOTE carries all its value inside the token.
- Per-token notional shrinks by `(1 − a)` per fixing, to 0.005% after 10 years at
  φ 1/yr. Prices are per token; indexes need a 1e27 fixed point.

## Ending a perpetual

v1 gets its end for free from maturity; v2 needs a rule for:

- feed deprecated, or stale for N fixings, or the stock delisted: the series closes at
  the last good fixing and releases everything (`a = 1`) under the normal rule.
- stock splits: a 3:1 split reads as `x = 0.33` and knocks everyone in. Handling depends
  on the feed's adjustment policy for stock tokens: not checked. v1 has the same risk,
  but only until maturity; a perpetual will meet a corporate action.

## Liquidity

- One NOTE/WRITER per stock and terms, instead of one series per week (26 live vintages
  per product at a 26-week tenor).
- One coupon per stock is enough: when vol moves, the price moves, not the terms.
- Shared state: after a crash every holder is knocked in until the stock regains `H`
  (TSLA took ~3 years to regain its 2021 high). Demand for a fresh note then needs a new
  series, so fragmentation returns in drawdowns, when volume is highest.
- Series with the same terms reach an identical state at every new high (same `H`,
  clean). They could then merge at the ratio of their per-token notional; v1 vintages
  never converge.

## Price

Smooth random walk, constant vol `σ`, rate `r` (the world of `ml/teacher.py`). Time
drops out; the price depends on `x` only:

```
clean       V0(x) = (c+φ)/(r+φ) + A·x^β₊ + B·x^β₋
knocked in  V1(x) = c/(r+φ) + x + C·x^β₊
β±          roots of ½σ²β(β−1) + rβ − (r+φ) = 0
A, B, C     from V0'(top) = 0,  V0(k') = V1(k'),  V0(top) = V1(top)
weekly fixings:  top = e^(0.5826·σ·√Δt),  k' = k / top      (continuous: top = 1, k' = k)
```

The weekly-fixing shift is the continuity correction of Broadie, Glasserman and Kou:
[1997](https://onlinelibrary.wiley.com/doi/abs/10.1111/1467-9965.00035) for the knock-in
barrier (*Mathematical Finance* 7(4)), and their 1999 extension to lookbacks for the
ratchet (*Connecting discrete and continuous path-dependent options*, *Finance and
Stochastics* 3). 0.5826 = −ζ(½)/√(2π).

WRITER = `1 + c/φ` − NOTE. Fair coupon: the `c` with `V0(1) = 1` (linear in `c`).

Check (vol 55%, k 60%, r 4%, φ 1/yr, 12 states right after a fixing, MC stderr ≤ 3 bps):

| | Error vs Monte Carlo |
|---|---|
| Formula, as monitoring gets finer (weekly → daily → 6-hourly) | 170 → 80 → 42 bps |
| Formula with the weekly-fixing shift | 5–51 bps, always high |
| v1 student, for comparison | ~135 bps worst case outside the band |

Fair coupon from the shifted formula, k 60%: 9.5 / 23.5 / 35.7 %/yr at vol 30 / 55 / 80%.

## The student

On-chain price = formula + student. The student learns only what the formula gets wrong:

| Gap | Source | Size |
|---|---|---|
| 1. Contract rules | weekly fixings, 24/5 feed | tens of bps (measured above) |
| 2. Stock behavior | jumps, changing vol, earnings | not measured; needs a richer teacher |

- Inputs: `x`, vol, `knockedIn`, time to next fixing, time to next earnings. These are
  positions in a repeating cycle, not countdowns to an end.
- `k`, `c`, `φ` and the teacher's jump constants are pinned per `weightsHash`. Vol stays
  the only live pricing input. Jump constants come from long off-chain stock history,
  checked against listed option prices (on-chain history starts ~June 2026: too short).
- The note knows nothing about earnings. The next earnings date is a Desk parameter,
  like vol: it changes price, never payout.

## Weekend on-chain price

Desk only, as a price level while the feed is blind (Fri 20:00 → Sun 20:00 ET):
time-averaged, capped against the last feed price, wider spread. Never for fixings, and
it says nothing about crash odds. Pool depth on Robinhood Chain: not checked.

## What changes

| Carries over | Changes |
|---|---|
| FixingsRecorder, pair mint, per-series escrow, Desk, fail-closed quoter, certified domain | `SeriesTerms`: drop `strikeTime`, `observationCount`, `acBarrierBps` (the ratchet is at 100%); add melt share `a` and coupon reserve `R` |
| `weightsHash` versioning, export format, Stylus engine | Quoter calls formula + student; `PricerInputs` shrinks |
| Payout rules normative for the teacher | Series never settles; per-holder indexes replace `settle` / `redeem`; a termination rule; a wrapper for integrations |

## Open

1. Only one parameter set and states right after a fixing are tested. Mid-week is not.
2. Jumps at a fixing, not measured: knock-in (`V0 → V1` at `x = k`) and heal (`V1 → V0`
   at `x = 1`, a knocked-in note). For a clean note the ratchet is only a kink.
3. Gas and rounding of `x^β` in fixed point: not measured.
4. Pitch: the formula alone (5–51 bps) already beats the v1 student. With gap 1 only,
   the net corrects tens of bps and is hard to justify; the surrogate's claim moves to
   gap 2, which needs a new teacher.
5. `c` is pinned per `weightsHash` and the fair `c` differs by vol, so one model per
   stock, unless `c` becomes a student input.
6. Termination and split handling: only the rule above; not designed further.

## Implementation (2026-10-02)

Rules and units: [v2-spec.md](v2-spec.md). Frontend guide: [interfaces-v2.md](interfaces-v2.md).
Self-review: [v2-contracts-review.md](v2-contracts-review.md). Teacher and student:
[p1-perp-student.md](p1-perp-student.md).

| Section above | Built | Where |
|---|---|---|
| The note | terms, the fixing rule, pair mint, structural collateral; 172 vectors exact against a Python reference, invariants | `PerpSeries`, `PerpPayout`, `PerpFactory`, `tools/perp_vectors.py` |
| Holders | a cumulative index per token, settled for both holders before every transfer; `claim(to)`; the wrapper | `PerpSeries`, `PerpToken`, `PerpWrapper` |
| Ending a perpetual | a fixing with no price reuses the last one; four in a row close the series with a full release at the last good fixing. Splits: not handled | `PerpSeries` |
| Liquidity | one NOTE/WRITER per terms and first fixing. The merge of series in the same state: not built | |
| Price | the closed form in 1e18 fixed point, with the weekly-fixing shift | `PerpFormula`, `PerpMath`, `tools/perp_formula.py` |
| The student | on-chain price = formula + student + coupon reserve; `model/p1` | `PerpQuoter`, Stylus `PerpPricer`, `ml/teacher_perp.py` |
| Weekend on-chain price | the Desk's logic (window, cap, wider spread, never for fixings or LP flows) against an interface; no pool adapter | `PerpDesk`, `IWeekendSource` |
| What changes | `PerpTerms`, the shrunken `PerpPricerInputs`, per-holder indexes, the termination rule, the wrapper; FixingsRecorder shared with v1; the Desk and the export format carried over | `contracts/src/Perp*.sol`, `stylus/pricer-model` |

End to end on a Nitro dev node with the Stylus student (`contracts/script/e2e-perp-devnode.sh`,
[log](../contracts/logs/e2e-perp-devnode-p1.log)): 281 forge tests pass (140 of them v1's).

### Decided while building

1. **`firstFixing` stays in the terms.** "Drop `strikeTime`" can't go all the way: the
   fixing grid needs an anchor, and a fresh series after a crash must differ from the old
   one in something. It no longer starts a countdown.
2. **The coupon is outside the model.** Every fixing pays `a × R` of what is left whatever
   the stock did, so the coupon part of NOTE is worth exactly R (at discount 0). The price
   is linear in R; the student prices the principal only.
3. **Discount rate 0, drift 4%**, teacher v3's convention, not this document's ρ = r = 4%.
   A pair redeems for `1 + R` at any time, so "WRITER = 1 + c/φ − NOTE" holds only if
   payouts are discounted at what the escrow earns. The formula takes both rates; with
   ρ = r it is the one above and reproduces the check table.
4. **The earnings input is a count**: fixings before the next release, not seconds to it.
   The value depends on the date only through that count, and between "before the next
   fixing" and "after it" the price steps by up to 204 bps.
5. **A missed fixing still melts.** It reuses the last fixing, so the notional per token
   depends only on how many fixing times have passed.
6. **Trades in tokens, prices per unit of notional**; one `Traded` event for all four trades.
7. **The coupon reserve is a whole number of bps.** Prices are in bps; with a finer
   reserve, buying both legs at the Desk would cost less than the pair redeems for.
8. **The formula-only pricer adds the fixing accrual** (the week's release builds up in the
   price and leaves it at the fixing) and refuses next to the barriers before a fixing.
   Without it the share price of a Desk stepped at every fixing, and an LP could time it.
9. **Friday's close is not a weekend price.** Inside the weekend window the Desk treats a
   feed that hasn't updated since the window began as blind, however recent its last round.

Items 7–9 came out of a second, adversarial review of the contracts
([v2-contracts-review.md](v2-contracts-review.md), section 10): eight confirmed defects in
the Desk, the wrapper and the formula-only pricer, none in the note core.

### Fair coupon under discount 0

The coupon with NOTE = 1 at x = 1. "Price" above solved V0(top) = 1 with ρ = r = 4%
(at x = 1 those are 9.54 / 23.62 / 35.92).

| vol | 20% | 30% | 40% | 55% | 70% | 80% | 90% |
|---|---|---|---|---|---|---|---|
| %/yr, formula, ρ = 0 | 1.36 | 5.83 | 11.59 | 20.22 | 27.99 | 32.65 | 36.93 |

The teacher (jumps, earnings) is within 0.5 points of these.

### The open items, answered

1. **Mid-week and other vols.** Right after a fixing, in a smooth random walk, the formula
   is off by +6 … +50 bps at vol 55% (the "5–51, always high" above), and by −37 … +64 over
   vol 20–90%: below 55% it is not always high. Mid-week it is off by up to 192 bps next to
   a barrier (177 in the teacher's world with jumps and earnings), 1–28 bps on average.
2. **Jumps at a fixing.** Knock-in at x = k: 192 bps (vol 90%) to 447 bps (vol 20%). Heal
   at x = 1: 88–134 bps. A clean note at x = 1 has a kink only (0.07 bps). The model
   refuses quotes in the last 6 hours before a fixing next to either barrier; v1's
   autocall jump needed a day.
3. **Gas and rounding of x^β.** One price is 27,476 gas (clean), 24,659 (knocked in). The
   fixed point is within 1.3e-7 bps of a 50-digit reference, 7e-11 bps from 20% vol up;
   below 8% vol (|β| > 25) the contract refuses. A whole Desk buy with a vol band is
   787,363 L2 gas with the student and 753,381 with the formula-only pricer, which
   evaluates the formula a second time for its accrual (v1 with k3: 775,042).
4. **Pitch.** Against the teacher with jumps and earnings, 330,554 states: the formula
   alone is off by up to 270 bps (p99 77, mean 13.5); formula + student by up to **20.9**
   (p99 2.2, mean 0.49), and 20.6 on a fresh set. With gap 1 alone the formula's worst is
   mid-week next to a barrier, not the tens of bps after a fixing: the net earns its place
   there too. The earnings date alone moves the price by 55–204 bps.
5. **One model per stock?** No longer for the coupon (decision 2). Still one per knock-in
   level, melt rate and set of jump constants.
6. **Termination**: built (four missed fixings). **Splits**: still open; the feed's
   adjustment policy for stock tokens is unverified.

### Not built

The merge of series that reached the same state; split handling; a pool adapter for the
weekend price (pool depth on Robinhood Chain is unchecked); v2 in the backend service;
a testnet deployment (`DeployPerp.s.sol` simulates cleanly, no deployer key is set).
