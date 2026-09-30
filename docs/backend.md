# Backend for the frontend

What runs on the host for the frontend and the demo video, and how to use it.
Spec: [backend-spec.md](backend-spec.md). Everything binds to 127.0.0.1; the
frontend reaches it through an SSH tunnel.

| Port | What | Started by |
|---|---|---|
| 8647 | Nitro dev node, JSON-RPC (chain id 412346) | `backend/devnode/up.sh` |

## Setup (once per host)

```sh
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt   # Python 3.12
```

Also needed: docker, `forge`/`cast` (`$HOME/.foundry/bin`), `cargo` with
`cargo-stylus` (`$HOME/.cargo/bin`), and for `/verify-quote` on the GPU the
CUDA torch venv `/opt/ai/cache/venv-cuda`.

## Dev node (WP0)

```sh
backend/devnode/up.sh                                   # create or start sp-devnode, check it sequences
backend/.venv/bin/python backend/devnode/deploy.py      # deploy, fund, stage, list; writes backend/config.json
backend/devnode/down.sh                                 # graceful stop; the chain stays
backend/devnode/down.sh --purge                         # stop and delete the chain
backend/devnode/up.sh --recreate                        # start over with an empty chain (then deploy.py)
```

- Container `sp-devnode`, image `offchainlabs/nitro-node:v3.11.4-7d5ac27 --dev`,
  host port `DEVNODE_PORT` (8647) bound to 127.0.0.1, no `--rm`. Flags beyond
  the e2e script: `--http.corsdomain='*' --http.vhosts='*'` (browser `fetch`
  through a tunnel), `--http.api=net,web3,eth,debug`, and
  `--execution.caching.archive` (state at past blocks, for the history backfill).
- The chain lives in the named volume `sp-devnode-data`, mounted where `--dev`
  keeps its data (`/tmp/dev-test`; `--persistent.global-config` is overridden
  by `--dev`). `up.sh` hands the fresh volume to the container's uid first.
- **Persistence.** `docker restart sp-devnode`, `down.sh` + `up.sh`, and a
  host shutdown (Docker stops containers with SIGTERM; the stop timeout is
  60 s) keep the chain: blocks, state and the deployment survive. A **hard
  kill** (SIGKILL, power loss) keeps the blocks but can leave the dev
  sequencer unable to sequence (`wrong msgIdx got N expected 1`). `up.sh`
  checks this with a 0-value transfer and exits 3; then
  `up.sh --recreate && deploy.py` starts over (`backend/ops/start.sh` does it
  on its own, WP6).
- Blocks are made only by transactions: views (`eth_call` at `latest`) see the
  time of the last block. A 0-value transfer ("poke") moves the chain's clock
  to now; the deploy script and the demo routes poke before they read.
- The genesis block of every fresh `--dev` chain is identical (hash
  `0x91d25f…0daa`), so a reset is recognized by the hash of the deployment
  block, which `config.json` records (`deploymentBlockHash`).

### What `deploy.py` does

The e2e script's steps 0-6, without the trades
(`contracts/script/e2e-devnode.sh`):

1. `cargo stylus deploy` of `stylus/pricer-model` with `model/k2` compiled in
   (build dir `backend/.build/stylus-target`); checks the deployed
   `weightsHash` against `model/k2/student_export.json`.
2. MockUSDG, SeriesFactory, NoteQuoter(93600 = 26 h staleness), Desk (curator =
   the dev account, 60 s pre-observation band), deployed from a forge build of
   a copy of `contracts/src` in `backend/.build/sol` (libraries at the pinned
   submodule commits in `backend/.build/lib`), so nothing under `contracts/` is
   written.
3. Funds the three anvil test accounts with 1 ETH and 100,000 USDG each, and
   seeds the vault: the dev account deposits 100,000 USDG (the "house" LP, so
   the test accounts start without positions).
4. Stages the default scenario: a MockChainlinkFeed `RHTSLA` (initial $250.00)
   with a round at the strike and at each past observation (the e2e
   `PATH_BPS`: a knock-in at observation 3, then recovery), the current spot at
   8500 bps of initial; a series with the K2 terms (ki 6000, ac 10000, coupon
   25 bps, 26 weekly observations) whose strike is 11 weeks before the next
   observation, so 10 observations are done and 16 remain; the strike and the
   10 fixings recorded, `advance()`. The series is **knocked in**.
5. Lists it: vol 5500, cap 100,000 NOTE, spread bid 20 / ask 30 / vol band 0,
   risk budget 2000 bps on its feed. Checks `quoteBuy(series, 1 NOTE, 0)`.
6. Writes `backend/config.json` and `deployments/412346.json`.

`--lead-secs` (env `DEVNODE_LEAD_SECS`) sets how far the next observation lies
ahead: **3 days** by default, so a long-lived node stays quotable (the e2e
script uses 240 s because it waits for the observation). Two things end
quotability on a node left alone: after 26 h without a feed round the quoter
refuses with `FeedStale` (push one with `/demo/feed`), and once the next
observation passes its fixing must be recorded (`/demo/fixing`).
`--if-missing` does nothing when `config.json`'s deployment is on the chain.
A full deploy takes about 15 s once the Stylus build is cached (the first
build about a minute).

### Accounts on the dev node

| Role | Address | Key |
|---|---|---|
| dev (curator, demo signer, house LP) | `0x3f1eae7d46d88f08fc2f8ed27fcb2ab183eb2d0e` | `0xb6b15c8cb491557369f3c7d2c287b053eb229daa9c22138887752191c9520659` (Nitro's public dev key) |
| LP (anvil #1) | `0x70997970c51812dc3a010c7d01b50e0d17dc79c8` | `0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d` |
| buyer (anvil #2) | `0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc` | `0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a` |
| hedger (anvil #3) | `0x90f79bf6eb2c4f870365e785982e1f101e93b906` | `0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6` |

These keys are public; they exist only on the local dev chain.

### `backend/config.json`

Generated by `deploy.py` (and rewritten by `/demo/stage` and `/demo/reset`),
so it is not committed: chain id, the RPC URL the service uses (`rpcUrl`) and
the one `/config` reports through the tunnel (`publicRpcUrl`), the deployment
block and its hash, the contract addresses (feeds by name), the model dir, the
demo settings (`enabled`, the env var names of the demo key and token), the
dev node's container, volume and port, the backend port, the SQLite path, the
poll interval and the history step, and the test accounts.
`deployments/412346.json` has the same addresses in the layout
`contracts/script/Deploy.s.sol` writes for the testnet.

### Choices where the spec is silent

- Build artifacts go to `backend/.build` (Solidity from a copy of
  `contracts/src`, the Stylus target dir), not into `contracts/` or `stylus/`.
- Default lead 3 days instead of the e2e script's 240 s (above).
- The vault seed comes from the dev account, not the LP test account.
- `config.json` and `deployments/412346.json` are generated, so they are
  git-ignored.

### Tests

```sh
cd backend && .venv/bin/python -m pytest tests -q                # against the dev node; skipped if it is down
SP_CLEAN=1 .venv/bin/python -m pytest tests/test_wp0_devnode.py   # also recreate the chain and deploy from scratch
```

`tests/test_wp0_devnode.py`: the deployment is quotable and listed as
specified, `cast call` from the host, CORS preflight and response headers,
`docker restart sp-devnode` keeps blocks and state and still sequences,
`eth_call` at past blocks (archive), and (with `SP_CLEAN=1`) `up.sh --recreate`
+ `deploy.py` from an empty chain.
