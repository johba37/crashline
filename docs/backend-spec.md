# Backend for the frontend: spec and work packages

Status: spec, 2026-09-30. Target: the Oct 4 submission's frontend (branch `max/frontend`)
and the demo video. Nothing here is built yet.

The frontend talks to two things on this host, both bound to 127.0.0.1 and reached through
an SSH tunnel: a long-lived Nitro dev node (JSON-RPC, port 8647) and a REST service (port
8650). The RPC is enough for live quotes, balances and every write; the service covers what
the RPC can't give cheaply: the catalog, history, per-wallet aggregation, the off-chain model,
and demo control. The public demo on the testnet uses the same service pointed at the testnet
RPC with the demo routes disabled.

## Fixed facts

| Item | Value |
|---|---|
| Repo layout | `contracts/` (Foundry), `stylus/pricer-model/` (Stylus pricer), `ml/` (teacher), `tools/` (integer student twin), `abi/` (exported ABIs), `model/k2/` (the certified model) |
| Dev node | `offchainlabs/nitro-node:v3.11.4-7d5ac27 --dev`, chain id 412346, container port 8547, host port `E2E_PORT` (8647). Dev key and three anvil keys: `contracts/script/e2e-devnode.sh` lines 42–45 |
| Deploy + stage | `contracts/script/e2e-devnode.sh` steps 0–4: node, `cargo stylus deploy`, MockUSDG (6 dec, public `mint(to, amount)`), MockChainlinkFeed (`pushRound(answer)`, `pushRoundAt(answer, updatedAt)`, 8 dec, roundId = 2^64 + n), SeriesFactory, NoteQuoter(93600), Desk(curator = dev, band 60 s), staged feed history, one series with a past strike, past fixings recorded |
| Interfaces | `contracts/src/interfaces/*.sol`; `abi/*.json` (10 files, `contracts/script/export-abi.sh`) |
| Series terms | `SeriesTerms {feed, strikeTime, observationInterval, observationCount, kiBarrierBps, acBarrierBps, couponBpsPerPeriod}`; `SeriesState {phase, initialFixing, observationsDone, knockedIn, autocalled, nextObservation, maturity, payoutPerNote}`; `maxPayoutPerNote()` in USDG base units per NOTE |
| Units | USDG 6 decimals, NOTE/WRITER 6 decimals (1 NOTE = 1 USDG notional), Desk shares 12 decimals, feed prices 8 decimals, bps = 1/10,000, times = unix seconds |
| Quoter reverts | `NotLive`, `FixingPending(obsTime)`, `FeedStale(updatedAt)`, `BadFeedAnswer`; Desk: `NotListed`, `TooCloseToObservation(obsTime)`, `ModelMismatch(field)`; pricer: `OutOfRange(field, value)`, `Uncertified(region)`, `Inconsistent(field)` |
| Off-chain student | `tools/pricer_quant.py`: `forward(export, raw)` is bit-exact with the Stylus contract; `check_domain` reproduces its refusals. `export` = `model/k2/student_export.json` (`weightsHash` inside). Inputs: `PricerInputs` order, see `ISurrogatePricer.sol` |
| Teacher | `ml/teacher.py`: `price(spotBpsOfInitial, volBpsAnnual, timeToMaturitySecs, timeToNextObsSecs, ...)` and `price_batch(F, total_paths, seed)` → (priceBps, stdErrBps). GPU variant `ml/teacher_torch.py` runs from `/opt/ai/cache/venv-cuda/bin/python` |
| Python | 3.12 on this host. The service gets its own venv `backend/.venv` (fastapi, uvicorn, web3, numpy, pycryptodome). Not the cuda venv |
| Frontend | Vite + React + wagmi/viem + RainbowKit, `frontend/` on `max/frontend`; config in `frontend/src/wagmi.ts` (Robinhood testnet only today), `VITE_RPC_URL` |

## Conventions for the service

- Directory `backend/`. FastAPI + uvicorn, web3.py, SQLite (stdlib `sqlite3`), no ORM, no
  Docker for the service itself. One process: the API and the indexer loop (a background
  task polling `eth_blockNumber`/`eth_getLogs` every 2 s from the deployment block).
- Config from a single file `backend/config.json` (chain id, RPC URL, contract addresses,
  deployment block, model dir, demo enabled, demo key env var name) written by WP0; the
  testnet variant is the same file with the testnet addresses from `deployments/46630.json`.
