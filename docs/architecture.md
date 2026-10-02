# Architecture

Four layers. Each one works on its own, every position is an ERC-20, and the
model is only called by the Desk when pricing a trade. Settlement never
touches the model. Interfaces: [`contracts/src/interfaces/`](../contracts/src/interfaces/),
frontend guide: [interfaces.md](interfaces.md). The lessons in brackets come from the
research on Opyn, Pendle, Gnosis CTF, Siren and Cega (2026-09-29).

```
  buyer          LP          hedger                frontend / other protocols
    │             │             │                        │
    ▼             ▼             ▼ (buys cover, or mints)   ▼ (events + views)
┌─ L3  INTEGRATIONS ─ opt-in; all policy lives here ─────────────────────────────────────┐
│                                                                                        │
│ Desk  (ERC-4626: LPs deposit USDG, shares are an ERC-20)                               │
│   • listing per series: pricer (certified model per product) + vol + cap               │
│   • buy / sell NOTE and WRITER ("cover") at quote ± spread ± fee              [8]      │
│     (the pricer's only in-path use); WRITER = maxPayout − NOTE                         │
│   • quotes a curated grid only: 1 series / underlying / week                  [2]      │
│   • sells cover for the premium and keeps the NOTE; holds WRITER only up to            │
│     the listing's cap; a risk budget per stock limits both                    [8]      │
│   • ε-bands at barriers, rounds vs trader                                     [9]      │
│   • integrator fee, capped (NOTE: of notional, cover: of premium); half stays here     │
│   • IS the market: no AMM pools for NOTE                                      [6]      │
│                                                                                        │
│ NoteOracle  (roadmap)  AggregatorV3 price per NOTE for lenders                [5]      │
│ Treasury    (roadmap)  idle USDG in yield venues (roadmap-usdg-yield.md)               │
│ Router      (stretch)  permit + buy in one transaction                                 │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
        │ quote()                                                       │ mint · redeem
        ▼                                                               │
┌─ L2  QUOTER ─ view only ─────────────────────────────────────┐        │
│                                                              │        │
│ NoteQuoter                                                   │        │
│   • note state from series + recorder, never from caller     │        │
│   • stale feed / out-of-range input → revert                 │        │
│   • fee is a call parameter, emitted in NoteQuoted           │        │
│   • one quoter for all models; the Desk passes the pricer    │        │
│   calls: SurrogatePricer, FixingsRecorder, feed              │        │
│                                                              │        │
└──────────────────────────────────────────────────────────────┘        │
══ MODEL BOUNDARY: nothing below calls the pricer ══════════════════════╪═════════════════
                                                                        ▼
┌─ L1  CORE ─ no model · no admin · no pause · no upgrade ───────────────────────────────┐
│                                                                                        │
│ NoteFactory.createSeries(terms, strikeTime)                                            │
│   • CREATE2 address = f(termsHash): same terms → same series                  [2]      │
│   • only factory clones may mint / burn (provenance check)                    [1]      │
│                                                                                        │
│ Series k ─ its own USDG escrow; claims ≤ own collateral                       [1]      │
│   • opens once the strike fixing is recorded                                  [2]      │
│   • mint n       lock n × maxPayout → n NOTE + n WRITER                                │
│   • redeemPair   burn both → collateral back, any time                                 │
│   • settle()     once; stores payoutPerUnit; moves no tokens                  [3][4]   │
│   • redeem(to)   pull; NOTE rounds down, WRITER = max − NOTE                  [3][4]   │
│   • mint after settle reverts; burn first; nonReentrant                       [3]      │
│   calls: FixingsRecorder, AutocallPayout                                               │
│                                                                                        │
│ NOTE   (ERC-20)  par + accrued coupons, or the loss if knocked in             [7]      │
│ WRITER (ERC-20)  maxPayout − NOTE: long knock-in put, short coupons           [7][8]   │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
                              ▼
┌─ L0  PRIMITIVES ─ pure or permissionless ──────────────────────────────────────────────┐
│                                                                                        │
│ SurrogatePricer (Stylus)  FixingsRecorder             AutocallPayout (library)         │
│   pure, w16a16, ~45k gas    permissionless, checked     pure: fixings → payout,        │
│   weightsHash pinned        against feed rounds;        same rules as teacher,         │
│   certified domain only     pause = staleness [!]       shared test vectors            │
│   one per product, any stock                                                           │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
  feed:  Chainlink RHTSLA/USD, 24/5 → FixingsRecorder, NoteQuoter
  cash:  USDG, 6 decimals; the issuer can freeze, wipe and pause               [4]
```

## Why each rule is there

