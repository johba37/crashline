"""Response objects computed from the chain at one pinned block plus the db.

A `Ctx` pins a request to the indexer's head: every eth_call runs at that
block (batched), every db query sees what was indexed up to it, and the
response carries its {"block", "time"}.
"""

from __future__ import annotations

import json
from typing import Any

from .accounting import UNIT, UNIT_PER_BPS
from .chain import Revert
from .indexer import Snapshot
from .service import SERVICE, ApiError

ZERO = "0x" + "00" * 20
PHASES = ("Pending", "Live", "Settled")
# quoteBuy reverts that say the Desk can't price the series now; others (CapExceeded) only limit size
PRICING_ERRORS = {"NotListed", "FixingPending", "FeedStale", "TooCloseToObservation", "OutOfRange",
                  "Uncertified", "Inconsistent", "NotLive", "BadFeedAnswer", "ModelMismatch"}


def s(x: int) -> str:
    """uint amounts are decimal strings in JSON."""
    return str(int(x))


class Ctx:
    def __init__(self, snap: Snapshot):
        self.snap = snap
        self.cfg, self.chain, self.db = snap.cfg, snap.chain, snap.db
        self.block: int = snap.head["number"]
        self.time: int = snap.head["time"]
        a = self.cfg["addresses"]
        self.desk = self.chain.at("desk", a["desk"])
        self.quoter = self.chain.at("quoter", a["noteQuoter"])
        self.usdg = self.chain.at("usdg", a["usdg"])
        self._feed_names = {v.lower(): k for k, v in a.get("feeds", {}).items()}

    @classmethod
    def now(cls) -> "Ctx":
        """At the indexer's head (503 while it has none)."""
        return cls(SERVICE.snap())

    def out(self, d: dict) -> dict:
        return {**d, "block": self.block, "time": self.time}

    def calls(self, calls: list) -> list:
        return self.chain.call_many(calls, self.block)

    def call(self, contract, fn: str, *args):
        return contract.call(fn, *args, block=self.block)

    def feed_name(self, feed: str) -> str:
        name = self._feed_names.get(feed)
        if name is None:
            try:
                name = self.chain.at("feed", feed).call("description", block=self.block).split(" / ")[0]
            except Exception:
                name = feed
            self._feed_names[feed] = name
        return name

    # --- series -------------------------------------------------------------------------
    def series_rows(self) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM series WHERE created_block <= ? ORDER BY created_block, address", (self.block,))]

    def series_row(self, address: str) -> dict:
        r = self.db.one("SELECT * FROM series WHERE address = ? AND created_block <= ?", (address, self.block))
        if r is None:
            raise ApiError(404, "UnknownSeries", {"address": address})
        return dict(r)


def max_bps(r: dict) -> int:
    """maxPayoutPerNote of a series row, in bps of notional."""
    return 10_000 + r["coupon"] * (r["count"] + 1)


def quotable_of(res: Any, state: dict) -> dict:
    """quoteBuy(series, 1 NOTE, 0) -> {ok, reason, args, until}."""
    if not isinstance(res, Revert) or res.name not in PRICING_ERRORS:
        return {"ok": True, "reason": None, "args": {}, "until": None}
    reason = res.name
    if reason == "NotLive" and state["phase"] == 2:
        reason = "Settled"
    until = None
    if reason == "TooCloseToObservation":
        until = res.fields["obsTime"]  # the band ends at the observation
    elif reason == "Uncertified":
        until = state["nextObservation"] or None  # the excluded regions are observation-day bands
    return {"ok": False, "reason": reason, "args": res.args_json(), "until": until}


def leg_quotes(qb: Any, qs: Any, qbc: Any, qsc: Any) -> dict:
    """The Desk's two prices of each leg at 1 unit, no fee (quoteBuy / quoteSell /
    quoteBuyCover / quoteSellCover): NOTE ask / bid, cover ask / bid in bps of notional,
    the vol band and the flat spread included. A quote that reverts is null and its
    error is in `errors` (CapExceeded on the NOTE ask and the cover bid: the Desk's WRITER
    cap is full, so it can't sell NOTE / buy cover back beyond its inventory)."""
    out: dict = {"note": {}, "cover": {}, "errors": {}}
    for leg, side, res in (("note", "askBps", qb), ("note", "bidBps", qs), ("cover", "askBps", qbc),
                           ("cover", "bidBps", qsc)):
        if isinstance(res, Revert):
            out[leg][side] = None
            out["errors"][f"{leg}.{side}"] = {"error": res.name, "args": res.args_json()}
        else:
            out[leg][side] = res[1]
    return out


