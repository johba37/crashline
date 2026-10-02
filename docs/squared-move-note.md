# Roadmap: a note that pays the squared weekly move

Status: idea, 2026-10-02. Nothing is built: no contract, no model, no gas number. Every
figure is from `ml/squared_note_check.py` (log `ml/squared_note_check.log`), in the P1
teacher's world (TSLA's jumps and earnings moves, payouts discounted at 0) or on TSLA's
weekly closes. The last section before "Reproduce" lists other rules that fit the same
frame, with the demand they have shown in traditional finance.

## Why

The v2 note ([v2-perpetual-note.md](v2-perpetual-note.md)) pays on one event: a fall of 40%
from the high. Until then nothing is paid to WRITER, and a rise does nothing at all. Squeeth
was the opposite: something happens on every move, in both directions.

The v2 frame takes any rule that splits a slice between the two tokens. This document swaps
the knock-in rule for "the week's move, squared" and asks what that does to the coupon and
to the model.

## The rule

Same frame as v2: a pair locks `1 + R` per unit of live notional, and every weekly fixing
releases a share `a` of it. Only the split changes. With `f` the fixing and `f0` the one
before:

```
d² = (f − f0)² / (f · f0)        the week's move, squared; up 25% and down 20% count the same
q  = min(1, d² / m²)             m = the cap: with m 20%, a week of −18.1% or +22.1% pays the whole slice
WRITER gets a · q                NOTE gets a · (1 − q + R)
```

There is no reference price and no knocked-in flag. The only state is the last fixing.

One fixing, per 1,000 USDG of live notional, with `a` 1.9% (as P1), cap `m` 20% and `R`
0.139 (fair at vol 55%). The slice is 19.00 and the coupon 2.64:

| Week's move | WRITER gets | NOTE gets |
|---|---:|---:|
| 0% | 0.00 | 21.63 |
| +5% | 1.13 | 20.50 |
| −5% | 1.25 | 20.38 |
| +10% | 4.32 | 17.32 |
| −10% | 5.28 | 16.36 |
| +20% | 15.83 | 5.80 |
| −20%, or +25% (both past the cap) | 19.00 | 2.64 |
| any larger move | 19.00 | 2.64 |

- NOTE is paid for calm weeks and charged for movement, whichever way. It is what
  Squeeth's Crab vault produced, without the hedging: the rule pays the squared move
  itself.
- WRITER is paid for movement. It is close to insurance against impermanent loss: a 50/50
  pool position loses about `d²/8` over the same week (algebra only, not simulated; it fits
  a position re-centred weekly, not a passive one).
- The most a week can cost NOTE is the slice: 1.9% of the notional.

## Price

Weeks don't depend on each other, so the price is one week's expectation, repeated. Per
unit of live notional, right after a fixing:

```
WRITER = Q            NOTE = 1 + R − Q
Q = qN + (qE − qN) · a(1 − a)^n / (1 − (1 − a)^13)
```

`qN` and `qE` are the expected `q` of a normal week and of the week with the earnings
release, `n` the number of fixings before that week. Mid-week only the slice released next
knows about the move so far:

```
NOTE = 1 + R − [ a · E(q | move so far, time left) + (1 − a) · Q ]
```

- **The stock price drops out.** Right after a fixing the price depends on vol and the
  earnings calendar only. Mid-week it moves by at most `a`: 190 bps here.
- **Below the cap the price is the variance.** Without a cap, `E(q) = vol² · week / m²`
  (within 1%), with or without jumps. Fair `R = Q`, so the coupon is about
  `(a / m²) · vol²` per year.
- **The coupon stays outside**, exact, as in P1.
- **Nothing jumps.** The payout is continuous in the fixing, and the price does not step
  at a fixing.

## The coupon

Fair coupon in %/yr at vol 20–90%. "A" is `a` 1.9% with cap 20%, "B" is `a` 5% with cap 30%.