- JSON: every uint256 amount is a decimal **string**; bps, counts and times are numbers;
  addresses lowercase hex. Every response carries `{"block": n, "time": t}` of the state it
  reflects. Errors: `{"error": "<Name>", "args": {...}}` with HTTP 4xx.
- CORS: allow all origins (it runs behind a tunnel). No auth except the demo token
  (`X-Demo-Token` header, value from env `DEMO_TOKEN`).
- Tests: `pytest` in `backend/tests/`, run against a dev node started by WP0 (skip if
  the node is not up). Each WP adds tests for its routes.
- Do not edit `contracts/`, `ml/`, `tools/`, `stylus/`, `model/` (other sessions own them).
  The one exception is the node start flags in WP0, which go in a new script, not in the
  e2e script.
- Docs: `docs/backend.md` (for the frontend dev: how to tunnel, the routes, examples) is
  written in WP1 and extended by every later WP. This spec stays as is.

## Routes

All `GET` unless marked. Field lists are the minimum; keep names as given.

### `GET /config`
```
{ chainId, rpcUrl (as seen from the tunnel: "http://localhost:8647"), deploymentBlock,
  addresses: { usdg, seriesFactory, noteQuoter, desk, surrogatePricer, feeds: {<name>: addr} },
  model: { dir, weightsHash, featureSpecVersion, certifiedDomain },
  desk: { maxFeeBps, maxCoverFeeBps, backstopShareBps, minSecsToObservation, minRequestShares, queueBatch },
  demo: bool, commit: "<git sha>", block, time }
```

### `GET /series` and `GET /series/{addr}`
One object per series (the list omits `fixings` and `trades`):
```
{ address, note, writer, recorder, feed, feedName, id,
  terms: { strikeTime, observationInterval, observationCount, kiBarrierBps, acBarrierBps, couponBpsPerPeriod },
  maxPayoutPerNote, maxBps,
  state: { phase: "Pending"|"Live"|"Settled", initialFixing, observationsDone, knockedIn, autocalled,
           nextObservation, maturity, payoutPerNote, pendingObservation: {pending, obsTime} },
  listing: null | { active, pricer, volBpsAnnual, capNotional, writerHeld },
  spread: { bidBps, askBps, volBandBps },
  desk: { noteHeld, writerHeld },
  quotable: { ok: bool, reason: null | "NotListed"|"FixingPending"|"FeedStale"|"TooCloseToObservation"|
              "OutOfRange"|"Uncertified"|"NotLive"|"Settled", args: {...}, until: null | time },
  mid: null | { noteBps, coverBps },      // the quoter at the listing's vol, null when not quotable
  fixings: [ { obsTime, price, roundId, index } ],          // index 0 = strike
  trades: [ <trade> ]                                        // last 50
}
```
`quotable.until` is the time the reason clears when it is known (band end, next feed
round is unknowable, so null for FeedStale).

### `GET /series/{addr}/history?from=&to=&step=`
Sampled marks, default step 3600 s, from strike to now:
```
{ points: [ { time, block, spotBps, spot, noteBps, coverBps, quotable } ],
  observations: [ { obsTime, index, fixingBps } ] }
```
`noteBps` is the quoter's mid at that block (null when not quotable). Sampling is done by
the indexer as blocks arrive; for the past (before the service started) it backfills with
`eth_call` at historic blocks, one per step.

### `GET /series/{addr}/curve?vs=spot|vol|weeks&n=`
The off-chain student at the series' current inputs, one input varied over its certified
range (n points, default 41):
```
{ vs, inputs: {<PricerInputs now>}, points: [ { x, noteBps, coverBps, inDomain } ] }
```
Outside the domain the point carries `inDomain: false` and null prices. `vs=vol` varies
`volBpsAnnual` (a single point for a vol-pinned model), `vs=weeks` varies
`observationsRemaining` with `timeToMaturitySecs` kept consistent.

### `GET /feeds/{addr}`
```
{ address, name, decimals, latest: { roundId, answer, updatedAt },
  risk: { atRisk, limit, room, budgetBps }, series: [addr],
  rounds: [ { roundId, answer, updatedAt } ]   // last 200, or ?from=&to=
}
```

### `GET /vault` and `GET /vault/history?from=&to=&step=`
```
{ totalAssets, totalSupply, sharePrice (USDG per share, decimal string with 12 places),
  idle, reserved, queuedShares, queue: { head, length },
  feeds: { <addr>: { atRisk, limit, room } },
  inventory: [ { series, noteHeld, writerHeld, mark, atRisk } ] }
history: { points: [ { time, block, totalAssets, totalSupply, sharePrice } ] }
```

