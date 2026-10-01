"""Chain actions the tests share (the e2e script's steps, in Python or with cast)."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from app.chain import Chain, address_of
from devnode import deploy as dp

CAST = shutil.which("cast") or str(Path.home() / ".foundry/bin/cast")
USDG = 10**6


def unique(prefix: str) -> str:
    return f"{prefix}{time.time_ns() % 10**10}"


def default_series(chain: Chain, cfg: dict) -> str:
    feed = cfg["addresses"]["feeds"]["RHTSLA"]
    factory = chain.at("factory", cfg["addresses"]["seriesFactory"])
    return next(s for s in factory.call("allSeries") if chain.at("series", s).call("terms")["feed"] == feed)


def cast_send(rpc: str, key: str, to: str, sig: str, *args) -> str:
    """`cast send`, as a user would; returns the tx hash (fails unless status 1)."""
    import json
    out = subprocess.run([CAST, "send", "--rpc-url", rpc, "--private-key", key, "--json", to, sig, *map(str, args)],
                         capture_output=True, text=True, check=True).stdout
    r = json.loads(out)
    assert r["status"] == "0x1", r
    return r["transactionHash"]


def buy(chain: Chain, cfg: dict, key: str, series: str, amount: int, fee_bps: int = 0, fee_to: str | None = None,
        rpc: str | None = None) -> dict:
    """approve + desk.buy at quoteBuy + 1 %; with `rpc`, through `cast send`."""
    a = cfg["addresses"]
    desk = chain.at("desk", a["desk"])
    me = address_of(key)
    dp.poke(chain, dp.DEV_KEY)
    cost, price = desk.call("quoteBuy", series, amount, fee_bps)
    max_cost = cost + cost // 100
    fee_to = fee_to or "0x" + "00" * 20
    if rpc:
        cast_send(rpc, key, a["usdg"], "approve(address,uint256)", a["desk"], max_cost)
        tx = cast_send(rpc, key, a["desk"], "buy(address,uint256,uint256,uint16,address,address)",
                       series, amount, max_cost, fee_bps, fee_to, me)
        r = chain.wait_receipt(tx)
    else:
        chain.at("usdg", a["usdg"]).send(key, "approve", a["desk"], max_cost)
        r = desk.send(key, "buy", series, amount, max_cost, fee_bps, fee_to, me)
    return {"tx": r["transactionHash"], "block": int(r["blockNumber"], 16), "quote": (cost, price)}


def sell(chain: Chain, cfg: dict, key: str, series: str, amount: int, fee_bps: int = 0) -> dict:
    a = cfg["addresses"]
    desk = chain.at("desk", a["desk"])
    me = address_of(key)
    note = chain.at("series", series).call("note")
    dp.poke(chain, dp.DEV_KEY)
    proceeds, price = desk.call("quoteSell", series, amount, fee_bps)
    chain.at("token", note).send(key, "approve", a["desk"], amount)
    r = desk.send(key, "sell", series, amount, proceeds - proceeds // 100, fee_bps, "0x" + "00" * 20, me)
    return {"tx": r["transactionHash"], "block": int(r["blockNumber"], 16), "quote": (proceeds, price)}
