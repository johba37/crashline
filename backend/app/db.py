"""SQLite store of the indexer (stdlib sqlite3, no ORM).

One file per chain (config `db`, env BACKEND_DB overrides). WAL mode: the
indexer thread writes, the request threads read, each with its own connection.
Amounts are TEXT (decimal strings: uint256 does not fit SQLite's int64).
`meta` holds the deployment fingerprint and the last indexed block; a
fingerprint or schema change wipes every table (the indexer rescans).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- blocks that carry an indexed event, and the indexed head
CREATE TABLE IF NOT EXISTS blocks (number INTEGER PRIMARY KEY, hash TEXT NOT NULL, time INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS series (
    address TEXT PRIMARY KEY, id TEXT, feed TEXT, note TEXT, writer TEXT, recorder TEXT,
    strike_time INTEGER, interval INTEGER, count INTEGER, ki INTEGER, ac INTEGER, coupon INTEGER,
    created_block INTEGER, created_time INTEGER, tx_hash TEXT);
CREATE TABLE IF NOT EXISTS trades (
    tx_hash TEXT NOT NULL, log_index INTEGER NOT NULL, block INTEGER NOT NULL, time INTEGER NOT NULL,
    series TEXT NOT NULL, kind TEXT NOT NULL, account TEXT NOT NULL, recipient TEXT NOT NULL,
    amount TEXT NOT NULL, price_bps INTEGER NOT NULL, usdg TEXT NOT NULL, fee_bps INTEGER NOT NULL,
    fee_receiver TEXT NOT NULL, weights_hash TEXT NOT NULL,
    PRIMARY KEY (tx_hash, log_index));
CREATE INDEX IF NOT EXISTS trades_series ON trades (series, block, log_index);
CREATE INDEX IF NOT EXISTS trades_account ON trades (account, block, log_index);
CREATE INDEX IF NOT EXISTS trades_recipient ON trades (recipient, block, log_index);
CREATE TABLE IF NOT EXISTS events (
    tx_hash TEXT NOT NULL, log_index INTEGER NOT NULL, block INTEGER NOT NULL, time INTEGER NOT NULL,
    address TEXT NOT NULL, name TEXT NOT NULL, series TEXT, feed TEXT, account TEXT, sender TEXT,
    args TEXT NOT NULL,
    PRIMARY KEY (tx_hash, log_index));
CREATE INDEX IF NOT EXISTS events_name ON events (name, block, log_index);
CREATE INDEX IF NOT EXISTS events_series ON events (series, block, log_index);
CREATE INDEX IF NOT EXISTS events_account ON events (account, block, log_index);
CREATE INDEX IF NOT EXISTS events_address ON events (address, name);
"""

TABLES = ("blocks", "series", "trades", "events")


class DB:
    def __init__(self, path: Path | str, schema: str = SCHEMA, tables: tuple[str, ...] = TABLES):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.schema = schema
        self.tables = tables
        self._local = threading.local()
        c = self.conn()
        c.executescript(schema)
        c.commit()

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
        return c

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value, conn: sqlite3.Connection | None = None) -> None:
        (conn or self.conn()).execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))

    def wipe(self) -> None:
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        for t in self.tables:
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM meta")
        c.execute("COMMIT")

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        return self.conn().execute(sql, params).fetchone()
