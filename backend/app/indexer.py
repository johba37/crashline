"""The indexer: a background thread that follows the chain from the deployment
block and keeps in SQLite what the routes can't read cheaply from the chain.

Every `pollSecs` (2 s) it reads `eth_blockNumber` and, for new blocks, runs
two `eth_getLogs` per chunk: the factory's `SeriesCreated`/`RecorderDeployed`
first (so a series created in the chunk is known), then every other event of
the factory's series, their feeds' recorders and the Desk. Rows and the new
head go in one transaction; (txHash, logIndex) is the primary key, so a
restart resumes from the stored head and never duplicates a row.

Lost blocks. Every poll compares the stored hash of the last indexed block with
the chain's; a hard-killed dev node comes back from its last flush and rebuilds
the blocks after it with other hashes. Then the indexer walks back to the last
stored block the chain still has, deletes everything indexed after it and
resumes from there.

Resets. The fingerprint (chain id, genesis hash, deployment block and its hash,
Desk address) is stored with the data; if `config.json` describes another
deployment, every table is wiped and the scan restarts. The genesis hash
alone can't tell dev chains apart (every fresh `--dev` chain has the same
one), so the deployment block's hash is part of it. If the chain loses the
deployment (the node was recreated but not redeployed) the indexer stops with
status `DeploymentMissing` until `config.json` changes; it re-reads the file
whenever its mtime changes.

Hooks (`indexer.hooks`) run in the indexer thread after each committed chunk
with (indexer, from_block, to_block, touched), where touched = {"series",
"feeds", "accounts", "desk" (event names), "newSeries"} (sets); the feed
rounds (app/feeds.py) and the history sampler (app/history.py) hang on them.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config as cfgmod
from .chain import REGISTRY, Chain, RpcError
from .db import DB, SCHEMA_VERSION

log = logging.getLogger("indexer")

TRADE_KINDS = {"NoteBought": "buy", "NoteSold": "sell", "CoverBought": "buyCover", "CoverSold": "sellCover"}
FACTORY_EVENTS = ("SeriesCreated", "RecorderDeployed")
DESK_EVENTS = ("SeriesListed", "SeriesDelisted", "SpreadSet", "RiskBudgetSet", *TRADE_KINDS, "Collected",
               "RedeemRequested", "RedeemFilled", "RedeemCancelled", "RedeemClaimed", "Deposit", "Withdraw",
               "MinSecsToObservationSet")
SERIES_EVENTS = ("Struck", "ObservationProcessed", "Settled", "Minted", "PairRedeemed", "Redeemed")
RECORDER_EVENTS = ("FixingRecorded",)

KIND_EVENTS = {"factory": FACTORY_EVENTS, "desk": DESK_EVENTS, "series": SERIES_EVENTS,
               "recorder": RECORDER_EVENTS}
EVENTS = {kind: {n: REGISTRY.kinds[kind].events[n] for n in names} for kind, names in KIND_EVENTS.items()}
TOPIC_FACTORY = [e.topic0 for e in EVENTS["factory"].values()]
TOPICS_OTHER = sorted({e.topic0 for k in ("desk", "series", "recorder") for e in EVENTS[k].values()})
BY_TOPIC = {(kind, e.topic0): e for kind, evs in EVENTS.items() for e in evs.values()}


@dataclass(frozen=True)
class Snapshot:
    """What a request reads: one config, one chain, one db, one indexed head."""
    cfg: dict
    chain: Chain
    db: DB
    head: dict | None  # {"number", "hash", "time"} of the last indexed block
    status: str


def fingerprint(cfg: dict) -> str:
    return json.dumps({k: cfg.get(k) for k in ("chainId", "genesisHash", "deploymentBlock", "deploymentBlockHash")}
                      | {"desk": cfg["addresses"]["desk"]}, sort_keys=True)


def db_path_for(cfg: dict) -> Path:
    return Path(os.environ["BACKEND_DB"]) if os.environ.get("BACKEND_DB") else cfgmod.db_path(cfg)


@dataclass
class Indexer:
    config_path: Path = cfgmod.CONFIG_PATH
    hooks: list[Callable] = field(default_factory=list)
    chunk: int = 5_000

    def __post_init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self.snap: Snapshot | None = None
        self.polls = 0
        self.cfg_mtime = 0.0
        self.error: str | None = None
        self.caught_up = threading.Event()
        self.rollbacks = 0

    # --- setup -------------------------------------------------------------------
    def setup(self) -> None:
        """(Re)load config.json, open the chain's db, wipe it if it holds another deployment."""
        with self._lock:
            self.cfg_mtime = self.config_path.stat().st_mtime
            cfg = cfgmod.load(self.config_path)
            chain = Chain(cfg["rpcUrl"])
            db = DB(db_path_for(cfg))
            fp = fingerprint(cfg)
            if db.get_meta("fingerprint") != fp or db.get_meta("schema") != SCHEMA_VERSION:
                log.info("new deployment or schema: wiping %s", db.path)
                db.wipe()
                c = db.conn()
                db.set_meta("fingerprint", fp, c)
                db.set_meta("schema", SCHEMA_VERSION, c)
                db.set_meta("last_block", cfg["deploymentBlock"] - 1, c)
            self.cfg, self.chain, self.db = cfg, chain, db
            self.last = int(db.get_meta("last_block"))
            self._load_known()
            row = db.one("SELECT number, hash, time FROM blocks WHERE number = ?", (self.last,))
            head = dict(row) if row else None
            self.caught_up.clear()
            self.snap = Snapshot(cfg, chain, db, head, "starting")

    def _load_known(self) -> None:
        """The series and recorders indexed so far, from the db."""
        self.series = {r["address"]: dict(r) for r in self.db.query("SELECT * FROM series")}
        self.recorders = {r["address"]: r["feed"] for r in
                          self.db.query("SELECT address, feed FROM events WHERE name = 'RecorderDeployed'")}

    def deployment_ok(self) -> bool:
        cfg = self.cfg
        try:
            if self.chain.block(0)["hash"] != cfg["genesisHash"]:
                return False
            return self.chain.block(cfg["deploymentBlock"])["hash"] == cfg["deploymentBlockHash"]
        except Exception:
            return False

    # --- loop --------------------------------------------------------------------------
    def start(self) -> None:
        if self.snap is None:
            self.setup()
        # one stop event per thread: a thread still inside a poll when stop() gave up
        # waiting for it must not come back to life when a new one starts
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="indexer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 30) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def reload_config(self) -> None:
        """Re-read config.json: a new deployment (or RPC) means setup(), else the new
        settings (e.g. a feed name) replace the old ones in place."""
        with self._lock:
            cfg = cfgmod.load(self.config_path)
            if fingerprint(cfg) != fingerprint(self.cfg) or cfg["rpcUrl"] != self.cfg["rpcUrl"]:
                self.setup()
                return
            self.cfg_mtime = self.config_path.stat().st_mtime
            self.cfg = cfg
            s = self.snap
            self.snap = Snapshot(cfg, s.chain, s.db, s.head, s.status)

    def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            busy = False
            try:
                if self.config_path.exists() and self.config_path.stat().st_mtime != self.cfg_mtime:
                    log.info("config.json changed: reloading")
                    self.reload_config()
                if stop.is_set():
                    break
                busy = self.poll_once()
                self.error = None
            except Exception as e:  # keep going: the node may be restarting
                self.error = f"{type(e).__name__}: {e}"
                log.warning("poll failed: %s", self.error)
                self._set_status("error")
            if not busy:
                stop.wait(self.cfg.get("pollSecs", 2))

    def _set_status(self, status: str, head: dict | None = None) -> None:
        s = self.snap
        self.snap = Snapshot(s.cfg, s.chain, s.db, head if head is not None else s.head, status)

    def poll_once(self) -> bool:
        """Index up to one chunk of new blocks; True if more are waiting."""
        with self._lock:
            self.polls += 1
            n = self.chain.block_number()
            if n < self.last or self.polls % 5 == 1:
                if not self.deployment_ok():
                    self._set_status("DeploymentMissing")
                    return False
            if self._lost_blocks(n):
                self._rollback()
            if n <= self.last:
                if self.snap.head is None:  # fresh start with nothing new: read the head once
                    b = self.chain.block(self.last)
                    self._store_blocks({self.last: b}, self.db.conn())
                    self._set_status("ok", b)
                else:
                    self._set_status("ok")
                self.caught_up.set()
                return False
            frm, to = self.last + 1, min(n, self.last + self.chunk)
            touched = self._index_range(frm, to)
            self.last = to
            for h in self.hooks:
                try:
                    h(self, frm, to, touched)
                except Exception as e:
                    log.exception("hook %s failed: %s", getattr(h, "__name__", h), e)
            more = to < n
            if not more:
                self.caught_up.set()
            return more

    # --- lost blocks ------------------------------------------------------------------------
    def _lost_blocks(self, n: int) -> bool:
        """The last indexed block is gone or has another hash: a hard-killed dev node comes
        back from its last flush and rebuilds the blocks after it differently."""
        row = self.db.one("SELECT hash FROM blocks WHERE number = ?", (self.last,))
        if row is None:
            return False
        return n < self.last or self.chain.block(self.last)["hash"] != row[0]

    def _rollback(self) -> None:
        """Delete what was indexed from blocks the chain no longer has and resume after the
        last stored block it still has."""
        anc = self.cfg["deploymentBlock"] - 1
        for r in self.db.query("SELECT number, hash FROM blocks WHERE number <= ? ORDER BY number DESC",
                               (self.last,)):
            try:
                if self.chain.block(r["number"])["hash"] == r["hash"]:
                    anc = r["number"]
                    break
            except RpcError:  # beyond the chain's head now
                continue
        log.warning("the chain lost blocks after %d (indexed up to %d): rolling back", anc, self.last)
        c = self.db.conn()
        c.execute("BEGIN IMMEDIATE")
        for table, col in (("trades", "block"), ("events", "block"), ("blocks", "number"),
                           ("series", "created_block"), ("rounds", "block"), ("samples", "block"),
                           ("nav_samples", "block")):
            c.execute(f"DELETE FROM {table} WHERE {col} > ?", (anc,))
        self.db.set_meta("last_block", anc, c)
        c.execute("COMMIT")
        self.last = anc
        self._load_known()
        row = self.db.one("SELECT number, hash, time FROM blocks WHERE number = ?", (anc,))
        self._set_status("ok", dict(row) if row else None)
        self.rollbacks += 1

    # --- one chunk --------------------------------------------------------------------------
    def _index_range(self, frm: int, to: int) -> dict:
        a = self.cfg["addresses"]
        factory, desk = a["seriesFactory"], a["desk"]
        logs = self.chain.get_logs(frm, to, [TOPIC_FACTORY], factory)
        new_series: dict[str, dict] = {}
        for lg in logs:
            e = BY_TOPIC.get(("factory", lg["topics"][0]))
            if e and e.name == "RecorderDeployed":
                args = e.decode(lg)
                self.recorders[args["recorder"]] = args["feed"]
            elif e and e.name == "SeriesCreated":
                args = e.decode(lg)
                new_series[args["series"]] = args
        addrs = [desk, *self.series, *new_series, *self.recorders]
        logs += self.chain.get_logs(frm, to, [TOPICS_OTHER], addrs)
        logs.sort(key=lambda lg: (int(lg["blockNumber"], 16), int(lg["logIndex"], 16)))

        blocks = sorted({int(lg["blockNumber"], 16) for lg in logs} | {to})
        infos = self._blocks(blocks)
        touched: dict[str, set] = {"series": set(), "feeds": set(), "accounts": set(), "desk": set(),
                                   "newSeries": set(new_series)}
        c = self.db.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            for lg in logs:
                self._store_log(c, lg, infos[int(lg["blockNumber"], 16)]["time"], touched)
            self._store_blocks({b: infos[b] for b in blocks}, c)
            self.db.set_meta("last_block", to, c)
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            self._load_known()
            raise
        self._set_status("ok", infos[to])
        return touched

    def _blocks(self, numbers: list[int]) -> dict[int, dict]:
        res = self.chain.batch([("eth_getBlockByNumber", [hex(b), False]) for b in numbers])
        out = {}
        for b, r in zip(numbers, res):
            if not isinstance(r, dict):
                r = self.chain.rpc("eth_getBlockByNumber", [hex(b), False])
            out[b] = {"number": b, "hash": r["hash"], "time": int(r["timestamp"], 16)}
        return out

    @staticmethod
    def _store_blocks(infos: dict[int, dict], c) -> None:
        c.executemany("INSERT OR REPLACE INTO blocks (number, hash, time) VALUES (?, ?, ?)",
                      [(b["number"], b["hash"], b["time"]) for b in infos.values()])

    def _kind_of(self, address: str) -> str | None:
        a = self.cfg["addresses"]
        if address == a["seriesFactory"]:
            return "factory"
        if address == a["desk"]:
            return "desk"
        if address in self.series:
            return "series"
        if address in self.recorders:
            return "recorder"
        return None

    def _store_log(self, c, lg: dict, t: int, touched: dict) -> None:
        address = lg["address"].lower()
        kind = self._kind_of(address)
        e = BY_TOPIC.get((kind, lg["topics"][0])) if kind else None
        if e is None:
            return
        args = e.decode(lg)
        block, idx, tx = int(lg["blockNumber"], 16), int(lg["logIndex"], 16), lg["transactionHash"]
        if e.name in TRADE_KINDS:
            account = args.get("buyer") or args.get("seller")
            amount = args.get("noteAmount", args.get("writerAmount"))
            usdg = args.get("cost", args.get("proceeds"))
            c.execute("INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (tx, idx, block, t, args["series"], TRADE_KINDS[e.name], account, args["to"], str(amount),
                       args["priceBps"], str(usdg), args["feeBps"], args["feeReceiver"], args["weightsHash"]))
            touched["series"].add(args["series"])
            touched["accounts"].update({account, args["to"]})
            touched["desk"].add(e.name)
            return
        series = feed = account = sender = None
        if kind == "factory" and e.name == "SeriesCreated":
            t_ = args["terms"]
            series, feed = args["series"], args["feed"]
            row = {"address": series, "id": args["seriesId"], "feed": feed, "note": args["note"],
                   "writer": args["writer"], "recorder": self._recorder_of(feed), "strike_time": t_["strikeTime"],
                   "interval": t_["observationInterval"], "count": t_["observationCount"], "ki": t_["kiBarrierBps"],
                   "ac": t_["acBarrierBps"], "coupon": t_["couponBpsPerPeriod"], "created_block": block,
                   "created_time": t, "tx_hash": tx}
            c.execute(f"INSERT OR IGNORE INTO series ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                      list(row.values()))
            self.series[series] = row
        elif kind == "factory":  # RecorderDeployed
            feed = args["feed"]
            address = args["recorder"]  # stored under the recorder's address: the lookup key
        elif kind == "desk":
            touched["desk"].add(e.name)
            series = args.get("series")
            feed = args.get("feed")
            account = args.get("owner")
            sender = args.get("sender")
        elif kind == "series":
            series = address
            feed = self.series[address]["feed"]
            account = args.get("to")
            sender = args.get("caller")
        elif kind == "recorder":
            feed = self.recorders[address]
            touched["feeds"].add(feed)
        if series:
            touched["series"].add(series)
        if feed:
            touched["feeds"].add(feed)
        for x in (account, sender):
            if x:
                touched["accounts"].add(x)
        c.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (tx, idx, block, t, address, e.name, series, feed, account, sender,
                   json.dumps(e.decode_json(lg))))

    def _recorder_of(self, feed: str) -> str | None:
        for r, f in self.recorders.items():
            if f == feed:
                return r
        return self.chain.at("factory", self.cfg["addresses"]["seriesFactory"]).call("recorderOf", feed)

    # --- helpers for the routes ---------------------------------------------------------
    def wait_caught_up(self, timeout: float = 30) -> bool:
        return self.caught_up.wait(timeout)
