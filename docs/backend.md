# Backend for the frontend

What runs on the host for the frontend and the demo video, and how to use it.
Spec: [backend-spec.md](backend-spec.md). Everything binds to 127.0.0.1; the
frontend reaches it through an SSH tunnel.

| Port | What | Started by |
|---|---|---|
| 8647 | Nitro dev node, JSON-RPC (chain id 412346) | `backend/devnode/up.sh` |
| 8650 | the REST service (this doc) | `backend/run.sh` |

From your machine:

```sh
ssh -N -L 8647:127.0.0.1:8647 -L 8650:127.0.0.1:8650 max@<host>
curl localhost:8650/config
```

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

### Tests

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
