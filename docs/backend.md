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

export const surrogateDevnode = defineChain({
  id: 412346,
  name: 'Surrogate Pricer dev node',
  nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
  rpcUrls: { default: { http: [import.meta.env.VITE_RPC_URL ?? 'http://localhost:8647'] } },
  testnet: true,
})

const chainId = Number(import.meta.env.VITE_CHAIN_ID ?? robinhoodTestnet.id)
const chain = chainId === surrogateDevnode.id ? surrogateDevnode : robinhoodTestnet
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
listed at vol 5500 with a 20/30 bps spread and a 20 % risk budget; the vault
holds 100,000 USDG. The chain's clock moves only with transactions (each
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

### What `deploy.py` does

The e2e script's steps 0-6, without the trades
(`contracts/script/e2e-devnode.sh`):

1. `cargo stylus deploy` of `stylus/pricer-model` with `model/k2` compiled in
   (build dir `backend/.build/stylus-target`); checks the deployed
   `weightsHash` against `model/k2/student_export.json`.
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
  "model": { "dir": "model/k2", "weightsHash": "0xaf76…cd8a", "featureSpecVersion": 1,
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
  "spread": { "bidBps": 20, "askBps": 30, "volBandBps": 0 },
  "desk": { "noteHeld": "0", "writerHeld": "1200000000" },
  "quotable": { "ok": true, "reason": null, "args": {}, "until": null },
  "mid": { "noteBps": 8838, "coverBps": 1837 },
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
  consistent); `vs=vol` varies `volBpsAnnual` (one point for K2, whose vol is
  pinned at 5500); `vs=weeks` varies `observationsRemaining` from 1 to the
  series' count, with `timeToMaturitySecs` consistent and the accrued coupon
  of the note at that point of its life.
- Each point: `cleanBps` from `tools/pricer_quant.forward` (the Stylus
  contract's twin; the tests compare points with the deployed contract),
  `noteBps = cleanBps + accrued`, `coverBps = maxBps − noteBps`. Outside the
  domain `inDomain: false`, null prices, and the model's refusal in `reason`.
- 409 with the quoter's error when there are no current inputs (NotLive,
  FixingPending, FeedStale), 409 `NotListed` without a listing.

`POST /verify-quote` with `{"series", "txHash"}` (a Desk trade) or
`{"inputs": <PricerInputs>, "accruedBps"?, "weightsHash"?}`

```json
{ "inputs": { … }, "accruedBps": 264,
  "onChain": { "priceBps": 8867, "weightsHash": "0xaf76…cd8a", "kind": "buy", "series": "0x32fb…eab8",
               "txHash": "0xfced…0e89", "block": 1879, "spreadBps": 30, "midBps": 8837 },
  "student": { "priceBps": 8573, "quoteBps": 8837, "weightsHash": "0xaf76…cd8a" },
  "teacher": { "priceBps": 8576.92, "stdErrBps": 3.69, "paths": 65536, "seed": 20260930,
               "config": "merton-tsla-2016-2026", "backend": "numpy", "device": "cpu", "secs": 0.09,
               "quoteBps": 8840.92,
               "note": "numpy teacher on the CPU with 65536 paths (2^18 on the GPU: set TEACHER_DEVICE=cuda)" },
  "check": { "onChainMidBps": 8837, "teacherQuoteBps": 8840.92, "diffBps": -3.92, "toleranceBps": 26.08,
             "within": true },
  "cached": false, "teacherSecs": 2.36, "secs": 2.37, "block": 1883, "time": 1790803151 }
```

- For a tx: the trade event gives `priceBps` (the leg's price, spread
  included) and `weightsHash`; the inputs are the quoter's at the trade's
  block and the listing's vol then. `midBps` is the NOTE quote the price
  implies, IDeskCover's formulas with the flat spread at that block: buy
  `price − ask`, sell `price + bid`, buyCover `maxBps − price + bid`,
  sellCover `maxBps − price − ask` (with a vol band this is the band's lower
  or higher quote).
