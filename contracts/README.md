# Contracts

Foundry project (Solidity 0.8.24, OpenZeppelin) for an autocallable note on
stock tokens. Each series escrows USDG and issues two plain ERC-20s: NOTE, the
note itself, and WRITER, the other side, which pays the coupons and receives
what the NOTE loses once it is knocked in, so it works as crash cover. A NOTE
and a WRITER together always redeem for the full maximum payout. Observation
fixings are proven on-chain against a Chainlink-style feed, and settlement never
calls a model. A view-only quoter turns a series and its feed into the inputs of
an on-chain model (the Stylus pricer), and an ERC-4626 Desk funded by LPs in
USDG buys and sells both legs around that model's quote.

| Layer | Contracts | What it does |
|---|---|---|
| L0 | `FixingsRecorder`; the Stylus pricer (`../stylus/pricer-model`) behind `ISurrogatePricer` | The recorder lets anyone name the feed round for an observation time and verifies it on-chain. The pricer is a pure integer model that reverts outside its certified domain. |
| L1 | `SeriesFactory`, `NoteSeries`, `SeriesToken`, `AutocallPayout` | Deterministic clones per series: the escrow, the two tokens, and the pure payout rule. No admin, no pause, no model. |
| L2 | `NoteQuoter` | Reads the note state from the series and recorder, never from the caller, and refuses a stale feed or an out-of-range input. |
| L3 | `Desk` | ERC-4626 on USDG. Trades NOTE and WRITER at two prices; the owner curates listings, spreads and risk budgets but cannot touch series collateral. |

Where to start: read `src/interfaces/` first. The interfaces carry the
normative comments (the payout rule in `INoteSeries`, the fixing rule in
`IFixingsRecorder`, the price formulas in `IDesk` and `IDeskCover`). Then read
each layer's implementation from the bottom up. `src/mocks/` holds test
stand-ins (`MockUSDG` with freeze and pause, `MockChainlinkFeed`); never deploy
them where value is at stake.

Commands, from this directory:

```sh
forge fmt --check          # formatting
forge build                # compiles src/ without compiler or lint findings
forge test                 # unit, fuzz and invariant tests
script/export-abi.sh       # writes ../abi/*.json for the frontend
script/e2e-devnode.sh      # full lifecycle on a local Nitro dev node (needs docker and cargo-stylus)
```

What was checked and what was not: [../docs/contracts-review.md](../docs/contracts-review.md)
(reentrancy, rounding, USDG freeze and pause, clone provenance, staleness).
Layer diagram: [../docs/architecture.md](../docs/architecture.md).
