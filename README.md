# Surrogate Pricer

On-chain fair-value quotes for path-dependent payoffs (autocallables on stock
tokens): an integer MLP distilled from a Monte Carlo teacher, running as a
Stylus contract, behind a model-free note core on Robinhood Chain, settled in USDG.

Architecture and the lessons it's built on: [docs/architecture.md](docs/architecture.md).
Frontend guide to the frozen v1 interfaces: [docs/interfaces.md](docs/interfaces.md).
Roadmap: [docs/v2-perpetual-note.md](docs/v2-perpetual-note.md), a perpetual note with
no expiry and one token per stock, priced by a closed form plus a learned correction.

```
contracts/src/interfaces/  frozen v1 interfaces (factory, series, tokens, quoter, Desk, recorder, pricer)
contracts/src/             SeriesFactory, NoteSeries, SeriesToken, AutocallPayout, NoteQuoter,
                           Desk (ERC-4626 on USDG), FixingsRecorder, MockChainlinkFeed, MockUSDG
contracts/script/          Deploy.s.sol (Robinhood testnet), e2e-devnode.sh, export-abi.sh
abi/                       interface ABIs for the frontend (contracts/script/export-abi.sh)
stylus/pricer-model/       Rust/Stylus model contract: priceBps(PricerInputs), weightsHash()
tools/pricer_quant.py      integer reference (bit-exact twin), float→int quantizer, hash
tools/payout_vectors.py    scalar payout reference → shared payout vectors (forge matches exactly)
tools/quoter_vectors.py    pricer inputs + quotes per model → quoter vectors
tools/make_synthetic.py    synthetic student + golden vectors (toy target, NOT the teacher)
model/k2/                  K2 round-2 student, whole note life (default build, docs/k2-round2.md)
model/k3/                  K3 student, vol a live input 20-90% (teacher v3, docs/k3-vol-input.md)
model/k1-r1/               K1 round-1 student (first week only; CI target)
model/synthetic/           toy student + vectors (CI target)
ml/                        jump-diffusion teacher (docs/teacher-v2.md), student training, evals
ml/data/                   TSLA daily closes with source URL and fetch date; multi/: 12 more symbols
tools/certify.py           attach a certified domain, generate golden + reject vectors
docs/model-export-format.md  the distillation ↔ contract boundary
docs/contracts-review.md   self-review: reentrancy, rounding, USDG freeze/pause, provenance, staleness
```

## Status (2026-09-30)

