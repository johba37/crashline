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
contracts/src/             FixingsRecorder, NoteQuoter (legacy API), MockChainlinkFeed
abi/                       interface ABIs for the frontend (contracts/script/export-abi.sh)
stylus/pricer-model/       Rust/Stylus model contract: priceBps(PricerInputs), weightsHash()
tools/pricer_quant.py      integer reference (bit-exact twin), float→int quantizer, hash
tools/make_synthetic.py    synthetic student + golden vectors (toy target, NOT the teacher)
model/k1-r1/               K1 round-1 student with its certified domain (default build)
model/synthetic/           toy student + vectors (second CI target)
ml/                        Monte Carlo teacher, student training, K1 eval (docs/k1-round0.md)
tools/certify.py           attach a certified domain, generate golden + reject vectors
docs/model-export-format.md  the distillation ↔ contract boundary
```

## Stylus model status (2026-09-29)

| Check | Result |
|---|---|
| Golden vectors (100) vs Python reference | exact, native `cargo test` and on a local Nitro dev node |
| Certified domain (format v2) | outside it: `OutOfRange` / `Inconsistent` / `Uncertified`; every rule has a reject vector |
| `weightsHash()` | recomputed at build time; a flipped weight byte fails the build |
| ABI | callable from Solidity through NoteQuoter's `ISurrogatePricer` / `PricerInputs` struct |
| Activation on Robinhood Chain testnet (46630) | `cargo stylus check` passes: 15.3 KB compressed, data fee 0.000077 ETH |
| Execution gas per quote | **~45,000** (Solidity caller, `gasleft()` delta, uncached init included, independent of input) |
| Quantization error (int16 vs float, synthetic) | p50 0.3 / p99 1.2 / max 3.2 bps |

Default model: `model/k1-r1` (K1 round 1, 1489 params, 11.2 KB). CI runs both:
`cargo test` and `PRICER_MODEL_DIR=../../model/synthetic cargo test`.

## Commands

```sh
# Python reference (numpy, torch for the synthetic fit, pycryptodome for keccak)
python3 -m venv --system-site-packages tools/.venv && tools/.venv/bin/pip install pycryptodome
cd tools && ../tools/.venv/bin/python make_synthetic.py --out ../model/synthetic

# Solidity
cd contracts && forge test && script/export-abi.sh

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