| vol | 20% | 30% | 40% | 55% | 70% | 90% |
|---|---:|---:|---:|---:|---:|---:|
| squared-move note A | 1.9 | 4.3 | 7.6 | 13.9 | 20.8 | 29.9 |
| squared-move note B | 2.2 | 5.0 | 8.9 | 16.8 | 26.8 | 42.3 |
| knock-in note P1 | 1.4 | 5.8 | 11.6 | 20.2 | 28.0 | 36.9 |
| part of the squared move the cap removes, A | 0% | 0.1% | 0.7% | 4.5% | 11.7% | 23.7% |
| the same, B | 0% | 0% | 0% | 0.4% | 1.9% | 6.7% |

A is `φ · R` as in the P1 write-up (average over the earnings cycle); B is the cash a position
kept at a constant size receives, `a · R · 365/7`. For A the two differ by 1%.

- **It follows vol squared.** From vol 20% to 30% the squared-move coupon grows 2.25 times,
  the knock-in coupon 4.3 times: a far barrier is almost never reached at low vol.
- **Earnings matter little.** The earnings week carries about twice a normal week's
  squared move (1.96 against 0.92 of an average week). Over the cycle A's fair coupon moves
  between 13.79% and 14.01% at vol 55%. P1's price differs by up to 204 bps depending on
  whether the release falls before or after the next fixing.
