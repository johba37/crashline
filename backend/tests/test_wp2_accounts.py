"""WP2: accounts, vault, feeds.

Acceptance: the e2e lifecycle (contracts/script/e2e-devnode.sh's steps,
replayed here on a freshly staged series) leaves /accounts/<buyer> with the
right NOTE amount and cost basis, /vault.queuedShares non-zero while the LP's
request is open and zero after processQueue, /feeds/<feed>.risk.room ==
limit - atRisk.
"""

from __future__ import annotations

import json
import time

from helpers import USDG, unique
from app.accounting import Book, position
from app.chain import address_of
from devnode import deploy as dp

WEEK = 604_800
LEAD = 120
LP_DEPOSIT = 100_000 * USDG
BUY_NOTE = 10_000 * USDG
SELL_NOTE = 4_000 * USDG
BUY_COVER = 12_000 * USDG
FEE_BPS, COVER_FEE_BPS = 20, 500
AC_FIX_BPS = 10_200
ZERO = "0x" + "00" * 20


def refresh_feeds(chain, cfg):
    """A fresh round (same price) on the deployment's feeds, so held series are quotable."""
    for f in cfg["addresses"]["feeds"].values():
        feed = chain.at("feed", f)
        feed.send(dp.DEV_KEY, "pushRound", feed.call("latestRoundData")[1])


def position_of(account, series):
    return next((p for p in account["positions"] if p["series"] == series), None)


def trade_of(service, tx):
    return service.wait_for(lambda: [t for t in service.get("/trades", limit=20)["trades"] if t["txHash"] == tx])[0]


def test_fifo_book():
    b = Book()
    b.open("note", 10, 1000)
    b.open("note", 10, 1300)
    b.close("note", 15, 1800)  # 10 @ 1000 + 5 of 10 @ 1300 = 1650
    assert b.realized == 150 and b.basis("note") == 650 and b.held("note") == 5
    b.close("note", 8, 400)  # 5 lots left (650), 3 more at zero cost
    assert b.realized == 150 + 400 - 650 and b.basis("note") == 0


def test_position_matches_desk_rules():
    st = {"phase": 1, "knockedIn": True, "observationsDone": 10, "payoutPerNote": 0}
    p = position(1000 * USDG, 0, st, 26, 1_067_500, 8838)
    assert p["value"] == 883_800_000 and p["atRisk"] == 883_800_000 - 1000 * 67_500
    assert not position(1, 1, st, 26, 1_067_500, None)["ok"]
    settled = dict(st, phase=2, payoutPerNote=1_027_500)
    assert position(10 * USDG, 0, settled, 26, 1_067_500, None) == {
        "ok": True, "value": 10_275_000, "atRisk": 0, "noteValue": 10_275_000, "writerValue": 0}