### `GET /accounts/{addr}`
```
{ usdg, shares, sharesValue,
  positions: [ { series, note, writer, noteMark, coverMark, costBasis: {note, writer}, realized } ],
  queue: [ { id, shares, requestedAt, position (requests ahead) } ],
  claimable,
  trades: [ <trade> ]  // last 50
}
```
Cost basis and realized P&L are FIFO over the account's trades, in USDG.

### `GET /trades?series=&account=&limit=&before=`
```
<trade> = { txHash, logIndex, block, time, series, kind: "buy"|"sell"|"buyCover"|"sellCover",
            account, to, amount, priceBps, usdg (cost or proceeds), feeBps, feeReceiver, weightsHash }
```
Also mint/redeemPair/redeem/queue events under `GET /events?series=&account=` with the raw
decoded args; not required for the first frontend.

### `POST /verify-quote`
Body `{ series, txHash }` (a Desk trade tx; quotes are views and emit nothing, the trade event carries `priceBps` and `weightsHash`) or `{ inputs: <PricerInputs> }`.
Runs the teacher (`ml/teacher.price_batch`, 2^18 paths, fixed seed; the torch backend
when `TEACHER_DEVICE=cuda`) and returns
```
{ inputs, onChain: { priceBps, weightsHash } | null, student: { priceBps },
  teacher: { priceBps, stdErrBps, paths, seed, config: "<teacher_config name>" }, secs }
```
Cached by (inputs, seed). Limit one run at a time; 429 when busy.

### Demo routes (`POST`, only when `config.demo`, header `X-Demo-Token`)
| Route | Body | Does |
|---|---|---|
| `/demo/faucet` | `{address, eth?, usdg?}` | sends 1 ETH and 100,000 USDG (defaults) from the dev key |
| `/demo/feed` | `{feed, spotBps}` | `pushRound(initial * spotBps / 1e4)` on the mock feed |
| `/demo/fixing` | `{series, fixingBps}` | the e2e script's step "next observation": pushes a round at `nextObservation` (must be in the past), records the fixing, calls `advance()` |
| `/demo/stage` | `{feedName, pathBps: [..], spotBps, observationsDone, leadSecs, terms?: {ki, ac, coupon, count}, list?: {volBps, capNotional, bidBps, askBps, volBandBps, riskBudgetBps}}` | the e2e script's steps 3–4 (+ listing, spread, risk budget when `list` is given) as one call; returns the new series object. Terms default to the K2 product: ki 6000, ac 10000, coupon 25, 26 weekly |
| `/demo/reset` | `{}` | stops the node, starts a fresh one, redeploys, restages the default scenario, rewrites `config.json`, restarts the indexer |

## Work packages

Each WP is one commit (or a few) on the working branch, with its tests green and
`docs/backend.md` updated. Order matters: WP0 → WP1 → WP2; WP3, WP4, WP5 are independent
after WP2; WP6 last.