| Tag | Rule | Precedent |
|---|---|---|
| [1] | Each series has its own USDG escrow, and only factory clones may mint or burn | Opyn Gamma audit C01 (fake oToken redeems from the shared pool); Ribbon/Aevo legacy vaults drained for $2.7M through the shared MarginPool, Dec 2025 |
| [2] | Same terms → same series (CREATE2); a series opens at its strike fixing; the Desk quotes a small standard grid | Opyn fixed expiry at 08:00 UTC against fragmentation; Pendle subsidizes each maturity with incentives |
| [3] | `settle` flips once and stores the payout; NOTE rounds down, WRITER = max − NOTE; no mint after settlement; burn before transfer | Gnosis CTF audit (unbounded minting after resolution); Opyn v1 `msg.value` reuse in a loop ($371k) |
| [4] | Pull-based redemption to any `to`; `settle` moves no tokens | USDG's issuer can freeze, wipe and pause (6 decimals, UUPS, no transfer fee) |
| [5] | No lending-collateral oracle for NOTE yet | Pendle PT-reUSD: a $320k trade → 3% move → $36.4M of Morpho liquidations (Aug 2026) |
| [6] | The Desk is the market; no AMM pools for NOTE | Pendle needed a purpose-built AMM; NOTE's value jumps at every fixing |
| [7] | ERC-20 tokens, not ERC-1155 | Siren lost $3.5M to reentrancy through ERC-1155 receive hooks; CTF needed ERC-20 wrappers |
| [8] | The Desk sells WRITER (crash cover) to holders of the stock and keeps NOTE; it holds WRITER only up to a cap | In Siren, Ribbon and Cega the vault is the option seller: it collects the premium and posts the collateral. Here that side is NOTE; the WRITER token is the option buyer. Until 2026-09-30 the Desk kept WRITER and lost the premium on every autocall |
| [9] | Caps, ε-band before observations, rounding against the trader | Ribbon auctions cleared 1.7–2.8 vol points below exchange prices |
| [!] | Feed pauses detected by staleness | `oraclePaused()` is absent on the TSLA feed (probed), whatever Chainlink's docs say |

