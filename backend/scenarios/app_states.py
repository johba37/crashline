#!/usr/bin/env python3
"""Three notes with a scripted future on the dev node, to see every state a position reaches in
the app: buy cover and a NOTE on each in the app, then move the dev clock past their weekly checks.

    backend/scenarios/app_states.py stage   # three new stocks at $250.00, each with one note to buy
    backend/scenarios/app_states.py play    # after buying: the dev clock nine weeks on, a check at a time
    backend/scenarios/app_states.py fresh   # a feed round at today's price (quotes stop 26 h after the last)

  CRASH  falls below its crash line ($150.00) at the 8th weekly check and is still there at the 9th.
         The note runs on: cover is switched on, the NOTE is in a crash, both can be sold back.
  ENDED  falls below its crash line too and ends 46% under its start: cover for 1,000 USDG collects
         460.00, a NOTE of 1,000 collects 607.50.
  EARLY  is back above its starting price at the 3rd weekly check, so the note ends early: a NOTE of
         1,000 collects 1,012.50, cover for 1,000 gets 55.00 back.

The first check is six days after staging (time to buy) and the others follow a week apart, so
EARLY ends about three weeks after staging, CRASH crosses its line after about eight and ENDED ends
after about nine. A second `stage` adds CRASH2, ENDED2 and EARLY2.

`play` needs the dev clock (docs/backend.md) and moves it one weekly check at a time: a check
still unrecorded nine days after its time takes the previous fixing (NoteSeries' fallback), so one
jump over all nine would lose the script. After each move it records every check that has passed:
on these notes as scripted (a note past its script stays at its last scripted price), on every
other live note at the price its feed is at. With CLOCK it moves the clock itself, else it says
which `clock.sh set-absolute` to run on the node's host and goes on once the chain is there. It
ends with `fresh`. The clock only moves forward; /demo/reset starts over.

It needs only Python and the service (through the SSH tunnel is fine).
Env: BACKEND_URL (http://127.0.0.1:$BACKEND_PORT, 8650), DEMO_TOKEN (sp-devnode-demo), CLOCK (how to
run clock.sh from here, e.g. "sudo -iu <user> <checkout>/backend/devnode/clock.sh" on the host).
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("BACKEND_URL", f"http://127.0.0.1:{os.environ.get('BACKEND_PORT', 8650)}")
TOKEN = os.environ.get("DEMO_TOKEN", "sp-devnode-demo")
CLOCK = os.environ.get("CLOCK")
LEAD = 6 * 86_400  # the first weekly check after staging
MARGIN = 3600  # `play` moves the clock this long past a check
LISTING = {"volBandBps": 200}  # as the default RHTSLA listing; the rest are /demo/stage's defaults

# Fixings in bps of the starting price: `past` the checks done when staged, `spot` the price then,
# `next` the checks `play` records (ENDED's last one is its end date's). The spots stay clear of the
# model's observation-day bands (docs/backend.md), so the notes have a price until the first check.
NOTES = {
    "CRASH": {"past": [9700, 9300, 9000, 9200], "spot": 8800,
              "next": [8600, 8900, 8400, 8000, 7600, 7100, 6600, 5500, 5400]},
    "ENDED": {"past": [9600, 9200, 8900, 9300, 9500, 9100, 8800, 8500, 8700, 9000, 8600, 8300, 8600, 8900, 8700,
                       8400, 8500, 8200], "spot": 8300,
              "next": [8100, 7700, 7200, 6600, 5700, 5300, 5500, 5200, 5400]},
    "EARLY": {"past": [9600, 9100], "spot": 9300, "next": [9600, 9800, 10300]},
}


def http(path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, headers={"content-type": "application/json",
                                                                 "X-Demo-Token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"{path}: {e.code} {e.read().decode()}")


def when(t: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t))


def usd(s: dict, bps: int) -> str:
    """`bps` of the note's starting price (8 decimals), in dollars."""
    return f"${int(s['state']['initialFixing']) * bps / 1e12:,.2f}"


def scripted(s: dict) -> dict | None:
    """The script of a note staged here, by its feed's name (CRASH, CRASH2, ...)."""
    m = re.fullmatch(f"({'|'.join(NOTES)})\\d*", s["feedName"])
    return NOTES[m[1]] if m else None


def level(s: dict) -> int:
    """The feed's latest price in bps of this note's starting price."""
    latest = http(f"/feeds/{s['feed']}")["latest"]
    return round(int(latest["answer"]) * 10_000 / int(s["state"]["initialFixing"]))