def series_objects(ctx: Ctx, rows: list[dict], full: bool = False) -> list[dict]:
    """The /series objects of `rows` at ctx.block; `full` adds fixings and the last 50 trades."""
    chain, desk = ctx.chain, ctx.desk
    per = 11
    calls = []
    for r in rows:
        sr = chain.at("series", r["address"])
        calls += [(sr, "state", ()), (sr, "pendingObservation", ()), (sr, "maxPayoutPerNote", ()),
                  (desk, "listing", (r["address"],)), (desk, "spread", (r["address"],)),
                  (chain.at("token", r["note"]), "balanceOf", (desk.address,)),
                  (chain.at("token", r["writer"]), "balanceOf", (desk.address,)),
                  (desk, "quoteBuy", (r["address"], UNIT, 0)), (desk, "quoteSell", (r["address"], UNIT, 0)),
                  (desk, "quoteBuyCover", (r["address"], UNIT, 0)), (desk, "quoteSellCover", (r["address"], UNIT, 0))]
    res = ctx.calls(calls)
    out, mids = [], []
    for i, r in enumerate(rows):
        st, pend, maxp, lst, spr, n_held, w_held, qb, qs, qbc, qsc = res[i * per:(i + 1) * per]
        for v in (st, pend, maxp, lst, spr, n_held, w_held):
            if isinstance(v, Revert):
                raise ApiError(502, "ChainReadFailed", {"series": r["address"], "error": v.name})
        quotable = quotable_of(qb, st)
        listed = lst["pricer"] != ZERO
        o = {
            "address": r["address"], "note": r["note"], "writer": r["writer"], "recorder": r["recorder"],
            "feed": r["feed"], "feedName": ctx.feed_name(r["feed"]), "id": r["id"],
            "terms": {"strikeTime": r["strike_time"], "observationInterval": r["interval"],
                      "observationCount": r["count"], "kiBarrierBps": r["ki"], "acBarrierBps": r["ac"],
                      "couponBpsPerPeriod": r["coupon"]},
            "maxPayoutPerNote": s(maxp), "maxBps": maxp // UNIT_PER_BPS,
            "state": {"phase": PHASES[st["phase"]], "initialFixing": s(st["initialFixing"]),
                      "observationsDone": st["observationsDone"], "knockedIn": st["knockedIn"],
                      "autocalled": st["autocalled"], "nextObservation": st["nextObservation"],
                      "maturity": st["maturity"], "payoutPerNote": s(st["payoutPerNote"]),
                      "pendingObservation": {"pending": pend[0], "obsTime": pend[1]}},
            "listing": {"active": lst["active"], "pricer": lst["pricer"], "volBpsAnnual": lst["volBpsAnnual"],
                        "capNotional": s(lst["capNotional"]), "writerHeld": s(lst["soldNotional"])} if listed else None,
            "spread": spr,
            "desk": {"noteHeld": s(n_held), "writerHeld": s(w_held)},
            "quotable": quotable,
            "mid": None,
            "quotes": None,
        }
        if quotable["ok"] and listed:
            o["quotes"] = leg_quotes(qb, qs, qbc, qsc)
            mids.append((o, lst))
        out.append(o)
    if mids:
        mres = ctx.calls([(ctx.quoter, "notePriceBps", (o["address"], lst["pricer"], lst["volBpsAnnual"]))
                          for o, lst in mids])
        for (o, _), m in zip(mids, mres):
            if isinstance(m, Revert):  # quoteBuy passed, so this can't happen; report it rather than guess
                o["quotable"] = quotable_of(m, {"phase": 1, "nextObservation": o["state"]["nextObservation"]})
                continue
            note_bps = m[0]
            o["mid"] = {"noteBps": note_bps, "coverBps": o["maxBps"] - min(note_bps, o["maxBps"])}
    if full:
        for o, r in zip(out, rows):
            o["fixings"] = fixings(ctx, r)
            o["trades"] = trades(ctx, series=r["address"], limit=50)
    return out