Sources: [Opyn Gamma OZ audit](https://www.openzeppelin.com/news/opyn-gamma-protocol-audit) ·
[Ribbon/Aevo drain](https://www.halborn.com/blog/post/explained-the-aevo-ribbon-finance-hack-december-2025) ·
[Opyn v1 post-mortem](https://medium.com/opyn/opyn-eth-put-exploit-post-mortem-1a009e3347a8) ·
[CTF audit](https://reports.chainsecurity.com/Polymarket/ChainSecurity_Polymarket_ConditionalTokens_Audit.pdf) ·
[USDG contract](https://github.com/paxosglobal/usdg-contract) ·
[Pendle AMM](https://docs.pendle.finance/pendle-v2/ProtocolMechanics/LiquidityEngines/AMM) ·
[PT-reUSD cascade](https://cryptobriefing.com/morpho-liquidations-pendle-reusd-cascade/) ·
[Siren exploit](https://quadrigainitiative.com/casestudy/sirenmarketreentrancybug.php) ·
[Ribbon auctions](https://www.research.ribbon.finance/blog/ribbon-auction-performance-analysis)

## Decisions baked into the interfaces

- **Implied vol is not a series term.** It changes the price, never the payout. Keeping it
  out of `SeriesTerms` keeps identical notes in one fungible series; vol is set per series
  in the Desk listing.
- **One certified model per product, not per stock or per note.** Every model input is
  relative to the strike, so the stock enters only through vol. K1 round 0 showed one
  network can't cover all term sheets (~18× over the gate), so each model is certified for
  one set of terms. The Desk listing names the pricer for each series; `listSeries` checks
  the series' terms and vol against the pricer's `certifiedRange`. Scaling to many stocks:
  a model with vol as a free input (one per product) if K1 round 2 certifies it, otherwise
  one model per product and vol level.
- **The payout rules are normative** (see `INoteSeries.sol`) and are the ones the Monte Carlo
  teacher (jump-diffusion calibrated to TSLA, [teacher-v2.md](teacher-v2.md)) prices: accrued coupon, autocall at `>= ac`, knock-in at `< ki`, both latching and
  checked at barrier observations only, and the maturity fixing **one period after the last
  observation**. That extra period is what keeps the knock-in barrier smooth for the model.
- **Each model carries a certified domain** (format v2): pinned note terms, exact derived
  fields, and excluded regions: for `model/k2` the autocall and knock-in observation-day
  bands, where the value jumps at the fixing. The Stylus contract reverts outside it, so a
  direct caller can't get an unmeasured price either.
- **No demo mode in production contracts.** The demo stages feed history on the mock feed
  and uses series whose strike lies in the past.
- **The Desk's LP flows pause while any held series can't be quoted** (weekends, pending
  fixing). Otherwise the share price would be unknown. A redemption *request* needs no
  share price: `IDeskQueue` takes it at any time and fills it, first in first out, at
  the share price of the moment the Desk can be valued and has idle USDG. Queued
  redemptions come before new risk and before other withdrawals.
- **The Desk trades both legs at two prices** (`IDeskCover`, additive to the frozen
  `IDesk`). A USDG vault has nothing to insure, so WRITER belongs with holders of the
  stock and NOTE with the vault. `buyCover` sells WRITER for the premium alone and the
  Desk keeps the NOTE; a NOTE buyer takes that NOTE, and its risk, out of the inventory.
  The Desk buys a leg below the model's quote and sells it above; the spread stays in
  the vault. The spread is a vol band (the model is asked at the listing's vol −/+ the
  band, once the model takes a range of vols) plus a flat floor. A risk budget per feed caps what its positions can lose, as a share of
  vault assets. Same series, same model: nothing below L3 changed.

## v2: the perpetual note, layer by layer

The same four layers, a second set of contracts next to v1 ([v2-perpetual-note.md](v2-perpetual-note.md),
[v2-spec.md](v2-spec.md), frontend guide [interfaces-v2.md](interfaces-v2.md)). Nothing in
v1 changed; both generations share one `FixingsRecorder` per feed.

```
┌─ L3  INTEGRATIONS ─────────────────────────────────────────────────────────────────────┐
│ PerpDesk     the v1 Desk for a note that never settles: both legs at two prices, a      │
│              risk budget per stock, the LP queue; adds the earnings date per listing,   │
│              the USDG its tokens are paid at every fixing, a capped weekend price       │
│ PerpWrapper  auto-compounding wrapper of one leg, for holders that never claim          │
└────────────────────────────────────────────────────────────────────────────────────────┘
        │ quote()                                                       │ mint · redeemPair · claim
        ▼                                                               │
┌─ L2  QUOTER ─ view only ─────────────────────────────────────┐        │
│ PerpQuoter   price = closed form + model correction + coupon │        │
│              reserve; state from the series, never the caller│        │
└──────────────────────────────────────────────────────────────┘        │
══ MODEL BOUNDARY: nothing below calls a pricer ════════════════════════╪═════════════════
                                                                        ▼
┌─ L1  CORE ─ no model · no admin · no pause · no upgrade ───────────────────────────────┐
│ PerpFactory.createSeries(terms)     same terms → same series (CREATE2)                  │
│ PerpSeries   one USDG escrow, no expiry                                                 │
│   • the first fixing sets the reference H                                               │
│   • every fixing: H ratchets up, or the note knocks in; a share a of everything is      │
│     released: NOTE a·(p + R), WRITER a·(1 − p); nothing is redeemed                      │
│   • mint n      lock n × notionalPerToken × (1 + R) → n NOTE + n WRITER                  │
│   • redeemPair  burn both → the same back, any time                                     │
│   • claim(to)   pull the USDG released so far (a cumulative index per token)            │
│   • four fixings in a row without a price → Closed: everything is released              │
│ NOTE, WRITER (PerpToken)  ERC-20; before a transfer the token tells its series, which   │
│                           settles both holders. No hook reaches a receiver.            │
└────────────────────────────────────────────────────────────────────────────────────────┘
                              ▼
┌─ L0  PRIMITIVES ───────────────────────────────────────────────────────────────────────┐
│ PerpPricer (Stylus)    PerpFormula (library)    PerpPayout (library)   FixingsRecorder  │
│   signed correction,     closed form in 1e18      pure: fixing →         v1's, shared   │
│   weightsHash pins the   fixed point, ~27k gas    releases, same rules                  │
│   weights, the domain    PerpFormulaPricer:       as the teacher, shared                │
│   and the product        correction 0             test vectors                          │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

| Decision | Why |
|---|---|
| The price is linear in the coupon reserve, and the model prices the principal only | The coupon stream is the same in every state of the stock (`a × R` of what is left, at every fixing), so its value is exact. One model then serves any coupon; the roadmap's open item 5 ("one model per stock unless c becomes a student input") goes away |
| The closed form is computed in Solidity by the quoter, the student returns only a correction | The formula stays readable and testable on its own (a formula-only pricer exists), the quote shows both parts, and the student has nothing to learn that the formula already knows |
| Discount rate 0 in model P1 | A pair redeems for `1 + R` at any time, so NOTE + WRITER = `1 + R` holds only if payouts are discounted at what the escrow earns: nothing. Teacher v3's convention (k3-vol-input.md). The roadmap's check table used 4%; the formula takes both rates |
| `firstFixing` stays in the terms | It anchors the fixing grid, and it is what makes a fresh series after a crash a different series |
| A token transfer calls the series | The cumulative index must be settled for both holders before balances move (Pendle's YT does the same). Only the series is called |
| Trades in tokens, prices per unit of notional | A token stands for less notional after each fixing; a price per unit of notional is what the model and the formula produce, and it doesn't drift with the melt |
| One `Traded` event and one `quote` view for all four sides | PerpDesk is 23.5 KB of the 24.6 KB limit |
| A missed fixing reuses the last one and still melts | The notional per token stays a function of the number of fixing times that have passed, whatever the feed did |
