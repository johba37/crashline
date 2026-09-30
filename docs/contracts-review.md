# Contracts self-review (lane C, 2026-09-30)

Scope: `contracts/src/` on `lane/contracts`. That means SeriesFactory, NoteSeries,
SeriesToken, AutocallPayout, FixingsRecorder, NoteQuoter and Desk, plus the two
mocks. The interfaces in `contracts/src/interfaces/` are frozen at `f31793d` and
were not changed. This is a self-review, not an audit. Each item below states
what the code does, what was checked, and the decision.

How to reproduce what this document relies on:

```sh
cd contracts
forge fmt --check && forge build          # no compiler or lint findings in src/
forge test                                # unit, fuzz (1,000 runs each), invariants (512 runs x depth 100)
forge build --sizes                       # Desk is the largest contract
../tools/.venv/bin/python ../tools/payout_vectors.py   # regenerates test/vectors/payout_vectors.json
../tools/.venv/bin/python ../tools/quoter_vectors.py   # regenerates test/vectors/quoter_vectors.json
script/e2e-devnode.sh                     # lifecycle on a Nitro dev node with the Stylus pricer
```

## 1. Reentrancy

| Where | External calls | Protection | Decision |
|---|---|---|---|
| `NoteSeries.mint / redeemPair / redeem / advance` | USDG `transferFrom`/`transfer`, own NOTE/WRITER clones, own recorder (view) | `nonReentrant` on every state-changing entry point. The guard lives in the clone's storage, which is zero-initialized; OZ's guard reverts only on `ENTERED`, so it works in clones without an initializer. | Keep. A test re-enters `mint` from a malicious 6-decimal collateral's `transferFrom` and gets `ReentrancyGuardReentrantCall`. |
| Order inside the series | `mint` pulls USDG before it mints tokens. `redeemPair` and `redeem` burn before they pay. `settle` (inside `advance`) moves no tokens. | Checks-effects-interactions, plus the guard | Keep |
| `Desk.buy / sell / collect`, ERC-4626 `deposit / mint / withdraw / redeem` | USDG, series (`mint`, `redeemPair`, `redeem`), NOTE/WRITER, quoter → pricer, feed | `nonReentrant` on all of them. State (`soldNotional`) is written before the transfers. | Keep |
| Desk views during a trade | `totalAssets()` is not guarded (read-only reentrancy) | The only contracts called mid-trade are factory clones, USDG and the quoter/pricer. None calls back into the Desk. A `nonReentrantView` guard would break OZ's own `deposit → previewDeposit → totalAssets` path. | Accept, documented |
| `SeriesFactory.createSeries` | `feed.decimals()` (untrusted) and the clones' `initialize` | The series is registered before the initializers run. A feed that re-enters `createSeries` with the same terms creates the series in the nested call, and the outer `cloneDeterministic` then reverts: the whole transaction reverts. | Keep |
| NOTE/WRITER tokens | none (plain OZ ERC-20, no hooks) | ERC-20 instead of ERC-1155 (the Siren lesson) | Keep |

Lints excluded in `foundry.toml` with the reason inline: `reentrancy-events` (every
entry point is guarded) and `calls-loop` (bounded loops over trusted contracts).

## 2. Rounding directions

Every rounding goes against the party that asks, and in favor of the escrow and then the LPs.

| Quantity | Rounding | Where |
|---|---|---|
| Collateral locked by `mint(n)` | `ceil(n * max / 1e6)` | NoteSeries |
| `redeemPair(n)` pays | `floor(n * max / 1e6)` | NoteSeries |
| `redeem` pays | `floor(n * payout / 1e6) + floor(w * (max - payout) / 1e6)`, as the interface NatSpec says | NoteSeries |
| Maturity payout of a knocked-in note | `floor(fixing * 1e6 / initial)` + coupons (coupons are exact: 1 bps = 100 units) | AutocallPayout |
| Barriers | exact integer cross-multiplication, no division: `fixing * 1e4 >= ac * initial`, `fixing * 1e4 < ki * initial` | AutocallPayout |
| Accrued coupon in the quote | `floor(c * elapsed / I)` bps (at most 1 bps below the continuous accrual) | NoteQuoter |
| Spot input | `floor(spot * 1e4 / initial)` | NoteQuoter |
| Desk buy cost (NOTE or WRITER) | `ceil(n * price / 1e4) + ceil(n * fee / 1e4)` | Desk |
| Desk sell proceeds (NOTE or WRITER) | `floor(n * price / 1e4) - fee`, with the fee capped at the gross amount | Desk |
| Desk prices | NOTE ask `min(hi + ask, max)`, NOTE bid `min(lo, max) - bid` floored at 0, cover = `max - NOTE` of the other side; `lo`/`hi` = the lower/higher of the model's quotes at the two ends of the vol band | Desk |
| Backstop slice of a fee | rounded up (stays with the LPs) | Desk |
| Desk marks (NOTE, WRITER) | floor per position; WRITER = max - NOTE, with NOTE capped at max | Desk |
| ERC-4626 | OZ defaults (shares down on deposit, up on withdraw) with virtual shares, offset 6 | Desk |