def fixings(ctx: Ctx, r: dict) -> list[dict]:
    """The recorder's fixings on this series' schedule (index 0 = strike, count + 1 = maturity)."""
    rows = ctx.db.query("SELECT args FROM events WHERE name = 'FixingRecorded' AND address = ? AND block <= ? "
                        "ORDER BY block, log_index", (r["recorder"], ctx.block))
    out = []
    for row in rows:
        a = json.loads(row["args"])
        k, rem = divmod(a["obsTime"] - r["strike_time"], r["interval"])
        if rem == 0 and 0 <= k <= r["count"] + 1:
            out.append({"obsTime": a["obsTime"], "price": a["price"], "roundId": a["roundId"], "index": k,
                        "updatedAt": a["timestamp"]})
    return sorted(out, key=lambda f: f["index"])


def trade_json(r) -> dict:
    return {"txHash": r["tx_hash"], "logIndex": r["log_index"], "block": r["block"], "time": r["time"],
            "series": r["series"], "kind": r["kind"], "account": r["account"], "to": r["recipient"],
            "amount": r["amount"], "priceBps": r["price_bps"], "usdg": r["usdg"], "feeBps": r["fee_bps"],
            "feeReceiver": r["fee_receiver"], "weightsHash": r["weights_hash"]}


def trades(ctx: Ctx, series: str | None = None, account: str | None = None, limit: int = 50,
           before: int | None = None) -> list[dict]:
    """Newest first. `account` matches the trader or the recipient; `before` is a block (exclusive)."""
    where, params = ["block <= ?"], [ctx.block]
    if series:
        where.append("series = ?")
        params.append(series)
    if account:
        where.append("(account = ? OR recipient = ?)")
        params += [account, account]
    if before is not None:
        where.append("block < ?")
        params.append(before)
    rows = ctx.db.query(f"SELECT * FROM trades WHERE {' AND '.join(where)} ORDER BY block DESC, log_index DESC "
                        "LIMIT ?", (*params, limit))
    return [trade_json(r) for r in rows]


def mark_inputs(ctx: Ctx, rows: list[dict]) -> dict[str, dict]:
    """series -> {row, state, maxPayout, listing, noteBps}: what Desk._position needs.
    noteBps is the quoter's mid at the listing's vol, None if not listed or it reverts."""
    if not rows:
        return {}
    chain = ctx.chain
    calls = []
    for r in rows:
        sr = chain.at("series", r["address"])
        calls += [(sr, "state", ()), (sr, "maxPayoutPerNote", ()), (ctx.desk, "listing", (r["address"],))]
    res = ctx.calls(calls)
    out, need = {}, []
    for i, r in enumerate(rows):
        st, maxp, lst = res[3 * i:3 * i + 3]
        out[r["address"]] = {"row": r, "state": st, "maxPayout": maxp, "listing": lst, "noteBps": None}
        if lst["pricer"] != ZERO:
            need.append(r["address"])
    mids = ctx.calls([(ctx.quoter, "notePriceBps", (a, out[a]["listing"]["pricer"],
                                                     out[a]["listing"]["volBpsAnnual"])) for a in need])
    for a, m in zip(need, mids):
        if not isinstance(m, Revert):
            out[a]["noteBps"] = m[0]
    return out


def share_price(total_assets: int, total_supply: int) -> str:
    """USDG per share (OpenZeppelin's conversion with the Desk's 6-decimal offset), 12 places."""
    scaled = UNIT * (total_assets + 1) * 10**12 // (total_supply + 10**6)
    return f"{scaled // 10**12}.{scaled % 10**12:012d}"


def known_feeds(ctx: Ctx) -> list[str]:
    feeds = {r["feed"] for r in ctx.db.query("SELECT DISTINCT feed FROM series WHERE created_block <= ?",
                                             (ctx.block,))}
    feeds |= {a.lower() for a in ctx.cfg["addresses"].get("feeds", {}).values()}
    return sorted(feeds)
