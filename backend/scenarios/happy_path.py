#!/usr/bin/env python3
"""Happy path on a fresh dev node with the Stylus k3 pricer: two weekly series on
one product, both legs traded, matured, settled and compared.

    backend/.venv/bin/python backend/scenarios/happy_path.py              # clock mode if the image exists
    backend/.venv/bin/python backend/scenarios/happy_path.py --mode hybrid

Series A's path stays between the knock-in and the autocall barrier, so it
reaches maturity unknocked and the NOTE pays 1 + c(N+1). Series B knocks in
and fixes below its initial at maturity, so the NOTE pays fixing/initial +
c(N+1) and WRITER the rest. Same terms (ki 6000, ac 10000, 25 bps, 26 weekly
observations), same strike time and initial fixing, two feeds.

  a. a new chain (up.sh --recreate on DEVNODE_NAME, never sp-devnode), deploy.py
     with model/k3, a backend of our own (BACKEND_PORT, own config and db);
     the LP gets USDG from /demo/faucet and deposits it in the Desk
  b. /demo/stage: A and B, 10 observations done, listed with k3 at vol 4200
     +- 300 bps, spread 25/35 bps, a 25% risk budget
  c. the buyer buys NOTE on both, a hedger buys cover (WRITER) on both, the
     buyer sells part of the NOTE mid-life; every trade checked with
     /verify-quote against teacher v3 at the band end the Desk priced at
  d. maturity. clock mode: the dev clock (backend/devnode/clock.sh) moves past
     each observation and /demo/fixing records it, through maturity; the LP
     queues a redemption while a fixing is pending. hybrid mode (the stock
     image, no clock): (a)-(c) on the weekly series, then two hourly twins of
     A and B staged with every barrier observation past, maturing a few
     minutes later, held through pairs a hedger mints (OTC sale of the NOTE to
     the buyer at the weekly series' ask)
  e. settle; NOTE and WRITER holders redeem, the Desk collects, the LP leaves
  f. the A vs B table; USDG conserved to the base unit at every step,
     escrow >= claims for every series, the Desk's balance >= its reserve

The backend's HTTP API does what it offers (stage, fixing, faucet, series,
trades, accounts, vault, verify-quote); user actions it doesn't expose are
signed here (approve, buy, sell, buyCover, deposit, requestRedeem,
processQueue, claim, redeem, mint, transfer, collect). Exit 0 only if every
step and check passes. The log goes to backend/scenarios/logs/.

Env: DEVNODE_NAME (sp-happy), DEVNODE_PORT (8847), DEVNODE_VOLUME
(sp-happy-data), BACKEND_PORT (8850), TEACHER_DEVICE (cuda when a GPU is
there), CARGO_TARGET_DIR (Stylus build dir), TMPDIR.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.chain import Chain, Revert, address_of  # noqa: E402
from devnode import deploy as dp  # noqa: E402

CLOCK_IMAGE = "sp-nitro-node:v3.11.4-7d5ac27-clock"
DEVNODE_DIR = BACKEND / "devnode"
LOGS = BACKEND / "scenarios/logs"
UNIT = 10**6
WEEK = 604_800
DEMO_TOKEN = "sp-devnode-demo"
ZERO = "0x" + "00" * 20
# an integrator that takes the trade fees (anvil #4; it only receives USDG)
INTEGRATOR = address_of("0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a")

# the product and the listing
TERMS = {"ki": 6000, "ac": 10000, "coupon": 25, "count": 26}
LISTING = {"volBps": 4200, "volBandBps": 300, "bidBps": 25, "askBps": 35, "capNotional": 100_000,
           "riskBudgetBps": 2500}
DONE = 10  # observations done when trading starts: 16 to go
LEAD = 3 * 86_400  # the next observation three days after staging
# fixings in bps of the initial: observations 1..10 staged, 11..26 forward, then maturity
PATH = {
    "A": {"past": [9400, 9100, 8800, 9000, 8600, 8300, 8700, 9100, 8900, 8800], "spot": 8800,
          "fwd": [8600, 8200, 7900, 8400, 8800, 9300, 8900, 8500, 8000, 7600, 8100, 8700, 9200, 9000, 8800, 9100],
          "maturity": 9200},
    "B": {"past": [9400, 8800, 5600, 7000, 7600, 8000, 7700, 8200, 8400, 8300], "spot": 8000,
          "fwd": [7900, 7500, 7200, 7600, 8100, 7800, 7300, 6900, 7200, 7500, 7700, 7400, 7900, 8100, 7600, 7700],
          "maturity": 7800},
}
LP_DEPOSIT = 50_000 * UNIT
BUY_NOTE = 10_000 * UNIT
BUY_COVER = 12_000 * UNIT  # 10,000 from the Desk's inventory, 2,000 from new pairs
SELL_NOTE = 4_000 * UNIT
NOTE_FEE_BPS = 10  # integrator fee on NOTE trades, of the notional
COVER_FEE_BPS = 200  # on cover trades, of the premium
SELL_AFTER = 4  # clock mode: the partial sell after 4 more observations (12 to go)
QUEUE_AT = 2  # clock mode: the LP queues a redemption while observation DONE + 2's fixing is pending
TWIN_LEAD = 240  # hybrid: the twins mature this long after they are staged
TWIN_INTERVAL = 3600


class Fail(Exception):
    pass


# --- log ------------------------------------------------------------------------------------------

class Log:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "w")
        self.path = path

    def __call__(self, msg: str = "") -> None:
        print(msg, flush=True)
        self.f.write(msg + "\n")
        self.f.flush()

    def step(self, msg: str) -> None:
        self(f"\n== {msg}  [{time.strftime('%H:%M:%S')}]")


def usd(x: int) -> str:
    sign = "-" if x < 0 else ""
    x = abs(int(x))
    return f"{sign}{x // UNIT:,}.{x % UNIT:06d}"


def payout_per_note(path: list[int], maturity_bps: int, initial: int, t: dict = TERMS) -> int:
    """AutocallPayout, independently: USDG base units per NOTE (fixings in bps of the initial)."""
    knocked = False
    for i, f in enumerate(path, start=1):
        price = initial * f // 10_000
        if price * 10_000 >= t["ac"] * initial:
            return UNIT + t["coupon"] * i * 100
        if price * 10_000 < t["ki"] * initial:
            knocked = True
    coupons = t["coupon"] * (t["count"] + 1) * 100
    fix = initial * maturity_bps // 10_000
    return fix * UNIT // initial + coupons if knocked and fix < initial else UNIT + coupons


# --- the clock ------------------------------------------------------------------------------------

class ChainClock:
    """The dev clock: block time = wall clock + an offset clock.sh raises."""
    name = "clock"

    def __init__(self, run: "Run"):
        self.run = run

    def advance_to(self, t: int) -> int:
        now = self.run.poke()
        if now <= t:
            out = self.run.sh([str(DEVNODE_DIR / "clock.sh"), "advance", str(t - now + 1)])
            self.run.log("   " + out.strip().replace("\n", "\n   "))
        now = self.run.poke()
        if now <= t:
            raise Fail(f"clock did not pass {t} (chain at {now})")
        return now


class WallClock:
    """The stock node: block time is the wall clock, so wait (only for short gaps)."""
    name = "hybrid"

    def __init__(self, run: "Run", max_wait: int = 900):
        self.run = run
        self.max_wait = max_wait

    def advance_to(self, t: int) -> int:
        now = self.run.poke()
        if t - now > self.max_wait:
            raise Fail(f"hybrid mode can't wait {t - now} s for the chain's clock")
        if now <= t:
            self.run.log(f"   waiting {t - now + 1} s of wall clock")
        while now <= t:
            time.sleep(min(5, t - now + 1))
            now = self.run.poke()
        return now


# --- the run --------------------------------------------------------------------------------------

class Run:
    def __init__(self, a: argparse.Namespace, log: Log):
        self.a, self.log = a, log
        self.name = os.environ.get("DEVNODE_NAME", "sp-happy")
        self.port = int(os.environ.get("DEVNODE_PORT", 8847))
        self.volume = os.environ.get("DEVNODE_VOLUME", "sp-happy-data")
        self.backend_port = int(os.environ.get("BACKEND_PORT", 8850))
        if self.name == "sp-devnode" or self.volume == "sp-devnode-data" or self.port == 8647 or \
                self.backend_port in (8650, 8651):
            raise Fail("refusing to touch the shared dev node / service (sp-devnode, 8647, 8650)")
        self.rpc = f"http://127.0.0.1:{self.port}"
        self.tmp = Path(tempfile.mkdtemp(prefix="sp-happy-"))
        self.cfg_path = self.tmp / "config.json"
        self.env = dict(os.environ, DEVNODE_NAME=self.name, DEVNODE_PORT=str(self.port),
                        DEVNODE_VOLUME=self.volume, BACKEND_CONFIG=str(self.cfg_path),
                        BACKEND_DB=str(self.tmp / "412346.sqlite"), BACKEND_PORT=str(self.backend_port),
                        PYTHONDONTWRITEBYTECODE="1")
        if "TEACHER_DEVICE" not in os.environ and shutil.which("nvidia-smi"):
            self.env["TEACHER_DEVICE"] = "cuda"
        if a.mode == "clock":
            self.env.update(DEVNODE_CLOCK="1", DEVNODE_IMAGE=CLOCK_IMAGE)
        else:
            self.env.pop("DEVNODE_CLOCK", None)
            self.env.pop("DEVNODE_IMAGE", None)
        self.api = httpx.Client(base_url=f"http://127.0.0.1:{self.backend_port}", timeout=300,
                                headers={"X-Demo-Token": DEMO_TOKEN})
        self.proc: subprocess.Popen | None = None
        self.clock = ChainClock(self) if a.mode == "clock" else WallClock(self)
        self.keys = dict(dp.TEST_KEYS)
        self.who = {address_of(k): n for n, k in self.keys.items()}
        self.who[address_of(dp.DEV_KEY)] = "dev"
        self.who[INTEGRATOR] = "integrator"
        self.series: dict[str, dict] = {}  # label -> {address, note, writer, ...}
        self.rows: dict[str, list] = {"A": [], "B": []}  # trades per weekly series
        self.desk_flow: dict[str, int] = {}  # label -> the Desk's USDG in - out over that series' actions
        self.received: dict[tuple, int] = {}  # (label, role) -> USDG
        self.checks = 0

    # --- processes ---
    def sh(self, cmd: list[str], timeout: int = 900) -> str:
        p = subprocess.run(cmd, env=self.env, capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise Fail(f"{' '.join(cmd)} exited {p.returncode}:\n{(p.stdout + p.stderr)[-3000:]}")
        return p.stdout

    def start(self) -> None:
        log = self.log
        log.step(f"a. fresh chain on {self.name} ({self.rpc}), image "
                 f"{CLOCK_IMAGE if self.a.mode == 'clock' else 'stock'}")
        out = self.sh([str(DEVNODE_DIR / "up.sh"), "--recreate"])
        log("   " + out.strip().replace("\n", "\n   "))
        out = self.sh([sys.executable, str(DEVNODE_DIR / "deploy.py"), "--model-dir", "model/k3",
                       "--rpc", self.rpc, "--public-rpc", f"http://localhost:{self.port}"], timeout=1800)
        for line in out.strip().splitlines():
            if line.strip().startswith(("pricer", "desk", "usdg", "1.", "4-5", "done")):
                log("   " + line.strip())
        self.cfg = json.loads(self.cfg_path.read_text())
        self.chain = Chain(self.rpc)
        a = self.cfg["addresses"]
        self.desk = self.chain.at("desk", a["desk"])
        self.usdg = self.chain.at("usdg", a["usdg"])
        logf = open(self.tmp / "backend.log", "ab")
        self.proc = subprocess.Popen([str(BACKEND / "run.sh")], env=self.env, stdout=logf, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        t0 = time.monotonic()
        while True:
            try:
                if self.api.get("/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None or time.monotonic() - t0 > 90:
                raise Fail(f"backend did not start: {(self.tmp / 'backend.log').read_text()[-3000:]}")
            time.sleep(0.5)
        c = self.get("/config")
        log(f"   backend on :{self.backend_port}, model {c['model']['dir']} {c['model']['weightsHash'][:18]}…, "
            f"teacher device {self.env.get('TEACHER_DEVICE', 'cpu')}")
        if c["model"]["dir"] != "model/k3":
            raise Fail("not the k3 pricer")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
        if not self.a.keep:
            subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
            subprocess.run(["docker", "volume", "rm", self.volume], capture_output=True)
            shutil.rmtree(self.tmp, ignore_errors=True)

    # --- API ---
    def _ok(self, r: httpx.Response, path: str) -> dict:
        if r.status_code != 200:
            raise Fail(f"{path}: {r.status_code} {r.text[:1500]}")
        return r.json()

    def get(self, path: str, **params) -> dict:
        return self._ok(self.api.get(path, params=params or None), path)

    def post(self, path: str, body: dict) -> dict:
        for _ in range(60):
            r = self.api.post(path, json=body)
            if r.status_code != 429:  # a teacher run in progress
                return self._ok(r, path)
            time.sleep(1)
        raise Fail(f"{path}: still busy")

    def synced(self, block: int | None = None) -> dict:
        block = block or self.chain.block_number()
        t0 = time.monotonic()
        while True:
            h = self.api.get("/health")
            if h.status_code == 200 and h.json()["block"] >= block:
                return h.json()
            if time.monotonic() - t0 > 30:
                raise Fail(f"indexer behind block {block}")
            time.sleep(0.2)

    def poke(self) -> int:
        return dp.poke(self.chain, dp.DEV_KEY)["time"]

    # --- chain actions (what the backend doesn't sign) ---
    def send(self, who: str, contract, fn: str, *args) -> dict:
        try:
            return contract.send(self.keys[who], fn, *args)
        except Revert as e:
            raise Fail(f"{who} {fn}{args}: reverted {e.name} {e.args_json()}") from None

    def bal(self, addr: str) -> int:
        return self.usdg.call("balanceOf", addr)

    # --- invariants ---
    def tracked(self) -> list[str]:
        out = [address_of(k) for k in self.keys.values()] + [address_of(dp.DEV_KEY), INTEGRATOR,
                                                              self.desk.address]
        factory = self.chain.at("factory", self.cfg["addresses"]["seriesFactory"])
        return out + [s.lower() for s in factory.call("allSeries")]

    def snapshot(self) -> dict[str, int]:
        return {a: self.bal(a) for a in self.tracked()}

    def baseline(self) -> None:
        self.base = self.snapshot()
        self.supply0 = self.usdg.call("totalSupply")
        if sum(self.base.values()) != self.supply0:
            raise Fail(f"USDG outside the tracked accounts: {self.supply0 - sum(self.base.values())}")
        self.log(f"   baseline: USDG supply {usd(self.supply0)}, all of it in {len(self.base)} tracked accounts")

    def invariants(self, what: str) -> None:
        """USDG conserved to the base unit (no mint after the baseline, every balance tracked),
        every series' escrow covers its claims, the Desk's balance covers its reserve."""
        snap = self.snapshot()
        supply = self.usdg.call("totalSupply")
        if supply != self.supply0:
            raise Fail(f"[{what}] USDG supply moved {supply - self.supply0}")
        moved = sum(snap.get(a, 0) - self.base.get(a, 0) for a in set(snap) | set(self.base))
        if moved != 0:
            raise Fail(f"[{what}] USDG not conserved: deltas sum to {moved}")
        if sum(snap.values()) != supply:
            raise Fail(f"[{what}] untracked USDG: {supply - sum(snap.values())}")
        factory = self.chain.at("factory", self.cfg["addresses"]["seriesFactory"])
        worst = None
        for s in factory.call("allSeries"):
            sc = self.chain.at("series", s)
            st, maxp = sc.call("state"), sc.call("maxPayoutPerNote")
            n = self.chain.at("token", sc.call("note")).call("totalSupply")
            w = self.chain.at("token", sc.call("writer")).call("totalSupply")
            escrow = self.bal(s)
            if st["phase"] == 2:
                p = st["payoutPerNote"]
                claims = n * p // UNIT + w * (maxp - p) // UNIT
            else:
                if n != w:
                    raise Fail(f"[{what}] {s}: NOTE {n} != WRITER {w} before settlement")
                claims = -(-n * maxp // UNIT)  # what the pairs can claim, rounded up
            if escrow < claims:
                raise Fail(f"[{what}] {s}: escrow {escrow} < claims {claims}")
            slack = escrow - claims
            worst = slack if worst is None else min(worst, slack)
        reserved = self.desk.call("reservedAssets")
        if self.bal(self.desk.address) < reserved:
            raise Fail(f"[{what}] Desk balance below its reserve {reserved}")
        self.checks += 1
        self.log(f"   check: USDG conserved (sum of deltas 0, supply {usd(supply)}), escrow >= claims for "
                 f"{len(factory.call('allSeries'))} series (min slack {worst} base units), Desk >= reserve")

    # --- steps ---
    def lp_deposit(self) -> None:
        log = self.log
        lp = address_of(self.keys["lp"])
        r = self.post("/demo/faucet", {"address": lp, "eth": 0, "usdg": LP_DEPOSIT // UNIT})
        log(f"   /demo/faucet: LP {lp} +{usd(int(r['sent']['usdg']))} USDG -> {usd(int(r['balances']['usdg']))}")
        self.baseline()
        self.lp_usdg0 = self.bal(lp)
        self.dev_shares = self.desk.call("balanceOf", address_of(dp.DEV_KEY))
        self.dev_value0 = self.desk.call("convertToAssets", self.dev_shares)
        self.send("lp", self.usdg, "approve", self.desk.address, LP_DEPOSIT)
        self.send("lp", self.desk, "deposit", LP_DEPOSIT, lp)
        self.lp_shares = self.desk.call("balanceOf", lp)
        self.synced()
        v = self.get("/vault")
        log(f"   LP deposits {usd(LP_DEPOSIT)} USDG: {self.lp_shares / 1e12:,.6f} shares; vault totalAssets "
            f"{usd(int(v['totalAssets']))}, share price {v['sharePrice']} (house LP: the dev account's "
            f"{usd(self.dev_value0)})")
        self.invariants("LP deposit")

    def stage(self) -> None:
        log = self.log
        t_next = self.poke() + LEAD
        listing = dict(LISTING)
        for label in ("A", "B"):
            p = PATH[label]
            o = self.post("/demo/stage", {"feedName": f"HAPPY-{label}", "pathBps": p["past"], "spotBps": p["spot"],
                                          "observationsDone": DONE, "nextObservation": t_next,
                                          "terms": TERMS, "list": listing})
            self.series[label] = o
            st = o["state"]
            log(f"   series {label} {o['address']} on {o['feedName']} {o['feed']}: strike "
                f"{time.strftime('%Y-%m-%d', time.gmtime(o['terms']['strikeTime']))}, initial "
                f"{int(st['initialFixing']) / 1e8:.2f}, {st['observationsDone']} done, knockedIn {st['knockedIn']}, "
                f"spot {p['spot']} bps; listed vol {o['listing']['volBpsAnnual']} +- {o['spread']['volBandBps']}, "
                f"spread {o['spread']['bidBps']}/{o['spread']['askBps']}")
            if not o["quotable"]["ok"]:
                raise Fail(f"{label} not quotable: {o['quotable']}")
        a, b = self.series["A"], self.series["B"]
        if a["terms"]["strikeTime"] != b["terms"]["strikeTime"] or a["state"]["initialFixing"] != \
                b["state"]["initialFixing"]:
            raise Fail("A and B should share strike time and initial fixing")
        if a["state"]["knockedIn"] or not b["state"]["knockedIn"]:
            raise Fail("A must be unknocked, B knocked in")
        for label in ("A", "B"):
            self.show_quotes(label)
        self.invariants("staged")

    def show_quotes(self, label: str) -> dict:
        o = self.get(f"/series/{self.series[label]['address']}")
        q, m = o["quotes"], o["mid"]
        if q is None:
            raise Fail(f"{label}: no quotes ({o['quotable']})")
        self.log(f"   {label} /series: mid NOTE {m['noteBps']} / cover {m['coverBps']} bps; NOTE bid/ask "
                 f"{q['note']['bidBps']}/{q['note']['askBps']}, cover bid/ask {q['cover']['bidBps']}/"
                 f"{q['cover']['askBps']} (maxBps {o['maxBps']})")
        return o

    def trade(self, label: str, kind: str, who: str, amount: int) -> dict:
        """One Desk trade, quoted first, then checked by /verify-quote."""
        log = self.log
        s = self.series[label]
        o = self.show_quotes(label)
        me = address_of(self.keys[who])
        fee = COVER_FEE_BPS if "Cover" in kind else NOTE_FEE_BPS
        quote_fn = {"buy": "quoteBuy", "sell": "quoteSell", "buyCover": "quoteBuyCover",
                    "sellCover": "quoteSellCover"}[kind]
        if kind in ("buyCover",):
            fd = self.get(f"/feeds/{s['feed']}")
            log(f"   risk room on {s['feedName']}: {usd(int(fd['risk']['room']))} USDG (quotes don't check it)")
        amt, price = self.desk.call(quote_fn, s["address"], amount, fee)
        desk0, me0, int0 = self.bal(self.desk.address), self.bal(me), self.bal(INTEGRATOR)
        if kind in ("buy", "buyCover"):
            limit = amt + amt // 200
            self.send(who, self.usdg, "approve", self.desk.address, limit)
        else:
            limit = amt - amt // 200
            tok = s["note"] if kind == "sell" else s["writer"]
            self.send(who, self.chain.at("token", tok), "approve", self.desk.address, amount)
        r = self.send(who, self.desk, kind, s["address"], amount, limit, fee, INTEGRATOR, me)
        block = int(r["blockNumber"], 16)
        d_desk, d_me, d_int = (self.bal(self.desk.address) - desk0, self.bal(me) - me0, self.bal(INTEGRATOR) - int0)
        self.desk_flow[label] = self.desk_flow.get(label, 0) + d_desk
        self.synced(block)
        v = self.post("/verify-quote", {"series": s["address"], "txHash": r["transactionHash"]})
        oc, st, te, ck = v["onChain"], v["student"], v["teacher"], v["check"]
        bq = ", ".join(f"{q['noteBps']}@{q['volBps']}" for q in oc["bandQuotes"])
        log(f"   {kind} {usd(amount)} by {who}: {oc['priceBps']} bps, {usd(abs(d_me))} USDG "
            f"({'paid' if d_me < 0 else 'received'}, fee {fee} bps, integrator +{usd(d_int)}), Desk {usd(d_desk)}")
        log(f"     band quotes {bq} -> priced at vol {oc['quoteVolBps']}: NOTE quote {oc['midBps']} "
            f"{'+' if kind in ('buy', 'sellCover') else '-'} {oc['spreadBps']}; formula {oc['expectedPriceBps']} "
            f"{'==' if oc['priceMatches'] else '!='} {oc['priceBps']}")
        log(f"     student {st['quoteBps']} (clean {st['priceBps']} + accrued {v['accruedBps']}) vs teacher "
            f"{te['teacher']} ({te['config']}, {te['backend']}, {te['paths']} paths) {te['quoteBps']:.1f} "
            f"+- {te['stdErrBps']:.2f}: diff {ck['diffBps']:+.1f} bps, tolerance {ck['toleranceBps']:.1f} -> "
            f"{'OK' if ck['within'] else 'OUT'}; observationsRemaining {v['inputs']['observationsRemaining']}")
        if not oc["priceMatches"]:
            raise Fail(f"{label} {kind}: price {oc['priceBps']} != formula {oc['expectedPriceBps']}")
        if not ck["within"]:
            raise Fail(f"{label} {kind}: teacher check failed {ck}")
        if st["quoteBps"] != oc["midBps"]:
            raise Fail(f"{label} {kind}: off-chain student {st['quoteBps']} != on-chain {oc['midBps']}")
        if te["teacher"] != "v3":
            raise Fail("verify-quote did not use teacher v3 for k3")
        row = {"kind": kind, "who": who, "amount": amount, "priceBps": oc["priceBps"], "usdg": abs(d_me),
               "quotes": o["quotes"], "mid": o["mid"], "obsRem": v["inputs"]["observationsRemaining"],
               "vol": oc["quoteVolBps"], "student": st["quoteBps"], "teacher": te["quoteBps"],
               "se": te["stdErrBps"], "diff": ck["diffBps"], "tol": ck["toleranceBps"], "tx": r["transactionHash"]}
        self.rows[label].append(row)
        self.received[(label, who)] = self.received.get((label, who), 0) + d_me
        self.invariants(f"{label} {kind}")
        return row

    def trades_mid_life(self, sell_now: bool) -> None:
        for label in ("A", "B"):
            self.trade(label, "buy", "buyer", BUY_NOTE)
        for label in ("A", "B"):
            self.trade(label, "buyCover", "hedger", BUY_COVER)
        if sell_now:
            for label in ("A", "B"):
                self.trade(label, "sell", "buyer", SELL_NOTE)
        acc = self.get(f"/accounts/{address_of(self.keys['buyer'])}")
        for pos in acc["positions"]:
            lab = next((k for k, v in self.series.items() if v["address"] == pos["series"]), pos["series"])
            self.log(f"   /accounts buyer {lab}: NOTE {usd(int(pos['note']))}, cost basis "
                     f"{usd(int(pos['costBasis']['note']))}, mark {pos['noteMark'] and usd(int(pos['noteMark']))}, "
                     f"realized {usd(int(pos['realized']))}")

    def fixing(self, label: str, bps: int) -> dict:
        r = self.post("/demo/fixing", {"series": self.series[label]["address"], "fixingBps": bps})
        st = r["series"]["state"]
        self.log(f"   {label}: fixing {r['fixing']['fixingBps']} bps at {r['obsTime']} -> obs done "
                 f"{st['observationsDone']}, knockedIn {st['knockedIn']}, phase {st['phase']}"
                 + (f", payoutPerNote {usd(int(st['payoutPerNote']))}" if st["phase"] == "Settled" else ""))
        return r

    def lp_queue(self) -> None:
        q = self.lp_shares // 4
        self.send("lp", self.desk, "requestRedeem", q)
        self.queued = q
        self.synced()
        v = self.get("/vault")
        self.log(f"   LP queues {q / 1e12:,.6f} shares (25%): /vault queuedShares "
                 f"{int(v['queuedShares']) / 1e12:,.6f}, "
                 f"navError {v['navError'] and v['navError']['error']}")
        if int(v["queuedShares"]) != q:
            raise Fail("queuedShares")
        self.invariants("LP queue request")

    def lp_queue_paid(self) -> None:
        lp = address_of(self.keys["lp"])
        d0 = self.bal(self.desk.address)
        self.send("lp", self.desk, "processQueue", 10)
        claimable = self.desk.call("claimableAssets", lp)
        self.send("lp", self.desk, "claim", lp)
        self.desk_flow["vault"] = self.desk_flow.get("vault", 0) + self.bal(self.desk.address) - d0
        if self.desk.call("queuedShares") != 0:
            raise Fail("queue not emptied")
        self.lp_claimed = claimable
        self.log(f"   processQueue + claim: LP gets {usd(claimable)} USDG for its queued shares")
        self.invariants("LP queue paid")

    def lp_exit(self) -> None:
        lp = address_of(self.keys["lp"])
        shares = self.desk.call("balanceOf", lp)
        mx = self.desk.call("maxRedeem", lp)
        if mx != shares:
            raise Fail(f"LP can redeem {mx} of {shares} shares")
        d0 = self.bal(self.desk.address)
        self.send("lp", self.desk, "redeem", shares, lp, lp)
        self.desk_flow["vault"] = self.desk_flow.get("vault", 0) + self.bal(self.desk.address) - d0
        self.lp_pnl = self.bal(lp) - self.lp_usdg0
        self.dev_value1 = self.desk.call("convertToAssets", self.dev_shares)
        self.synced()
        v = self.get("/vault")
        self.log(f"   LP redeems its other {shares / 1e12:,.6f} shares: LP P&L {usd(self.lp_pnl)} USDG on "
                 f"{usd(LP_DEPOSIT)}; house LP {usd(self.dev_value0)} -> {usd(self.dev_value1)}; vault "
                 f"{usd(int(v['totalAssets']))}, share price {v['sharePrice']}")
        self.invariants("LP exit")

    def redeem_all(self, labels: list[str]) -> None:
        """NOTE and WRITER holders redeem, the Desk collects (anyone may call collect)."""
        for label in labels:
            s = self.series[label]
            sc = self.chain.at("series", s["address"])
            for who in ("buyer", "hedger"):
                me = address_of(self.keys[who])
                n = self.chain.at("token", s["note"]).call("balanceOf", me)
                w = self.chain.at("token", s["writer"]).call("balanceOf", me)
                if n == 0 and w == 0:
                    continue
                b0 = self.bal(me)
                self.send(who, sc, "redeem", n, w, me)
                got = self.bal(me) - b0
                self.received[(label, who)] = self.received.get((label, who), 0) + got
                self.received[(label, f"{who}-redeem")] = got
                self.log(f"   {label}: {who} redeems NOTE {usd(n)} + WRITER {usd(w)} for {usd(got)} USDG")
            if s.get("weekly", True):
                n = self.chain.at("token", s["note"]).call("balanceOf", self.desk.address)
                w = self.chain.at("token", s["writer"]).call("balanceOf", self.desk.address)
                d0 = self.bal(self.desk.address)
                self.send("lp", self.desk, "collect", s["address"])
                got = self.bal(self.desk.address) - d0
                self.desk_flow[label] = self.desk_flow.get(label, 0) + got
                self.received[(label, "desk-collect")] = got
                self.log(f"   {label}: Desk collects NOTE {usd(n)} + WRITER {usd(w)} for {usd(got)} USDG")
            dust = self.bal(s["address"])
            self.log(f"   {label}: escrow left {dust} base units")
            if dust > 3:
                raise Fail(f"{label}: escrow not emptied ({dust})")
            self.invariants(f"{label} redeemed")

    def check_settled(self, label: str, path: list[int], mat: int) -> int:
        o = self.get(f"/series/{self.series[label]['address']}")
        st = o["state"]
        want = payout_per_note(path, mat, int(st["initialFixing"]))
        self.log(f"   {label}: Settled {st['phase'] == 'Settled'}, autocalled {st['autocalled']}, knockedIn "
                 f"{st['knockedIn']}, payoutPerNote {usd(int(st['payoutPerNote']))} (independent payoff "
                 f"{usd(want)}), WRITER {usd(int(o['maxPayoutPerNote']) - int(st['payoutPerNote']))}")
        if st["phase"] != "Settled" or int(st["payoutPerNote"]) != want or st["autocalled"]:
            raise Fail(f"{label}: settlement {st} != expected payout {want}")
        self.series[label]["payout"] = want
        self.series[label]["maxPayout"] = int(o["maxPayoutPerNote"])
        return want

    # --- modes ---
    def run_clock(self) -> None:
        log = self.log
        log.step("c. mid-life trades at 16 observations to go (the sell comes later)")
        self.trades_mid_life(sell_now=False)
        log.step("d. the dev clock through every observation to maturity, /demo/fixing for each")
        a = self.series["A"]
        t_next = a["state"]["nextObservation"]
        for k in range(DONE + 1, TERMS["count"] + 2):  # 11..26, then maturity (27)
            obs_t = a["terms"]["strikeTime"] + k * WEEK
            if obs_t != t_next:
                raise Fail(f"schedule: obs {k} at {obs_t}, series says {t_next}")
            now = self.clock.advance_to(obs_t + 5)
            log(f"   observation {k if k <= TERMS['count'] else 'maturity'}: chain time {now} "
                f"({time.strftime('%Y-%m-%d %H:%M', time.gmtime(now))} UTC)")
            if k == DONE + QUEUE_AT:
                self.lp_queue()  # the fixing is pending: no NAV, but the queue takes requests
            for label in ("A", "B"):
                p = PATH[label]
                bps = p["fwd"][k - DONE - 1] if k <= TERMS["count"] else p["maturity"]
                self.fixing(label, bps)
            if k == DONE + QUEUE_AT:
                self.lp_queue_paid()
            if k == DONE + SELL_AFTER:
                log(f"   mid-life sell, {TERMS['count'] - k} observations to go")
                for label in ("A", "B"):
                    self.trade(label, "sell", "buyer", SELL_NOTE)
            t_next = self.get(f"/series/{a['address']}")["state"]["nextObservation"]
        self.invariants("matured")
        log.step("e. settlement: NOTE and WRITER holders redeem, the Desk collects, the LP leaves")
        for label in ("A", "B"):
            p = PATH[label]
            self.check_settled(label, p["past"] + p["fwd"], p["maturity"])
        self.redeem_all(["A", "B"])
        self.lp_exit()
        for label in ("A", "B"):
            s = self.series[label]
            for tok in ("note", "writer"):
                ts = self.chain.at("token", s[tok]).call("totalSupply")
                if ts != 0:
                    raise Fail(f"{label} {tok} supply left {ts}")
        if self.desk.call("heldSeries"):
            raise Fail(f"Desk still holds {self.desk.call('heldSeries')}")

    def run_hybrid(self) -> None:
        log = self.log
        log.step("c. mid-life trades at 16 observations to go, the partial sell right after")
        self.trades_mid_life(sell_now=True)
        asks = {label: self.get(f"/series/{self.series[label]['address']}")["quotes"]["note"]["askBps"]
                for label in ("A", "B")}
        log.step("d. HYBRID: hourly twins of A and B, every barrier observation past, maturing in minutes")
        t_mat = self.poke() + TWIN_LEAD
        held = BUY_NOTE - SELL_NOTE
        for label in ("A", "B"):
            p = PATH[label]
            o = self.post("/demo/stage", {"feedName": f"TWIN-{label}", "pathBps": p["past"] + p["fwd"],
                                          "spotBps": p["fwd"][-1], "observationsDone": TERMS["count"],
                                          "nextObservation": t_mat, "terms": dict(TERMS, interval=TWIN_INTERVAL)})
            tw = f"T{label}"
            self.series[tw] = dict(o, weekly=False)
            st = o["state"]
            log(f"   twin {tw} {o['address']}: interval {o['terms']['observationInterval']} s, "
                f"{st['observationsDone']} done, knockedIn {st['knockedIn']}, maturity {st['maturity']}")
            hedger, buyer = address_of(self.keys["hedger"]), address_of(self.keys["buyer"])
            sc = self.chain.at("series", o["address"])
            need = sc.call("previewMint", held)
            self.send("hedger", self.usdg, "approve", o["address"], need)
            self.send("hedger", sc, "mint", held, hedger)
            price = held * asks[label] // 10_000  # the weekly series' NOTE ask, OTC
            self.send("buyer", self.usdg, "transfer", hedger, price)
            self.send("hedger", self.chain.at("token", o["note"]), "transfer", buyer, held)
            self.received[(tw, "hedger")] = price - need
            self.received[(tw, "buyer")] = -price
            log(f"   {tw}: hedger mints {usd(held)} pairs for {usd(need)} USDG, sells the NOTE to the buyer at "
                f"{label}'s ask {asks[label]} bps ({usd(price)} USDG), keeps the WRITER")
            self.invariants(f"{tw} minted")
        self.lp_queue()
        self.lp_queue_paid()
        self.clock.advance_to(t_mat + 2)
        for label in ("A", "B"):
            tw = f"T{label}"
            r = self.post("/demo/fixing", {"series": self.series[tw]["address"], "fixingBps": PATH[label]["maturity"]})
            st = r["series"]["state"]
            log(f"   {tw}: maturity fixing {r['fixing']['fixingBps']} bps -> {st['phase']}, payoutPerNote "
                f"{usd(int(st['payoutPerNote']))}")
        self.invariants("twins matured")
        log.step("e. HYBRID settlement on the twins; the weekly A and B stay open at the Desk (marked)")
        for label in ("A", "B"):
            p = PATH[label]
            self.check_settled(f"T{label}", p["past"] + p["fwd"], p["maturity"])
        self.redeem_all(["TA", "TB"])
        self.lp_exit()

    # --- table ---
    def table(self) -> None:
        log = self.log
        hyb = self.a.mode == "hybrid"
        log.step("f. A vs B" + (" (HYBRID: trades on the weekly series, settlement on their hourly twins)"
                                if hyb else ""))
        log(f"{'':40s} {'A (no knock-in)':>22s} {'B (knocked in)':>22s}")

        def line(name, fa, fb):
            log(f"{name:40s} {fa:>22s} {fb:>22s}")

        def both(i, fmt):
            return fmt(self.rows["A"][i]), fmt(self.rows["B"][i])

        for i in range(len(self.rows["A"])):
            r = self.rows["A"][i]
            line(f"trade {i + 1}: {r['kind']} {usd(r['amount'])[:-7]}", *both(i, lambda x: f"{x['priceBps']} bps"))
            for leg in ("note", "cover"):
                line(f"  before: {leg.upper() if leg == 'note' else leg} bid/ask",
                     *both(i, lambda x: f"{x['quotes'][leg]['bidBps']}/{x['quotes'][leg]['askBps']}"))
            line("  observations to go / vol used", *both(i, lambda x: f"{x['obsRem']} / {x['vol']}"))
            line("  model vs teacher v3 (bps)", *both(i, lambda x: f"{x['student']} vs {x['teacher']:.1f}"))
            line("  diff / tolerance", *both(i, lambda x: f"{x['diff']:+.1f} / {x['tol']:.1f}"))
            line("  USDG", *both(i, lambda x: usd(x["usdg"])))
        sa, sb = ("TA", "TB") if hyb else ("A", "B")
        pa, pb = self.series[sa]["payout"], self.series[sb]["payout"]
        mx = self.series[sa]["maxPayout"]
        line("payoutPerNote (NOTE)", usd(pa), usd(pb))
        line("payoutPerWriter (WRITER)", usd(mx - pa), usd(mx - pb))
        line("buyer: NOTE redeemed for", usd(self.received.get((sa, "buyer-redeem"), 0)),
             usd(self.received.get((sb, "buyer-redeem"), 0)))
        line("hedger: WRITER redeemed for", usd(self.received.get((sa, "hedger-redeem"), 0)),
             usd(self.received.get((sb, "hedger-redeem"), 0)))
        if not hyb:
            line("Desk: collected", usd(self.received.get(("A", "desk-collect"), 0)),
                 usd(self.received.get(("B", "desk-collect"), 0)))
        tot = {lab: self.received.get((lab, "buyer"), 0) for lab in ("A", "B", "TA", "TB")}
        toth = {lab: self.received.get((lab, "hedger"), 0) for lab in ("A", "B", "TA", "TB")}
        if hyb:
            line("buyer P&L, weekly trades (open)", usd(tot["A"]), usd(tot["B"]))
            line("buyer P&L, twin (OTC + redeem)", usd(tot["TA"]), usd(tot["TB"]))
            line("hedger P&L, weekly (open)", usd(toth["A"]), usd(toth["B"]))
            line("hedger P&L, twin (mint, sale, redeem)", usd(toth["TA"]), usd(toth["TB"]))
            line("Desk cash flow, weekly (positions open)", usd(self.desk_flow.get("A", 0)),
                 usd(self.desk_flow.get("B", 0)))
        else:
            line("buyer P&L (all trades + redeem)", usd(tot["A"]), usd(tot["B"]))
            line("hedger P&L (premium + redeem)", usd(toth["A"]), usd(toth["B"]))
            line("Desk P&L (trades + collect)", usd(self.desk_flow.get("A", 0)), usd(self.desk_flow.get("B", 0)))
        log(f"LP: deposited {usd(LP_DEPOSIT)}, P&L {usd(self.lp_pnl)}; house LP {usd(self.dev_value0)} -> "
            f"{usd(self.dev_value1)} ({usd(self.dev_value1 - self.dev_value0)})"
            + (" (at the marked NAV: the Desk's weekly A and B positions are open)" if hyb else ""))
        integ = self.bal(INTEGRATOR) - self.base.get(INTEGRATOR, 0)
        log(f"integrator fees received: {usd(integ)}")
        if not hyb:
            desk_pnl = self.desk_flow.get("A", 0) + self.desk_flow.get("B", 0)
            vault_gain = self.lp_pnl + self.dev_value1 - self.dev_value0
            log(f"Desk P&L A + B {usd(desk_pnl)} = LPs' gain {usd(vault_gain)} (LP + house), "
                f"difference {desk_pnl - vault_gain} base units (share rounding)")
            if abs(desk_pnl - vault_gain) > 10:
                raise Fail("Desk P&L and the LPs' gain disagree")
            dust = sum(self.bal(self.series[k]["address"]) for k in ("A", "B"))
            zero = sum(tot[k] + toth[k] for k in ("A", "B")) + desk_pnl + integ + dust
            log(f"buyer + hedger + Desk + integrator + escrow dust ({dust}) over A and B: {zero} base units")
            if zero != 0:
                raise Fail("A and B flows don't add up to zero")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=("auto", "clock", "hybrid"), default="auto",
                    help="clock: the dev clock image; hybrid: the stock image; auto: clock if the image exists")
    ap.add_argument("--keep", action="store_true", help="leave the node, its volume and the run dir")
    ap.add_argument("--log", help="log file (default backend/scenarios/logs/happy_path-<mode>.log)")
    a = ap.parse_args()
    if a.mode == "auto":
        has = subprocess.run(["docker", "image", "inspect", CLOCK_IMAGE], capture_output=True).returncode == 0
        a.mode = "clock" if has else "hybrid"
    log = Log(Path(a.log) if a.log else LOGS / f"happy_path-{a.mode}.log")
    log(f"happy path, mode {a.mode.upper()}"
        + (" (no dev clock: settlement on hourly twins, see the docstring)" if a.mode == "hybrid" else "")
        + f", started {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    run = None
    t0 = time.monotonic()
    try:
        run = Run(a, log)
        run.start()
        run.lp_deposit()
        log.step("b. two weekly series, same product and strike: A stays above the knock-in, B knocked in")
        run.stage()
        run.run_clock() if a.mode == "clock" else run.run_hybrid()
        run.table()
        log(f"\nHAPPY PATH PASS ({a.mode}): {run.checks} invariant checks, "
            f"{sum(len(v) for v in run.rows.values())} trades verified against teacher v3, "
            f"{time.monotonic() - t0:.0f} s")
        return 0
    except Fail as e:
        log(f"\nHAPPY PATH FAIL ({a.mode}): {e}")
        return 1
    finally:
        if run is not None:
            run.stop()
        log(f"log: {log.path}")


if __name__ == "__main__":
    sys.exit(main())
