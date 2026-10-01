"""WP5: demo control.

Acceptance: faucet -> balances change; stage with observationsDone: 20 and a
path that knocks in -> /series shows knockedIn: true and a lower mid; fixing
on a series whose next observation is past -> observationsDone increments;
reset -> /config has new addresses and /trades is empty.
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import time

import pytest

from conftest import Service
from helpers import default_series, unique
from app import config as cfgmod
from app.chain import address_of
from devnode import deploy as dp


@pytest.fixture
def token(cfg) -> dict:
    return {"X-Demo-Token": cfgmod.demo_token(cfg)}


def post(service, path, body, headers, status=200):
    r = service.client.post(path, json=body, headers=headers)
    assert r.status_code == status, f"{path}: {r.status_code} {r.text}"
    return r.json()


def test_guard(service, chain, cfg, token):
    body = {"address": "0x" + "11" * 20}
    assert service.client.post("/demo/faucet", json=body).json()["error"] == "BadDemoToken"
    r = service.client.post("/demo/faucet", json=body, headers={"X-Demo-Token": "wrong"})
    assert r.status_code == 401
    # a config with the demo off: 403 even with the right token
    tmp = tempfile.mkdtemp(prefix="sp-demo-off-")
    path = os.path.join(tmp, "config.json")
    cfgmod.save(dict(cfgmod.load(), demo={"enabled": False, "keyEnv": "DEMO_KEY", "tokenEnv": "DEMO_TOKEN"}),
                cfgmod.Path(path))
    os.environ["BACKEND_CONFIG"] = path
    svc = Service(8654, os.path.join(tmp, "d.sqlite"))
    try:
        svc.start()
        r = svc.client.post("/demo/faucet", json=body, headers=token)
        assert r.status_code == 403 and r.json()["error"] == "DemoDisabled"
        assert svc.get("/config")["demo"] is False
    finally:
        os.environ.pop("BACKEND_CONFIG", None)
        svc.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_faucet_changes_balances(service, chain, cfg, token):
    to = address_of("0x" + secrets.token_hex(32))
    usdg = chain.at("usdg", cfg["addresses"]["usdg"])
    r = post(service, "/demo/faucet", {"address": to}, token)
    assert r["balances"] == {"eth": str(10**18), "usdg": str(100_000 * 10**6)}
    assert chain.balance(to) == 10**18 and usdg.call("balanceOf", to) == 100_000 * 10**6
    r = post(service, "/demo/faucet", {"address": to, "eth": "0.5", "usdg": 250}, token)
    assert r["sent"] == {"eth": str(5 * 10**17), "usdg": str(250 * 10**6)}
    assert chain.balance(to) == 15 * 10**17 and usdg.call("balanceOf", to) == 100_250 * 10**6
    assert service.get(f"/accounts/{to}")["usdg"] == str(100_250 * 10**6)
    post(service, "/demo/faucet", {"address": "0x12"}, token, status=400)
    post(service, "/demo/faucet", {"address": to, "usdg": "0.0000001"}, token, status=400)


def test_feed_moves_the_spot(service, chain, cfg, token):
    s0 = default_series(chain, cfg)
    before = service.get(f"/series/{s0}")
    r = post(service, "/demo/feed", {"feed": "RHTSLA", "spotBps": 9100}, token)
    assert r["round"]["answer"] == str(dp.INITIAL * 9100 // 10_000) and r["initial"] == str(dp.INITIAL)
    o = next(x for x in r["series"] if x["address"] == s0)
    assert o["mid"]["noteBps"] > before["mid"]["noteBps"]  # higher spot, higher NOTE
    f = service.get(f"/feeds/{cfg['addresses']['feeds']['RHTSLA']}")
    assert f["latest"]["answer"] == r["round"]["answer"]
    h = service.get(f"/series/{s0}/history", **{"from": r["time"] - 1})
    assert h["points"][-1]["spotBps"] == 9100
    post(service, "/demo/feed", {"feed": cfg["addresses"]["feeds"]["RHTSLA"], "spotBps": dp.SPOT_BPS}, token)
    post(service, "/demo/feed", {"feed": "NOPE", "spotBps": 9000}, token, status=400)


def test_stage_knock_in_lowers_the_mid(service, chain, cfg, token):
    ki_path = dp.PATH_BPS[:20]  # 5500 at observation 3: knocked in
    assert min(ki_path) < 6000
    clean_path = [max(p, 7500) for p in ki_path]
    common = {"spotBps": 8000, "observationsDone": 20, "leadSecs": 2 * 86400, "list": {}}
    a = post(service, "/demo/stage", {"feedName": unique("CLEAN"), "pathBps": clean_path, **common}, token)
    b = post(service, "/demo/stage", {"feedName": unique("KI"), "pathBps": ki_path, **common}, token)
    assert a["state"]["observationsDone"] == b["state"]["observationsDone"] == 20
    assert a["state"]["knockedIn"] is False and b["state"]["knockedIn"] is True
    assert a["quotable"]["ok"] and b["quotable"]["ok"]
    assert b["mid"]["noteBps"] < a["mid"]["noteBps"]
    assert [f["index"] for f in b["fixings"]] == list(range(21))
    listed = {x["address"]: x for x in service.get("/series")["series"]}
    assert listed[b["address"]]["state"]["knockedIn"] is True
    assert listed[b["address"]]["mid"] == b["mid"]
    # the listing parameters and the new feed name reach /config
    assert b["listing"]["volBpsAnnual"] == 5500 and b["spread"] == {"bidBps": 20, "askBps": 30, "volBandBps": 0}
    assert service.get("/config")["addresses"]["feeds"][b["feedName"]] == b["feed"]
    # terms and an unlisted stage
    c = post(service, "/demo/stage", {"feedName": unique("U"), "pathBps": [], "spotBps": 9000,
                                      "observationsDone": 0, "leadSecs": 86400 * 2,
                                      "terms": {"coupon": 40, "count": 13}}, token)
    assert c["terms"]["couponBpsPerPeriod"] == 40 and c["terms"]["observationCount"] == 13
    assert c["listing"] is None and c["quotable"]["reason"] == "NotListed"
    post(service, "/demo/stage", {"feedName": unique("X"), "pathBps": [9000], "spotBps": 9000,
                                  "observationsDone": 3, "leadSecs": 100}, token, status=400)


def test_stage_interval_and_shared_schedule(service, chain, cfg, token):
    """terms.interval stages a series on an hourly grid (not listable: the Desk is weekly), all
    its barrier observations past, maturing at nextObservation; two stages with the same
    nextObservation share the strike time."""
    t_next = dp.poke(chain, dp.DEV_KEY)["time"] + 1800
    common = {"spotBps": 9000, "observationsDone": 26, "nextObservation": t_next,
              "terms": {"interval": 3600}}
    a = post(service, "/demo/stage", {"feedName": unique("HA"), "pathBps": [9000] * 26, **common}, token)
    b = post(service, "/demo/stage", {"feedName": unique("HB"), "pathBps": [9000] * 3 + [5500] + [8000] * 22,
                                      **common}, token)
    for o in (a, b):
        assert o["terms"]["observationInterval"] == 3600 and o["state"]["observationsDone"] == 26
        assert o["state"]["nextObservation"] == o["state"]["maturity"] == t_next
        assert o["terms"]["strikeTime"] == t_next - 27 * 3600 and o["listing"] is None
    assert a["state"]["knockedIn"] is False and b["state"]["knockedIn"] is True
    post(service, "/demo/stage", {"feedName": unique("HX"), "pathBps": [], "spotBps": 9000, "observationsDone": 0,
                                  "leadSecs": 3600, "terms": {"interval": 3600}}, token, status=400)
    post(service, "/demo/stage", {"feedName": unique("HY"), "pathBps": [], "spotBps": 9000, "observationsDone": 0,
                                  "leadSecs": 100, "terms": {"interval": 600}}, token, status=400)


def test_fixing_advances_the_series(service, chain, cfg, token):
    st = post(service, "/demo/stage", {"feedName": unique("FIX"), "pathBps": dp.PATH_BPS[:10], "spotBps": 8500,
                                       "observationsDone": 10, "leadSecs": 15, "list": {}}, token)
    r = service.client.post("/demo/fixing", json={"series": st["address"], "fixingBps": 9000}, headers=token)
    assert r.status_code == 409 and r.json()["error"] == "ObservationNotPassed"
    while dp.poke(chain, dp.DEV_KEY)["time"] <= st["state"]["nextObservation"] + 1:  # chain time (dev clock)
        time.sleep(0.5)
    r = post(service, "/demo/fixing", {"series": st["address"], "fixingBps": 9000}, token)
    s = r["series"]
    assert r["pushed"] is True and r["fixing"]["fixingBps"] == 9000
    assert s["state"]["observationsDone"] == 11 and s["state"]["phase"] == "Live"
    assert s["fixings"][-1] == {"obsTime": r["obsTime"], "price": str(dp.INITIAL * 9000 // 10_000),
                                "roundId": r["fixing"]["roundId"], "index": 11, "updatedAt": r["obsTime"]}
    assert s["state"]["nextObservation"] == r["obsTime"] + 604800
    assert service.get(f"/series/{st['address']}")["state"]["observationsDone"] == 11


@pytest.mark.destructive
def test_reset(service, chain, cfg, token, keys):
    from helpers import USDG, buy
    service.synced(chain, timeout=60)
    buy(chain, cfgmod.load(), keys["buyer"], default_series(chain, cfgmod.load()), 10 * USDG)
    old = service.synced(chain)
    assert service.get("/trades")["trades"]  # something to be wiped
    r = post(service, "/demo/reset", {}, token)
    assert r["addresses"]["desk"] != old["addresses"]["desk"]
    new = service.get("/config")
    for k in ("usdg", "seriesFactory", "noteQuoter", "desk", "surrogatePricer"):
        assert new["addresses"][k] != old["addresses"][k], k
        assert new["addresses"][k] == cfgmod.load()["addresses"][k]
    assert new["deploymentBlock"] == r["deploymentBlock"]
    assert service.get("/trades")["trades"] == []
    series = service.get("/series")["series"]
    assert len(series) == 1 and series[0]["feedName"] == "RHTSLA" and series[0]["quotable"]["ok"]
    assert service.get("/vault")["totalAssets"] == str(dp.LP_SEED)