What the tests prove:

- Invariant `invariant_escrow_covers_claims`: at every step of 512 runs × 100
  random calls, a series' USDG covers what its outstanding NOTE and WRITER can claim.
  The claims are computed exactly, before the per-holder floors. A mutation
  (mint rounding down) breaks it.
- `afterInvariant`: after settling and redeeming every holder, the escrow holds
  less than one base unit per rounding operation (each mint, pair redemption and
  redeemed leg) and no token supply is left. **This is the precise form of the
  brief's "≤ 1 base unit per holder".** A holder whose redemption is a single
  leg leaves less than 1 unit. A holder who redeems both NOTE and WRITER in one
  call can leave up to 2 units, because the interface fixes two separate floors.
  Mint ceilings also stay in the escrow.
- Fuzz `testFuzz_round_trip_never_costs_lps`: a buy followed by a sell at the
  same quote lowers the Desk's assets by at most 2 base units. Those come from
  mint rounding (locked in the series escrow) and the floor of the WRITER mark.
  **This fuzz found a real bug**: when a sell's fee was larger than its gross
  proceeds, the integrator's cut was paid out of LP funds. The fee is now capped
  at the gross amount (regression test `test_sell_fee_capped_at_gross`).

## 3. USDG freeze and pause

USDG's issuer can freeze an account, wipe it, and pause all transfers.

| Case | Behaviour | Decision |
|---|---|---|
| Holder frozen | `redeem(…, to)` and `redeemPair(…, to)` pay any `to`: the holder picks an unfrozen address. Test `test_redeem_to_other_account_when_frozen`. | Keep (pull-based) |
| USDG paused | Every token movement reverts. `advance()` and settlement still work, because they move no tokens (test `test_usdg_pause_blocks_then_resumes`), and redemptions resume after unpause. Desk views keep working. | Keep |
| Series escrow frozen or wiped by the issuer | Every holder of that series is stuck or loses collateral. Nothing in-protocol can prevent this. The damage is limited to one series, because each series has its own escrow. | Accept, stated risk |
| Desk frozen | LP flows and trading stop. Series positions stay redeemable once unfrozen; `collect` pays the Desk itself. | Accept, stated risk |
| Fee-on-transfer or rebasing collateral | USDG has neither. The factory accepts only a 6-decimal collateral and does not check for fees. | Accept for USDG. Don't reuse the factory with fee tokens. |

`MockUSDG` reproduces freeze and pause for the tests and the dev node. It has an
open `mint`, so it must never be deployed where value is at stake.

## 4. Clone provenance

| Rule | Implementation | Test |
|---|---|---|
| Only factory clones are series | The factory deploys its own NoteSeries and SeriesToken implementations in its constructor. Their `factory` is an immutable, so every clone sees it through `delegatecall`, and `initialize` requires `msg.sender == factory`. Implementations mark themselves initialized in their constructor. | `test_only_factory_initializes_clones`, `test_implementations_are_inert`, `test_foreign_clone_is_rejected` |
| Same terms → same series | CREATE2 salt = `seriesId = keccak256(abi.encode(terms))`, NOTE/WRITER salts derived from it. `createSeries` is idempotent. `predictSeries` matches. | `test_createSeries_matches_prediction_and_registers`, `test_createSeries_idempotent`, fuzz |
| One recorder per feed, not spoofable | CREATE2 with the feed in the init code (`recorderOf` is deterministic). The series stores the recorder the factory deployed. | `test_deployRecorder_idempotent_and_deterministic` |
| Tokens mint and burn only for their series | `OnlySeries` in SeriesToken. A series only ever calls its own two tokens. | `test_token_mint_burn_only_series` |
| The Desk only lists and collects factory series | `factory.isSeries` in `listSeries` and `collect` (`NotFactorySeries`), including a series with identical terms from a second factory | `test_listSeries_NotFactorySeries` |
| Invariant | `invariant_no_foreign_clone_accepted`: random attempts to initialize foreign clones, re-initialize real ones, or mint real tokens never succeed | invariant suite |

