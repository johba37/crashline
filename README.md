# Crashline

On-chain fair-value quotes for path-dependent payoffs (autocallables on stock
tokens): an integer MLP distilled from a Monte Carlo teacher, running as a
Stylus contract, behind a model-free note core on Robinhood Chain, settled in USDG.

> **The Big Short, fixed.** In 2008 crash insurance was **priced wrong**, the sellers
> **couldn't pay** (AIG needed $182B), and the **banks decided what positions were worth**.
> Crashline keeps the good part (crash protection, and yield for taking risk) and fixes all
> three: a **public price anyone can recompute**, **full collateral** locked from day one, and
> **marks computed in public**. See [docs/pitch.md](docs/pitch.md).

## What it is

- **A note core:** maturity-dated autocallable notes on Robinhood stock tokens, fully collateralized
  in USDG. Minting locks the maximum payout and creates two ERC-20s:
  - **NOTE:** coupons, with the loss if knocked in
  - **WRITER:** the other side, i.e. crash protection
- **A pricer:** a 16-bit integer surrogate of a Monte Carlo pricer, running in Stylus (~45k gas per
  quote). It quotes what a note is worth *right now*, where no closed form exists, and it
  **refuses to quote outside its certified domain**, where its fidelity wasn't measured.
- **A Desk** that buys and sells NOTE at the pricer's quote plus a visible, capped fee.

**Settlement never touches the model.** Payouts are exact arithmetic on recorded fixings.

## Who it's for