def stage() -> None:
    taken = http("/config")["addresses"]["feeds"]
    tag = next(t for t in itertools.chain([""], map(str, itertools.count(2)))
               if not any(name + t in taken for name in NOTES))
    next_obs = None
    for name, note in NOTES.items():
        at = {"leadSecs": LEAD} if next_obs is None else {"nextObservation": next_obs}  # one schedule for all
        s = http("/demo/stage", {"feedName": name + tag, "pathBps": note["past"], "spotBps": note["spot"],
                                 "observationsDone": len(note["past"]), "list": LISTING, **at})
        next_obs = s["state"]["nextObservation"]
        q = s["quotes"]
        price = (f"cover costs {q['cover']['askBps'] / 100:.2f}%, a NOTE {q['note']['askBps'] / 100:.2f}% of the amount"
                 if q else f"no price ({s['quotable']['reason']})")
        print(f"{s['feedName']:8s} {usd(s, note['spot'])}, until {when(s['state']['maturity'])}: {price}", flush=True)
    print(f"The first weekly check is {when(next_obs)}. Buy cover and a NOTE on each in the app, then: play")


def chain_time() -> int:
    """The chain's clock. The node makes a block only for a transaction, and a faucet of nothing sends one."""
    return http("/demo/faucet", {"address": "0x" + "00" * 20, "eth": 0, "usdg": 0})["time"]


def move_clock(target: int) -> None:
    if CLOCK:
        subprocess.run([*shlex.split(CLOCK), "set-absolute", str(target)], check=True)
    else:
        print(f"On the dev node's host, run\n\n    backend/devnode/clock.sh set-absolute {target}\n\n"
              "Waiting for the clock ...", flush=True)
    while http("/health")["time"] < target:
        time.sleep(2)


def record(s: dict) -> None:
    """The next check of note `s`: at its script's price or, for a note without one, at its feed's."""
    n = scripted(s)
    i = s["state"]["observationsDone"] - len(n["past"]) if n else 0
    bps = n["next"][min(i, len(n["next"]) - 1)] if n else level(s)
    r = http("/demo/fixing", {"series": s["address"], "fixingBps": bps})
    st, what = r["series"]["state"], ""
    if st["phase"] == "Settled":
        pay = int(st["payoutPerNote"])  # USDG base units per NOTE: /1000 is USDG per 1,000 NOTE
        what = (f": {'ended early' if st['autocalled'] else 'ended'}, a NOTE of 1,000 collects {pay / 1000:,.2f} "
                f"and cover for 1,000 collects {(int(r['series']['maxPayoutPerNote']) - pay) / 1000:,.2f} USDG")
    elif st["knockedIn"] and not s["state"]["knockedIn"]:
        what = ": below its crash line, cover is switched on"
    print(f"{when(r['obsTime'])}  {s['feedName']:8s} {usd(s, bps)} ({bps / 100:.0f}% of its start){what}", flush=True)


def play() -> None:
    while True:
        now = chain_time()
        # read anew every round: the fallback may have processed a check that was left too long
        live = [s for s in http("/series")["series"] if s["state"]["phase"] == "Live"]
        due = [s for s in live if s["state"]["nextObservation"] < now]
        if due:
            record(min(due, key=lambda s: s["state"]["nextObservation"]))
            continue
        ahead = [s["state"]["nextObservation"] for s in live
                 if (n := scripted(s)) and s["state"]["observationsDone"] < len(n["past"]) + len(n["next"])]
        if not ahead:
            break
        move_clock(min(ahead) + MARGIN)
    fresh()
    print(f"Every check up to {when(now)} is recorded. See My positions in the app.")


def fresh() -> None:
    series = http("/series")["series"]
    for feed in dict.fromkeys(s["feed"] for s in series if s["state"]["phase"] == "Live"):
        # /demo/feed counts in bps of the starting price of the feed's first note
        s = next(s for s in series if s["feed"] == feed and s["state"]["initialFixing"] != "0")
        r = http("/demo/feed", {"feed": feed, "spotBps": level(s)})
        print(f"{r['name']:8s} {usd(s, r['spotBps'])}, a round at {when(r['round']['updatedAt'])}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=("stage", "play", "fresh"))
    {"stage": stage, "play": play, "fresh": fresh}[ap.parse_args().step]()


if __name__ == "__main__":
    main()
