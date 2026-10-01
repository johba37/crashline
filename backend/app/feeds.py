"""Feed rounds: the mock feed (and Chainlink's) emit no events, so after every
indexed chunk this hook compares each known feed's `latestRound()` with the
rounds stored and fetches the new ones with batched `getRoundData`. Feeds
whose rounds changed are added to `touched["feeds"]` for later hooks."""

from __future__ import annotations

import logging

from .chain import Revert

log = logging.getLogger("feeds")

PHASE_SHIFT = 64
FIRST_SYNC_MAX = 5_000  # a real feed may have many rounds: start this far back


def known_feeds(indexer) -> list[str]:
    feeds = {r["feed"] for r in indexer.db.query("SELECT DISTINCT feed FROM series")}
    feeds |= {a.lower() for a in indexer.cfg["addresses"].get("feeds", {}).values()}
    return sorted(feeds)


def sync_rounds(indexer, frm: int, to: int, touched: dict) -> None:
    chain, db = indexer.chain, indexer.db
    feeds = known_feeds(indexer)
    if not feeds:
        return
    latest = chain.call_many([(chain.at("feed", f), "latestRound", ()) for f in feeds], to)
    for feed, rid in zip(feeds, latest):
        if isinstance(rid, Revert):  # NoRounds yet
            continue
        phase, n_last = rid >> PHASE_SHIFT, rid & ((1 << PHASE_SHIFT) - 1)
        row = db.one("SELECT MAX(n) FROM rounds WHERE feed = ?", (feed,))
        have = row[0] or max(0, n_last - FIRST_SYNC_MAX)
        if n_last <= have:
            continue
        ids = [(phase << PHASE_SHIFT) | n for n in range(have + 1, n_last + 1)]
        f = chain.at("feed", feed)
        res = chain.call_many([(f, "getRoundData", (i,)) for i in ids], to)
        rows = []
        for i, r in zip(ids, res):
            if isinstance(r, Revert):
                continue
            _, answer, _, updated_at, _ = r
            rows.append((feed, i & ((1 << PHASE_SHIFT) - 1), str(i), str(answer), updated_at, to))
        c = db.conn()
        c.execute("BEGIN IMMEDIATE")
        c.executemany("INSERT OR IGNORE INTO rounds (feed, n, round_id, answer, updated_at, block) "
                      "VALUES (?,?,?,?,?,?)", rows)
        c.execute("COMMIT")
        touched["feeds"].add(feed)
