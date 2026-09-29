# Roadmap: v2 perpetual note

Status: roadmap, 2026-09-29. Not for the Oct 4 submission; v1 interfaces stay frozen.
Numbers below: `ml/perp_note_check.py` (~1 min, numpy only).

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