- **A fixed coupon is rarely right.** The coupon that would have broken even on TSLA ran
  from 6.0% (2017) to 31.4% (2020), median 11.6% (A's cap). As in v2, the price has to
  carry the difference.

### Two dials

The coupon is set by how much is at stake each week (`a`) and by the move that takes the
whole stake (`m`). At vol 55%:

| `a` per week | cap `m` | half the notional is back after | fair `R` | coupon per year at a constant size | cap removes |
|---:|---:|---:|---:|---:|---:|
| 1.9% | 15% | 36 weeks | 0.2236 | 22.2% | 13.5% |
| 1.9% (A) | 20% | 36 weeks | 0.1389 | 13.8% | 4.5% |
| 1.9% | 30% | 36 weeks | 0.0644 | 6.4% | 0.4% |
| 5% (B) | 30% | 13.5 weeks | 0.0644 | 16.8% | 0.4% |
| 10% | 45% | 6.6 weeks | 0.0287 | 15.0% | 0.0% |
| 25% | 70% | 2.4 weeks | 0.0119 | 15.5% | 0.0% |
| 50% | 100% | 1 week | 0.0058 | 15.2% | 0.0% |

- A near cap with a small `a` protects NOTE (it can lose 1.9% a week at most) and cuts the
  large moves out of WRITER's payout.
- A far cap with a large `a` is nearly pure variance, but the notional comes back fast: the
  coupon is only earned by a holder who reinvests every week, which is the wrapper's job
  and costs the Desk's spread each time.

## What the holder lives through

TSLA's weekly closes, cash per 1,000 USDG of notional held from the start of each year,
without reinvestment. Both coupons are fair at vol 59% in their own model (knock-in `R`
0.224, squared-move A `R` 0.157). What is still in the note at the year's end is not
marked, so a knocked-in note carries its loss into the next year.

| Year | TSLA vol | Knock-in note | weeks knocked in | Squared-move note A | its worst week | coupon that would have broken even |
|---|---:|---:|---:|---:|---:|---:|
| 2011 | 46% | +141 | 0 | +36 | −7 | 10.2% |
| 2012 | 47% | +141 | 0 | +22 | −10 | 10.4% |
| 2013 | 61% | +141 | 0 | +11 | −11 | 14.4% |
| 2014 | 43% | +141 | 0 | +41 | −8 | 8.7% |
| 2015 | 40% | +143 | 0 | +55 | −2 | 7.5% |
| 2016 | 44% | +3 | 48 | +31 | −9 | 9.2% |
| 2017 | 35% | +121 | 13 | +62 | −4 | 6.0% |
| 2018 | 56% | +141 | 0 | +17 | −7 | 14.1% |
| 2019 | 51% | +32 | 31 | +28 | −7 | 11.6% |
| 2020 | 91% | +103 | 12 | −111 | −14 | 31.4% |
| 2021 | 57% | +141 | 0 | 0 | −16 | 15.1% |
| 2022 | 63% | +6 | 33 | −16 | −6 | 19.2% |
| 2023 | 57% | −166 | 52 | +6 | −15 | 13.5% |
| 2024 | 63% | −155 | 49 | −3 | −10 | 17.2% |
| 2025 | 49% | +40 | 28 | +21 | −7 | 11.6% |
| 2026 (40 weeks) | 41% | +120 | 0 | +44 | −9 | 7.9% |

- **They fail in different years.** The knock-in note lost in 2023–24, the long stretch
  below the 2021 high. The squared-move note lost in 2020, a year of 91% vol with seven
  weeks beyond the cap, in which the knock-in note was knocked in for 12 weeks and healed.
- **Many small results instead of a rare large one.** The squared-move note never lost more
  than 16 in a week. The knock-in note was knocked in for 266 of 848 weeks.
- **The sums (+1,094 against +244) are not a ranking.** TSLA recovered from every fall,
  which a fair coupon does not assume.

In the model, at a constant vol of 55%, one year of A is worth ±18 around zero (1 in 20
years below −32, 1 in 100 below −47; worst of 200,000 years −102). 2020 was worse than all
of them, because vol does not stay constant.

## What it means for the model

Most of P1's work disappears.

| | Knock-in note (P1) | Squared-move note |
|---|---|---|
| State | reference, knocked-in flag | none |
| Teacher | fixed point over the earnings cycle, grid and solver | a closed form per week (a sum over the number of jumps) |
| Inputs | spot / reference, vol, time, earnings count, flag | move since the last fixing, vol, time, earnings count |
| Jumps in the price | 192–447 bps at the knock-in, 88–134 at the heal | none |
| Refused | last 6 hours near either barrier | nothing |
| What the formula misses | up to 270 bps, 13.5 on average, in five dimensions | the tail beyond the cap, a function of vol and the earnings count |

What a formula with one normal distribution per week (that week's variance, jumps and
earnings included) misses, in bps of live notional right after a fixing:

| vol | 20% | 30% | 40% | 55% | 70% | 90% |
|---|---:|---:|---:|---:|---:|---:|
| cap 15% | 0.0 | 2.9 | 22.7 | 90.4 | 159.3 | 213.0 |
| cap 20% (A) | 0.0 | 0.1 | 2.9 | 27.0 | 77.7 | 149.1 |
| cap 30% (B) | 0.0 | 0.0 | 0.0 | 1.2 | 9.7 | 41.9 |
| cap 50% | 0.0 | 0.0 | 0.0 | 0.1 | 0.1 | 0.8 |

Mid-week the same formula is within 3.9 bps (A) and 6.9 bps (B) at every vol, any move so
far up to ±40% and with an earnings release ahead.

- **With a cap of 30% no network is needed up to vol 70%**: the formula is within 10 bps.
- **With a cap of 20% the gap is real but small in shape**: two curves over vol (`qN`,
  `qE`), which a table in the pricer can hold. The exact sum is also an option in Stylus.
- **The formula needs the normal cdf on chain**, six per quote. `PerpMath` has `exp` and
  `ln` only.
- **Vol has to be right.** The quotes at vol − 2 and vol + 2 points are 180 bps apart at
  vol 55% (A), 93 (B); P1: 121 on average. Nothing else moves the price much.
- **The tail is the one modelled part, and history agrees with it.** Over the teacher's fit
  window the realized break-even coupon at cap 20% was 14.7% ± 1.0; the teacher says 15.2%
  at that vol, a smooth walk 15.8%. At cap 30%: 7.17% ± 0.57 against 7.14%.

## Reading vol from the price

`Q = (1 + R) − NOTE price`, and without a cap `vol² = Q · m² / week`. Reading vol that way,
with no model:

| true vol | 20% | 30% | 40% | 55% | 70% | 90% |
|---|---:|---:|---:|---:|---:|---:|
| cap 20% | 20.0 | 30.0 | 39.9 | 53.8 | 65.9 | 78.9 |
| cap 30% | 20.0 | 30.0 | 40.0 | 55.0 | 69.5 | 87.3 |
| cap 50% | 20.0 | 30.0 | 40.0 | 55.1 | 70.2 | 90.2 |

It is the average expected over the note's life (about a year for A, 20 weeks for B), not
this week's vol. It needs a NOTE market that does not take its price from the Desk.

## What would change in the contracts

- **`PerpPayout`:** the rule above; terms carry `m²` instead of `kiBarrierBps`; the state
  loses the reference and the flag. Series, factory, tokens, indexes and wrapper keep
  their shape.
- **Pricer:** the formula with a normal cdf, plus a table or the exact sum for the tail.
- **Quoter:** "move since the last fixing" replaces "spot over reference".
- **Desk:** its risk bound becomes `a` per week on the NOTE it holds.
- **A stock split** costs NOTE one slice (it reads as a move beyond the cap) and then
  passes. In v2 it knocks every holder in.
- **Pushing a fixing price** pays in proportion and never more than a slice: there is no
  barrier to push it across.

## The other reading: the price level, squared

Squeeth's own index is the level squared: the slice would pay `(f / H)²` with `H` fixed.

- **Value** in a smooth walk without a cap: `x² · a·g / (1 − (1 − a)·g)`, `g = e^((2r + vol²)·week)`.
  That is 1.14 `x²` at vol 20%, 1.63 at 55%, 9.2 at 90%, and infinite from vol 96%.
- **It is a leveraged long position** (about twice the stock's move), so the other side
  is a leveraged short, not a coupon note. The fixed `H` also goes stale as the stock
  moves away.

Not pursued here.

## Limits and open items

1. **Which dials.** B if the aim is a price that needs no network and gives a clean vol
   reading; A to keep P1's melt and a NOTE that cannot lose more than 1.9% a week.
2. **Missed fixings.** A missed week pays `q = 0`, and the next recorded fixing sees a move
   of several weeks under one cap. A series that closes pays NOTE in full. Both favour
   NOTE; not decided.
3. **Constant vol.** Vol by calendar year ran from 35% to 91%, and a large week tends to
   follow a large week (correlation 0.15). The bad years are worse than the model's.
4. **No risk premium.** "Fair" is break-even in the model. Sellers of movement are
   normally paid more than that.
5. **Trading costs.** A week's expected payout is 26 bps of notional (A). That is the size
   of the Desk's spread (20–30 bps in the test setup) and far below the 180 bps between its
   two vol quotes. Through the Desk this is a position for months, not a bet on one
   earnings week.
6. **Weekly sampling.** A round trip inside a week pays nothing. Daily fixings would track
   movement more closely, but "four missed fixings close the series" would then trigger on
   a long market closure.
7. **One stock.** The tail is TSLA's.
8. **Regulation.** This is a variance swap. The CFTC's 2023 order on Squeeth is the
   precedent to read; not assessed here.

## Extended research: other rules from traditional finance

Added 2026-10-02. Which other rules fit the frame and have shown demand elsewhere. The
demand figures are from a web search on that day and are checked no further than the linked
pages. Only the put-write and range numbers are computed (section (12) of the log); nothing
else here is priced.

### The building blocks

The frame takes any rule that splits a slice between the two tokens. Most retail structured
products are one choice from each line:

- **Shape of the split:** a straight line between two price levels, on/off, or squared.
- **Reference price:** fixed, the running high (v2), or the last fixing.
- **Memory:** none, a knock-in flag (v2), or a knock-out.
- **What is at risk:** the principal (v2) or only the coupon.
- **Feed:** one stock, the worst of several, or a ratio of two.

What does not fit is a payout without a bound, such as an uncapped long position.

### Products that fit

| Product | Evidence of demand | Rule in the frame | State and model |
|---|---|---|---|
| Autocallable, barrier reverse convertible | US structured notes above $195bn in 2025, over 43% of it autocallables ([SRP][srp]); Korea 96.7tn won outstanding ([Bloomingbit][kr]) | built (v1, v2). Missing variants: worst of a basket, a coupon paid only above a level | worst-of needs several feeds and how they move together |
| Covered-call or put-write income | JEPI about $45bn ([DividendVision][cc]); TSLY, on TSLA alone, about $0.67bn ([Yahoo][tsly]) | NOTE loses the week's fall and gains nothing from a rise | none; closed form |
| Buffer | buffer ETFs about $87bn ([ETF Database][buf]); buffered annuities $79.6bn sold in 2025 ([LIMRA][limra]) | NOTE loses only the part of a fall beyond, say, 20% of the reference: no cliff | reference only, no flag; the price is continuous |
| Principal-protected with capped upside | fixed indexed annuities $128.2bn sold in 2025 ([LIMRA][limra]) | the principal always goes to NOTE; the rule splits the coupon reserve by the stock's rise | fits: the rule splits the whole release, `a · (1 + R)` |
| Range accrual | a staple of rate and FX notes; no figure checked | the coupon is paid only in weeks the stock stays inside a band | none; closed form; a cliff at the band's edge |
| Accumulator ("buy at a discount every week") | common in Asian private banking; no figure checked | each weekly slice buys stock below the reference | needs a payout in stock tokens |
| Leveraged long/short pair | large single-stock leveraged ETFs; no figure checked | the slice is split by the week's return | fits, but perps on the chain already do this |

Crypto exchanges sell the same shapes as Dual Investment (put-write), Snowball
(autocallable) and Shark Fin (principal-protected) ([Gate][sf]). No volume figures found.

[srp]: https://www.structuredretailproducts.com/insights/83579/autocallable-etfs-a-new-structured-payoff-design
[kr]: https://en.bloomingbit.io/feed/news/121435
[cc]: https://www.dividendvision.com/best/covered-call-etfs
[tsly]: https://finance.yahoo.com/quote/TSLY/
[buf]: https://etfdb.com/news/2025/12/15/buffer-etfs-claim-place-advisor-portfolios/
[limra]: https://www.limra.com/en/newsroom/news-releases/2026/limra-u.s.-retail-annuity-sales-top-$460-billion-in-2025-marking-fourth-year-of-record-sales/
[sf]: https://www.gate.com/learn/articles/what-is-a-sharkfin-structured-product/307

### Which to look at first

1. **Covered call on the running high.** It exists: v2 with the knock-in at 100% of the
   reference, which the factory allows (`kiBarrierBps` up to 10,000). Every fixing below
   the reference then pays `fixing / reference`. `model/p1` is trained for 60% only, so it
   needs its own model or a check of the formula at that setting. Not priced.
2. **Buffer.** One changed line in the v2 rule: pay `min(1, fixing / reference + buffer)`.
   It removes the cliff at the barrier, the source of P1's price jumps and refused quotes.
   The coupon will be lower than the knock-in note's. Not priced.
3. **Weekly put-write.** The one-sided sibling of the squared-move note, with the same
   on-chain maths (the normal cdf). Its coupon follows vol, not vol squared:

   | vol | 20% | 30% | 40% | 55% | 70% | 90% |
   |---|---:|---:|---:|---:|---:|---:|
   | expected loss, % of the slice | 1.03 | 1.57 | 2.11 | 2.91 | 3.72 | 4.79 |
   | coupon per year, 1.9% at stake per week | 1.0% | 1.6% | 2.1% | 2.9% | 3.7% | 4.7% |
   | coupon per year, 25% at stake per week | 13.5% | 20.5% | 27.5% | 37.9% | 48.4% | 62.4% |

   It only makes sense with a large weekly slice.
4. **Range accrual.** A week moves more than 10% with a chance of 0.2% at vol 20%, 17.4%
   at vol 55% and 38.9% at vol 90%, so a ±10% band pays the coupon in about five weeks of
   six at vol 55%. The on/off payout brings back a cliff at the band's edge.
5. **Principal-protected.** It opens another group of buyers: those who will not risk the
   principal. Elsewhere the upside is paid from the interest on the principal, and USDG in
   escrow earns none. It depends on [roadmap-usdg-yield.md](roadmap-usdg-yield.md).

The worst-of basket is where the largest coupons come from, and it is the most work:
several feeds, and a model of how the stocks move together.

## Reproduce

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/ai/cache/venv-cuda/bin/python ml/squared_note_check.py > ml/squared_note_check.log   # ~20 s
```

Section (1) of the log checks the closed form against a simulation of the week from its
parts (six states, 2^22 weeks each, max |z| 2.17).