## 5. Oracle staleness and fixings

| Item | Implementation | Decision |
|---|---|---|
| Quote staleness | `NoteQuoter.MAX_FEED_STALENESS` (immutable, 26 h in Deploy.s.sol and the e2e): `FeedStale` on weekends and halts. `answer <= 0` → `BadFeedAnswer`. | Keep. **Residual risk:** within 26 h a quote can use a price up to 26 h old. On a 24/5 feed that updates about every 30 minutes on weekdays this only bites around halts. A tighter value can be set per deployment (constructor argument). |
| `oraclePaused()` | Not used: it does not exist on the real feed (DESIGN.md). Pauses show up as staleness. | Keep |
| Fixing rule | FixingsRecorder cases A and B (DESIGN.md Decision 1), checked on-chain against feed rounds, with `PRICE_MAX` guarding against the round-1 scale anomaly | Keep |
| **Change: strictly past observations** | `recordFixing` now requires `obsTime < block.timestamp` (was `<=`). Several Arbitrum blocks share a timestamp, so at `obsTime == now` a round with `updatedAt == obsTime` could still land after the fixing is recorded, and the recorded round would not be the last one. The series treats an observation as pending only once `obsTime < now`, which is consistent. | Keep |
| Pending fixing | `pendingObservation()` is true once an observation time has passed and its fixing is neither recorded nor past the fallback deadline. The quoter then reverts `FixingPending`, and Desk LP flows pause. | Keep |
| Fallback | A fixing unrecorded `MAX_ROLL (8 d) + FALLBACK_GRACE (1 d)` after its time reuses the previous fixing. A fixing recorded before the series processes it always wins. **Residual:** case A fixings can be recorded arbitrarily late, so between the deadline and the next `advance()` the outcome depends on whether someone records first. Both outcomes use feed data. Only an unrecordable fixing (feed dead) forces the fallback in practice. | Accept, documented in NoteSeries NatSpec |
| Strike | No fallback. An unstruck series never holds collateral, because `mint` requires Live. | Keep |
| Pre-observation band | Desk `minSecsToObservation` (1 h in Deploy.s.sol): `TooCloseToObservation`. The model separately refuses its excluded observation-day bands (`Uncertified`). | Keep. The e2e sets 60 s through the constructor so it can trade minutes before an observation. That is a curator parameter, not a demo flag. |

## 6. Desk economics and ERC-4626 deviations

- **Both legs, two prices (added 2026-09-30, `IDeskCover`).** The Desk used to sell
  NOTE at the mid and keep every WRITER: a USDG vault buying crash insurance, which
  loses the premium on each autocall. It now also sells and buys back WRITER
  (`buyCover` / `sellCover`), serves every trade from inventory first, mints a pair
  only for the shortfall and redeems the pairs a handed-in leg completes. One code
  path serves both legs (`_buy`, `_sell`); the four entry points differ in the leg and
  the price. Checked:
  - *No pair arbitrage.* NOTE bid + cover bid ≤ maxPayout ≤ NOTE ask + cover ask, also
    when the model quotes above maxPayout (the mid is capped). Fuzz
    `testFuzz_no_pair_arbitrage` mints pairs and sells both legs, then buys both legs
    and redeems; without the cap on the mid it fails.
  - *Round trips.* `testFuzz_cover_round_trip_never_costs_lps`: cover bought and sold
    back lowers the Desk's assets by at most 2 base units at any spread, fee and price,
    and never pays the trader. The NOTE round trip is unchanged.
  - *WRITER cap.* `capNotional` now limits the WRITER the Desk holds, from NOTE buys
    beyond the inventory and from cover sold back that it can't pair.
    `Listing.soldNotional` is no longer stored; `listing()` reads the WRITER balance.
  - *Risk budget.* After a trade that added to a position, what the Desk's unsettled
    positions on the series' feed can lose (NOTE mark − coupons, WRITER mark) must fit
    `riskBudgetBps[feed]` of vault assets. It is 0 until set, so a new feed is closed by
    default. A held series that can't be quoted counts 1 USDG per unit at risk and only
    its NOTE's coupons as an asset, so the check never reverts with a quoter error.
    Trades that shrink a position skip it. **Residual:** the check quotes every held
    series (≤ `MAX_HELD_SERIES`), so a trade that adds risk costs one model call per
    held series; quotes don't apply it (read `risk(feed)`); an LP withdrawal can push
    the ratio over the limit, which only blocks new risk.
  - *Vol band.* `Spread.volBandBps` prices the two sides at the listing's vol −/+ the
    band (two quoter calls; one when the band is 0). The Desk takes the lower quote as
    its bid and the higher as its ask, so bid ≤ ask and the no-arbitrage bound hold even
    where the note gains with vol; both fuzz tests run over bands up to 15 vol points
    and models sloping either way. `setSpread` and `listSeries` require both ends of
    the band inside the pricer's certified vol range, so a vol-pinned model (k2) takes
    no band. Marks stay at the listing's vol. **Residual:** the band only widens the
    spread when the model's price moves with vol; near a certain payout it adds nothing
    and the flat spreads are the floor. Gas of the second quote with a vol-input Stylus
    model: not measured (no such model yet).
  - *Reentrancy and order.* Unchanged: `nonReentrant` on the four trades; USDG in,
    mint, leg out, fee out, then the budget check on the final state.
  - Each check above was confirmed by breaking it: five mutations of `Desk.sol` each
    fail at least one test in `DeskCover.t.sol`.