def test_e2e_lifecycle(service, chain, cfg, keys):
    a = cfg["addresses"]
    desk, usdg = chain.at("desk", a["desk"]), chain.at("usdg", a["usdg"])
    dev, lp, buyer, hedger = (address_of(keys[k]) for k in ("dev", "lp", "buyer", "hedger"))
    refresh_feeds(chain, cfg)

    # 3-4, 6: staged series, listed with the e2e parameters
    addrs = json.loads(json.dumps(a))
    st = dp.stage(chain, dp.DEV_KEY, addrs, unique("E2E"), dp.PATH_BPS, dp.SPOT_BPS, 10, LEAD,
                  listing={"capNotional": 50_000 * USDG})
    series, feed, t_next = st["series"], st["feed"], st["nextObservation"]
    note, writer = chain.at("token", st["note"]), chain.at("token", st["writer"])

    # 5. LP deposit
    usdg.send(keys["lp"], "mint", lp, LP_DEPOSIT)
    usdg.send(keys["lp"], "approve", a["desk"], LP_DEPOSIT)
    shares0 = desk.call("balanceOf", lp)
    desk.send(keys["lp"], "deposit", LP_DEPOSIT, lp)
    lp_shares = desk.call("balanceOf", lp) - shares0

    # 7. buy NOTE
    usdg.send(keys["buyer"], "mint", buyer, 20_000 * USDG)
    dp.poke(chain, dp.DEV_KEY)
    cost, _ = desk.call("quoteBuy", series, BUY_NOTE, FEE_BPS)
    usdg.send(keys["buyer"], "approve", a["desk"], cost + cost // 200)
    r = desk.send(keys["buyer"], "buy", series, BUY_NOTE, cost + cost // 200, FEE_BPS, dev, buyer)
    service.synced(chain)
    buy_trade = trade_of(service, r["transactionHash"])
    buy_cost = int(buy_trade["usdg"])
    acct = service.get(f"/accounts/{buyer}")
    p = position_of(acct, series)
    assert p["note"] == str(BUY_NOTE) and p["writer"] == "0"
    assert p["costBasis"] == {"note": str(buy_cost), "writer": "0"} and p["realized"] == "0"
    assert acct["usdg"] == str(usdg.call("balanceOf", buyer, block=acct["block"]))
    mid = service.get(f"/series/{series}")["mid"]["noteBps"]
    assert p["noteMark"] == str(BUY_NOTE * mid * 100 // USDG)

    # 7b. buy cover
    usdg.send(keys["hedger"], "mint", hedger, 5_000 * USDG)
    dp.poke(chain, dp.DEV_KEY)
    cost, _ = desk.call("quoteBuyCover", series, BUY_COVER, COVER_FEE_BPS)
    usdg.send(keys["hedger"], "approve", a["desk"], cost + cost // 50)
    r = desk.send(keys["hedger"], "buyCover", series, BUY_COVER, cost + cost // 50, COVER_FEE_BPS, dev, hedger)
    service.synced(chain)
    cover_cost = int(trade_of(service, r["transactionHash"])["usdg"])
    assert note.call("balanceOf", a["desk"]) == BUY_COVER - BUY_NOTE
    p = position_of(service.get(f"/accounts/{hedger}"), series)
    assert p["writer"] == str(BUY_COVER) and p["costBasis"] == {"note": "0", "writer": str(cover_cost)}

    # 8. sell NOTE mid-life: FIFO leaves 6/10 of the buy's cost
    dp.poke(chain, dp.DEV_KEY)
    proceeds, _ = desk.call("quoteSell", series, SELL_NOTE, FEE_BPS)
    note.send(keys["buyer"], "approve", a["desk"], SELL_NOTE)
    r = desk.send(keys["buyer"], "sell", series, SELL_NOTE, proceeds - proceeds // 200, FEE_BPS, dev, buyer)
    service.synced(chain)
    sell_proceeds = int(trade_of(service, r["transactionHash"])["usdg"])
    sold_basis = buy_cost * SELL_NOTE // BUY_NOTE
    p = position_of(service.get(f"/accounts/{buyer}"), series)
    assert p["note"] == str(BUY_NOTE - SELL_NOTE)
    assert p["costBasis"]["note"] == str(buy_cost - sold_basis)
    assert p["realized"] == str(sell_proceeds - sold_basis)

    # the feed: risk room = limit - atRisk, the staged rounds
    f = service.get(f"/feeds/{feed}")
    at_risk, limit = desk.call("risk", feed, block=f["block"])
    assert f["risk"] == {"atRisk": str(at_risk), "limit": str(limit), "room": str(limit - at_risk),
                         "budgetBps": 2000}
    assert int(f["risk"]["room"]) == int(f["risk"]["limit"]) - int(f["risk"]["atRisk"]) and at_risk > 0
    assert f["decimals"] == 8 and f["series"] == [series] and f["name"] == st["feedName"]
    assert len(f["rounds"]) == 12  # strike, 10 observations, the current spot
    assert f["rounds"][0] == {"roundId": str(2**64 + 1), "answer": str(dp.INITIAL), "updatedAt": st["strikeTime"]}
    assert f["latest"]["answer"] == str(dp.INITIAL * dp.SPOT_BPS // 10_000)

    # the vault: inventory marks add up to totalAssets
    v = service.get("/vault")
    assert v["navError"] is None
    inv = {i["series"]: i for i in v["inventory"]}
    assert inv[series]["noteHeld"] == str(BUY_COVER - BUY_NOTE + SELL_NOTE)
    assert int(v["totalAssets"]) == int(v["idle"]) + sum(int(i["mark"]) for i in v["inventory"])
    assert v["feeds"][feed] == {k: f["risk"][k] for k in ("atRisk", "limit", "room")}
    assert v["totalAssets"] == str(desk.call("totalAssets", block=v["block"]))

    # 9. the observation passes: the LP queues 20 % while the fixing is pending
    wait = t_next + 2 - time.time()
    if wait > 0:
        time.sleep(wait)
    dp.poke(chain, dp.DEV_KEY)
    assert desk.call("maxRedeem", lp) == 0
    queued = lp_shares // 5
    q_before = desk.call("queuedShares")
    desk.send(keys["lp"], "requestRedeem", queued)
    service.synced(chain)
    v = service.get("/vault")
    assert int(v["queuedShares"]) == q_before + queued > 0
    assert v["navError"]["error"] == "FixingPending" and v["totalAssets"] is None
    acct = service.get(f"/accounts/{lp}")
    assert [q["shares"] for q in acct["queue"]] == [str(queued)]
    assert acct["queue"][0]["position"] == 0 and acct["sharesValue"] is None

    # the fixing: autocall
    feed_c = chain.at("feed", feed)
    feed_c.send(dp.DEV_KEY, "pushRoundAt", dp.INITIAL * AC_FIX_BPS // 10_000, t_next)
    chain.at("recorder", st["recorder"]).send(dp.DEV_KEY, "recordFixing", t_next, 2**64 + 10 + 3)
    chain.at("series", series).send(dp.DEV_KEY, "advance")
    payout = 10**6 + 2500 * 11

    # 10. redeem, collect, the queue is paid
    s_c = chain.at("series", series)
    s_c.send(keys["buyer"], "redeem", BUY_NOTE - SELL_NOTE, 0, buyer)
    s_c.send(keys["hedger"], "redeem", 0, BUY_COVER, hedger)
    desk.send(dp.DEV_KEY, "collect", series)
    service.synced(chain)
    p = position_of(service.get(f"/accounts/{buyer}"), series)
    redeemed = (BUY_NOTE - SELL_NOTE) * payout // USDG
    assert p["note"] == "0" and p["costBasis"]["note"] == "0"
    assert p["realized"] == str(sell_proceeds + redeemed - buy_cost)
    p = position_of(service.get(f"/accounts/{hedger}"), series)
    assert p["writer"] == "0" and p["realized"] == str(BUY_COVER * (1_067_500 - payout) // USDG - cover_cost)

    desk.send(dp.DEV_KEY, "processQueue", 10)
    service.synced(chain)
    v = service.get("/vault")
    assert v["queuedShares"] == "0" and v["navError"] is None
    assert series not in {i["series"] for i in v["inventory"]}  # collected
    acct = service.get(f"/accounts/{lp}")
    assert acct["queue"] == [] and int(acct["claimable"]) > 0
    assert acct["claimable"] == str(desk.call("claimableAssets", lp, block=acct["block"]))
    desk.send(keys["lp"], "claim", lp)
    service.synced(chain)
    assert service.get(f"/accounts/{lp}")["claimable"] == "0"
