"""WP1 routes: /health, /config, /series, /series/{addr}, /trades, /events."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query

from .config import ROOT
from .service import SERVICE, ApiError, addr, git_commit, model_exports
from .views import Ctx, s, series_objects, trades

router = APIRouter()


@router.get("/health")
def health():
    snap = SERVICE.indexer.snap
    ok = snap is not None and snap.head is not None and snap.status == "ok"
    body = {"ok": ok, "status": snap.status if snap else "starting", "error": SERVICE.indexer.error,
            "rollbacks": SERVICE.indexer.rollbacks,
            "block": snap.head["number"] if snap and snap.head else None,
            "time": snap.head["time"] if snap and snap.head else None}
    if not ok:
        raise ApiError(503, "NotReady", body)
    return body


@router.get("/config")
def get_config():
    c = Ctx.now()
    a = c.cfg["addresses"]
    pricer = c.chain.at("pricer", a["surrogatePricer"])
    (fee, cover_fee, backstop, min_secs, min_req, batch, weights, fsv) = c.calls([
        (c.desk, "MAX_FEE_BPS", ()), (c.desk, "MAX_COVER_FEE_BPS", ()), (c.desk, "BACKSTOP_SHARE_BPS", ()),
        (c.desk, "minSecsToObservation", ()), (c.desk, "MIN_REQUEST_SHARES", ()), (c.desk, "QUEUE_BATCH", ()),
        (pricer, "weightsHash", ()), (pricer, "featureSpecVersion", ())])
    model_dir, export = model_exports().get(weights, (c.cfg["modelDir"], None))
    if export is None:
        export = json.loads((ROOT / model_dir / "student_export.json").read_text())
    return c.out({
        "chainId": c.cfg["chainId"],
        "rpcUrl": c.cfg.get("publicRpcUrl", c.cfg["rpcUrl"]),
        "deploymentBlock": c.cfg["deploymentBlock"],
        "addresses": {k: a[k] for k in ("usdg", "seriesFactory", "noteQuoter", "desk", "surrogatePricer")}
        | {"feeds": dict(a.get("feeds", {}))},
        "model": {"dir": model_dir, "weightsHash": weights, "featureSpecVersion": fsv,
                  "certifiedDomain": export.get("certifiedDomain")},
        "desk": {"maxFeeBps": fee, "maxCoverFeeBps": cover_fee, "backstopShareBps": backstop,
                 "minSecsToObservation": min_secs, "minRequestShares": s(min_req), "queueBatch": batch},
        "demo": bool(c.cfg.get("demo", {}).get("enabled")),
        "commit": git_commit(),
    })


@router.get("/series")
def list_series():
    c = Ctx.now()
    return c.out({"series": series_objects(c, c.series_rows())})


@router.get("/series/{address}")
def get_series(address: str):
    c = Ctx.now()
    row = c.series_row(addr(address))
    return c.out(series_objects(c, [row], full=True)[0])


@router.get("/trades")
def get_trades(series: str | None = None, account: str | None = None,
               limit: int = Query(50, ge=1, le=500), before: int | None = Query(None, ge=0)):
    c = Ctx.now()
    return c.out({"trades": trades(c, addr(series, "series") if series else None,
                                   addr(account, "account") if account else None, limit, before)})


@router.get("/events")
def get_events(series: str | None = None, account: str | None = None, name: str | None = None,
               limit: int = Query(100, ge=1, le=1000), before: int | None = Query(None, ge=0)):
    """Non-trade events with their decoded args (mint, redeemPair, redeem, fixings, listings, queue, ...)."""
    c = Ctx.now()
    where, params = ["block <= ?"], [c.block]
    if series:
        where.append("series = ?")
        params.append(addr(series, "series"))
    if account:
        a = addr(account, "account")
        where.append("(account = ? OR sender = ?)")
        params += [a, a]
    if name:
        where.append("name = ?")
        params.append(name)
    if before is not None:
        where.append("block < ?")
        params.append(before)
    rows = c.db.query(f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY block DESC, log_index DESC "
                      "LIMIT ?", (*params, limit))
    return c.out({"events": [{"txHash": r["tx_hash"], "logIndex": r["log_index"], "block": r["block"],
                              "time": r["time"], "address": r["address"], "name": r["name"], "series": r["series"],
                              "feed": r["feed"], "args": json.loads(r["args"])} for r in rows]})
