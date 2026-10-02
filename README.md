# Surrogate Pricer

On-chain fair-value quotes for path-dependent payoffs (autocallables on stock
tokens): an integer MLP distilled from a Monte Carlo teacher, running as a
Stylus contract, behind a model-free note core on Robinhood Chain, settled in USDG.

Architecture and the lessons it's built on: [docs/architecture.md](docs/architecture.md).
Frontend guide to the frozen v1 interfaces: [docs/interfaces.md](docs/interfaces.md).
v2, built next to v1 (v1 is untouched): [docs/v2-perpetual-note.md](docs/v2-perpetual-note.md),
a perpetual note with no expiry and one token per stock, priced by a closed form plus a
learned correction. Exact rules: [docs/v2-spec.md](docs/v2-spec.md); frontend guide:
[docs/interfaces-v2.md](docs/interfaces-v2.md); self-review:
[docs/v2-contracts-review.md](docs/v2-contracts-review.md); the student:
[docs/p1-perp-student.md](docs/p1-perp-student.md).
On the roadmap: [docs/roadmap-usdg-yield.md](docs/roadmap-usdg-yield.md), interest on
the USDG that sits idle in the Desk and in the series; and
[docs/options-calibration.md](docs/options-calibration.md), a teacher calibrated to listed
option prices (the market's crash premium), one model per group of stocks; and
[docs/squared-move-note.md](docs/squared-move-note.md), the v2 frame with a rule that pays
the squared weekly move instead of a knock-in (an idea with checked numbers, nothing built).

```
contracts/src/interfaces/  frozen v1 interfaces (factory, series, tokens, quoter, Desk, recorder, pricer)
                           + IDeskCover: the Desk's WRITER leg, two prices, risk budget
                           + IDeskQueue: LP redemption queue
contracts/src/             SeriesFactory, NoteSeries, SeriesToken, AutocallPayout, NoteQuoter,
                           Desk (ERC-4626 on USDG), FixingsRecorder, MockChainlinkFeed, MockUSDG
                           v2: PerpFactory, PerpSeries, PerpToken, PerpPayout, PerpFormula + PerpMath (the
                           closed form in fixed point), PerpQuoter, PerpFormulaPricer, PerpDesk, PerpWrapper;
                           interfaces IPerpFactory, IPerpSeries, IPerpQuoter, IPerpPricer, IPerpDesk
contracts/script/          Deploy.s.sol (Robinhood testnet), e2e-devnode.sh, export-abi.sh;
                           v2: DeployPerp.s.sol, e2e-perp-devnode.sh
abi/                       interface ABIs for the frontend (contracts/script/export-abi.sh)
stylus/pricer-model/       Rust/Stylus model contract: priceBps(PricerInputs), weightsHash(); built from a
                           feature spec 2 export it is the v2 PerpPricer: correctionBps(PerpPricerInputs), product()
tools/pricer_quant.py      integer reference (bit-exact twin), float→int quantizer, hash
tools/payout_vectors.py    scalar payout reference → shared payout vectors (forge matches exactly)
tools/quoter_vectors.py    pricer inputs + quotes per model → quoter vectors
tools/make_synthetic.py    synthetic student + golden vectors (toy target, NOT the teacher)
tools/perp_vectors.py      v2: scalar reference of the perpetual fixing rule → payout vectors
tools/perp_formula.py      v2: bit-exact integer twin of PerpFormula.sol → formula vectors; --check vs 50 digits
tools/perp_quoter_vectors.py  v2: PerpQuoter's inputs and price assembly per model → quoter vectors
tools/make_synthetic_perp.py  v2: synthetic spec-2 student (toy correction, NOT the teacher)
model/p1/                  v2 student P1: correction to the closed form, vol 20-90% (docs/p1-perp-student.md)
model/synthetic-p/         v2 toy student + vectors (CI target)
model/k3/                  K3 student, vol a live input 20-90% (default build, teacher v3, docs/k3-vol-input.md)
model/k2/                  K2 round-2 student, whole note life at vol 55% (docs/k2-round2.md)
model/k1-r1/               K1 round-1 student (first week only; CI target)
model/synthetic/           toy student + vectors (CI target)
ml/                        jump-diffusion teacher (docs/teacher-v2.md), student training, evals;
                           v2: teacher_perp.py (jumps + scheduled earnings, by quadrature), perp_formula.py,
                           perp_measure.py, perp_sets.py, perp_train.py, perp_eval.py
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
| Note core, quoter, Desk | 140 forge tests pass: 260 payout-conformity vectors, quoter vectors for k1-r1, k2 and k3 (four vols), fuzz at 1,000 runs, invariants at 512 runs × 100 calls (escrow ≥ claims, no foreign clones). The Desk trades NOTE and WRITER (cover) at two prices and keeps NOTE, within a risk budget per stock; LPs can queue redemptions at any time | `forge test`, [docs/contracts-review.md](docs/contracts-review.md) |
| End to end | local Nitro dev node with the Stylus k3 pricer, listed at vol 5500 ± a 200 bps band: series struck in the past, NOTE buy, cover buy by a hedger, **mid-life sell at observationsRemaining 16 at the band's quote ± the spread**, autocall (the LP queues a redemption while the fixing is pending), redeem, collect, queue paid, LP withdraw; the same with k2 (no band) | `contracts/script/e2e-devnode.sh`, logs [k3](contracts/logs/e2e-devnode-k3.log), [k2](contracts/logs/e2e-devnode-k2.log) |
| Happy path | backend-driven on a fresh dev node with the k3 pricer: two weekly series on one product (A stays above the knock-in, B knocks in), both legs traded, every trade checked against teacher v3, matured with the dev clock, settled, redeemed, collected, LP out; USDG conserved to the base unit at every step | `backend/scenarios/happy_path.py`, logs [clock](backend/scenarios/logs/happy_path-clock.log), [hybrid](backend/scenarios/logs/happy_path-hybrid.log), [docs/backend.md](docs/backend.md#happy-path-scenario) |
| Robinhood Chain testnet (46630) | `Deploy.s.sol` simulates cleanly; not broadcast yet (no deployer key set); `cargo stylus check` passes for k2 | |

### v2 perpetual note (2026-10-02)

| Part | Result | Source |
|---|---|---|
| Note core | no expiry: every fixing ratchets the reference or knocks the note in, and releases a share of the escrow to NOTE and WRITER through a cumulative index; four fixings without a price close the series. 172 payout vectors (3,807 fixings, ten years of weekly fixings among them) reproduced exactly by the library and a live series; invariants at 256 runs × 100 calls: escrow ≥ every holder's claim + the pair value of the supply | `forge test`, `tools/perp_vectors.py`, [docs/v2-contracts-review.md](docs/v2-contracts-review.md) |
| Closed form on chain | `PerpFormula` in 1e18 fixed point: 1,764 vectors bit-exact against the integer twin; within 1.3e-7 bps of a 50-digit reference (7e-11 bps from 20% vol up); **27,476 gas** per price | `tools/perp_formula.py --check`, [log](contracts/logs/perp-formula-check.log) |
| Teacher (perpetual) | teacher v3's jump-diffusion plus a scheduled earnings move, calibrated to TSLA with 40 earnings releases from SEC EDGAR (8.32% per release; variance: diffusion 41%, jumps 51%, earnings 8%); priced by quadrature, no sampling; 16/16 checks, incl. brute-force Monte Carlo in 52 states and the fixing rule against the contracts' integers | `ml/test_teacher_perp.py`, [docs/p1-perp-student.md](docs/p1-perp-student.md) |
| Student `model/p1` | a signed correction to the closed form, vol 20–90%, 7,145 params; **GATE PASS**: formula + student within **20.9 bps** of the teacher on the gate set (p99 2.2, mean 0.49; gate 50) and 20.6 on a fresh confirmation set; the formula alone is off by up to 270. Refuses the last 6 hours before a fixing next to the two barriers. Stylus: 21,785 bytes, golden vectors exact | `ml/perp_eval.py`, [docs/p1-perp-student.md](docs/p1-perp-student.md) |
| Quoter, Desk, wrapper | price = closed form + student + coupon reserve (the price is linear in the reserve, so one model serves any coupon); the Desk trades both legs at two prices with the v1 risk budget and queue, counts the USDG its tokens are paid at once, takes the earnings date per listing and a capped weekend price; the wrapper reinvests for holders that never claim. Desk invariants at 96 runs × 120 calls: no unexpected revert, never both legs, USDG accounted to the base unit. A second, adversarial review confirmed eight defects outside the note core; seven are fixed with regression tests, one is v1's queue behaviour ([review](docs/v2-contracts-review.md), section 10) | `forge test` (281 tests: 140 v1, 141 v2), [docs/interfaces-v2.md](docs/interfaces-v2.md) |
| End to end | Nitro dev node with the Stylus `PerpPricer`: a series with five past fixings (one ratchets the reference), NOTE and cover bought, NOTE wrapped, a sell, **every quote equal to the Python integer twins**, then a fixing that knocks the note in, claims, collect, the wrapper reinvesting, everybody out; USDG conserved to the base unit. A Desk buy with a vol band (three quotes) is 787,363 L2 execution gas with `model/p1`, 753,381 with the formula-only pricer (v1 with k3: 775,042) | `contracts/script/e2e-perp-devnode.sh`, logs [p1](contracts/logs/e2e-perp-devnode-p1.log), [formula only](contracts/logs/e2e-perp-devnode-formula.log), [synthetic-p](contracts/logs/e2e-perp-devnode-synthetic-p.log) |
| Robinhood Chain testnet (46630) | `DeployPerp.s.sol` simulates cleanly; not broadcast (no deployer key set) | |

Not built: a merge of series that reached the same state, stock-split handling, a pool
adapter for the weekend price (interface and mock only), v2 in the backend service.

Known limit (teacher v2 / model/k2; lifted by teacher v3 / model/k3, which certifies 20–90%):
the teacher's jump variance is pinned from history, so total vol must stay above 44.7%.
Listed TSLA options (6-month ATM ~43%) sit below that; see
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
| Execution gas per quote | **~45,000** measured on the synthetic model (3,873 params; Solidity caller, `gasleft()` delta, uncached init included); not yet re-measured for k2/k3. A whole Desk buy incl. the k2 quote and the risk-budget check is 647,750 L2 execution gas on the dev node (497,983 before the check); with k3 and a vol band (two model calls) 775,042 |
| Quantization error (int16 vs float, synthetic) | p50 0.3 / p99 1.2 / max 3.2 bps |
| One crate, two contracts (2026-10-02) | a feature spec 2 export builds the v2 `PerpPricer` (`model/p1`: 21,785 bytes, golden vectors exact, dev node e2e); a spec 1 export still builds `SurrogatePricer` (`model/k3`: 23,879 bytes after the change, 23,901 before; all four v1 models pass their golden vectors and the v1 e2e passes) |

Default model: `model/k3` (K3, 7,465 params, vol 20–90%; `model/k2` until 2026-10-01). CI runs all four:
`cargo test`, `PRICER_MODEL_DIR=../../model/k2 cargo test`,
`PRICER_MODEL_DIR=../../model/k1-r1 cargo test` and
`PRICER_MODEL_DIR=../../model/synthetic cargo test`; and the two feature spec 2 models,
which build the v2 `PerpPricer` contract from the same crate:
`PRICER_MODEL_DIR=../../model/p1 cargo test` and `PRICER_MODEL_DIR=../../model/synthetic-p cargo test`.

## Commands

```sh
# Python reference (numpy, torch for the synthetic fit, pycryptodome for keccak)
python3 -m venv --system-site-packages tools/.venv && tools/.venv/bin/pip install pycryptodome
cd tools && ../tools/.venv/bin/python make_synthetic.py --out ../model/synthetic

# Teacher and student
tools/.venv/bin/python ml/test_teacher.py              # teacher checks (a)-(h); (g) uses /opt/ai/cache/venv-cuda if present
tools/.venv/bin/python ml/round2_eval.py --model model/k2   # gate on the held-out set T

# Solidity (v1 and v2)
cd contracts && forge fmt --check && forge test && script/export-abi.sh
tools/.venv/bin/python tools/payout_vectors.py          # regenerate the payout vectors
contracts/script/e2e-devnode.sh                         # full lifecycle on a Nitro dev node (docker), model/k3
PRICER_MODEL_DIR=model/k2 contracts/script/e2e-devnode.sh   # the same with K2 (vol pinned, no band)
forge script script/Deploy.s.sol --rpc-url https://rpc.testnet.chain.robinhood.com   # add --broadcast with DEPLOYER_KEY

# v2 perpetual note
tools/.venv/bin/python tools/perp_vectors.py            # fixing-rule vectors
tools/.venv/bin/python tools/perp_formula.py            # closed-form vectors; --check: vs a 50-digit reference (needs mpmath)
tools/.venv/bin/python tools/perp_quoter_vectors.py --model model/p1 --out contracts/test/vectors/perp_quoter_vectors_p1.json
contracts/script/e2e-perp-devnode.sh                    # lifecycle on a Nitro dev node (docker, port 8657), model/p1
PERP_PRICER=formula contracts/script/e2e-perp-devnode.sh   # the same with the closed form alone (no Stylus build)
forge script script/DeployPerp.s.sol --rpc-url https://rpc.testnet.chain.robinhood.com

# Stylus contract
cd stylus/pricer-model
cargo test                                   # golden vectors + ABI path
PRICER_MODEL_DIR=../../model/p1 cargo test   # v2: builds the PerpPricer contract (feature spec 2)
cargo stylus check --endpoint https://rpc.testnet.chain.robinhood.com
cargo stylus deploy --endpoint <rpc> --private-key-path <file>

# Local gas measurement
docker run -d --rm --name sp-devnode -p 127.0.0.1:8547:8547 \
  offchainlabs/nitro-node:v3.11.4-7d5ac27 --dev --http.addr 0.0.0.0 --http.api=net,web3,eth,debug
```

Toolchain is pinned in `rust-toolchain.toml` (1.91.0) for reproducible
`cargo stylus verify`.
