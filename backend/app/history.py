"""History (WP3): marks per series and the vault's NAV over time.

Sampling. An indexer hook samples, at the new head block, every series whose
last on-chain sample is `historyStepSecs` (3600 s) old or that something
touched in the chunk (a trade, a fixing or observation, a listing change, a
new feed round), and the vault's NAV likewise (any Desk event, or a step).
On the dev node, where blocks are rarer than the step, that is every block.

Backfill. A background thread fills the grid strike + k * step where no
sample lies in a slot (`historyStepSecs`; before the series existed
`replayStepSecs`, both 3600 s by default):
  - after the series existed on-chain: `eth_call` at the last block inside
    the slot (the node runs in archive mode); a slot without a block has no
    state change and stays empty;
  - before it existed (the dev node's scenarios stage a strike weeks before
    their first block, so there is no block to call): the quoter's answer is
    replayed off-chain (app/replay.py: the recorded fixings, the feed round in
    force, NoteQuoter's derivation, the bit-exact student). These points have
    `source: "replay"` and `block: null`.
The vault's NAV is backfilled by `eth_call` only (the vault has no life
before its deployment).

Points carry the quoter's mid (`notePriceBps` at the listing's vol, null when
it refuses) and the feed's spot. The routes add a live point at the
response's block, so the last point is what /series/{addr} shows now.
"""

from __future__ import annotations

import bisect
import logging
import threading

from fastapi import APIRouter, Query

from .chain import Revert
from .indexer import Snapshot, fingerprint
from .replay import Terms, quote_points
from .service import SERVICE, addr, model_exports
from .views import ZERO, Ctx, fixings, max_bps, s, share_price

log = logging.getLogger("history")
router = APIRouter()


def ctx_at(snap: Snapshot, block: int, time: int) -> Ctx:
    return Ctx(Snapshot(snap.cfg, snap.chain, snap.db, {"number": block, "time": time, "hash": None}, snap.status))


def chain_points(c: Ctx, rows: list[dict]) -> list[dict]:
    """The quoter's mid and the spot of each series at c.block."""
    if not rows:
        return []
    calls = []
    for r in rows:
        calls += [(c.chain.at("series", r["address"]), "state", ()), (c.desk, "listing", (r["address"],)),
                  (c.chain.at("feed", r["feed"]), "latestRoundData", ())]
    res = c.calls(calls)
    pts, need = [], []
    for i, r in enumerate(rows):
        st, lst, rd = res[3 * i:3 * i + 3]
        initial = st["initialFixing"] if not isinstance(st, Revert) else 0
        spot = rd[1] if not isinstance(rd, Revert) else None
        p = {"series": r["address"], "time": c.time, "block": c.block, "spot": spot,
             "spotBps": spot * 10_000 // initial if spot is not None and initial else None,
             "noteBps": None, "coverBps": None, "quotable": False, "reason": None}
        pts.append(p)
        if isinstance(lst, Revert) or lst["pricer"] == ZERO:
            p["reason"] = "NotListed"
        else:
            need.append((p, r, lst))
    mids = c.calls([(c.quoter, "notePriceBps", (r["address"], lst["pricer"], lst["volBpsAnnual"]))
                    for _, r, lst in need])
    for (p, r, _), m in zip(need, mids):
        if isinstance(m, Revert):
            p["reason"] = m.name
        else:
            mb = max_bps(r)
            p.update(noteBps=m[0], coverBps=mb - min(m[0], mb), quotable=True)
    return pts


def store_points(db, pts: list[dict], source: str, trigger: str) -> None:
    c = db.conn()
    c.execute("BEGIN IMMEDIATE")
    c.executemany(
        "INSERT OR IGNORE INTO samples (series, time, block, spot, spot_bps, note_bps, cover_bps, quotable, reason, "
        "source, trigger) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(p["series"], p["time"], p["block"] if p["block"] is not None else -1,
          str(p["spot"]) if p["spot"] is not None else None, p["spotBps"], p["noteBps"], p["coverBps"],
          int(p["quotable"]), p["reason"], source, trigger) for p in pts])
    c.execute("COMMIT")


