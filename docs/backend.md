# Backend for the frontend

What runs on the host for the frontend and the demo video, and how to use it.
Spec: [backend-spec.md](backend-spec.md).

## For the frontend: start here

Two things run on the host, both bound to 127.0.0.1 and reached through an SSH tunnel:

| Port | What |
|---|---|
| **8647** | the Nitro dev node, JSON-RPC, **chain id 412346** (a local Arbitrum chain: 1 block per transaction) |
| **8650** | the REST service: catalog, history, accounts, the off-chain model, demo control |

```sh
ssh -N -L 8647:127.0.0.1:8647 -L 8650:127.0.0.1:8650 max@<host>
curl localhost:8650/config          # everything below starts here
```

**Reads and writes.** Live quotes, balances and every transaction go to the
node over JSON-RPC (wagmi/viem, the ABIs in `abi/`). The service gives what
the RPC can't give cheaply: the series catalog with its state and mid
(`/series`), trades and events, history for charts, per-wallet positions with
cost basis (`/accounts/{addr}`), the vault (`/vault`), feeds and risk room
(`/feeds/{addr}`), the model's curves and a teacher check (`/verify-quote`),
and demo control (`/demo/*`). Every response says which block it reflects
(`block`, `time`); poll it (every few seconds is fine), there are no
websockets.

**Addresses come from `GET /config`**, never from constants: the dev node's
addresses change with every `/demo/reset`. `/config` also has the model's
certified domain and the Desk's fee caps and queue constants.

**wagmi.** A chain entry for the dev node, next to Robinhood testnet, chosen by env:

```ts
// frontend/src/wagmi.ts
import { defineChain } from 'viem'
import { robinhoodTestnet } from 'wagmi/chains'

export const crashlineDevnode = defineChain({
  id: 412346,
  name: 'Crashline dev node',
  nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
  rpcUrls: { default: { http: [import.meta.env.VITE_RPC_URL ?? 'http://localhost:8647'] } },
  testnet: true,
})

const chainId = Number(import.meta.env.VITE_CHAIN_ID ?? robinhoodTestnet.id)
const chain = chainId === crashlineDevnode.id ? crashlineDevnode : robinhoodTestnet
export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8650'
// getDefaultConfig({ ..., chains: [chain], transports: { [chain.id]: http(import.meta.env.VITE_RPC_URL) } })
```

```sh
# frontend/.env.local for the dev node
VITE_CHAIN_ID=412346
VITE_RPC_URL=http://localhost:8647
VITE_API_URL=http://localhost:8650
```

**Wallets.** In MetaMask (or Rabby): add a network with chain id 412346, RPC
`http://localhost:8647`, currency ETH, and import the test accounts' keys.
They are funded at every deploy with 1 ETH and 100,000 USDG (MockUSDG, 6
decimals; its `mint(to, amount)` is public, and `/demo/faucet` tops up any
address):