| Part | Result | Source |
|---|---|---|
| Teacher | Merton jump-diffusion calibrated to 10 years of TSLA daily closes (λ 72.2/yr, σJ 5.26%, jump vol 44.7% of the pinned 55% total); 8/8 checks pass, incl. λ=0 bit-identical to the K1 GBM teacher | `ml/test_teacher.py`, [docs/teacher-v2.md](docs/teacher-v2.md) |
| Student `model/k2` | whole note life (observationsRemaining 1–26, timeToNextObs 0–7 days); **GATE PASS** on the held-out set vs the jump teacher: max 18.7 / p99 7.9 / mean 1.65 bps outside the two observation-day bands (gate 50), max label stderr 3.24 bps; fresh confirmation set T2: max 17.8 bps | `ml/round2_eval.py`, [docs/k2-round2.md](docs/k2-round2.md) |
| Teacher v3 | jump sizes scale with vol (TSLA's shape, 57% jump share at every vol: nothing refused), payouts discounted at `rDiscount` 0 (the escrow earns nothing); 16/16 checks, incl. the v2 path bit-identical to the frozen K2 teacher (`ml/reference/k2/`) | `ml/test_teacher.py`, [docs/k3-vol-input.md](docs/k3-vol-input.md) |
| Student `model/k3` | the K2 product at any vol 20–90%; **GATE PASS** vs teacher v3: max 37.9 / p99 14.1 / mean 2.65 bps (gate 50; the first model failed at the vol floor, 71.2); clean confirmations T2 34.7 and T3 (vol endpoints) 38.0; vol-band spread P(vol − 2) − P(vol + 2) within 38.5 bps of the teacher's; 23,901 bytes, testnet activation check passes | `ml/round3_eval.py`, [docs/k3-vol-input.md](docs/k3-vol-input.md) |
| Note core, quoter, Desk | 97 forge tests pass: 260 payout-conformity vectors, quoter vectors for k1-r1 and k2, fuzz at 1,000 runs, invariants at 512 runs × 100 calls (escrow ≥ claims, no foreign clones) | `forge test`, [docs/contracts-review.md](docs/contracts-review.md) |
| End to end | local Nitro dev node with the Stylus k2 pricer: series struck in the past, buy, **mid-life sell at observationsRemaining 16 at the model's quote**, autocall, redeem, collect, LP withdraw | `contracts/script/e2e-devnode.sh`, [log](contracts/logs/e2e-devnode-k2.log) |
| Robinhood Chain testnet (46630) | `Deploy.s.sol` simulates cleanly; not broadcast yet (no deployer key set); `cargo stylus check` passes for k2 | |

Known limit (teacher v2 / model/k2; lifted by teacher v3 / model/k3, which certifies 20–90%):
the teacher's jump variance is pinned from history, so total vol must stay above 44.7%. Listed TSLA options (6-month ATM ~43%) sit below that; see
[docs/teacher-v2.md](docs/teacher-v2.md) "Listed options". Across 12 more stocks (v2 input,
[docs/multi-stock-jumps.md](docs/multi-stock-jumps.md)), 7 sit below it; a fixed jump share
of variance lands within 7.6 bps of each large cap's own fit, and misses by 16–27 bps on
stocks with rare large earnings drops.

## Stylus model status

| Check | Result |
|---|---|
| Golden vectors (100) vs Python reference | exact, native `cargo test` and on a local Nitro dev node |
| Certified domain (format v2) | outside it: `OutOfRange` / `Inconsistent` / `Uncertified`; every rule has a reject vector |
| `weightsHash()` | recomputed at build time; a flipped weight byte fails the build |
| ABI | callable from Solidity through NoteQuoter's `ISurrogatePricer` / `PricerInputs` struct |
| Activation on Robinhood Chain testnet (46630) | `cargo stylus check` passes for k2: 23,913 bytes compressed (limit 24,576; docs/k2-round2.md) |
| Execution gas per quote | **~45,000** measured on the synthetic model (3,873 params; Solidity caller, `gasleft()` delta, uncached init included); not yet re-measured for k2. A whole Desk buy incl. the k2 quote is 497,983 L2 execution gas on the dev node |
| Quantization error (int16 vs float, synthetic) | p50 0.3 / p99 1.2 / max 3.2 bps |

Default model: `model/k2` (K2 round 2, 7,465 params). CI runs all three:
`cargo test`, `PRICER_MODEL_DIR=../../model/k1-r1 cargo test` and
`PRICER_MODEL_DIR=../../model/synthetic cargo test`.

## Commands

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
contracts/script/e2e-devnode.sh                         # full lifecycle on a Nitro dev node (docker)
forge script script/Deploy.s.sol --rpc-url https://rpc.testnet.chain.robinhood.com   # add --broadcast with DEPLOYER_KEY

# Stylus contract
cd stylus/pricer-model
cargo test                                   # golden vectors + ABI path
cargo stylus check --endpoint https://rpc.testnet.chain.robinhood.com
cargo stylus deploy --endpoint <rpc> --private-key-path <file>

# Local gas measurement
docker run -d --rm --name sp-devnode -p 127.0.0.1:8547:8547 \
  offchainlabs/nitro-node:v3.11.4-7d5ac27 --dev --http.addr 0.0.0.0 --http.api=net,web3,eth,debug
```

Toolchain is pinned in `rust-toolchain.toml` (1.91.0) for reproducible
`cargo stylus verify`.
