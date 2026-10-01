"""WP5: demo control, POST only, when config.demo.enabled and with the header
X-Demo-Token (env DEMO_TOKEN; on the dev node "sp-devnode-demo" if unset).
Transactions are signed by the demo key (env named by config.demo.keyEnv,
DEMO_KEY; on the dev node Nitro's dev key, the curator and the feeds' owner).

  /demo/faucet  {address, eth?, usdg?}       ETH and USDG (whole units; 1 and 100,000 by default)
  /demo/feed    {feed, spotBps}              pushRound(initial * spotBps / 1e4) on a mock feed
  /demo/fixing  {series, fixingBps}          round at the passed next observation, recordFixing, advance()
  /demo/stage   {feedName, pathBps, ...}     deploy.stage (+ listing): a new feed and series
                                             (terms.interval, nextObservation: see docs/backend.md)
  /demo/reset   {}                           new chain, deploy.deploy_all, config.json rewritten

Each route waits (up to 15 s) until the indexer has the transactions' block,
so what it returns, and what the other routes show next, includes them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Body, Header

from . import config as cfgmod
from .chain import Revert
from .history import HISTORY
from .service import SERVICE, ApiError, addr
from .views import Ctx, s, series_objects

if str(cfgmod.BACKEND) not in sys.path:  # deploy.py lives in backend/devnode
    sys.path.insert(0, str(cfgmod.BACKEND))
from devnode import deploy as dp  # noqa: E402

router = APIRouter(prefix="/demo")
_reset_lock = threading.Lock()


def guard(token: str | None) -> tuple[dict, str]:
    """(config, demo key) if the demo routes are open to this token."""
    snap = SERVICE.indexer.snap
    cfg = snap.cfg if snap else cfgmod.load()
    if not cfg.get("demo", {}).get("enabled"):
        raise ApiError(403, "DemoDisabled", {})
    want = cfgmod.demo_token(cfg)
    if not want or token != want:
        raise ApiError(401, "BadDemoToken", {"header": "X-Demo-Token"})
    key = cfgmod.demo_key(cfg)
    if not key:
        raise ApiError(503, "NoDemoKey", {"env": cfg.get("demo", {}).get("keyEnv", "DEMO_KEY")})
    return cfg, key


def wait_indexed(block: int, timeout: float = 15) -> Ctx:
    t0 = time.monotonic()
    while True:
        snap = SERVICE.indexer.snap
        if snap and snap.head and snap.head["number"] >= block and snap.status == "ok":
            return Ctx(snap)
        if time.monotonic() - t0 > timeout:
            raise ApiError(504, "IndexerBehind", {"block": block})
        time.sleep(0.1)


def units(v, decimals: int, what: str) -> int:
    """A whole-unit amount (number or decimal string) in base units."""
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise ApiError(400, "BadAmount", {what: v}) from None
    q = d * (10 ** decimals)
    if d < 0 or q != q.to_integral_value():
        raise ApiError(400, "BadAmount", {what: v})
    return int(q)


def _int(body: dict, k: str, lo: int | None = None, hi: int | None = None, default=None) -> int:
    v = body.get(k, default)
    if v is None or isinstance(v, bool):
        raise ApiError(400, "BadRequest", {"missing": k})
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ApiError(400, "BadRequest", {k: body.get(k)}) from None
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ApiError(400, "BadRequest", {k: v, "range": [lo, hi]})
    return v


def _block(receipt: dict) -> int:
    return int(receipt["blockNumber"], 16)


@router.post("/faucet")
def faucet(body: dict = Body(...), x_demo_token: str | None = Header(None)):
    cfg, key = guard(x_demo_token)
    to = addr(body.get("address", ""), "address")
    eth = units(body.get("eth", 1), 18, "eth")
    usdg = units(body.get("usdg", 100_000), 6, "usdg")
    chain = SERVICE.indexer.chain
    blocks = [dp.poke(chain, key)["number"]]
    if eth:
        blocks.append(_block(chain.transfer_eth(key, to, eth)))
    if usdg:
        blocks.append(_block(chain.at("usdg", cfg["addresses"]["usdg"]).send(key, "mint", to, usdg)))
    c = wait_indexed(max(blocks))
    return c.out({"address": to, "sent": {"eth": str(eth), "usdg": str(usdg)},
                  "balances": {"eth": str(c.chain.balance(to, c.block)),
                               "usdg": s(c.call(c.usdg, "balanceOf", to))}})


def _feed_address(cfg: dict, feed: str) -> str:
    feeds = cfg["addresses"].get("feeds", {})
    if feed in feeds:
        return feeds[feed].lower()
    return addr(feed, "feed")


@router.post("/feed")
def feed(body: dict = Body(...), x_demo_token: str | None = Header(None)):
    cfg, key = guard(x_demo_token)
    snap = SERVICE.snap()
    f = _feed_address(cfg, str(body.get("feed", "")))
    spot_bps = _int(body, "spotBps", 1, 100_000)
    c = Ctx(snap)
    rows = [r for r in c.series_rows() if r["feed"] == f]
    if not rows and f not in {a.lower() for a in cfg["addresses"].get("feeds", {}).values()}:
        raise ApiError(404, "UnknownFeed", {"address": f})
    # "initial": the strike fixing of the feed's first series, else the feed's first round
    initial = 0
    for r in rows:
        initial = c.call(c.chain.at("series", r["address"]), "state")["initialFixing"]
        if initial:
            break
    fc = c.chain.at("feed", f)
    if not initial:
        initial = c.call(fc, "getRoundData", 2**64 + 1)[1]
    answer = initial * spot_bps // 10_000
    try:
        r = dp.push_round(c.chain, key, f, answer)
    except Revert as e:
        raise ApiError(409, e.name, e.args_json()) from None
    c = wait_indexed(_block(r))
    rid, ans, _, upd, _ = c.call(fc, "latestRoundData")
    return c.out({"feed": f, "name": c.feed_name(f), "initial": s(initial), "spotBps": spot_bps,
                  "round": {"roundId": s(rid), "answer": s(ans), "updatedAt": upd},
                  "series": series_objects(c, rows)})


@router.post("/fixing")
def fixing(body: dict = Body(...), x_demo_token: str | None = Header(None)):
    cfg, key = guard(x_demo_token)
    c = Ctx(SERVICE.snap())
    r = c.series_row(addr(body.get("series", ""), "series"))
    fixing_bps = _int(body, "fixingBps", 1, 100_000)
    chain = c.chain
    now = dp.poke(chain, key)["time"]
    sc = chain.at("series", r["address"])
    st = sc.call("state")
    if st["phase"] == 2:
        raise ApiError(409, "Settled", {"series": r["address"]})
    t_next = st["nextObservation"]
    if t_next >= now:
        raise ApiError(409, "ObservationNotPassed", {"obsTime": t_next, "now": now, "until": t_next + 1})
    initial = st["initialFixing"] or chain.at("feed", r["feed"]).call("getRoundData", 2**64 + 1)[1]
    fc = chain.at("feed", r["feed"])
    rec = chain.at("recorder", r["recorder"])
    pushed = False
    try:
        if not rec.call("isRecorded", t_next):
            last = fc.call("latestRoundData")[3]
            if last < t_next:  # the e2e step: a round exactly at the observation
                fc.send(key, "pushRoundAt", initial * fixing_bps // 10_000, t_next)
                pushed = True
                rid = fc.call("latestRound")
            else:  # later rounds exist: the fixing is the round in force at the observation
                rid = _round_in_force(fc, t_next)
            rec.send(key, "recordFixing", t_next, rid)
        receipt = sc.send(key, "advance")
    except Revert as e:
        raise ApiError(409, e.name, e.args_json()) from None
    c = wait_indexed(_block(receipt))
    fx = rec.call("fixingOf", t_next, block=c.block)
    o = series_objects(c, [r], full=True)[0]
    return c.out({"obsTime": t_next, "pushed": pushed, "fixing": {"price": s(fx["price"]), "roundId": s(fx["roundId"]),
                                                                  "fixingBps": fx["price"] * 10_000 // initial},
                  "series": o})


def _round_in_force(fc, t: int) -> int:
    """The last round with updatedAt <= t (binary search over the feed's rounds)."""
    latest = fc.call("latestRound")
    phase, n = latest >> 64, latest & (2**64 - 1)
    lo, hi = 1, n
    if fc.call("getRoundData", (phase << 64) | 1)[3] > t:
        raise ApiError(409, "NoRoundBefore", {"obsTime": t})
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fc.call("getRoundData", (phase << 64) | mid)[3] <= t:
            lo = mid
        else:
            hi = mid - 1
    return (phase << 64) | lo


@router.post("/stage")
def stage(body: dict = Body(...), x_demo_token: str | None = Header(None)):
    cfg, key = guard(x_demo_token)
    c = Ctx(SERVICE.snap())
    name = body.get("feedName")
    if not isinstance(name, str) or not name or len(name) > 32:
        raise ApiError(400, "BadRequest", {"feedName": name})
    path = body.get("pathBps", [])
    if not isinstance(path, list) or not all(isinstance(x, int) and 0 < x <= 100_000 for x in path):
        raise ApiError(400, "BadRequest", {"pathBps": path})
    t = body.get("terms") or {}
    terms = {k: int(t[k]) for k in ("ki", "ac", "coupon", "count", "interval") if k in t}
    listing = None
    if body.get("list") is not None:
        lst = dict(body["list"])
        if "capNotional" in lst:
            lst["capNotional"] = units(lst["capNotional"], 6, "capNotional")
        listing = {k: (int(v) if k != "capNotional" else v) for k, v in lst.items()
                   if k in dp.DEFAULT_LIST}
    addrs = {**cfg["addresses"], "feeds": dict(cfg["addresses"].get("feeds", {}))}
    # the next observation: leadSecs from now, or at nextObservation (unix time; series staged
    # with the same one share a schedule)
    next_obs = _int(body, "nextObservation", 1) if body.get("nextObservation") is not None else None
    lead = None if next_obs is not None else _int(body, "leadSecs", 1, 604_799)
    try:
        st = dp.stage(c.chain, key, addrs, name, path, _int(body, "spotBps", 1, 100_000),
                      _int(body, "observationsDone", 0, 104), lead,
                      terms=terms, listing=listing, next_observation=next_obs)
    except ValueError as e:
        raise ApiError(400, "BadStage", {"message": str(e)}) from None
    except Revert as e:
        raise ApiError(409, e.name, e.args_json()) from None
    new = cfgmod.load(SERVICE.indexer.config_path)
    new["addresses"]["feeds"][name] = st["feed"]
    cfgmod.save(new, SERVICE.indexer.config_path)
    SERVICE.indexer.reload_config()  # /config names the feed from the next read on
    block = c.chain.block_number()
    c = wait_indexed(block)
    return c.out(series_objects(c, [c.series_row(st["series"])], full=True)[0])


@router.post("/reset")
def reset(body: dict = Body(default={}), x_demo_token: str | None = Header(None)):
    cfg, key = guard(x_demo_token)
    if cfg.get("chainId") != cfgmod.DEVNODE_CHAIN_ID:
        raise ApiError(403, "NotDevnode", {"chainId": cfg.get("chainId")})
    if not _reset_lock.acquire(blocking=False):
        raise ApiError(429, "Busy", {"message": "a reset is in progress"})
    t0 = time.monotonic()
    try:
        SERVICE.indexer.stop()
        env = {"DEVNODE_PORT": str(cfg.get("devnode", {}).get("port", cfgmod.devnode_port())),
               "DEVNODE_NAME": cfg.get("devnode", {}).get("container", "sp-devnode"),
               "DEVNODE_VOLUME": cfg.get("devnode", {}).get("volume", "sp-devnode-data")}
        p = subprocess.run([str(cfgmod.BACKEND / "devnode/up.sh"), "--recreate"], capture_output=True, text=True,
                           env=dict(os.environ, **env), timeout=300)
        if p.returncode != 0:
            raise ApiError(500, "NodeRestartFailed", {"output": (p.stdout + p.stderr)[-2000:]})
        dl = cfg.get("defaultListing", {})
        new = dp.deploy_all(cfg["rpcUrl"], cfg.get("publicRpcUrl"), cfg.get("modelDir", dp.DEFAULT_MODEL_DIR),
                            int(body.get("leadSecs", dp.DEFAULT_LEAD)), key=key, write=False,
                            vol_bps=dl.get("volBps"), vol_band_bps=dl.get("volBandBps"))
        for k in ("backendPort", "db", "pollSecs", "historyStepSecs", "replayStepSecs", "demo"):
            if k in cfg:
                new[k] = cfg[k]
        cfgmod.save(new, SERVICE.indexer.config_path)
        dp.write_deployments(new)
    finally:
        SERVICE.indexer.setup()
        SERVICE.indexer.start()
        _reset_lock.release()
    HISTORY.wake.set()
    SERVICE.indexer.wait_caught_up(60)
    c = wait_indexed(new["deploymentBlock"], timeout=60)
    return c.out({"addresses": new["addresses"], "deploymentBlock": new["deploymentBlock"],
                  "series": [r["address"] for r in c.series_rows()], "secs": round(time.monotonic() - t0, 1)})