| Role | Holds | Gets |
|---|---|---|
| **Yield seeker** | NOTE | Monthly coupons; carries the crash risk (at most what they paid) |
| **Protection buyer** (a hedger, or by default the Desk's LPs) | WRITER | A payout if the stock knocks in; pays the coupons |
| **Integrators** (apps, other protocols) | – | One public, verifiable price for every position |

Six walkthroughs with numbers: [docs/user-stories.md](docs/user-stories.md).

## Why it matters

- **A big product with a hidden price:** US structured notes hit a record $149.4B in 2024. German issuers
  alone hold €100.1B in listed structured securities, and Robinhood stock tokens are EU-only, so
  that's our home market. Issuers' markups of about 1–3% over fair value are measured academically.
- **Nobody on-chain can price path-dependent notes**, so they don't exist there, or settlement
  trusts an operator's number. Earlier attempts (Ribbon, Friktion, Cega) proved demand, then died
  on economics and exploits. The lessons are in [docs/market.md](docs/market.md).

## How it works

Architecture and the lessons it's built on: [docs/architecture.md](docs/architecture.md).
Frontend guide to the frozen v1 interfaces: [docs/interfaces.md](docs/interfaces.md).
Limitations and risks: [docs/risk.md](docs/risk.md).
Roadmap: [docs/v2-perpetual-note.md](docs/v2-perpetual-note.md), a perpetual note with
no expiry and one token per stock, priced by a closed form plus a learned correction.
Also on the roadmap: [docs/roadmap-usdg-yield.md](docs/roadmap-usdg-yield.md), interest on
the USDG that sits idle in the Desk and in the series; and
[docs/options-calibration.md](docs/options-calibration.md), a teacher calibrated to listed
option prices (the market's crash premium), one model per group of stocks.

## Deployed contracts

Robinhood Chain testnet (chain ID 46630), deployed 2026-10-02. The addresses are also in
[deployments/46630.json](deployments/46630.json), which the app reads. Four notes are listed
(TSLA, NVDA, ETH, BTC; their feeds and series are in [docs/interfaces.md](docs/interfaces.md));
no trade has run there yet.

| Contract | Address | Explorer |
|---|---|---|
| SurrogatePricer (Stylus, `model/k3`) | `0xAC002A7788B8Bf4645D5B7607fa2de7E608F6Bb7` | [explorer](https://explorer.testnet.chain.robinhood.com/address/0xAC002A7788B8Bf4645D5B7607fa2de7E608F6Bb7) |
| SeriesFactory | `0x7A878B50509ba94D51C43941B451A2641D7973e2` | [explorer](https://explorer.testnet.chain.robinhood.com/address/0x7A878B50509ba94D51C43941B451A2641D7973e2) |
| NoteQuoter | `0x682EFa8609014D617649122A2281FFeC0279756A` | [explorer](https://explorer.testnet.chain.robinhood.com/address/0x682EFa8609014D617649122A2281FFeC0279756A) |
| FixingsRecorder (of the RHTSLA feed) | `0xcfF29DB4E2e64B5A3F6971c65F842873CfFC5c64` | [explorer](https://explorer.testnet.chain.robinhood.com/address/0xcfF29DB4E2e64B5A3F6971c65F842873CfFC5c64) |
| MockChainlinkFeed (RHTSLA, staged prices) | `0xb32871181e23404F88632fA353D553584611259A` | [explorer](https://explorer.testnet.chain.robinhood.com/address/0xb32871181e23404F88632fA353D553584611259A) |
| Desk (ERC-4626) | `0x613e80C7c94f1f0ad1D9B8b82606d6eDf4A1DAea` | [explorer](https://explorer.testnet.chain.robinhood.com/address/0x613e80C7c94f1f0ad1D9B8b82606d6eDf4A1DAea) |
| USDG (Paxos, existing) | `0x7E955252E15c84f5768B83c41a71F9eba181802F` | [explorer](https://explorer.testnet.chain.robinhood.com/address/0x7E955252E15c84f5768B83c41a71F9eba181802F) |

## Demo

- Frontend: TBD
- Demo video (≤ 2:00): TBD
- Pitch video (≤ 2:00): TBD

## Judging criteria → evidence

| Criterion | Evidence |
|---|---|
| Smart contract quality | Model-free core with no admin, no pause and no upgrade; a separate USDG escrow per series; every rule traced to a real exploit ([architecture.md](docs/architecture.md)); the Stylus model matches its Python reference exactly on golden vectors and **refuses to quote outside its certified domain** (where its fidelity was measured; every rejection rule has a test vector), and its `weightsHash` is recomputed at build. CI runs the default and both secondary models. Core invariants (escrow ≥ claims, no foreign clones) at 512 runs × 100 calls ([contracts-review.md](docs/contracts-review.md)) |
| Product-market fit | $149.4B US / €100.1B German structured-note markets; hidden issuer markups; two-sided on-chain users ([market.md](docs/market.md), [user-stories.md](docs/user-stories.md)) |
| Innovation and creativity | Monte-Carlo-class pricing of path-dependent payoffs inside a transaction: a surrogate distilled from a simulation (the labels come from code, not market data), verifiable by anyone |
| Real problem solving | Structured-note prices are the issuer's private model; this makes every quote public and recomputable |
| Use of Arbitrum technology | Stylus is the enabling constraint (k2: 23,913 of 24,576 bytes compressed, ~45k gas per quote on the synthetic model, reproducible `cargo stylus verify`); deployed on Robinhood Chain (Arbitrum Orbit) |
| Presentation | Pitch and demo videos: TBD |

## Sponsor technologies

- **Robinhood Chain:** the venue; the stock tokens are the underlyings; Chainlink feeds for fixings.
- **Paxos USDG:** collateral, coupons and settlement currency (6 decimals; the design accounts for the issuer's freeze and pause powers).
- **OpenZeppelin Contracts v5.7.0:** token and vault building blocks (git submodule).

## How we differ

- **Arboretum** (closed-form Black-Scholes in Stylus) stops where closed form ends; we start there.
- **Contour** (structured notes on stock tokens) priced its terms off-chain; our price is on-chain and recomputable.
- **Ribbon, Friktion, Cega** sold opaque option vaults; we issue maturity-dated notes you can enter and exit at a public price.

## Built during the buildathon

All code was written during the buildathon window (Sep 14 to Oct 4, 2026). This repo was
created Sep 29, 2026. Commit `db8f539` imports Solidity from the team's own
[gap-guard](https://github.com/johba37/gap-guard) repo, which was also created Sep 29.
OpenZeppelin Contracts and forge-std are unmodified dependencies (git submodules).
The progress log will be added before submission.

## Roadmap (written as the prize milestones)

The prize pays 25% on signing, 25% after a 1-month check-in, and 50% after a mainnet launch plus an agreed KPI.
- **M1 (check-in):** the real Monte Carlo teacher and distilled weights with a published fidelity report; the Desk live on testnet; invariant and fuzz suites; an external review of the core.
- **M2 (mainnet launch):** core, pricer and Desk on Robinhood Chain mainnet (chain 4663) with real feeds and conservative caps. KPI candidates: USDG notional issued, number of series, and integrations calling the pricer.
- **M3:** a collateral valuation oracle for NOTE/WRITER, built with the lessons of the Pendle PT-reUSD cascade; a mint-and-sell Router; a second payoff type.

## For developers

```
contracts/README.md        entry point for reading the contracts: layers, where to start, commands
contracts/src/interfaces/  v1 interfaces (factory, series, tokens, quoter, Desk, recorder, pricer)
                           + IDeskCover: the Desk's WRITER leg, two prices, risk budget
                           + IDeskQueue: LP redemption queue
contracts/src/             SeriesFactory, NoteSeries, SeriesToken, AutocallPayout, NoteQuoter,
                           Desk (ERC-4626 on USDG), FixingsRecorder
contracts/src/mocks/       MockChainlinkFeed, MockUSDG (test and dev-node stand-ins)
contracts/script/          Deploy.s.sol (Robinhood testnet), curator.sh (the curator's steps there),
                           e2e-devnode.sh, export-abi.sh
abi/                       interface ABIs for the frontend (contracts/script/export-abi.sh)
stylus/pricer-model/       Rust/Stylus model contract: priceBps(PricerInputs), weightsHash()
tools/pricer_quant.py      integer reference (bit-exact twin), float→int quantizer, hash
tools/payout_vectors.py    scalar payout reference → shared payout vectors (forge matches exactly)
tools/quoter_vectors.py    pricer inputs + quotes per model → quoter vectors
tools/make_synthetic.py    synthetic student + golden vectors (toy target, NOT the teacher)
model/k3/                  K3 student, vol a live input 20-90% (default build, teacher v3, docs/k3-vol-input.md)
model/k2/                  K2 round-2 student, whole note life at vol 55% (docs/k2-round2.md)
model/k1-r1/               K1 round-1 student (first week only; CI target)
model/synthetic/           toy student + vectors (CI target)
ml/                        jump-diffusion teacher (docs/teacher-v2.md), student training, evals
ml/data/                   TSLA daily closes with source URL and fetch date; multi/: 12 more symbols
tools/certify.py           attach a certified domain, generate golden + reject vectors
docs/model-export-format.md  the distillation ↔ contract boundary
docs/contracts-review.md   self-review: reentrancy, rounding, USDG freeze/pause, provenance, staleness
```

### Status (2026-10-02)

| Part | Result | Source |
|---|---|---|
| Teacher | Merton jump-diffusion calibrated to 10 years of TSLA daily closes (λ 72.2/yr, σJ 5.26%, jump vol 44.7% of the pinned 55% total); 8/8 checks pass, incl. λ=0 bit-identical to the K1 GBM teacher | `ml/test_teacher.py`, [docs/teacher-v2.md](docs/teacher-v2.md) |
| Student `model/k2` | whole note life (observationsRemaining 1–26, timeToNextObs 0–7 days); **GATE PASS** on the held-out set vs the jump teacher: max 18.7 / p99 7.9 / mean 1.65 bps outside the two observation-day bands (gate 50), max label stderr 3.24 bps; fresh confirmation set T2: max 17.8 bps | `ml/round2_eval.py`, [docs/k2-round2.md](docs/k2-round2.md) |
| Teacher v3 | jump sizes scale with vol (TSLA's shape, 57% jump share at every vol: nothing refused), payouts discounted at `rDiscount` 0 (the escrow earns nothing); 16/16 checks, incl. the v2 path bit-identical to the frozen K2 teacher (`ml/reference/k2/`) | `ml/test_teacher.py`, [docs/k3-vol-input.md](docs/k3-vol-input.md) |
| Student `model/k3` | the K2 product at any vol 20–90%; **GATE PASS** vs teacher v3: max 37.9 / p99 14.1 / mean 2.65 bps (gate 50; the first model failed at the vol floor, 71.2); clean confirmations T2 34.7 and T3 (vol endpoints) 38.0; vol-band spread P(vol − 2) − P(vol + 2) within 38.5 bps of the teacher's; 23,901 bytes, testnet activation check passes | `ml/round3_eval.py`, [docs/k3-vol-input.md](docs/k3-vol-input.md) |
| Note core, quoter, Desk | 140 forge tests pass: 260 payout-conformity vectors, quoter vectors for k1-r1, k2 and k3 (four vols), fuzz at 1,000 runs, invariants at 512 runs × 100 calls (escrow ≥ claims, no foreign clones). The Desk trades NOTE and WRITER (cover) at two prices and keeps NOTE, within a risk budget per stock; LPs can queue redemptions at any time | `forge test`, [docs/contracts-review.md](docs/contracts-review.md) |
| End to end | local Nitro dev node with the Stylus k3 pricer, listed at vol 5500 ± a 200 bps band: series struck in the past, NOTE buy, cover buy by a hedger, **mid-life sell at observationsRemaining 16 at the band's quote ± the spread**, autocall (the LP queues a redemption while the fixing is pending), redeem, collect, queue paid, LP withdraw; the same with k2 (no band) | `contracts/script/e2e-devnode.sh`, logs [k3](contracts/logs/e2e-devnode-k3.log), [k2](contracts/logs/e2e-devnode-k2.log) |
| Happy path | backend-driven on a fresh dev node with the k3 pricer: two weekly series on one product (A stays above the knock-in, B knocks in), both legs traded, every trade checked against teacher v3, matured with the dev clock, settled, redeemed, collected, LP out; USDG conserved to the base unit at every step | `backend/scenarios/happy_path.py`, logs [clock](backend/scenarios/logs/happy_path-clock.log), [hybrid](backend/scenarios/logs/happy_path-hybrid.log), [docs/backend.md](docs/backend.md#happy-path-scenario) |
| Robinhood Chain testnet (46630) | deployed 2026-10-02: the k3 pricer (Stylus, activated; `weightsHash` matches `model/k3`, 100 golden and 35 reject vectors exact on chain), factory, quoter, Desk on the real USDG, a staged mock feed and its recorder. Four series are listed since (TSLA, NVDA, ETH, BTC, each on a mock feed that mirrors its real feed hourly); no trade has run there yet | `deployments/46630.json`, addresses in [docs/interfaces.md](docs/interfaces.md) |

Known limit (teacher v2 / model/k2; lifted by teacher v3 / model/k3, which certifies 20–90%):
the teacher's jump variance is pinned from history, so total vol must stay above 44.7%.
Listed TSLA options (6-month ATM ~43%) sit below that; see
[docs/teacher-v2.md](docs/teacher-v2.md) "Listed options". Across 12 more stocks (v2 input,
[docs/multi-stock-jumps.md](docs/multi-stock-jumps.md)), 7 sit below it; a fixed jump share
of variance lands within 7.6 bps of each large cap's own fit, and misses by 16–27 bps on
stocks with rare large earnings drops.

### Stylus model status

| Check | Result |
|---|---|
| Golden vectors (100) vs Python reference | exact, native `cargo test`, on a local Nitro dev node and (k3) on Robinhood Chain testnet |
| Certified domain (format v2) | outside it: `OutOfRange` / `Inconsistent` / `Uncertified`; every rule has a reject vector |
| `weightsHash()` | recomputed at build time; a flipped weight byte fails the build |
| ABI | callable from Solidity through NoteQuoter's `ISurrogatePricer` / `PricerInputs` struct |
| Activation on Robinhood Chain testnet (46630) | k3 deployed and activated (a local `--no-verify` build): 23,923 bytes compressed (limit 24,576), data fee 0.000072 ETH, 5.9M gas to deploy and 3.6M to activate. `cargo stylus check` passed for k2 at 23,913 bytes (docs/k2-round2.md) |
| Execution gas per quote | **~45,000** measured on the synthetic model (3,873 params; Solidity caller, `gasleft()` delta, uncached init included); not yet re-measured for k2/k3. A whole Desk buy incl. the k2 quote and the risk-budget check is 647,750 L2 execution gas on the dev node (497,983 before the check); with k3 and a vol band (two model calls) 775,042 |
| Quantization error (int16 vs float, synthetic) | p50 0.3 / p99 1.2 / max 3.2 bps |

Default model: `model/k3` (K3, 7,465 params, vol 20–90%; `model/k2` until 2026-10-01). CI runs all four:
`cargo test`, `PRICER_MODEL_DIR=../../model/k2 cargo test`,
`PRICER_MODEL_DIR=../../model/k1-r1 cargo test` and
`PRICER_MODEL_DIR=../../model/synthetic cargo test`.

### Commands

```sh
# Python reference (numpy, torch for the synthetic fit, pycryptodome for keccak)
python3 -m venv --system-site-packages tools/.venv && tools/.venv/bin/pip install pycryptodome
cd tools && ../tools/.venv/bin/python make_synthetic.py --out ../model/synthetic

# Teacher and student
tools/.venv/bin/python ml/test_teacher.py              # teacher checks (a)-(h); (g) uses /opt/ai/cache/venv-cuda if present
tools/.venv/bin/python ml/round2_eval.py --model model/k2   # gate on the held-out set T

# Solidity
cd contracts && forge fmt --check && forge test && script/export-abi.sh
tools/.venv/bin/python tools/payout_vectors.py          # regenerate the payout vectors
contracts/script/e2e-devnode.sh                         # full lifecycle on a Nitro dev node (docker), model/k3
PRICER_MODEL_DIR=model/k2 contracts/script/e2e-devnode.sh   # the same with K2 (vol pinned, no band)
forge script script/Deploy.s.sol --rpc-url https://rpc.testnet.chain.robinhood.com   # add --broadcast with DEPLOYER_KEY; PRICER and CURATOR as env

# Stylus contract
cd stylus/pricer-model
cargo test                                   # golden vectors + ABI path
cargo stylus check --endpoint https://robinhood-testnet.drpc.org   # rpc.testnet.chain.robinhood.com refuses the activation dry-run
cargo stylus deploy --endpoint https://robinhood-testnet.drpc.org --private-key-path <file>

# Local gas measurement
docker run -d --rm --name sp-devnode -p 127.0.0.1:8547:8547 \
  offchainlabs/nitro-node:v3.11.4-7d5ac27 --dev --http.addr 0.0.0.0 --http.api=net,web3,eth,debug
```

Toolchain is pinned in `rust-toolchain.toml` (1.91.0) for reproducible
`cargo stylus verify`.

## License

[MIT](LICENSE)
