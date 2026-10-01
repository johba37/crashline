"""WP1: service skeleton, indexer, catalog.

Acceptance: after WP0's scenario, /series returns the staged series with
quotable.ok and a mid; after a `cast send desk.buy(...)` the trade appears in
/trades within 5 s; restarting the service does not duplicate trades.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from conftest import ROOT, RPC
from helpers import USDG, buy, default_series, unique
from app.chain import address_of
from app.service import git_commit
from devnode import deploy as dp

ZERO = "0x" + "00" * 20


def test_config(service, chain, cfg):
    c = service.synced(chain)
    a = cfg["addresses"]
    assert c["chainId"] == 412346 and c["rpcUrl"] == cfg["publicRpcUrl"] == f"http://localhost:{RPC.rsplit(':', 1)[1]}"
    assert c["deploymentBlock"] == cfg["deploymentBlock"]
    for k in ("usdg", "seriesFactory", "noteQuoter", "desk", "surrogatePricer"):
        assert c["addresses"][k] == a[k]
    assert c["addresses"]["feeds"]["RHTSLA"] == a["feeds"]["RHTSLA"]
    export = json.loads((ROOT / cfg["modelDir"] / "student_export.json").read_text())  # model/k3 by default
    assert c["model"] == {"dir": cfg["modelDir"], "weightsHash": export["weightsHash"], "featureSpecVersion": 1,
                          "certifiedDomain": export["certifiedDomain"]}
    assert c["desk"] == {"maxFeeBps": 200, "maxCoverFeeBps": 1000, "backstopShareBps": 5000,
                         "minSecsToObservation": 60, "minRequestShares": str(10 * 10**12), "queueBatch": 8}
    assert c["demo"] is True and c["commit"] == git_commit()
    assert isinstance(c["block"], int) and isinstance(c["time"], int)


def test_series_quotable_with_mid(service, chain, cfg):
    s0 = default_series(chain, cfg)
    service.synced(chain)
    d = service.get("/series")
    o = next(x for x in d["series"] if x["address"] == s0)
    assert "fixings" not in o and "trades" not in o
    assert o["feedName"] == "RHTSLA" and o["feed"] == cfg["addresses"]["feeds"]["RHTSLA"]
    assert o["terms"] == {"strikeTime": o["terms"]["strikeTime"], "observationInterval": 604800,
                          "observationCount": 26, "kiBarrierBps": 6000, "acBarrierBps": 10000,
                          "couponBpsPerPeriod": 25}
    assert o["maxPayoutPerNote"] == "1067500" and o["maxBps"] == 10675
    st = o["state"]
    assert st["phase"] == "Live" and st["observationsDone"] == 10 and st["knockedIn"] and not st["autocalled"]
    assert st["initialFixing"] == str(dp.INITIAL) and st["pendingObservation"]["pending"] is False
    assert o["listing"]["active"] and o["listing"]["volBpsAnnual"] == 5500
    assert o["listing"]["capNotional"] == str(100_000 * USDG)
    band = cfg.get("defaultListing", {}).get("volBandBps", 0)  # 200 for model/k3, 0 for model/k2
    assert o["spread"] == {"bidBps": 20, "askBps": 30, "volBandBps": band}
    assert o["quotable"] == {"ok": True, "reason": None, "args": {}, "until": None}
    # the mid is the quoter's at the listing's vol, at the block the response reflects
    q = chain.at("quoter", cfg["addresses"]["noteQuoter"])
    pricer = cfg["addresses"]["surrogatePricer"]
    note_bps, wh = q.call("notePriceBps", s0, pricer, 5500, block=d["block"])
    assert o["mid"] == {"noteBps": note_bps, "coverBps": 10675 - note_bps}
    # both legs' two prices at 1 unit: the Desk's quotes, and IDeskCover's formulas over the band
    desk = chain.at("desk", cfg["addresses"]["desk"])
    want = {"note": {"askBps": desk.call("quoteBuy", s0, USDG, 0, block=d["block"])[1],
                     "bidBps": desk.call("quoteSell", s0, USDG, 0, block=d["block"])[1]},
            "cover": {"askBps": desk.call("quoteBuyCover", s0, USDG, 0, block=d["block"])[1],
                      "bidBps": desk.call("quoteSellCover", s0, USDG, 0, block=d["block"])[1]}, "errors": {}}
    assert o["quotes"] == want
    ends = [q.call("notePriceBps", s0, pricer, 5500 + k * band, block=d["block"])[0] for k in (-1, 1)]
    assert want["note"]["askBps"] == max(ends) + 30 and want["note"]["bidBps"] == min(ends) - 20
    assert want["cover"]["askBps"] == 10675 - want["note"]["bidBps"]
    assert want["cover"]["bidBps"] == 10675 - want["note"]["askBps"]
    assert chain.block(d["block"])["time"] == d["time"]


def test_series_detail_fixings(service, chain, cfg):
    s0 = default_series(chain, cfg)
    o = service.get(f"/series/{s0}")
    assert [f["index"] for f in o["fixings"]] == list(range(11))  # strike + 10 observations
    strike = o["terms"]["strikeTime"]
    for f in o["fixings"]:
        assert f["obsTime"] == strike + f["index"] * 604800
        want = dp.INITIAL if f["index"] == 0 else dp.INITIAL * dp.PATH_BPS[f["index"] - 1] // 10_000
        assert f["price"] == str(want) and f["roundId"] == str(2**64 + f["index"] + 1)
    assert isinstance(o["trades"], list)


def test_errors(service):
    r = service.client.get("/series/0x1234")
    assert r.status_code == 400 and r.json() == {"error": "BadAddress", "args": {"address": "0x1234"}}
    r = service.client.get("/series/0x" + "ab" * 20)
    assert r.status_code == 404 and r.json()["error"] == "UnknownSeries"
    r = service.client.get("/trades", params={"limit": 0})
    assert r.status_code == 400 and r.json()["error"] == "BadRequest"


def test_cors(service):
    r = service.client.options("/series", headers={"Origin": "http://localhost:5173",
                                                  "Access-Control-Request-Method": "GET"})
    assert r.headers["access-control-allow-origin"] in ("*", "http://localhost:5173")
    r = service.client.get("/config", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "*"


def test_cast_buy_appears_within_5s(service, chain, cfg, keys):
    s0 = default_series(chain, cfg)
    service.synced(chain)
    b = buy(chain, cfg, keys["buyer"], s0, 1_000 * USDG, fee_bps=20, fee_to=address_of(keys["dev"]), rpc=RPC)
    t0 = time.monotonic()
    found = service.wait_for(lambda: [t for t in service.get("/trades", series=s0)["trades"]
                                      if t["txHash"] == b["tx"]], timeout=5, poll=0.1)
    assert time.monotonic() - t0 < 5
    t = found[0]
    buyer = address_of(keys["buyer"])
    cost, price = chain.at("desk", cfg["addresses"]["desk"]).call("quoteBuy", s0, 1_000 * USDG, 20,
                                                                   block=b["block"] - 1)
    assert t["kind"] == "buy" and t["account"] == buyer and t["to"] == buyer and t["series"] == s0
    assert t["amount"] == str(1_000 * USDG) and t["feeBps"] == 20 and t["feeReceiver"] == address_of(keys["dev"])
    assert t["block"] == b["block"] and isinstance(t["logIndex"], int)
    assert t["weightsHash"] == service.get("/config")["model"]["weightsHash"]
    # the event's price and cost are the Desk's quote in the trade's block
    rq = chain.at("desk", cfg["addresses"]["desk"]).call("quoteBuy", s0, 1_000 * USDG, 20, block=b["block"])
    assert (int(t["usdg"]), t["priceBps"]) in {(cost, price), rq}
    # by account, and in the series' last trades
    assert any(x["txHash"] == b["tx"] for x in service.get("/trades", account=buyer)["trades"])
    assert service.get(f"/series/{s0}")["trades"][0]["txHash"] == b["tx"]


def test_restart_does_not_duplicate(service, chain, cfg, keys):
    s0 = default_series(chain, cfg)
    buy(chain, cfg, keys["buyer"], s0, 100 * USDG)
    service.synced(chain)
    before = service.get("/trades", limit=500)["trades"]
    service.restart()
    service.synced(chain)
    after = service.get("/trades", limit=500)["trades"]
    assert after == before
    keys_ = [(t["txHash"], t["logIndex"]) for t in after]
    assert len(keys_) == len(set(keys_))
    buy(chain, cfg, keys["buyer"], s0, 100 * USDG)
    service.synced(chain)
    assert len(service.get("/trades", limit=500)["trades"]) == len(before) + 1


def _stage(chain, cfg, **kw):
    addrs = json.loads(json.dumps(cfg["addresses"]))
    args = dict(feed_name=unique("T"), path_bps=dp.PATH_BPS, spot_bps=8500, observations_done=10,
                lead_secs=2 * 86400, listing={})
    args.update(kw)
    return dp.stage(chain, dp.DEV_KEY, addrs, **args)


def _series(service, chain, addr):
    service.synced(chain)
    return service.get(f"/series/{addr}")


def test_quotable_reasons(service, chain, cfg):
    """quoteBuy's revert decoded into the reason names, with `until` where it is known."""
    not_listed = _stage(chain, cfg, listing=None)
    o = _series(service, chain, not_listed["series"])
    assert o["listing"] is None and o["mid"] is None
    assert o["quotable"] == {"ok": False, "reason": "NotListed", "args": {"series": not_listed["series"]},
                             "until": None}

    band = _stage(chain, cfg, lead_secs=45)  # inside the Desk's 60 s pre-observation band
    o = _series(service, chain, band["series"])
    assert o["quotable"]["reason"] == "TooCloseToObservation" and o["mid"] is None
    assert o["quotable"]["until"] == band["nextObservation"] == o["quotable"]["args"]["obsTime"]

    ac_day = _stage(chain, cfg, spot_bps=9800, lead_secs=3600)  # K2's autocall observation-day band
    o = _series(service, chain, ac_day["series"])
    assert o["quotable"]["reason"] == "Uncertified" and o["quotable"]["args"] == {"region": 0}
    assert o["quotable"]["until"] == ac_day["nextObservation"]

    low = _stage(chain, cfg, spot_bps=4000)  # below the certified spot range
    o = _series(service, chain, low["series"])
    assert o["quotable"]["reason"] == "OutOfRange"
    assert o["quotable"]["args"] == {"field": 0, "value": "4000"}  # int64: a string in JSON

    stale = _stage(chain, cfg, push_spot=False)  # latest round = the last fixing, 5 days old
    o = _series(service, chain, stale["series"])
    assert o["quotable"]["reason"] == "FeedStale" and o["quotable"]["until"] is None
    assert o["quotable"]["args"]["updatedAt"] == stale["nextObservation"] - 604800

    called = _stage(chain, cfg, path_bps=[9400, 10300], observations_done=2)  # autocalls at 2
    o = _series(service, chain, called["series"])
    assert o["state"]["phase"] == "Settled" and o["state"]["autocalled"]
    assert o["state"]["payoutPerNote"] == str(10**6 + 2 * 2500)
    assert o["quotable"]["reason"] == "Settled"

    pending = band  # its observation passes 45 s after staging, unrecorded
    wait = pending["nextObservation"] + 2 - chain.block("latest")["time"]
    if wait > 0:
        time.sleep(wait)
    dp.poke(chain, dp.DEV_KEY)
    o = _series(service, chain, pending["series"])
    assert o["state"]["pendingObservation"] == {"pending": True, "obsTime": pending["nextObservation"]}
    assert o["quotable"]["reason"] == "FixingPending" and o["quotable"]["until"] is None
    assert o["quotable"]["args"] == {"obsTime": pending["nextObservation"]}


def test_events_route(service, chain, cfg):
    service.synced(chain)
    ev = service.get("/events", name="SeriesListed")["events"]
    assert ev and all(e["name"] == "SeriesListed" for e in ev)
    e = ev[-1]
    assert e["args"]["capNotional"] == str(100_000 * USDG)  # uint128: a string
    assert e["args"]["volBpsAnnual"] == 5500
    s0 = default_series(chain, cfg)
    names = {e["name"] for e in service.get("/events", series=s0, limit=1000)["events"]}
    assert {"SeriesCreated", "Struck", "ObservationProcessed", "SeriesListed", "SpreadSet"} <= names
