"""WP2 routes: /accounts/{addr}, /vault, /feeds/{addr}."""

from __future__ import annotations

from fastapi import APIRouter, Query

from .accounting import UNIT, fifo, note_per_unit, position
from .chain import Revert
from .service import ApiError, addr
from .views import Ctx, known_feeds, mark_inputs, s, share_price, trades

router = APIRouter()


def _risk_json(at_risk: int, limit: int) -> dict:
    """room = limit - atRisk, negative when the feed is over its budget."""
    return {"atRisk": s(at_risk), "limit": s(limit), "room": str(limit - at_risk)}


@router.get("/vault")
def get_vault():
    c = Ctx.now()
    feeds = known_feeds(c)
    head = c.calls([(c.desk, "totalAssets", ()), (c.desk, "totalSupply", ()),
                    (c.usdg, "balanceOf", (c.desk.address,)), (c.desk, "reservedAssets", ()),
                    (c.desk, "queuedShares", ()), (c.desk, "queue", ()), (c.desk, "heldSeries", ())]
                   + [(c.desk, "risk", (f,)) for f in feeds])
    total_assets, total_supply, balance, reserved, queued, (q_head, q_len), held = head[:7]
    risks = head[7:]
    rows = [c.series_row(a) for a in held]
    marks = mark_inputs(c, rows)
    bal = c.calls([x for r in rows for x in ((c.chain.at("token", r["note"]), "balanceOf", (c.desk.address,)),
                                             (c.chain.at("token", r["writer"]), "balanceOf", (c.desk.address,)))])
    inventory = []
    for i, r in enumerate(rows):
        m = marks[r["address"]]
        n, w = bal[2 * i], bal[2 * i + 1]
        p = position(n, w, m["state"], r["count"], m["maxPayout"], m["noteBps"])
        inventory.append({"series": r["address"], "noteHeld": s(n), "writerHeld": s(w),
                          "mark": s(p["value"]), "atRisk": s(p["atRisk"]), "quotable": p["ok"]})
    nav_error = total_assets.to_json() if isinstance(total_assets, Revert) else None
    ok = nav_error is None
    return c.out({
        "totalAssets": s(total_assets) if ok else None,
        "totalSupply": s(total_supply),
        "sharePrice": share_price(total_assets, total_supply) if ok else None,
        "navError": nav_error,
        "idle": s(max(balance - reserved, 0)),
        "reserved": s(reserved),
        "queuedShares": s(queued),
        "queue": {"head": q_head, "length": q_len},
        "feeds": {f: _risk_json(*r) for f, r in zip(feeds, risks) if not isinstance(r, Revert)},
        "inventory": inventory,
    })


@router.get("/feeds/{address}")
def get_feed(address: str, frm: int | None = Query(None, alias="from", ge=0), to: int | None = Query(None, ge=0)):
    c = Ctx.now()
    feed = addr(address)
    if feed not in known_feeds(c):
        raise ApiError(404, "UnknownFeed", {"address": feed})
    f = c.chain.at("feed", feed)
    dec, latest, risk, budget = c.calls([(f, "decimals", ()), (f, "latestRoundData", ()), (c.desk, "risk", (feed,)),
                                         (c.desk, "riskBudgetBps", (feed,))])
    series = [r["address"] for r in c.db.query(
        "SELECT address FROM series WHERE feed = ? AND created_block <= ? ORDER BY created_block", (feed, c.block))]
    if frm is None and to is None:
        rows = c.db.query("SELECT * FROM (SELECT * FROM rounds WHERE feed = ? AND block <= ? ORDER BY n DESC "
                          "LIMIT 200) ORDER BY n", (feed, c.block))
    else:
        rows = c.db.query("SELECT * FROM rounds WHERE feed = ? AND block <= ? AND updated_at >= ? AND updated_at <= ? "
                          "ORDER BY n LIMIT 10000", (feed, c.block, frm or 0, to if to is not None else 2**40))
    return c.out({
        "address": feed,
        "name": c.feed_name(feed),
        "decimals": dec,
        "latest": None if isinstance(latest, Revert) else
        {"roundId": s(latest[0]), "answer": s(latest[1]), "updatedAt": latest[3]},
        "risk": _risk_json(*risk) | {"budgetBps": budget},
        "series": series,
        "rounds": [{"roundId": r["round_id"], "answer": r["answer"], "updatedAt": r["updated_at"]} for r in rows],
    })


@router.get("/accounts/{address}")
def get_account(address: str):
    c = Ctx.now()
    a = addr(address)
    rows = c.series_rows()
    q_ids = [int(r["args_id"]) for r in c.db.query(
        "SELECT json_extract(args, '$.id') AS args_id FROM events WHERE name = 'RedeemRequested' AND account = ? "
        "AND block <= ? ORDER BY block, log_index", (a, c.block))]
    base = c.calls([(c.usdg, "balanceOf", (a,)), (c.desk, "balanceOf", (a,)), (c.desk, "claimableAssets", (a,)),
                    (c.desk, "queue", ())]
                   + [x for r in rows for x in ((c.chain.at("token", r["note"]), "balanceOf", (a,)),
                                                (c.chain.at("token", r["writer"]), "balanceOf", (a,)))])
    usdg, shares, claimable, (q_head, q_len) = base[:4]
    held = {r["address"]: (base[4 + 2 * i], base[5 + 2 * i]) for i, r in enumerate(rows)}
    (shares_value,) = c.calls([(c.desk, "convertToAssets", (shares,))])

    books = fifo(c.db, a, c.block)
    pos_rows = [r for r in rows if any(held[r["address"]]) or r["address"] in books]
    marks = mark_inputs(c, pos_rows)
    positions = []
    for r in pos_rows:
        n, w = held[r["address"]]
        m = marks[r["address"]]
        per, _ = note_per_unit(m["state"], r["count"], m["maxPayout"], m["noteBps"])
        b = books.get(r["address"])
        positions.append({
            "series": r["address"], "note": s(n), "writer": s(w),
            "noteMark": s(n * per // UNIT) if per is not None else None,
            "coverMark": s(w * (m["maxPayout"] - per) // UNIT) if per is not None else None,
            "costBasis": {"note": s(b.basis("note") if b else 0), "writer": s(b.basis("writer") if b else 0)},
            "realized": str(b.realized if b else 0),
        })

    queue = []
    own = [i for i in q_ids if q_head <= i < q_len]
    if own:
        span = list(range(q_head, max(own) + 1))
        rest = c.calls([(c.desk, "redeemRequest", (i,)) for i in span])
        remaining = {i: r[1] for i, r in zip(span, rest)}
        times = {int(r["id"]): r["time"] for r in c.db.query(
            "SELECT json_extract(args, '$.id') AS id, time FROM events WHERE name = 'RedeemRequested' "
            "AND account = ? AND block <= ?", (a, c.block))}
        for i in own:
            if remaining[i]:
                queue.append({"id": i, "shares": s(remaining[i]), "requestedAt": times[i],
                              "position": sum(1 for j in range(q_head, i) if remaining[j])})
    return c.out({
        "usdg": s(usdg), "shares": s(shares),
        "sharesValue": None if isinstance(shares_value, Revert) else s(shares_value),
        "positions": positions,
        "queue": queue,
        "claimable": s(claimable),
        "trades": trades(c, account=a, limit=50),
    })