- **Valuation:** the NOTE and WRITER the Desk holds are marked at the model's mid
  quote. They are marked exactly, without the model, after settlement and in the
  final period of a note that isn't knocked in (NOTE = max, certain). That second
  case matters: with one series a week, some series is always in its final week,
  and the model refuses `observationsRemaining = 0`.
- **LP flows pause** (max* = 0, `totalAssets` reverts with the quoter's or model's
  error) while any held series can't be quoted: weekends, a pending fixing, the
  model's excluded bands, and the final period of a knocked-in note. These are
  the documented deviations from the ERC-4626 MUSTs (see the Desk NatSpec).
  `maxWithdraw` and `maxRedeem` are also capped by idle USDG.
- **Curator trust:** the owner picks the pricer and the vol per listing, and so
  determines the LP share price. A malicious or wrong model can misprice trades
  and marks. Listing checks the pricer's certified range against the series (ki,
  ac, coupon, vol, observation count). The observation interval is not in
  `certifiedRange`: a series with another interval is caught at quote time by
  the model's `Inconsistent(6)`, not at listing.
- **Solvency of the Desk:** buys need idle USDG for the pairs' collateral beyond
  the buyer's payment: about 9% of notional for a NOTE buy, about 95% for a cover
  buy (the Desk funds the NOTE's share). Sells of a leg the Desk can't pair are paid
  from idle USDG. All revert on insufficient balance and never touch series collateral.
- **Inflation:** virtual shares with a 6-decimal offset (shares have 12
  decimals). In `test_inflation_attack_is_unprofitable`, the victim loses at most
  donation / 1e6 and the attacker gets back about half of the donation.
- **Bounded loops:** `MAX_HELD_SERIES = 64` (`HeldSeriesLimit`). A series leaves
  the held set on `collect`.

## 7. Payout conformity with the teacher

`tools/payout_vectors.py` was compared line by line with the teacher's `_simulate`
(`ml/teacher.py`, first the K1 version, then lane A's jump teacher as merged
into main at `5353120`). They agree on the autocall test (`>=`, checked first),
the knock-in test (`<`, latching, barrier observations only, not at maturity),
the autocall payout 1 + c·i, the maturity fixing one interval after observation
N, and the maturity payout with the loss measured against the **initial**
fixing. The K1 teacher used `acBarrierBps` there (identical at ac = 10000). That
mismatch was reported, and the merged teacher uses the initial fixing. The
contracts' fallback fixing is not simulated by the teacher. That is consistent
with teacher-spec §3 (no feed gaps in simulation).

## 8. Out of scope / not done

- No external audit. No formal verification.
- `Deploy.s.sol` was only simulated against Robinhood Chain testnet: no
  `DEPLOYER_KEY` was available, so nothing was broadcast and there is no
  `deployments/46630.json`.
- NOTE collateral oracle, router and permit (roadmap, per architecture.md).
