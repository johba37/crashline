#!/usr/bin/env python3
"""Keeps the dev node's mock feeds at real prices: every PRICE_PUSH_SECS (300) it reads
each feed's real price and pushes it with /demo/feed, as bps of the feed's initial fixing.

    backend/ops/push-prices.py             # until stopped (the sp-prices unit's ExecStart)
    backend/ops/push-prices.py --once      # one pass
    backend/ops/push-prices.py --dry-run   # one pass that says what it would push

Sources (SOURCES, by feed name): ETH from Coinbase's spot price, RHTSLA from the real
`RHTSLA / USD` feed on Robinhood Chain mainnet (DESIGN.md). Neither needs a key.

A round is pushed when the price moved by 1 bp of the initial fixing or more, or when the
feed's last round is KEEP_FRESH_SECS old: every push moves the chain's clock to now, and
the quoter refuses a feed older than 26 h (weekends too, when the real stock feed rests).
A real price outside the model's certified spot range would stop the series' quotes
(Uncertified), so then the feed keeps its own price and is only kept fresh. The default
RHTSLA series, struck at $250.00, is in that case.

Env: BACKEND_URL (http://127.0.0.1:$BACKEND_PORT, 8650), DEMO_TOKEN (sp-devnode-demo),
PRICE_PUSH_SECS.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from decimal import Decimal

API = os.environ.get("BACKEND_URL", f"http://127.0.0.1:{os.environ.get('BACKEND_PORT', 8650)}")
TOKEN = os.environ.get("DEMO_TOKEN", "sp-devnode-demo")
EVERY = int(os.environ.get("PRICE_PUSH_SECS", 300))
KEEP_FRESH_SECS = 6 * 3600


def http(url: str, body: dict | None = None, headers: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json",
                                                          "user-agent": "crashline-push-prices", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def coinbase(pair: str):
    """Coinbase's spot price, in feed units (8 decimals)."""
    return lambda: int(Decimal(http(f"https://api.coinbase.com/v2/prices/{pair}/spot")["data"]["amount"]) * 10**8)


def chainlink(rpc: str, feed: str):
    """The answer of a Chainlink feed's `latestRoundData()` (8 decimals, as the mock's)."""
    call = {"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": feed, "data": "0xfeaf968c"}, "latest"]}
    return lambda: int(http(rpc, call)["result"][2 + 64:2 + 128], 16)


SOURCES = {
    "ETH": coinbase("ETH-USD"),
    "RHTSLA": chainlink("https://rpc.mainnet.chain.robinhood.com", "0x4A1166a659A55625345e9515b32adECea5547C38"),
}


def usd(price: int) -> str:
    return f"${price / 1e8:,.2f}"


def run(dry: bool) -> None:
    series = http(f"{API}/series")["series"]
    spot = next(r for r in http(f"{API}/config")["model"]["certifiedDomain"]["ranges"] if r["name"] == "spotBpsOfInitial")
    for name, source in SOURCES.items():
        # "initial" as /demo/feed takes it: the strike fixing of the feed's first series
        s = next((s for s in series if s["feedName"] == name and s["state"]["initialFixing"] != "0"), None)
        if s is None:
            continue
        try:
            initial = int(s["state"]["initialFixing"])
            latest = http(f"{API}/feeds/{s['feed']}")["latest"]
            held = round(int(latest["answer"]) * 10_000 / initial)
            real = source()
            bps = round(real * 10_000 / initial)
            what = f"real {usd(real)} = {bps} bps"
            if not spot["min"] <= bps <= spot["max"]:
                what += f", outside the model's {spot['min']}..{spot['max']}: kept at {usd(int(latest['answer']))}"
                bps = held
            if bps == held and time.time() - latest["updatedAt"] < KEEP_FRESH_SECS:
                print(f"{name}: {what}, no round needed", flush=True)
            elif dry:
                print(f"{name}: {what}, would push {bps} bps", flush=True)
            else:
                r = http(f"{API}/demo/feed", {"feed": name, "spotBps": bps}, {"X-Demo-Token": TOKEN})
                print(f"{name}: {what}, pushed {usd(int(r['round']['answer']))} at block {r['block']}", flush=True)
        except Exception as e:  # one source or one push failing must not stop the others
            print(f"{name}: skipped ({e})", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    ap.add_argument("--dry-run", action="store_true", help="one pass that pushes nothing")
    a = ap.parse_args()
    while True:
        try:
            run(a.dry_run)
        except Exception as e:  # the service is down or restarting: the next pass tries again
            print(f"skipped ({e})", flush=True)
        if a.once or a.dry_run:
            return
        time.sleep(EVERY)


if __name__ == "__main__":
    main()