- `student.priceBps` and `teacher.priceBps` are **clean** prices (coupon from
  now on); `quoteBps` adds the coupon accrued since the strike at the trade's
  time, which is what the quoter adds. `check` compares the teacher's quote
  with the on-chain mid against 3 stdErr + 15 bps.
- The teacher (`ml/teacher.py`) needs torch, so it runs in a subprocess with
  `TEACHER_PYTHON` (default `/opt/ai/cache/venv-cuda/bin/python`):
  `TEACHER_DEVICE=cuda` → the torch backend (`ml/teacher_torch.py`) with 2^18
  paths (about 1 s on the RTX 3090), else the numpy teacher on the CPU with
  2^16 paths and a `note`. Seed fixed (`TEACHER_SEED`, 20260930),
  `TEACHER_PATHS` overrides the path count. One run at a time: a request that
  would start a second run gets 429 `Busy`. Results are cached in memory by
  (inputs, seed, paths, backend); `cached: true` on a hit.

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
  otherwise; the route pokes first, so "passed" is wall-clock time). It pushes
  a round exactly at the observation time with `initial × fixingBps / 1e4`,
  records the fixing and calls `advance()`. If the feed already has a round
  after the observation (a `/demo/feed` after it passed), the fixing is the
  round in force at the observation and `pushed` is false. A fixing at or
  above the autocall barrier settles the series; one below the knock-in
  barrier knocks it in.
- **stage**: `{"feedName", "pathBps": [..], "spotBps", "observationsDone",
  "leadSecs", "terms"?: {"ki", "ac", "coupon", "count"}, "list"?: {"volBps",
  "capNotional" (NOTE, whole units), "bidBps", "askBps", "volBandBps",
  "riskBudgetBps"}}` — the e2e steps 3–4 as one call: a new mock feed named
  `feedName` (names must be new: 400 otherwise) with a round at the strike
  and at each of the `observationsDone` past observations (`pathBps[i-1]` bps
  of the initial $250.00) and the current spot; a series whose strike is
  `observationsDone + 1` weeks before the next observation, `leadSecs`
  (1..604799) from now; the fixings recorded, `advance()`. Terms default to
  K2's (ki 6000, ac 10000, coupon 25, 26 weekly); K2 lists only those terms.
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
`DEVNODE_PORT` (then re-run `deploy.py` so `config.json` has the RPC port).
The SQLite db is `backend/data/<chainId>.sqlite`; deleting it while the
service is stopped just makes it rescan.

**Testnet.** On a host that serves the testnet:

```sh
backend/.venv/bin/python backend/ops/make-config.py deployments/46630.json \
    --rpc https://rpc.testnet.chain.robinhood.com --out backend/config.json   # demo off; --deployment-block N if the RPC has no old state
backend/run.sh
```

`make-config.py` takes the addresses from the file `Deploy.s.sol` writes
(`mockFeed` becomes the feed `RHTSLA`), finds the deployment block by
bisecting `eth_getCode(seriesFactory)` (or takes `--deployment-block`), and
records the genesis and deployment block hashes. The history backfill needs
`eth_call` at past blocks; on an RPC without archive state the backfill of
past slots logs errors and the history holds only what was sampled live.
Nothing else changes: the same routes, `/config` reports the testnet
addresses, `demo: false`, and `/demo/*` answers 403.

### Choices where the spec is silent (WP6)

- systemd user units (available here) with `start.sh`/`stop.sh` wrappers that
  also work without them; `sp-reset` is a unit you start by hand.
- The node unit recovers from a broken sequencer by recreating the chain; the
  indexer rolls back lost blocks instead of trusting its db.
- `backend/ops/make-config.py` writes the testnet variant of `config.json`.

## Tests

```sh
cd backend && .venv/bin/python -m pytest tests -q                # against the dev node; skipped if it is down
SP_CLEAN=1 .venv/bin/python -m pytest tests/test_wp0_devnode.py   # also recreate the chain and deploy from scratch
```

The route tests start their own service (`run.sh` on 8651 with a temporary
database), so a service on 8650 keeps running.

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
  the e2e buy (10,000 NOTE, 20 bps fee): teacher within 3 stdErr + 15 bps of
  `priceBps − spread`, the student's quote equal to the on-chain mid, the
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