def nav_point(c: Ctx) -> dict | None:
    try:
        ta, ts = c.calls([(c.desk, "totalAssets", ()), (c.desk, "totalSupply", ())])
    except Exception:  # no Desk code at that block
        return None
    if isinstance(ts, Revert):
        return None
    ok = not isinstance(ta, Revert)
    return {"time": c.time, "block": c.block, "totalAssets": s(ta) if ok else None, "totalSupply": s(ts),
            "sharePrice": share_price(ta, ts) if ok else None}


def store_nav(db, p: dict, trigger: str) -> None:
    db.conn().execute("INSERT OR IGNORE INTO nav_samples (block, time, total_assets, total_supply, share_price, "
                      "trigger) VALUES (?,?,?,?,?,?)", (p["block"], p["time"], p["totalAssets"], p["totalSupply"],
                                                        p["sharePrice"], trigger))


class History:
    def __init__(self, indexer):
        self.indexer = indexer
        self.wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._block_times: dict[int, int] = {}
        self._staleness: dict[str, int] = {}
        self._empty: set[tuple[str, int]] = set()  # past slots without a block: they stay empty
        self._empty_for = ""

    # --- live sampling (indexer hook) ------------------------------------------------------
    def hook(self, indexer, frm: int, to: int, touched: dict) -> None:
        snap = indexer.snap
        head, db, cfg = snap.head, snap.db, snap.cfg
        step = cfg.get("historyStepSecs", 3600)
        rows = [dict(r) for r in db.query("SELECT * FROM series WHERE created_block <= ?", (to,))]
        last = {r[0]: r[1] for r in db.query(
            "SELECT series, MAX(time) FROM samples WHERE source = 'chain' GROUP BY series")}
        due, events = [], set()
        for r in rows:
            ev = r["address"] in touched["series"] or r["feed"] in touched["feeds"]
            if ev or head["time"] >= last.get(r["address"], -(2**62)) + step:
                due.append(r)
                if ev:
                    events.add(r["address"])
        c = ctx_at(snap, to, head["time"])
        if due:
            pts = chain_points(c, due)
            store_points(db, [p for p in pts if p["series"] in events], "chain", "event")
            store_points(db, [p for p in pts if p["series"] not in events], "chain", "step")
        last_nav = db.one("SELECT MAX(time) FROM nav_samples")[0]
        nav_ev = bool(touched["desk"] or touched["series"] or touched["feeds"])
        if nav_ev or last_nav is None or head["time"] >= last_nav + step:
            p = nav_point(c)
            if p:
                store_nav(db, p, "event" if nav_ev else "step")
        if touched["newSeries"] or touched["desk"] & {"SeriesListed"}:
            self.wake.set()

    # --- backfill thread ------------------------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="history-backfill", daemon=True)
        self._thread.start()
        self.wake.set()

    def stop(self) -> None:
        self._stop.set()
        self.wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.wake.wait(60)
            self.wake.clear()
            if self._stop.is_set():
                return
            try:
                snap = self.indexer.snap
                if snap is None or snap.head is None or snap.status != "ok":
                    continue
                self.backfill(snap)
            except Exception as e:
                log.exception("backfill failed: %s", e)

    def backfill(self, snap: Snapshot) -> None:
        self._block_times = {}
        if self._empty_for != fingerprint(snap.cfg):
            self._empty, self._empty_for = set(), fingerprint(snap.cfg)
        for r in snap.db.query("SELECT * FROM series WHERE created_block <= ?", (snap.head["number"],)):
            self.fill_series(snap, dict(r))
        self.fill_nav(snap)

    def block_at(self, chain, t: int, lo: int, hi: int) -> int | None:
        """The last block in [lo, hi] with timestamp <= t (binary search, cached)."""
        def time_of(n: int) -> int:
            if n not in self._block_times:
                self._block_times[n] = chain.block(n)["time"]
            return self._block_times[n]
        if time_of(lo) > t:
            return None
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if time_of(mid) <= t:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def _slots(self, start: int, end: int, step: int, have: list[int]) -> list[int]:
        """Grid times in [start, end] whose slot [t, t + step) holds no sample."""
        out = []
        for t in range(start, end + 1, step):
            k = bisect.bisect_left(have, t)
            if k == len(have) or have[k] >= t + step:
                out.append(t)
        return out

    def max_staleness(self, snap: Snapshot) -> int:
        q = snap.cfg["addresses"]["noteQuoter"]
        if q not in self._staleness:
            self._staleness[q] = snap.chain.at("quoter", q).call("MAX_FEED_STALENESS")
        return self._staleness[q]

    def fill_series(self, snap: Snapshot, r: dict) -> None:
        db, chain, head = snap.db, snap.chain, snap.head
        step = snap.cfg.get("historyStepSecs", 3600)
        replay_step = snap.cfg.get("replayStepSecs", 3600)
        have = [x[0] for x in db.query("SELECT time FROM samples WHERE series = ? ORDER BY time", (r["address"],))]
        before = [t for t in self._slots(r["strike_time"], head["time"], replay_step, have) if t < r["created_time"]]
        if before:
            self._replay(snap, r, before)
        # the on-chain grid starts at the first slot boundary at or after the series' creation
        k0 = -(-(r["created_time"] - r["strike_time"]) // step)
        after = self._slots(r["strike_time"] + k0 * step, head["time"], step, have)
        pts = []
        for t in after:
            if (r["address"], t) in self._empty:
                continue
            b = self.block_at(chain, min(t + step - 1, head["time"]), r["created_block"], head["number"])
            if b is None or self._block_times[b] < t:
                if t + step <= head["time"]:
                    self._empty.add((r["address"], t))
                continue
            pts += chain_points(ctx_at(snap, b, self._block_times[b]), [r])
        if pts:
            store_points(db, pts, "chain", "backfill")

    def _replay(self, snap: Snapshot, r: dict, ts: list[int]) -> None:
        c = ctx_at(snap, snap.head["number"], snap.head["time"])
        lst = c.call(c.desk, "listing", r["address"])
        if lst["pricer"] == ZERO:
            return  # priced once it is listed
        weights = c.call(c.chain.at("pricer", lst["pricer"]), "weightsHash")
        found = model_exports().get(weights)
        if found is None:
            return
        fx = {f["index"]: int(f["price"]) for f in fixings(c, r)}
        rounds = [(x[0], int(x[1])) for x in snap.db.query(
            "SELECT updated_at, answer FROM rounds WHERE feed = ? ORDER BY updated_at", (r["feed"],))]
        pts = quote_points(ts, Terms.of_row(r), fx, rounds, found[1], lst["volBpsAnnual"], self.max_staleness(snap))
        mb = max_bps(r)
        for p in pts:
            p.update(series=r["address"], block=None,
                     coverBps=mb - min(p["noteBps"], mb) if p["noteBps"] is not None else None)
        store_points(snap.db, pts, "replay", "backfill")

    def fill_nav(self, snap: Snapshot) -> None:
        cfg, db, chain, head = snap.cfg, snap.db, snap.chain, snap.head
        step = cfg.get("historyStepSecs", 3600)
        start = cfg.get("deployedAt") or chain.block(cfg["deploymentBlock"])["time"]
        have = [x[0] for x in db.query("SELECT time FROM nav_samples ORDER BY time")]
        for t in self._slots(start, head["time"], step, have):
            if ("nav", t) in self._empty:
                continue
            b = self.block_at(chain, min(t + step - 1, head["time"]), cfg["deploymentBlock"], head["number"])
            if b is None or self._block_times[b] < t:
                if t + step <= head["time"]:
                    self._empty.add(("nav", t))
                continue
            p = nav_point(ctx_at(snap, b, self._block_times[b]))
            if p:
                store_nav(db, p, "backfill")


HISTORY = History(SERVICE.indexer)


# --- routes -------------------------------------------------------------------------------------

def _thin(points: list[dict], origin: int, step: int, keep) -> list[dict]:
    """The last point of every `step` bucket, plus every point `keep` says to keep."""
    last_in_bucket: dict[int, int] = {}
    for i, p in enumerate(points):
        last_in_bucket[(p["time"] - origin) // step] = i
    idx = set(last_in_bucket.values()) | {i for i, p in enumerate(points) if keep(p)}
    return [points[i] for i in sorted(idx)]


@router.get("/series/{address}/history")
def series_history(address: str, frm: int | None = Query(None, alias="from", ge=0), to: int | None = Query(None, ge=0),
                   step: int = Query(3600, ge=1)):
    c = Ctx.now()
    r = c.series_row(addr(address))
    frm = r["strike_time"] if frm is None else frm
    to = c.time if to is None else to
    obs_index = {r["strike_time"] + i * r["interval"]: i for i in range(r["count"] + 2)}
    rows = c.db.query("SELECT * FROM samples WHERE series = ? AND time >= ? AND time <= ? AND block <= ? "
                      "ORDER BY time, block", (r["address"], frm, to, c.block))
    pts = [{"time": x["time"], "block": x["block"] if x["block"] >= 0 else None, "spotBps": x["spot_bps"],
            "spot": x["spot"], "noteBps": x["note_bps"], "coverBps": x["cover_bps"], "quotable": bool(x["quotable"]),
            "reason": x["reason"], "source": x["source"], "observation": obs_index.get(x["time"]),
            "_trigger": x["trigger"]} for x in rows]
    pts = _thin(pts, r["strike_time"], step, lambda p: p["observation"] is not None or p["_trigger"] == "event")
    if frm <= c.time <= to:
        live = chain_points(c, [r])[0]
        pts = [p for p in pts if p["block"] != c.block]
        pts.append({"time": c.time, "block": c.block, "spotBps": live["spotBps"],
                    "spot": str(live["spot"]) if live["spot"] is not None else None, "noteBps": live["noteBps"],
                    "coverBps": live["coverBps"], "quotable": live["quotable"], "reason": live["reason"],
                    "source": "chain", "observation": obs_index.get(c.time), "_trigger": "live"})
    for p in pts:
        del p["_trigger"]
    fx = fixings(c, r)
    initial = next((int(f["price"]) for f in fx if f["index"] == 0), None)
    observations = [{"obsTime": f["obsTime"], "index": f["index"],
                     "fixingBps": int(f["price"]) * 10_000 // initial if initial else None} for f in fx]
    return c.out({"series": r["address"], "step": step, "points": pts, "observations": observations})


@router.get("/vault/history")
def vault_history(frm: int | None = Query(None, alias="from", ge=0), to: int | None = Query(None, ge=0),
                  step: int = Query(3600, ge=1)):
    c = Ctx.now()
    frm = c.cfg.get("deployedAt", 0) if frm is None else frm
    to = c.time if to is None else to
    rows = c.db.query("SELECT * FROM nav_samples WHERE time >= ? AND time <= ? AND block <= ? ORDER BY block",
                      (frm, to, c.block))
    pts = [{"time": x["time"], "block": x["block"], "totalAssets": x["total_assets"],
            "totalSupply": x["total_supply"], "sharePrice": x["share_price"], "_trigger": x["trigger"]} for x in rows]
    pts = _thin(pts, frm, step, lambda p: p["_trigger"] == "event")
    if frm <= c.time <= to:
        live = nav_point(c)
        if live:
            pts = [p for p in pts if p["block"] != c.block] + [dict(live, _trigger="live")]
    for p in pts:
        del p["_trigger"]
    return c.out({"step": step, "points": pts})