### WP0: persistent dev node and deployment
Scope: `backend/devnode/up.sh` starts the node with `--http.corsdomain='*'
--http.vhosts='*' --http.api=net,web3,eth,debug` and **without** `--rm`, container name
`sp-devnode`, host port from `DEVNODE_PORT` (8647), data in a named volume; `down.sh`;
`backend/devnode/deploy.py` (or `.sh` wrapping cast, as the e2e script does) deploys the
Stylus pricer from `model/k2` and the Solidity contracts, stages the default scenario (the
e2e script's `PATH_BPS`, `SPOT_BPS=8500`, `TARGET_REM=16`, `LEAD` given), lists the series
(vol 5500, cap 100,000, spread 20/30/0, risk budget 2000 bps), and writes
`backend/config.json` and `deployments/412346.json`. Fund the three anvil test accounts.
Acceptance: `up.sh && deploy.py` from a clean state ends with a quotable listed series;
`cast call` from the host works; a browser `fetch` against the RPC gets CORS headers;
`docker restart sp-devnode` keeps the chain (if Nitro `--dev` does not persist across
restarts, say so in `docs/backend.md` and make `up.sh` redeploy and restage on every start).

### WP1: service skeleton, indexer, catalog
Scope: `backend/app/` with `main.py`, `config.py`, `chain.py` (web3 + ABI loading from
`abi/`), `indexer.py` (SQLite tables `blocks`, `series`, `trades`, `events`; scans
`SeriesCreated`, `SeriesListed/Delisted`, `SpreadSet`, `RiskBudgetSet`, the four trade
events, `Minted`, `PairRedeemed`, `Redeemed`, `Struck`,
`ObservationProcessed`, `Settled`, `FixingRecorded`, the queue events; idempotent on
restart, handles the dev node being reset by comparing the genesis hash stored with the
DB). Routes `/config`, `/series`, `/series/{addr}`, `/trades`. `quotable` is computed by
calling `quoteBuy` with 1 NOTE and decoding the revert selector into the reason names
above. `backend/run.sh` starts uvicorn on `BACKEND_PORT` (8650). `docs/backend.md` with the
tunnel command and these routes.
Acceptance: after WP0's scenario, `/series` returns the staged series with `quotable.ok`
true and a `mid`; after a `cast send desk.buy(...)` the trade appears in `/trades` within
5 s; restarting the service does not duplicate trades.

### WP2: accounts, vault, feeds
Scope: `/accounts/{addr}`, `/vault`, `/feeds/{addr}`; FIFO cost basis; `risk(feed)` per
feed; queue requests per owner from the queue events plus `redeemRequest(id)`.
Acceptance: the e2e lifecycle (`contracts/script/e2e-devnode.sh` run against this node
with `E2E_PORT`; or its steps replayed by a test) leaves `/accounts/<buyer>` with the right
NOTE amount and cost basis, `/vault.queuedShares` non-zero while the LP's request is open
and zero after `processQueue`, `/feeds/<feed>.risk.room == limit - atRisk`.

### WP3: history sampling
Scope: the indexer samples every series' mid, the feed's spot and the vault's NAV once per
`step` (3600 s, or every block on the dev node when blocks are rarer than that), backfills
from the strike with `eth_call` at historic blocks; routes `/series/{addr}/history`,
`/vault/history`.
Acceptance: after WP0's scenario the series history has one point per past observation at
least, observations are marked, the last point equals `/series/{addr}.mid`; a `/demo/feed`
call (WP5, or `cast send pushRound`) adds a point within one poll.

### WP4: model routes
Scope: `/series/{addr}/curve` on `tools/pricer_quant.forward` with the listing's model
export; `/verify-quote` on `ml/teacher.price_batch` (import `ml/` with `sys.path`; the
torch backend when `TEACHER_DEVICE=cuda` and the cuda venv is used, else numpy on CPU with
2^16 paths and a note in the response); single-flight lock and cache.
Acceptance: `curve?vs=spot` at the series' current inputs reproduces `/series/{addr}.mid`
bit-exactly at the current spot point; `/verify-quote` on the e2e buy tx returns a teacher
price within 3 stdErr + 15 bps of the on-chain `priceBps` minus the spread.

### WP5: demo control
Scope: the five demo routes, guarded by `config.demo` and `X-Demo-Token`; `/demo/stage`
and `/demo/reset` reuse WP0's deploy code as functions; the indexer picks up the new
series without restart (reset restarts it).
Acceptance: `faucet` → balances change; `stage` with `observationsDone: 20` and a path
that knocks in → `/series` shows `knockedIn: true` and a lower `mid`; `fixing` on a series
whose next observation is past → `observationsDone` increments; `reset` → `/config` has new
addresses and `/trades` is empty.

### WP6: operations and hand-off
Scope: systemd user units (or a `backend/ops/` pair of `start.sh`/`stop.sh` with nohup and
pid files if systemd --user is not available) for the node, the service and the reset;
`docs/backend.md` final: the two ports, the ssh command
(`ssh -N -L 8647:127.0.0.1:8647 -L 8650:127.0.0.1:8650 max@<host>`), the wagmi chain entry
for chain 412346 (`VITE_CHAIN_ID`, `VITE_RPC_URL`, `VITE_API_URL`), how to use `/config`
for addresses, the test accounts (anvil keys 1–3 from the e2e script, funded), the demo
token, and what changes for the testnet (same service, `deployments/46630.json`, demo off).
Acceptance: after a host reboot, `start.sh` (or the units) bring both up and `/config`
answers; the doc is complete enough that the frontend dev needs nothing else.

## Out of scope
Websockets/SSE (poll), auth beyond the demo token, a public tunnel (the demo build on
GitHub Pages targets the testnet), price alerts, any change to the contracts or the model.
