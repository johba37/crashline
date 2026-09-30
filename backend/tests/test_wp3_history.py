"""WP3: history sampling.

Acceptance: after WP0's scenario the series history has one point per past
observation at least, observations are marked, the last point equals
/series/{addr}.mid; a `cast send pushRound` adds a point within one poll.
Also: the off-chain replay agrees with the on-chain quoter, the vectorized
student is bit-exact, and a gap while the service was down is backfilled with
eth_call at past blocks.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import sqlite3
import tempfile
import time

import pytest

from conftest import ROOT, RPC, Service
from helpers import USDG, buy, cast_send, default_series, unique
from app import config as cfgmod
from app.replay import Terms, quote_points
from app.student import domain_error, forward, forward_batch
from devnode import deploy as dp

EXPORT = json.loads((ROOT / "model/k2/student_export.json").read_text())


def test_forward_batch_is_bit_exact():
    random.seed(7)
    rows = []
    while len(rows) < 400:
        spot = random.randint(5000, 12000)
        obs, tn, ki = random.randint(1, 26), random.randint(0, 604800), random.randint(0, 1)
        r = [spot, spot - 6000, 5500, 6000, 10000, 25, tn + obs * 604800, tn, obs, ki]
        if domain_error(EXPORT, r) is None:
            rows.append(r)
    rows += [[5000, -1000, 5500, 6000, 10000, 25, 604800, 0, 1, 1], [12000, 6000, 5500, 6000, 10000, 25,
                                                                     16329600, 604800, 26, 0]]
    assert [int(x) for x in forward_batch(EXPORT, rows)] == [forward(EXPORT, r) for r in rows]


def test_default_history(service, chain, cfg):
    s0 = default_series(chain, cfg)
    service.synced(chain)
    h = service.wait_for(lambda: (lambda d: d if len(d["points"]) > 100 else None)(
        service.get(f"/series/{s0}/history")), timeout=60)
    o = service.get(f"/series/{s0}")
    strike, done = o["terms"]["strikeTime"], o["state"]["observationsDone"]
    pts = h["points"]
    assert [p["time"] for p in pts] == sorted(p["time"] for p in pts)
    # a point at the strike and at every past observation, marked
    marked = {p["observation"]: p for p in pts if p["observation"] is not None}
    assert set(range(done + 1)) <= set(marked)
    for i in range(1, done + 1):
        p = marked[i]
        assert p["time"] == strike + i * 604800
        assert p["spotBps"] == dp.PATH_BPS[i - 1]  # the spot at an observation is its fixing
    assert [(x["index"], x["fixingBps"]) for x in h["observations"]] == \
        [(0, 10000)] + [(i, dp.PATH_BPS[i - 1]) for i in range(1, done + 1)]
    # the model's answer at the observations it certifies (the knock-in day at 5500 is excluded)
    assert marked[1]["quotable"] and marked[1]["noteBps"] > 0
    assert marked[3]["reason"] == "Uncertified" and marked[3]["noteBps"] is None
    # hourly grid from the strike, replayed before the series existed
    assert all(p["source"] == "replay" and p["block"] is None for p in pts if p["time"] < o["time"] - 3 * 86400)
    # the last point is the live mid
    last = pts[-1]
    assert last["block"] == h["block"] and last["time"] == h["time"]
    o2 = service.get(f"/series/{s0}")
    if o2["block"] == h["block"]:
        assert last["noteBps"] == o2["mid"]["noteBps"] and last["coverBps"] == o2["mid"]["coverBps"]
    q = chain.at("quoter", cfg["addresses"]["noteQuoter"])
    assert last["noteBps"] == q.call("notePriceBps", s0, cfg["addresses"]["surrogatePricer"], 5500,
                                     block=h["block"])[0]
    # a coarser step keeps the observations
    daily = service.get(f"/series/{s0}/history", step=86400)
    assert len(daily["points"]) < len(pts) / 10
    assert {p["observation"] for p in daily["points"] if p["observation"] is not None} >= set(range(done + 1))


def test_last_point_equals_series_mid(service, chain, cfg):
    s0 = default_series(chain, cfg)
    service.synced(chain)
    for _ in range(5):  # both reads at the same block
        o = service.get(f"/series/{s0}")
        h = service.get(f"/series/{s0}/history", **{"from": o["time"] - 86400})
        if o["block"] == h["block"]:
            break
    assert o["block"] == h["block"]
    assert h["points"][-1]["noteBps"] == o["mid"]["noteBps"]
    assert h["points"][-1]["coverBps"] == o["mid"]["coverBps"]


def test_push_round_adds_point_within_one_poll(service, chain, cfg):
    s0 = default_series(chain, cfg)
    feed = cfg["addresses"]["feeds"]["RHTSLA"]
    service.synced(chain)
    last = chain.at("feed", feed).call("latestRoundData")[3]
    while dp.poke(chain, dp.DEV_KEY)["time"] <= last:  # a round must be later than the last one
        time.sleep(0.25)
    tx = cast_send(RPC, dp.DEV_KEY, feed, "pushRound(int256)", dp.INITIAL * 8600 // 10_000)
    block = int(chain.wait_receipt(tx)["blockNumber"], 16)
    t0 = time.monotonic()
    service.wait_for(lambda: service.get("/health")["block"] >= block, timeout=10, poll=0.1)
    assert time.monotonic() - t0 <= cfg["pollSecs"] + 1  # one poll
    dp.poke(chain, dp.DEV_KEY)  # a later block, so what we find below is stored, not the live point
    service.synced(chain)
    h = service.get(f"/series/{s0}/history", **{"from": chain.block(block)["time"] - 1})
    stored = [p for p in h["points"] if p["block"] is not None and block <= p["block"] < h["block"]]
    assert stored, h["points"][-5:]
    p = stored[0]
    assert p["spotBps"] == 8600 and p["spot"] == str(dp.INITIAL * 8600 // 10_000) and p["quotable"]
    dp.push_round(chain, dp.DEV_KEY, feed, dp.INITIAL * dp.SPOT_BPS // 10_000)  # back to 8500


def _staged(chain, cfg, **kw):
    addrs = json.loads(json.dumps(cfg["addresses"]))
    args = dict(feed_name=unique("H"), path_bps=dp.PATH_BPS, spot_bps=8500, observations_done=10,
                lead_secs=2 * 86400, listing={})
    args.update(kw)
    return dp.stage(chain, dp.DEV_KEY, addrs, **args)


def test_replay_matches_the_quoter(service, chain, cfg):
    """The off-chain replay at a block's time gives the quoter's answer at that block."""
    variants = [_staged(chain, cfg), _staged(chain, cfg, spot_bps=6500, lead_secs=3000),
                _staged(chain, cfg, spot_bps=11000, observations_done=20, path_bps=dp.PATH_BPS),
                _staged(chain, cfg, spot_bps=4500), _staged(chain, cfg, push_spot=False)]
    service.synced(chain)
    dp.poke(chain, dp.DEV_KEY)
    b = chain.block("latest")
    q = chain.at("quoter", cfg["addresses"]["noteQuoter"])
    stale = q.call("MAX_FEED_STALENESS")
    for st in variants:
        o = service.get(f"/series/{st['series']}")
        rows = {f["index"]: int(f["price"]) for f in o["fixings"]}
        f = chain.at("feed", st["feed"])
        n = f.call("latestRound", block=b["number"]) - 2**64
        rounds = [(r[3], r[1]) for r in (f.call("getRoundData", 2**64 + k, block=b["number"])
                                         for k in range(1, n + 1))]
        t = o["terms"]
        terms = Terms(t["strikeTime"], t["observationInterval"], t["observationCount"], t["kiBarrierBps"],
                      t["acBarrierBps"], t["couponBpsPerPeriod"])
        (p,) = quote_points([b["time"]], terms, rows, rounds, EXPORT, 5500, stale)
        try:
            want = q.call("notePriceBps", st["series"], cfg["addresses"]["surrogatePricer"], 5500,
                          block=b["number"])[0]
            assert p["noteBps"] == want, st
        except Exception as e:
            assert p["noteBps"] is None and p["reason"] == getattr(e, "name", None), (st, p, e)


def test_vault_history(service, chain, cfg, keys):
    s0 = default_series(chain, cfg)
    b = buy(chain, cfg, keys["buyer"], s0, 50 * USDG)
    service.synced(chain)
    h = service.get("/vault/history", **{"from": 0})
    assert any(p["block"] == b["block"] for p in h["points"])  # sampled on the trade
    v = service.get("/vault")
    last = h["points"][-1]
    if v["block"] == h["block"]:
        assert (last["totalAssets"], last["totalSupply"], last["sharePrice"]) == \
            (v["totalAssets"], v["totalSupply"], v["sharePrice"])
    assert [p["block"] for p in h["points"]] == sorted(p["block"] for p in h["points"])


def test_backfill_by_eth_call_after_downtime(chain, cfg):
    """A service with a 10 s step is stopped while blocks are made; on restart the empty
    slots are filled with eth_call at the blocks that were made meanwhile."""
    tmp = tempfile.mkdtemp(prefix="sp-backfill-")
    cfg2 = dict(cfgmod.load(), historyStepSecs=10)
    cfg_path = os.path.join(tmp, "config.json")
    cfgmod.save(cfg2, cfgmod.Path(cfg_path))
    svc = Service(8652, os.path.join(tmp, "b.sqlite"))
    os.environ["BACKEND_CONFIG"] = cfg_path
    try:
        svc.start()
        svc.synced(chain)
        svc.stop()
        made = []
        for _ in range(4):  # blocks in four later 10 s slots while the service is down
            time.sleep(10)
            made.append(dp.poke(chain, dp.DEV_KEY))
        svc.start()
        svc.synced(chain)
        s0 = default_series(chain, cfg)
        db = sqlite3.connect(os.path.join(tmp, "b.sqlite"))

        want = {m["number"] for m in made[:-1]}  # the last one may be the head, sampled live

        def filled():
            rows = db.execute("SELECT block FROM samples WHERE series = ? AND trigger = 'backfill' AND "
                              "source = 'chain'", (s0,)).fetchall()
            nav = db.execute("SELECT block FROM nav_samples WHERE trigger = 'backfill'").fetchall()
            return ({r[0] for r in rows} >= want and {r[0] for r in nav} >= want) or None
        svc.wait_for(filled, timeout=120, poll=1)
        h = svc.get(f"/series/{s0}/history", step=1, **{"from": made[0]["time"]})
        assert {m["number"] for m in made} <= {p["block"] for p in h["points"]}
    finally:
        os.environ.pop("BACKEND_CONFIG", None)
        svc.stop()
        shutil.rmtree(tmp, ignore_errors=True)