| Account | Address | Private key |
|---|---|---|
| LP (anvil #1) | `0x70997970c51812dc3a010c7d01b50e0d17dc79c8` | `0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d` |
| buyer (anvil #2) | `0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc` | `0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a` |
| hedger (anvil #3) | `0x90f79bf6eb2c4f870365e785982e1f101e93b906` | `0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6` |

MetaMask caches nonces per network: after a `/demo/reset` use "Clear activity
tab data" (Settings → Advanced) or transactions stay pending.

**Demo control** (`POST /demo/faucet|feed|fixing|stage|reset`, below) takes
the header **`X-Demo-Token: sp-devnode-demo`** on the dev node (the host's
operator can change it in `backend/.env`).

**What the dev node looks like after a deploy or a reset**: one series on the
feed `RHTSLA` (initial $250.00) with the K2 terms (knock-in 60 %, autocall
100 %, 25 bps a week, 26 weekly observations), 10 observations done (it
knocked in at the 3rd), 16 to go, the next one 3 days out, spot 85 %,
priced by the Stylus **k3** pricer (vol a live input, 20–90 %), listed at
vol 5500 with a vol band of 200 bps and a 20/30 bps spread, and a 20 % risk
budget; the vault holds 100,000 USDG. `/series` has both legs' bid and ask
(`quotes`). The chain's clock moves only with transactions (each
`/demo/*` call moves it to now). Quotes stop when the feed is more than 26 h
old (`/demo/feed` pushes a round) and when the next observation has passed
(`/demo/fixing` records it).

**The testnet** (Robinhood Chain testnet, 46630) uses the same service with a
`config.json` made from `deployments/46630.json` (`backend/ops/make-config.py`,
below), the testnet RPC, and the demo routes off (403). The frontend then uses
`VITE_CHAIN_ID=46630`, the testnet RPC and wherever that service runs; the
routes and fields are the same. The public demo on GitHub Pages targets the
testnet.

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
  `up.sh --recreate && deploy.py` starts over (the `sp-devnode` unit does it
  on its own, WP6). It can also lose the last few blocks (the service rolls
  back, WP6).
- Blocks are made only by transactions: views (`eth_call` at `latest`) see the
  time of the last block. A 0-value transfer ("poke") moves the chain's clock
  to now; the deploy script and the demo routes poke before they read.
- The genesis block of every fresh `--dev` chain is identical (hash
  `0x91d25f…0daa`), so a reset is recognized by the hash of the deployment
  block, which `config.json` records (`deploymentBlockHash`).

### Dev clock (DEV ONLY)

Nitro has no time-travel RPC (no `evm_increaseTime`), so a weekly note can't
mature in a test run on the stock node. The dev clock is a patched image,
**`sp-nitro-node:v3.11.4-7d5ac27-clock`**, that only a local `--dev` node
uses. **Robinhood testnet and any mainnet run stock Nitro.** Nothing in
`contracts/` or the backend depends on the patch.

```sh
backend/devnode/nitro-clock/build.sh                      # build the image (~9 min cold, docker buildx, ~25 GB cache)
DEVNODE_CLOCK=1 backend/devnode/up.sh --recreate          # a dev node on the clock image (fresh chain)
backend/devnode/clock.sh show                             # offset and latest block timestamp
backend/devnode/clock.sh advance 604800                   # +1 week; mines a block at the new time
backend/devnode/clock.sh set-absolute 1792000000          # next block at that time (never backwards)
```

- **The patch** (`backend/devnode/nitro-clock/sequencer-clock.patch`, against
  OffchainLabs/nitro v3.11.4 = 7d5ac27, the stock image's commit) changes one
  line, where the sequencer stamps a block (`execution/gethexec/sequencer.go`,
  `timestamp := time.Now().Unix()`). It becomes `time.Now().Unix() +
  devClockOffset()`, and adds `devclock.go`. The offset is an integer number
  of seconds, re-read for every block from the file named by
  `SP_CLOCK_OFFSET_FILE`. If the env is unset, the file is missing or the
  value is unparsable or negative, the offset is 0, which is stock behaviour.
  The node logs `DEV ONLY: block timestamp offset changed` when the offset
  changes. ArbOS still never lets a block's time fall below its parent's.
- **The image** is the stock image with `/usr/local/bin/nitro` replaced.
  `build.sh` shallow-clones nitro (default `~/.cache/sp-nitro-src`, set
  `NITRO_SRC`), applies the patch and appends `Dockerfile.clock` to Nitro's
  own Dockerfile. Only the stages the node binary needs are built: brotli,
  contracts, `libstylus.a` and the Go build. The JIT, prover and replay
  machines are skipped; `--dev` doesn't validate. Stylus works as on stock
  (deploy, activation and calls).
- **Disk.** A cold build needs about **25 GB of BuildKit cache** (21 GB
  measured). With docker's default builder and the containerd image store,
  that cache lives under `/var/lib/containerd`, which is on the **root disk**
  on this host, even though docker's data-root is `/opt/ai/docker`. So
  `build.sh` prunes the whole build cache when it finishes
  (`docker builder prune -af`). Set `KEEP_BUILD_CACHE=1` to keep it.
  `SP_BUILDER=container` builds in a throwaway `docker-container` builder
  instead: its state is a docker volume under the data-root, and the builder
  and the volume are removed afterwards. That path isn't exercised yet. Only
  the image stays: about 1.5 GB of content, about 5.2 GB with the stock layers
  it shares.
- **`up.sh`**: `DEVNODE_CLOCK=1` (or `DEVNODE_IMAGE=<the clock image>`) runs
  the clock image with `SP_CLOCK_OFFSET_FILE=/tmp/dev-test/clock-offset`. That
  file is inside the chain's volume, so the offset survives restarts and block
  time stays monotonic. Image and env are fixed at container creation, so
  switching an existing node needs `--recreate`, and `up.sh` warns on a
  mismatch. `DEVNODE_IMAGE` alone selects any image. The default is still the
  stock image.
- **`clock.sh`** (honors `DEVNODE_NAME`/`DEVNODE_PORT`) writes the offset
  through `docker exec`. The offset only grows. `advance` and `set-absolute`
  send a 0-value self-transfer from the dev key, print the old and new latest
  block timestamps, and exit 1 unless `latest.timestamp` moved by at least the
  request. On a stock container it refuses, because there is no offset file.
- Blocks are still made only by transactions, so the dev node shows the new
  time only after a block. `advance` mines one; after that, every block is
  wall clock + offset.
- `contracts/script/e2e-devnode.sh` runs on the clock image with
  `E2E_IMAGE=sp-nitro-node:v3.11.4-7d5ac27-clock` (offset 0: no offset file is
  passed).

### What `deploy.py` does

The e2e script's steps 0-6, without the trades
(`contracts/script/e2e-devnode.sh`):

1. `cargo stylus deploy` of `stylus/pricer-model` with `--model-dir` compiled
   in (default `model/k3`, env `PRICER_MODEL_DIR`; build dir
   `backend/.build/stylus-target`, or `CARGO_TARGET_DIR`); checks the deployed
   `weightsHash` against the model's `student_export.json`.
2. MockUSDG, SeriesFactory, NoteQuoter(93600 = 26 h staleness), Desk (curator =
   the dev account, 60 s pre-observation band), deployed by a fresh random
   deployer key (funded by the dev account) from a forge build of
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
5. Lists it: vol 5500 (`--vol`), cap 100,000 NOTE, spread bid 20 / ask 30,
   vol band 200 bps (`--vol-band`; 0 when the model's certified vol is one
   value, as for `--model-dir model/k2`), risk budget 2000 bps on its feed.
   Checks `quoteBuy(series, 1 NOTE, 0)`.
6. Writes `backend/config.json` (or `BACKEND_CONFIG`) and
   `deployments/412346.json`. The default listing is recorded as
   `defaultListing` and `/demo/reset` keeps it; the node's container and
   volume names come from `DEVNODE_NAME` / `DEVNODE_VOLUME`, so a reset on a
   second node recreates that node.

`--model-dir model/k2` deploys the K2 student (vol pinned at 5500, no band),
as the dev node did before 2026-10-01.

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

### Choices where the spec is silent (WP0)

- Build artifacts go to `backend/.build` (Solidity from a copy of
  `contracts/src`, the Stylus target dir), not into `contracts/` or `stylus/`.
- Default lead 3 days instead of the e2e script's 240 s (above).
- The vault seed comes from the dev account, not the LP test account.
- The contracts (and the pricer) are deployed by a new random key each time:
  a fresh dev chain replays the dev key's nonces, so deploying from it would
  give every reset the same addresses. The dev key stays the curator, the
  owner of the mock feeds (it pushes their rounds) and the house LP.
- `config.json` and `deployments/412346.json` are generated, so they are
  git-ignored.

## The service (WP1)

```sh
backend/run.sh     # uvicorn app.main:app on 127.0.0.1:$BACKEND_PORT (8650): the API and the indexer, one process
```

`run.sh` sources `backend/.env` if it exists (`DEMO_TOKEN`, `DEMO_KEY`,
`TEACHER_DEVICE`, ...). `BACKEND_CONFIG` points at another config file,
`BACKEND_DB` at another SQLite file (the tests run a second instance on 8651 this way).

### How it works

- **Indexer** (`app/indexer.py`): a thread that polls `eth_blockNumber` every
  2 s (`pollSecs`) and indexes new blocks in chunks from the deployment block
  with two `eth_getLogs` per chunk: the factory's `SeriesCreated` and
  `RecorderDeployed` first, then the Desk's, the series' and the recorders'
  events (`SeriesListed/Delisted`, `SpreadSet`, `RiskBudgetSet`, the four
  trade events, `Collected`, the four queue events, `Deposit`, `Withdraw`,
  `Minted`, `PairRedeemed`, `Redeemed`, `Struck`, `ObservationProcessed`,
  `Settled`, `FixingRecorded`). Tables `blocks` (blocks with events, and the
  head), `series`, `trades`, `events` in `backend/data/412346.sqlite`. Rows and
  the new head are one transaction; (txHash, logIndex) is the key, so a
  restart resumes where it stopped and never duplicates.
- **Resets**: the db stores a fingerprint of the deployment (chain id, genesis
  hash, deployment block and its hash, Desk). When `config.json` changes (the
  indexer re-reads it when its mtime changes) and describes another
  deployment, the db is wiped and rescanned. When the chain no longer holds
  the deployment (node recreated, not redeployed), `/health` and every route
  answer 503 `DeploymentMissing` until it is redeployed.
- **Consistency**: a response reflects the indexer's head: every `eth_call`
  runs at that block (batched), every db read stops at it, and
  `{"block", "time"}` are that block's number and timestamp. The head lags the
  chain by at most one poll.

### Conventions

- Every uint amount is a decimal **string** (USDG and NOTE/WRITER base units, 6
  decimals; shares 12 decimals; feed prices 8 decimals); more generally every
  integer wider than 53 bits is a string (`roundId`, prices, an `int64` in an
  error's args), because JavaScript numbers can't hold it. bps, counts, times
  (unix seconds) are numbers. Addresses are lowercase.
- Errors: HTTP 4xx/5xx with `{"error": "<Name>", "args": {...}}`: 400
  `BadAddress` / `BadRequest`, 404 `UnknownSeries`, 409 with a contract
  error's name for a revert, 502 `RpcError`, 503 `NotReady` /
  `DeploymentMissing`.
- CORS allows every origin. No auth except the demo routes' token.

### Routes

`GET /health` — 200 `{ok, status, block, time}` once the indexer has a head, else 503.

`GET /config`

```json
{ "chainId": 412346, "rpcUrl": "http://localhost:8647", "deploymentBlock": 71,
  "addresses": { "usdg": "0x…", "seriesFactory": "0x…", "noteQuoter": "0x…", "desk": "0x…",
                 "surrogatePricer": "0x…", "feeds": { "RHTSLA": "0x…" } },
  "model": { "dir": "model/k3", "weightsHash": "0x745c…523f", "featureSpecVersion": 1,
             "certifiedDomain": { "ranges": [ … ], "consistency": { … }, "exclusions": [ … ] } },
  "desk": { "maxFeeBps": 200, "maxCoverFeeBps": 1000, "backstopShareBps": 5000,
            "minSecsToObservation": 60, "minRequestShares": "10000000000000", "queueBatch": 8 },
  "demo": true, "commit": "<git sha>", "block": 262, "time": 1790800770 }
```

Take every contract address from here: the dev node's addresses change with
every reset. `rpcUrl` is the node as seen through the tunnel.

`GET /series` — `{"series": [ <series> ... ]}` without `fixings` and `trades`.

`GET /series/{addr}` — one series:

```json
{ "address": "0x32fb…eab8", "note": "0x1fb9…1465", "writer": "0xeab8…48c2",
  "recorder": "0xbacb…7291", "feed": "0xc646…839d", "feedName": "RHTSLA", "id": "0xa863…6b3a",
  "terms": { "strikeTime": 1784407017, "observationInterval": 604800, "observationCount": 26,
             "kiBarrierBps": 6000, "acBarrierBps": 10000, "couponBpsPerPeriod": 25 },
  "maxPayoutPerNote": "1067500", "maxBps": 10675,
  "state": { "phase": "Live", "initialFixing": "25000000000", "observationsDone": 10,
             "knockedIn": true, "autocalled": false, "nextObservation": 1791059817,
             "maturity": 1800736617, "payoutPerNote": "0",
             "pendingObservation": { "pending": false, "obsTime": 1791059817 } },
  "listing": { "active": true, "pricer": "0xfa52…593d", "volBpsAnnual": 5500,
               "capNotional": "100000000000", "writerHeld": "1200000000" },
  "spread": { "bidBps": 20, "askBps": 30, "volBandBps": 200 },
  "desk": { "noteHeld": "0", "writerHeld": "1200000000" },
  "quotable": { "ok": true, "reason": null, "args": {}, "until": null },
  "mid": { "noteBps": 8838, "coverBps": 1837 },
  "quotes": { "note": { "bidBps": 8800, "askBps": 8887 }, "cover": { "bidBps": 1788, "askBps": 1875 }, "errors": {} },
  "fixings": [ { "obsTime": 1784407017, "price": "25000000000", "roundId": "18446744073709551617",
                 "index": 0, "updatedAt": 1784407017 }, … ],
  "trades": [ <trade>, … ],
  "block": 262, "time": 1790800770 }
```

- `quotable` is `quoteBuy(series, 1 NOTE, 0)` at the response's block, its
  revert decoded: `NotListed`, `FixingPending`, `FeedStale`,
  `TooCloseToObservation`, `OutOfRange`, `Uncertified`, `NotLive` (not struck
  yet), `Settled` (the quoter's `NotLive` on a settled series), and in
  principle `Inconsistent` / `BadFeedAnswer`. `args` are the error's
  arguments. `until` is when the reason clears if that is known: the
  observation time for `TooCloseToObservation` (the band ends there) and for
  `Uncertified` (K2's excluded regions are observation-day bands); null
  otherwise (a feed round, a recorded fixing or a listing can't be predicted).
- A `CapExceeded` from that quote (the Desk's WRITER cap is full, so it can't
  sell NOTE beyond its inventory) counts as quotable: the series has a price;
  the cap is in `listing.capNotional` / `writerHeld`.
- `mid` is the quoter's NOTE fair value at the listing's vol
  (`notePriceBps`), and `coverBps = maxBps - noteBps`; null when not quotable.
  The Desk's two prices are `mid ± spread` (with a vol band: see
  `IDeskCover.sol`).
- `quotes`: the Desk's prices at 1 unit, no fee, at the response's block:
  `{"note": {"bidBps", "askBps"}, "cover": {"bidBps", "askBps"}, "errors": {}}`
  from `quoteSell` / `quoteBuy` / `quoteSellCover` / `quoteBuyCover`: the band
  and the flat spread applied (NOTE ask = higher band quote + ask, NOTE bid =
  lower − bid, cover ask = maxBps − NOTE bid, cover bid = maxBps − NOTE ask).
  A quote that reverts is null with its error in `errors` (e.g.
  `"note.askBps": {"error": "CapExceeded", ...}` when the WRITER cap is full).
  null when not quotable.
- `fixings`: the recorder's fixings on this series' schedule, `index` 0 =
  strike, 1..count observations, count + 1 = maturity; `updatedAt` is the feed
  round's time. `trades`: the last 50, newest first.

`GET /trades?series=&account=&limit=&before=` — newest first, `limit` 1..500
(default 50), `account` matches the trader or the recipient, `before` is a
block number (exclusive) for paging.

```json
{ "txHash": "0xe441…8bb0", "logIndex": 7, "block": 101, "time": 1790800712,
  "series": "0x32fb…eab8", "kind": "buy", "account": "0x3c44…93bc", "to": "0x3c44…93bc",
  "amount": "100000000", "priceBps": 8868, "usdg": "88680000", "feeBps": 0,
  "feeReceiver": "0x0000000000000000000000000000000000000000", "weightsHash": "0xaf76…cd8a" }
```

`kind` is `buy` | `sell` | `buyCover` | `sellCover`; `amount` is NOTE for
buy/sell and WRITER for the cover trades; `priceBps` is the price applied
(spread included) of that leg; `usdg` is the cost (buys) or the proceeds
(sells), fee included.

`GET /events?series=&account=&name=&limit=&before=` — every other indexed
event with its decoded args (`Minted`, `PairRedeemed`, `Redeemed`, `Struck`,
`ObservationProcessed`, `Settled`, `FixingRecorded`, `SeriesCreated`,
`SeriesListed`, `SpreadSet`, `RiskBudgetSet`, `Collected`, `Deposit`,
`Withdraw`, the queue events, ...), newest first; `account` matches the
event's owner/recipient or its sender/caller.

```json
{ "txHash": "0xe441…8bb0", "logIndex": 5, "block": 101, "time": 1790800712,
  "address": "0x32fb…eab8", "name": "Minted", "series": "0x32fb…eab8", "feed": "0xc646…839d",
  "args": { "caller": "0xab0f…f4ce", "to": "0xab0f…f4ce", "amount": "100000000", "collateralIn": "106750000" } }
```

### Choices where the spec is silent (WP1)

- `before` in `/trades` is a block number (the dev node puts one tx in a block).
- `quotable` treats `CapExceeded` as quotable (above).
- `/events` also takes `name` and `before`; `/health` exists for scripts and the tests.
- Amounts in `/events` args follow the 53-bit rule above.

## Accounts, vault, feeds (WP2)

`GET /accounts/{addr}`

```json
{ "usdg": "97867680000", "shares": "0", "sharesValue": "0",
  "positions": [ { "series": "0x32fb…eab8", "note": "2400000000", "writer": "0",
                   "noteMark": "2121120000", "coverMark": "0",
                   "costBasis": { "note": "2132320000", "writer": "0" }, "realized": "0" } ],
  "queue": [ { "id": 3, "shares": "20000000000000000", "requestedAt": 1790801031, "position": 0 } ],
  "claimable": "0",
  "trades": [ <trade>, … ],
  "block": 436, "time": 1790800907 }
```

- `usdg`: USDG balance. `shares`: Desk shares held (12 decimals; shares in the
  queue are held by the Desk and not counted). `sharesValue`:
  `convertToAssets(shares)`, null while the vault's NAV is unknown (a held
  series can't be quoted).
- `positions`: every series where the account holds NOTE or WRITER or has
  trade history. `noteMark`/`coverMark`: the holding at the Desk's marks
  (the quoter's mid capped at the max payout; the payout itself once it is
  certain), null when that needs a quote and there is none.
- `costBasis` and `realized` (USDG base units, `realized` may be negative) are
  FIFO over the account's Desk trades: a buy or buyCover whose recipient is
  the account opens a lot at the USDG paid (fee included); a sell or
  sellCover by the account closes lots at the proceeds (net of fee); a
  settlement `redeem` by the account closes lots at each leg's payout (from
  the series' `Settled` event). Tokens that arrived another way (mint,
  transfer) have no lot; selling more than the lots hold realizes the rest at
  zero cost. Mints and pair redemptions are not trades and don't move the basis.
- `queue`: the account's open redemption requests (unfilled shares > 0;
  `redeemRequest(id)` at the response's block); `position` is the number of
  open requests ahead of it. `claimable`: USDG set aside for filled requests.
- `trades`: the last 50 where the account traded or received.

`GET /vault`

```json
{ "totalAssets": "100009200000", "totalSupply": "100000000000000000", "sharePrice": "1.000091999999",
  "navError": null, "idle": "99568320000", "reserved": "0", "queuedShares": "0",
  "queue": { "head": 0, "length": 0 },
  "feeds": { "0xc646…839d": { "atRisk": "440880000", "limit": "20001840000", "room": "19560960000" }, … },
  "inventory": [ { "series": "0x32fb…eab8", "noteHeld": "0", "writerHeld": "1200000000",
                   "mark": "440880000", "atRisk": "440880000", "quotable": true } ],
  "block": 436, "time": 1790800907 }
```

- `sharePrice`: USDG per share, OpenZeppelin's conversion with the Desk's
  6-decimal offset, `1e6 * (totalAssets + 1) / (totalSupply + 1e6)`, 12
  decimal places.
- `totalAssets` reverts on-chain while a held series can't be quoted (a
  pending fixing, a stale feed, an observation-day band): then `totalAssets`
  and `sharePrice` are null and `navError` is the revert
  (`{"error": "FixingPending", "args": {...}}`). Deposits and ERC-4626
  withdrawals are closed in that state; queue requests are not.
- `idle` = USDG balance − `reserved` (USDG set aside for claims).
- `feeds`: `risk(feed)` for every feed of an indexed series; `room =
  limit − atRisk`, negative when the feed is over its budget (vault assets
  fell). Quotes don't check it: read it before offering a trade that adds to
  the Desk's position (IDeskCover).
- `inventory`: `heldSeries()`, each with the Desk's balances, its mark and
  what it can lose (Desk._position in Python, `app/accounting.py`);
  `quotable: false` rows are valued at the NOTE's coupons, with both legs
  fully at risk, as the risk check does.

`GET /feeds/{addr}?from=&to=`

```json
{ "address": "0xc646…839d", "name": "RHTSLA", "decimals": 8,
  "latest": { "roundId": "18446744073709551628", "answer": "21250000000", "updatedAt": 1790800620 },
  "risk": { "atRisk": "440880000", "limit": "20001840000", "room": "19560960000", "budgetBps": 2000 },
  "series": [ "0x32fb…eab8" ],
  "rounds": [ { "roundId": "18446744073709551617", "answer": "25000000000", "updatedAt": 1784407017 }, … ],
  "block": 436, "time": 1790800907 }
```

`rounds`: the last 200 (oldest first), or every round with `from <=
updatedAt <= to`. The feeds emit no events: after every indexed chunk the
indexer compares `latestRound()` with the stored rounds and fetches the new
ones (`app/feeds.py`, table `rounds`). 404 `UnknownFeed` for a feed no series uses.

### Choices where the spec is silent (WP2)

- FIFO lots belong to the trade's recipient (buys) and to the seller (sells);
  settlement redemptions close lots; mints, pair redemptions and transfers
  are outside the basis.
- `room` is signed; `navError` reports why the NAV is unknown; inventory rows
  carry `quotable`.

## History (WP3)

`GET /series/{addr}/history?from=&to=&step=`

```json
{ "series": "0x32fb…eab8", "step": 3600,
  "points": [
    { "time": 1785011817, "block": null, "spotBps": 9400, "spot": "23500000000", "noteBps": 9547,
      "coverBps": 1128, "quotable": true, "reason": null, "source": "replay", "observation": 1 },
    { "time": 1786221417, "block": null, "spotBps": 5500, "spot": "13750000000", "noteBps": null,
      "coverBps": null, "quotable": false, "reason": "Uncertified", "source": "replay", "observation": 3 },
    …,
    { "time": 1790801242, "block": 491, "spotBps": 8500, "spot": "21250000000", "noteBps": 8838,
      "coverBps": 1837, "quotable": true, "reason": null, "source": "chain", "observation": null } ],
  "observations": [ { "obsTime": 1784407017, "index": 0, "fixingBps": 10000 },
                    { "obsTime": 1785011817, "index": 1, "fixingBps": 9400 }, … ],
  "block": 491, "time": 1790801242 }
```

- A point is the **quoter's** mid at that time (`notePriceBps` at the
  listing's vol; `coverBps = maxBps − noteBps`) and the feed's spot
  (`spotBps` = spot / initial fixing in bps, `spot` in feed units).
  `quotable: false` with `reason` (the quoter's or model's error) when it
  refused; then `noteBps` is null. This is the quoter's view, so the Desk's
  60 s pre-observation band doesn't blank points; `/series/{addr}.quotable`
  is the Desk's view.
- `observation`: the fixing index (0 = strike) when the point lies exactly
  on the schedule, else null; `observations` lists the recorded fixings with
  `fixingBps`. The grid starts at the strike, so every past observation has a
  point.
- `from`/`to` (unix seconds) default to the strike and now. `step` thins the
  stored points to the last one per `step` bucket, always keeping the
  observation points and the ones sampled on an event (a trade, a feed
  round, a fixing). The last point is always computed live at the response's
  block, so it equals `/series/{addr}.mid` read at the same block.
- `source: "chain"` points come from `eth_call` at a block (`block` set).
  `source: "replay"` points (`block: null`) lie **before the series existed
  on-chain**: the dev node's scenarios stage a strike weeks before their first
  block, so there is no block to call. For those the service replays what the
  quoter would have answered (`app/replay.py`): the note's state from its
  recorded fixings (each taken as processed once its time has passed), the
  feed round in force at that time (FeedStale if it is more than 26 h old),
  NoteQuoter's input derivation, the model's domain check and the bit-exact
  student (`tools/pricer_quant.py`, vectorized in `app/student.py`), plus the
  accrued coupon. A test checks the replay against the on-chain quoter.
- **Staged feeds are weekly**: the e2e staging pushes one round per
  observation, so in the replayed past the quoter answers only in the 26 h
  after each observation and says `FeedStale` in between. The spot line is
  continuous; the mid line is a series of day-long segments, one per week.

`GET /vault/history?from=&to=&step=` — `{"step", "points": [ {time, block,
totalAssets, totalSupply, sharePrice} ]}` from the deployment on
(`totalAssets`/`sharePrice` null where the NAV was unknown), thinned like the
series history, the last point live.

Sampling (`app/history.py`): after each indexed chunk a hook samples, at the
new head, every series whose last on-chain sample is `historyStepSecs`
(3600 s) old or that the chunk touched (trade, fixing, observation, listing,
a new feed round), and the NAV likewise (any Desk event, or a step). On the
dev node blocks are rarer than the step, so that is every block. A
background thread backfills the grid: slots after the series existed get an
`eth_call` at the last block inside the slot (the node runs in archive mode;
a slot without a block had no state change and stays empty), slots before it
are replayed (`replayStepSecs`, 3600 s). Tables `samples`, `nav_samples`.

### Choices where the spec is silent (WP3)

- History points are the quoter's view (above); points carry `reason`,
  `source` and `observation` besides the listed fields.
- Pre-chain history is replayed off-chain rather than left empty
  (`eth_call` can't reach a time before the first block).
- The stored resolution is the step (3600 s) plus event points; a smaller
  `step` in the request returns what is stored, no interpolation.

## Model routes (WP4)

`GET /series/{addr}/curve?vs=spot|vol|weeks&n=41`

```json
{ "series": "0x32fb…eab8", "vs": "spot", "field": "spotBpsOfInitial",
  "inputs": { "spotBpsOfInitial": 8500, "distToKnockInBps": 2500, "volBpsAnnual": 5500, "kiBarrierBps": 6000,
              "acBarrierBps": 10000, "couponBpsPerPeriod": 25, "timeToMaturitySecs": 9933466,
              "timeToNextObsSecs": 256666, "observationsRemaining": 16, "flags": 1 },
  "accruedBps": 264, "weightsHash": "0xaf76…cd8a",
  "points": [ { "x": 5000, "noteBps": 5662, "coverBps": 5013, "cleanBps": 5398, "inDomain": true, "current": false },
              { "x": 8500, "noteBps": 8837, "coverBps": 1838, "cleanBps": 8573, "inDomain": true, "current": true },
              { "x": 10000, "noteBps": null, "coverBps": null, "cleanBps": null, "inDomain": false, "current": false,
                "reason": { "error": "Uncertified", "args": { "region": 0 } } }, … ],
  "block": 1883, "time": 1790803151 }
```

- `inputs` are `NoteQuoter.inputs(series, listing vol)` at the response's
  block: exactly what the model sees now. One field is varied over the
  listing model's certified range in `n` even steps (2..401), and the current
  value is always included (`current: true`), so that point equals
  `/series/{addr}.mid` at the same block, bit for bit.
- `vs=spot` varies `spotBpsOfInitial` (with `distToKnockInBps` kept
  consistent); `vs=vol` varies `volBpsAnnual` (2000..9000 for K3; one point
  for K2, whose vol is pinned at 5500); `vs=weeks` varies `observationsRemaining` from 1 to the
  series' count, with `timeToMaturitySecs` consistent and the accrued coupon
  of the note at that point of its life.
- Each point: `cleanBps` from `tools/pricer_quant.forward` (the Stylus
  contract's twin; the tests compare points with the deployed contract),
  `noteBps = cleanBps + accrued`, `coverBps = maxBps − noteBps`. Outside the
  domain `inDomain: false`, null prices, and the model's refusal in `reason`.
- 409 with the quoter's error when there are no current inputs (NotLive,
  FixingPending, FeedStale), 409 `NotListed` without a listing.

`POST /verify-quote` with `{"series", "txHash"}` (a Desk trade) or
`{"inputs": <PricerInputs>, "accruedBps"?, "weightsHash"?}`; either may add `"teacher": "v2" | "v3" | "gbm"`
(default: the model's own teacher)

```json
{ "inputs": { …, "volBpsAnnual": 3900, … }, "accruedBps": 264,
  "onChain": { "priceBps": 10378, "weightsHash": "0x745c…523f", "kind": "buy", "series": "0x13f4…1897",
               "txHash": "0x…", "block": 118, "spreadBps": 35, "midBps": 10343,
               "volBpsAnnual": 4200, "volBandBps": 300, "quoteVolBps": 3900,
               "bandQuotes": [ { "volBps": 3900, "noteBps": 10343 }, { "volBps": 4500, "noteBps": 10142 } ],
               "expectedPriceBps": 10378, "priceMatches": true },
  "student": { "priceBps": 10079, "quoteBps": 10343, "weightsHash": "0x745c…523f", "model": "model/k3" },
  "teacher": { "priceBps": 10074.2, "stdErrBps": 1.89, "paths": 262144, "seed": 20260930,
               "config": "merton-tsla-share-2016-2026", "teacher": "v3", "backend": "torch", "device": "cuda",
               "secs": 0.9, "quoteBps": 10338.2 },
  "check": { "onChainMidBps": 10343, "teacherQuoteBps": 10338.2, "diffBps": 4.8, "toleranceBps": 45.67,
             "modelErrorBps": 40, "within": true, "priceMatches": true },
  "cached": false, "teacherSecs": 2.36, "secs": 2.37, "block": 1883, "time": 1790803151 }
```

- For a tx: the trade event gives `priceBps` (the leg's price, spread
  included) and `weightsHash`. The service reads the quoter's NOTE quote and
  inputs at the trade's block at each vol the Desk asked the model (the
  listing's vol − and + `volBandBps`, or the vol itself with no band:
  `onChain.bandQuotes`), and picks the end IDeskCover's formula uses for that
  side: the higher quote for buy (`min(hi + ask, maxBps)`) and sellCover
  (`maxBps −` that), the lower for sell (`lo − bid`) and buyCover (`maxBps −`
  that). `midBps` is that quote, `quoteVolBps` its vol, `spreadBps` the flat
  part applied, `expectedPriceBps` the formula's price and `priceMatches`
  whether the trade paid it. `inputs`, the student and the teacher are at
  that vol, so with a band the check compares like with like.
- **The teacher is the model's own**: `model/k3` → teacher v3
  (`ml/teacher_config_v3.json`, vol-scaled jumps, `rDiscount` 0),
  `model/k2` → the pinned v2 (`ml/teacher_config.json`), `model/k1-r1` → the
  GBM teacher; `teacher.teacher` and `teacher.config` name it. The body's
  `teacher` overrides it (e.g. `"v2"` to see what the v2 teacher says about a
  k3 quote).
- `student.priceBps` and `teacher.priceBps` are **clean** prices (coupon from
  now on); `quoteBps` adds the coupon accrued since the strike at the trade's
  time, which is what the quoter adds. `check` compares the teacher's quote
  with the on-chain quote against 3 stdErr + the model's certified error
  (`modelErrorBps`: 40 for k3, whose max on T3 is 38.0 bps; 15 otherwise),
  and repeats `priceMatches`.
- The teacher (`ml/teacher.py`) needs torch, so it runs in a subprocess with
  `TEACHER_PYTHON` (default `/opt/ai/cache/venv-cuda/bin/python`):
  `TEACHER_DEVICE=cuda` → the torch backend (`ml/teacher_torch.py`) with 2^18
  paths (about 1 s on the RTX 3090), else the numpy teacher on the CPU with
  2^16 paths and a `note`. Seed fixed (`TEACHER_SEED`, 20260930),
  `TEACHER_PATHS` overrides the path count. One run at a time: a request that
  would start a second run gets 429 `Busy`. Results are cached in memory by
  (inputs, teacher, seed, paths, backend); `cached: true` on a hit.

### Choices where the spec is silent (WP4)

- The curve always contains the current value; `n` is the grid, so there are
  `n` or `n + 1` points. Points carry `cleanBps`, `current` and `reason`.
- `vs=weeks` moves the accrued coupon with the remaining weeks.
- `/verify-quote` returns clean and accrued-inclusive prices and the `check`
  block; the cache is in memory (a restart clears it).

## Demo control (WP5)

`POST` only, when `config.json` has `demo.enabled: true` (the dev node; 403
`DemoDisabled` otherwise), with the header `X-Demo-Token` (401
`BadDemoToken` otherwise). The token is the env var `DEMO_TOKEN`; on the dev
node it defaults to **`sp-devnode-demo`** when unset (set your own in
`backend/.env`). Transactions are signed by the key in the env var `DEMO_KEY`,
on the dev node by default Nitro's dev key (the curator, the mock feeds'
owner). Each route waits until the indexer has its transactions (so the
response and the next reads include them) and returns data at that block.

| Route | Body | Returns |
|---|---|---|
| `/demo/faucet` | `{"address", "eth"?: 1, "usdg"?: 100000}` (whole units, numbers or decimal strings) | `{address, sent: {eth, usdg} (base units), balances: {eth, usdg}}` |
| `/demo/feed` | `{"feed": name or address, "spotBps"}` | `{feed, name, initial, spotBps, round: {roundId, answer, updatedAt}, series: [<series>]}` |
| `/demo/fixing` | `{"series", "fixingBps"}` | `{obsTime, pushed, fixing: {price, roundId, fixingBps}, series: <series with fixings, trades>}` |
| `/demo/stage` | see below | the new `<series>` (with fixings, trades) |
| `/demo/reset` | `{"leadSecs"?}` | `{addresses, deploymentBlock, series: [addr], secs}` |

- **faucet** sends from the demo key: ETH by transfer, USDG by `mint`.
- **feed** pushes `pushRound(initial × spotBps / 1e4)`, where `initial` is
  the strike fixing of the feed's first struck series (else the feed's first
  round). A round must be later than the feed's last one; the route first
  moves the chain's clock past it.
- **fixing** is the e2e script's "next observation" step: the series' next
  observation must have passed (409 `ObservationNotPassed` with `until`
  otherwise; the route pokes first, so "passed" is the chain's time: the
  wall clock on the stock node, wall clock + offset on the dev clock). It pushes
  a round exactly at the observation time with `initial × fixingBps / 1e4`,
  records the fixing and calls `advance()`. If the feed already has a round
  after the observation (a `/demo/feed` after it passed), the fixing is the
  round in force at the observation and `pushed` is false. A fixing at or
  above the autocall barrier settles the series; one below the knock-in
  barrier knocks it in.
- **stage**: `{"feedName", "pathBps": [..], "spotBps", "observationsDone",
  "leadSecs" | "nextObservation", "terms"?: {"ki", "ac", "coupon", "count",
  "interval"}, "list"?: {"volBps",
  "capNotional" (NOTE, whole units), "bidBps", "askBps", "volBandBps",
  "riskBudgetBps"}}` — the e2e steps 3–4 as one call: a new mock feed named
  `feedName` (names must be new: 400 otherwise) with a round at the strike
  and at each of the `observationsDone` past observations (`pathBps[i-1]` bps
  of the initial $250.00) and the current spot; a series whose strike is
  `observationsDone + 1` intervals before the next observation, `leadSecs`
  (1..interval − 1) from now, or at `nextObservation` (unix time, within one
  interval; series staged with the same one share a strike time and
  schedule); the fixings recorded, `advance()`. Terms default to K2's (ki
  6000, ac 10000, coupon 25, 26 weekly); K2 and K3 list only those terms.
  `terms.interval` (seconds, ≥ 3600, default a week) stages another grid: the
  Desk lists weekly series only, so an hourly series is unlisted, e.g. one
  with every barrier observation past that matures within the hour.
  With `list` (any subset; defaults vol 5500, cap 100,000, 20/30/0, budget
  2000) the series is listed, its spread set and the feed's risk budget set.
  The feed is added to `config.json`'s `feeds`, so `/config` names it.
- **reset** (dev node only): stops the node, `up.sh --recreate` (a new empty
  chain), `deploy.py`'s `deploy_all` (new addresses: a fresh deployer key),
  rewrites `config.json` (keeping the service settings) and
  `deployments/412346.json`, restarts the indexer (the db is wiped: new
  fingerprint). Takes about 30 s. A second reset while one runs gets 429.
  Every other service reading the same `config.json` (e.g. the one on 8650
  when the tests run on 8651) follows by itself.

### Choices where the spec is silent (WP5)

- Faucet amounts and `list.capNotional` in whole units; responses in base units.
- `/demo/feed` takes a feed name or address and the "initial" of the feed's
  first struck series; `/demo/fixing` falls back to the round in force when a
  later round exists.
- `/demo/stage` rejects a feed name that exists and records new names in
  `config.json`.
- The dev node's default demo token `sp-devnode-demo` (the tunnel is the
  access control; override with `DEMO_TOKEN`).

## Operations (WP6)

The node and the service run as **systemd user units** (the host has
lingering on, so the user's units start at boot without a login):

```sh
backend/ops/install.sh                    # once: installs and enables sp-devnode + sp-backend, installs sp-reset
backend/ops/start.sh                      # bring both up, wait until /config answers
backend/ops/stop.sh                       # stop both (the node gracefully: the chain stays)
systemctl --user start sp-reset           # new chain + default scenario (same as POST /demo/reset)
systemctl --user status sp-devnode sp-backend
journalctl --user -u sp-backend -f        # the service's log (sp-devnode: the node unit's)
docker logs -f sp-devnode                 # the node itself
```

| Unit | Does |
|---|---|
| `sp-devnode` | oneshot, `backend/ops/node.sh`: waits for docker, `up.sh` (start or create the container), `up.sh --recreate` if the node can't sequence after a hard kill, `deploy.py --if-missing`. Stop: `down.sh` (graceful, 60 s). |
| `sp-backend` | `backend/run.sh` on 8650, restarted if it exits; after `sp-devnode`. |
| `sp-reset` | oneshot, `backend/ops/reset.sh`: `POST /demo/reset` to the service, or `up.sh --recreate` + `deploy.py` when it is down. Not enabled: run it by hand. |
| `sp-prices` | `backend/ops/push-prices.py`: every 5 min, each mock feed's real price through `/demo/feed` (ETH from Coinbase, RHTSLA from the real feed on Robinhood Chain mainnet), and a round at least every 6 h so no feed turns stale. A real price outside the model's spot range is not pushed (the default RHTSLA series, struck at $250.00). Not enabled: `systemctl --user enable --now sp-prices`. |

`start.sh` and `stop.sh` use the units when they are installed and fall back
to `nohup` with a pid file in `backend/run/` when they are not (no systemd
--user). After a reboot the units bring both up by themselves; `start.sh`
does the same by hand (and restarts the node unit if its container died).

**After a crash** (power loss, `docker kill`): the node comes back from its
last flush, which can lose the last blocks, and sometimes can't sequence at
all (`wrong msgIdx`). `node.sh` detects the latter and recreates the chain
and redeploys (new addresses in `/config`); the service detects the former
(the stored hash of its last block no longer matches), rolls back to the
last block the chain still has and re-indexes (`/health` counts
`rollbacks`).

**Settings** go in `backend/.env` (read by `run.sh` and the ops scripts):
`DEMO_TOKEN` (default `sp-devnode-demo` on the dev node), `DEMO_KEY` (default
Nitro's dev key on the dev node), `TEACHER_DEVICE=cuda` (the GPU teacher),
`TEACHER_PYTHON`, `TEACHER_SEED`, `TEACHER_PATHS`, `BACKEND_PORT`,
`DEVNODE_PORT` (then re-run `deploy.py` so `config.json` has the RPC port),
`ARCHIVE_RPC_URL` (the testnet, below).
The SQLite db is `backend/data/<chainId>.sqlite`; deleting it while the
service is stopped just makes it rescan.

**Testnet.** On a host that serves the testnet:

```sh
backend/.venv/bin/python backend/ops/make-config.py deployments/46630.json \
    --rpc https://rpc.testnet.chain.robinhood.com --deployment-block 127553444 \
    --out backend/config.json   # demo off; the block is given because this RPC has no old state
backend/ops/install.sh          # with this config: sp-backend alone, the dev node's units stopped and removed
backend/ops/start.sh
```

`make-config.py` takes the addresses from the file `Deploy.s.sol` writes
(its `feeds` map of name to address if it has one, else `mockFeed` as the
feed `RHTSLA`), finds the deployment block by
bisecting `eth_getCode(seriesFactory)` (or takes `--deployment-block`), and
records the genesis and deployment block hashes. The history backfill needs
`eth_call` at past blocks; on an RPC without archive state the backfill of
past slots logs errors and the history holds only what was sampled live.
Nothing else changes: the same routes, `/config` reports the testnet
addresses, `demo: false`, and `/demo/*` answers 403.

With `config.json` for another chain than the dev node's, `install.sh`,
`start.sh` and `stop.sh` leave the dev node alone, and `node.sh` does nothing
(it would otherwise redeploy and rewrite `config.json`). The dev node's chain
stays in its docker volume; to go back, restore a dev-node `config.json` and
run `install.sh` again.

RPC, measured 2026-10-02: `rpc.testnet.chain.robinhood.com` takes batches of
55 calls (66 are refused, 429; the service sends its `eth_call`s 50 to a
request, `CALL_BATCH` in `app/chain.py`) and log ranges of 100,000 blocks, but keeps
state for the last 15 to 55 minutes only: a call at an older block fails with
`historical state … is not available`. Alchemy's free plan keeps all state
and takes batches of 200, but limits `eth_getLogs` to 10 blocks, which the
indexer can't work with; dRPC's free endpoint refuses batches of more than 3.

So the service takes two RPCs. `--rpc` is the one it indexes and reads from.
**`ARCHIVE_RPC_URL`** in `backend/.env` (it may carry a key; restart the
service after setting it) is asked only for what `--rpc` refuses for pruned
state (`app/chain.py`: per call inside a batch, in requests of 20 calls,
at most 10 calls a second, a 429 waits and tries again). With it the history
backfill and the catch-up after a stop work on the public RPC; without it
the history holds only what was sampled live. At start the service checks
that the archive is the same chain and logs its host, or that it is not
used and why. An RPC with a key as `--rpc` needs `--public-rpc`, the URL
`/config` hands to browsers; `/health` and the 503s never show an RPC's path.

The service holds no key on the testnet. The curator stages feeds and series,
lists them, pushes a price at least every 26 h and records each observation's
fixing with `contracts/script/curator.sh` on their own machine (its head has
the commands). A feed the curator deploys gets its name from a `feeds` entry
in `deployments/46630.json` and a new `make-config.py` run.

`curator.sh keep` is the recurring part as one run, meant for cron every hour:
each feed of an active listing gets the price of its real Chainlink feed on
Robinhood Chain mainnet (there are none on the testnet), each observation
that has passed gets its fixing, and each series that has ended gets a
successor with the same terms, struck at the time the old one ended and
listed as the old one was. A mock feed takes prices from its owner only and
the Desk lists for its owner only, so the run needs the wallet that owns
both; with any other wallet it still records fixings and creates the
successor, says what it couldn't do and exits 1.

Since 2026-10-02 that wallet is a keeper key on this host
(`0x100a2cEAAFd6489a3Af9d9f9Ca537d792aE84cA7`, an encrypted keystore and its
password file in `/opt/ai/secrets`, outside the repo): it owns the Desk and
the five feeds of the listed series, and the user's crontab runs `keep` with
it at minute 2 of every hour, logging to `/opt/ai/cache/curator-keep.log`.
The service itself still holds no key.

### Choices where the spec is silent (WP6)

- systemd user units (available here) with `start.sh`/`stop.sh` wrappers that
  also work without them; `sp-reset` is a unit you start by hand.
- The node unit recovers from a broken sequencer by recreating the chain; the
  indexer rolls back lost blocks instead of trusting its db.
- `backend/ops/make-config.py` writes the testnet variant of `config.json`.

## Happy path scenario

```sh
backend/.venv/bin/python backend/scenarios/happy_path.py                 # clock mode if the clock image exists
backend/.venv/bin/python backend/scenarios/happy_path.py --mode hybrid   # the stock image (no dev clock)
```

One script, driven through this service's HTTP API, on a **fresh node of its
own**: container `DEVNODE_NAME` (default `sp-happy`) on `DEVNODE_PORT` (8847),
volume `DEVNODE_VOLUME` (`sp-happy-data`), a service on `BACKEND_PORT` (8850)
with its own `config.json` and database in a temp dir. It refuses
`sp-devnode`, 8647 and 8650, so the shared node and service are never
touched, and removes its container, volume and temp dir at the end
(`--keep` leaves them). About 2.5 minutes in clock mode, 5 in hybrid
(`TEACHER_DEVICE=cuda` is set when there is a GPU).

1. `up.sh --recreate` (with `DEVNODE_CLOCK=1` in clock mode), `deploy.py
   --model-dir model/k3`, `run.sh`; `/demo/faucet` gives the LP 50,000 USDG and
   it deposits them in the Desk.
2. `/demo/stage` with one `nextObservation`: series **A** (path between the
   knock-in and the autocall barrier) and **B** (knocked in at observation 3),
   both on the K2 terms with the same strike time and initial ($250.00), 10
   observations done, listed with k3 at **vol 4200 ± 300 bps**, spread 25/35, a
   25 % risk budget.
3. The buyer buys 10,000 NOTE on each, a hedger buys 12,000 WRITER (cover) on
   each, and the buyer sells 4,000 NOTE back mid-life. Before each trade the
   script reads `/series` (`quotes`: both legs' bid and ask); after it,
   `/verify-quote` on the tx must say `priceMatches` (the Desk's formula at the
   band end it used) and `within` (teacher v3 at that vol, 3 stdErr + 40 bps),
   and the off-chain student must equal the on-chain quote.
4. **Clock mode**: `clock.sh advance` past each observation and `/demo/fixing`
   for A and B, through maturity (17 steps). The LP queues 25 % of its shares
   while a fixing is pending (`/vault` shows `navError: FixingPending`), and
   `processQueue` + `claim` pay it once the fixings are in; the partial sell
   happens with 12 observations to go. **Hybrid mode** (labelled HYBRID in the
   output): the trades happen on the weekly series (the sell right away), then
   two hourly twins of A and B are staged (`terms.interval` 3600, every
   barrier observation past, maturity four minutes out), a hedger mints 6,000
   pairs of each and sells the NOTE to the buyer at the weekly series' ask,
   and the script waits for maturity on the wall clock.
5. Settlement: A (no knock-in) must pay 1 + c(N + 1) = 1.0675 per NOTE and
   WRITER nothing; B (knocked in, maturity fixing 78 %) 0.78 + 0.0675 = 0.8475
   per NOTE and 0.22 per WRITER, both against an independent payoff function.
   NOTE and WRITER holders redeem, the Desk collects (clock mode), the LP
   redeems the rest of its shares.
6. A table, A vs B: each trade's price, both legs' bid/ask before it, the vol
   the Desk priced at, model vs teacher v3, payoutPerNote and per WRITER, what
   the NOTE and WRITER holders received, each party's P&L per series, the
   LPs' P&L. Clock mode also checks that the Desk's P&L over A and B equals
   the LPs' gain (to share rounding) and that buyer + hedger + Desk +
   integrator + escrow dust add up to zero.

After every step: USDG is conserved to the base unit (the supply doesn't move
after the baseline and the balances of every account, the Desk and every
series sum to it), every series' escrow covers its claims (pairs while live,
NOTE and WRITER payouts once settled), and the Desk's balance covers what it
set aside for claims. Exit 0 only if everything passes; the logs of passing
runs are `backend/scenarios/logs/happy_path-{clock,hybrid}.log`.

## App states scenario

```sh
python3 backend/scenarios/app_states.py stage   # three new stocks, each with one listed note
python3 backend/scenarios/app_states.py play    # after buying in the app: nine weeks on, a check at a time
python3 backend/scenarios/app_states.py fresh   # a round at today's price on every live feed
```

For the frontend, on the dev node the app reads (not a node of its own), over
the HTTP API only, so it runs through the tunnel with plain Python. `stage`
adds three K2-term notes at $250.00 with the first check six days out: `CRASH`
(knocks in at its 8th check from now and runs on), `ENDED` (knocks in and
matures at 54 % nine checks from now) and `EARLY` (autocalls at its 3rd).
Buy both legs of each in the app, then `play`. It moves the clock **one
observation at a time** (nine moves): a fixing still unrecorded
`MAX_ROLL + FALLBACK_GRACE` = 9 days after its observation is replaced by the
previous one (NoteSeries), so one jump over all nine loses the script and
autocalls a note that has no fixing yet (the fallback of observation 1 is the
initial). With `CLOCK` set to how `clock.sh` runs from there (on the host:
`CLOCK="sudo -iu <user> <checkout>/backend/devnode/clock.sh"`) it moves the
clock itself; without it, it prints each `clock.sh set-absolute` to run on
the host and waits for the chain to get there. After each move it calls
`/demo/fixing` for every passed observation, on these notes as scripted and
on every other live note at its feed's price (a note above its initial, like
a fresh ETH one, autocalls), and it ends with `fresh`. The clock stays ahead
until `/demo/reset`. `fresh` is `/demo/feed` at the price each live feed is
at, for when the 26 h staleness limit pauses the quotes.

## Tests

```sh
cd backend && .venv/bin/python -m pytest tests -q                # against the dev node; skipped if it is down
SP_CLEAN=1 .venv/bin/python -m pytest tests/test_wp0_devnode.py   # also recreate the chain and deploy from scratch
.venv/bin/python -m pytest tests/test_archive.py -q              # the archive fallback; needs no node (marker `offline`)
```

The route tests start their own service (`run.sh` on 8651 with a temporary
database), so a service on 8650 keeps running. They read the model, the vol band
and the RPC port from `config.json`. Against a second node (not `sp-devnode`):
`DEVNODE_PORT=8847 BACKEND_CONFIG=<its config.json> TEST_BACKEND_PORT=8851
.venv/bin/python -m pytest tests/test_wp1_catalog.py ... tests/test_wp5_demo.py`;
`test_wp0` and `test_wp6` restart and kill `sp-devnode` by name, so leave them
out there.

- `test_wp0_devnode.py`: the deployment is quotable and listed as specified,
  `cast call` from the host, CORS preflight and response headers,
  `docker restart sp-devnode` keeps blocks and state and still sequences,
  `eth_call` at past blocks (archive), and (with `SP_CLEAN=1`) `up.sh
  --recreate` + `deploy.py` from an empty chain.
- `test_wp1_catalog.py`: `/config` fields against the chain and the model
  export; `/series` has the staged series quotable with the quoter's mid at
  the response's block; fixings on the schedule; error bodies; CORS; a
  `cast send` buy shows up in `/trades` within 5 s with the event's fields;
  a service restart leaves `/trades` unchanged; every `quotable` reason on a
  purpose-staged series (NotListed, TooCloseToObservation with `until`,
  Uncertified, OutOfRange, FeedStale, Settled, FixingPending); `/events`.
- `test_wp2_accounts.py`: the e2e lifecycle replayed on a freshly staged
  series (LP deposit, buy, buyCover, mid-life sell, the observation passes,
  the LP queues 20 % while the fixing is pending, autocall, redeem, collect,
  processQueue, claim) with `/accounts` NOTE amounts, FIFO cost basis and
  realized P&L after each step, `/feeds` risk (`room = limit − atRisk`, equal
  to `risk(feed)` at the response block) and rounds, `/vault` inventory marks
  adding up to `totalAssets`, `queuedShares` non-zero while the request is
  open (with `navError` FixingPending) and zero after `processQueue`; FIFO and
  `position` unit checks.
- `test_wp3_history.py`: the vectorized student equals `pricer_quant.forward`
  bit for bit; the default series' history has a marked point at the strike
  and every past observation (spot = the fixing), the observation list, the
  last point equal to the quoter's mid and to `/series/{addr}.mid` at the same
  block, a coarser `step` keeping the observations; a `cast send pushRound`
  is indexed within one poll and leaves a stored point with the new spot; the
  replay equals the on-chain quoter on five staged states (normal, near the
  knock-in, 6 observations left, out of range, stale feed); `/vault/history`
  samples a trade's block; a service stopped while blocks are made
  backfills those blocks with `eth_call` on restart (series and NAV).
- `test_wp4_model.py`: `curve?vs=spot`'s current point equals
  `/series/{addr}.mid` at the same block, its `inputs` are the quoter's, and
  sampled points equal the deployed Stylus contract's `priceBps` (`eth_call`);
  `vs=vol` (one point) and `vs=weeks` (1..26, checked against the contract);
  excluded bands flagged with their region; 409 FeedStale; `/verify-quote` on
  the e2e buy (10,000 NOTE, 20 bps fee): the model's own teacher (v3 for k3)
  within 3 stdErr + the model's allowance of `priceBps − spread`, the
  student's quote equal to the on-chain quote at the band end the buy used,
  a buy and a sell priced at the higher and the lower band end, the
  cache; `{inputs}` bodies, 429 on simultaneous runs, 400/404 errors; with
  `TEACHER_DEVICE=cuda` the torch backend with 2^18 paths (skipped without a GPU).
- `test_wp5_demo.py`: the token (401) and `demo.enabled: false` (403);
  faucet defaults and custom amounts change the balances (chain and
  `/accounts`); `/demo/feed` moves the spot, the mid, `/feeds` and the
  history; `/demo/stage` with 20 observations done: the knock-in path is
  `knockedIn` with a lower mid than the same stage without it, listing,
  spread and the new feed name in `/config`, custom terms unlisted
  (NotListed), a bad stage 400; `/demo/fixing` 409 before the observation,
  then `observationsDone` 10 → 11 with the new fixing; `/demo/reset` (runs
  last): new addresses in `/config` and `config.json`, `/trades` empty, the
  default series quotable, the vault at its seed.
- `test_wp6_ops.py`: the units are installed, enabled, point at this
  checkout, lingering is on; `stop.sh` takes the node and the service down
  (nothing answers) and `start.sh` brings both up with the same chain and
  deployment, `/config` answering on 8650 (the reboot path without the
  reboot); the indexer rolls back a block whose hash changed and drops what
  it had indexed from it; after a `docker kill -s KILL sp-devnode`,
  `start.sh` brings back a node that sequences and holds `config.json`'s
  deployment (recreated if needed), and the service on 8650 follows with
  only blocks the chain has.
