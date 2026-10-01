#!/usr/bin/env python3
"""Deploy everything to the dev node and stage the default scenario.

    backend/.venv/bin/python backend/devnode/deploy.py            # deploy + stage + list
    backend/.venv/bin/python backend/devnode/deploy.py --if-missing  # no-op if config.json's deployment is on chain

Mirrors contracts/script/e2e-devnode.sh steps 0-6 (without the trades):
  1. the Stylus pricer from model/k2 (`cargo stylus deploy`, build dir backend/.build/stylus-target)
  2. MockUSDG, SeriesFactory, NoteQuoter(93600), Desk(curator = dev key, band 60 s), from a fresh deployer key,
     built with forge in backend/.build/sol (a copy of contracts/src, so nothing under contracts/ is written)
  3. funds the three anvil test accounts (1 ETH, 100,000 USDG each) and seeds the vault
     with 100,000 USDG from the dev account
  4. stages the default series: feed history with the e2e PATH_BPS, spot 8500 bps of initial,
     16 observations remaining, the next observation --lead-secs from now; past fixings recorded
  5. lists it: vol 5500, cap 100,000 NOTE, spread 20/30/0, risk budget 2000 bps on its feed
  6. writes backend/config.json and deployments/412346.json

The functions are reused by the service's /demo/stage and /demo/reset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import config as cfgmod  # noqa: E402
from app.chain import Chain, Revert, address_of  # noqa: E402

ROOT = BACKEND.parent
BUILD = BACKEND / ".build"
HOME = Path.home()
FORGE = os.environ.get("FORGE") or shutil.which("forge") or str(HOME / ".foundry/bin/forge")
CARGO_BIN = HOME / ".cargo/bin"

# Nitro's dev key (prefunded on --dev) and three anvil test keys: the LP, the buyer, the hedger
# (contracts/script/e2e-devnode.sh lines 42-45).
DEV_KEY = cfgmod.NITRO_DEV_KEY
TEST_KEYS = {
    "lp": "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "buyer": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    "hedger": "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
}

WEEK = 604_800
ROUND_BASE = 2**64  # MockChainlinkFeed roundId = 2^64 + n
USDG = 10**6
ETH = 10**18

# the e2e scenario
FEED_NAME = "RHTSLA"
INITIAL = 25_000_000_000  # $250.00, 8 decimals
SPOT_BPS = 8500
TARGET_REM = 16
DEFAULT_LEAD = 3 * 86_400  # long-lived node: the next observation 3 days out
PATH_BPS = [9400, 8800, 5500, 7200, 8100, 8600, 9100, 8300, 8700, 9000, 8200, 7900, 8400, 8800, 9300,
            8900, 8500, 8000, 8600, 9200, 8700, 8300, 8800, 9100, 8900]
K2_TERMS = {"ki": 6000, "ac": 10000, "coupon": 25, "count": 26}
DEFAULT_LIST = {"volBps": 5500, "capNotional": 100_000 * USDG, "bidBps": 20, "askBps": 30,
                "volBandBps": 0, "riskBudgetBps": 2000}
MAX_FEED_STALENESS = 93_600  # NoteQuoter(26 h)
MIN_SECS_TO_OBSERVATION = 60
LP_SEED = 100_000 * USDG
FUND_ETH = 1 * ETH
FUND_USDG = 100_000 * USDG
DEPLOYER_ETH = 5 * ETH
DEFAULT_MODEL_DIR = "model/k2"


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _tree_digest(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(d)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _pinned_libs() -> dict[str, str]:
    """contracts/lib/<name> -> the commit the repo pins (git ls-tree)."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "HEAD", "contracts/lib/"],
                         capture_output=True, text=True, check=True).stdout
    pins = {}
    for line in out.splitlines():
        meta, path = line.split("\t")
        mode, kind, sha = meta.split()
        if kind == "commit":
            pins[Path(path).name] = sha
    return pins


def _gitmodule_url(name: str) -> str:
    out = subprocess.run(["git", "-C", str(ROOT), "config", "-f", ".gitmodules",
                          f"submodule.contracts/lib/{name}.url"], capture_output=True, text=True)
    return out.stdout.strip()


def ensure_libs() -> Path:
    """The Solidity libraries at their pinned commits under backend/.build/lib, cloned
    from a populated contracts/lib checkout if there is one, else from .gitmodules."""
    lib = BUILD / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    for name, sha in _pinned_libs().items():
        dst = lib / name
        if dst.exists():
            head = subprocess.run(["git", "-C", str(dst), "rev-parse", "HEAD"], capture_output=True, text=True)
            if head.stdout.strip() == sha:
                continue
            shutil.rmtree(dst)
        src = ROOT / "contracts/lib" / name
        origin = str(src) if (src / ".git").exists() else _gitmodule_url(name)
        log(f"  cloning {name} from {origin}")
        subprocess.run(["git", "clone", "-q", "--no-checkout", origin, str(dst)], check=True)
        subprocess.run(["git", "-C", str(dst), "-c", "advice.detachedHead=false", "checkout", "-q", sha], check=True)
    return lib


def build_contracts() -> Path:
    """forge build of a copy of contracts/src in backend/.build/sol; returns the out dir."""
    sol = BUILD / "sol"
    sol.mkdir(parents=True, exist_ok=True)
    libs = ensure_libs()
    src = ROOT / "contracts/src"
    stamp = sol / ".src-digest"
    digest = _tree_digest(src) + hashlib.sha256((ROOT / "contracts/foundry.toml").read_bytes()).hexdigest()
    out = sol / "out"
    if stamp.exists() and stamp.read_text() == digest and (out / "Desk.sol/Desk.json").exists():
        return out
    if (sol / "src").exists():
        shutil.rmtree(sol / "src")
    shutil.copytree(src, sol / "src")
    shutil.copy(ROOT / "contracts/foundry.toml", sol / "foundry.toml")
    if (sol / "lib").is_symlink() or (sol / "lib").exists():
        (sol / "lib").unlink()
    (sol / "lib").symlink_to(libs)
    log("  forge build (backend/.build/sol)")
    subprocess.run([FORGE, "build", "--skip", "test", "--skip", "script"], cwd=sol, check=True,
                   stdout=subprocess.DEVNULL)
    stamp.write_text(digest)
    return out


def bytecode(name: str) -> str:
    art = json.loads((BUILD / "sol/out" / f"{name}.sol" / f"{name}.json").read_text())
    return art["bytecode"]["object"]


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------

def wait_for_node(rpc_url: str, timeout: float = 120) -> Chain:
    chain = Chain(rpc_url)
    t0 = time.monotonic()
    while True:
        try:
            chain.chain_id
            return chain
        except Exception:
            if time.monotonic() - t0 > timeout:
                raise SystemExit(f"no node at {rpc_url}")
            time.sleep(1)


def deploy_pricer(rpc_url: str, key: str, model_dir: Path) -> str:
    """`cargo stylus deploy` of stylus/pricer-model with the model's weights compiled in."""
    env = dict(os.environ, PRICER_MODEL_DIR=str(model_dir), CARGO_TARGET_DIR=str(BUILD / "stylus-target"),
               PATH=f"{CARGO_BIN}:{os.environ.get('PATH', '')}")
    log(f"  cargo stylus deploy ({model_dir.relative_to(ROOT)})")
    p = subprocess.run(["cargo", "stylus", "deploy", "--endpoint", rpc_url, "--private-key", key, "--no-verify"],
                       cwd=ROOT / "stylus/pricer-model", env=env, capture_output=True, text=True)
    text = re.sub(r"\x1b\[[0-9;]*m", "", p.stdout + p.stderr)
    m = re.search(r"deployed code at address: (0x[0-9a-fA-F]{40})", text)
    if p.returncode != 0 or not m:
        raise RuntimeError("cargo stylus deploy failed:\n" + "\n".join(text.splitlines()[-30:]))
    return m.group(1).lower()


def deploy_core(chain: Chain, key: str, pricer: str, model_dir: Path, curator: str) -> dict:
    """The Solidity side, deployed from `key`, the Desk owned by `curator`; returns the
    config's `addresses` block (without feeds) and the first block."""
    build_contracts()
    want = json.loads((model_dir / "student_export.json").read_text())["weightsHash"]
    got = chain.at("pricer", pricer).call("weightsHash")
    if got != want:
        raise RuntimeError(f"pricer weightsHash {got} != export {want}")
    r = chain.send_tx(key, None, bytecode("MockUSDG"))
    usdg, first_block = r["contractAddress"].lower(), int(r["blockNumber"], 16)
    factory = chain.deploy(key, bytecode("SeriesFactory"), ["address"], [usdg])
    quoter = chain.deploy(key, bytecode("NoteQuoter"), ["uint40"], [MAX_FEED_STALENESS])
    desk = chain.deploy(key, bytecode("Desk"), ["address", "address", "address", "address", "uint32"],
                        [usdg, factory, quoter, curator, MIN_SECS_TO_OBSERVATION])
    return {"addresses": {"usdg": usdg, "seriesFactory": factory, "noteQuoter": quoter, "desk": desk,
                          "surrogatePricer": pricer, "feeds": {}},
            "deploymentBlock": first_block}


def fund(chain: Chain, key: str, usdg: str, to: str, eth: int = FUND_ETH, usdg_amount: int = FUND_USDG) -> None:
    if eth:
        chain.transfer_eth(key, to, eth)
    if usdg_amount:
        chain.at("usdg", usdg).send(key, "mint", to, usdg_amount)


def seed_vault(chain: Chain, key: str, addrs: dict, amount: int = LP_SEED) -> None:
    dev = address_of(key)
    u = chain.at("usdg", addrs["usdg"])
    u.send(key, "mint", dev, amount)
    u.send(key, "approve", addrs["desk"], amount)
    chain.at("desk", addrs["desk"]).send(key, "deposit", amount, dev)


def poke(chain: Chain, key: str) -> dict:
    """The dev node makes blocks only on txs, and views read the latest one: a 0-value
    self-transfer moves the chain's clock to now."""
    r = chain.transfer_eth(key, address_of(key), 0)
    return chain.block(int(r["blockNumber"], 16))


def push_round(chain: Chain, key: str, feed: str, answer: int) -> dict:
    """`pushRound(answer)` on a mock feed. Rounds must be strictly later than the last one
    and the dev node can put several blocks in one second, so first move the chain's
    clock past the latest round. Returns the receipt."""
    f = chain.at("feed", feed)
    try:
        last = f.call("latestRoundData")[3]
    except Revert:  # no rounds yet
        last = 0
    while poke(chain, key)["time"] <= last:
        time.sleep(0.25)
    return f.send(key, "pushRound", answer)


def stage(chain: Chain, key: str, addrs: dict, feed_name: str, path_bps: list[int], spot_bps: int,
          observations_done: int, lead_secs: int, terms: dict | None = None, listing: dict | None = None,
          initial: int = INITIAL, push_spot: bool = True) -> dict:
    """The e2e script's steps 3-4: a new feed with a staged history, a series whose strike is
    (observations_done + 1) weeks before the next observation, the past fixings recorded,
    `advance()`; with `listing`, the listing, its spread and the feed's risk budget.
    `push_spot=False` leaves the last fixing as the latest round (a stale feed).
    Returns {feed, series, note, writer, recorder, strikeTime, nextObservation}."""
    t = dict(K2_TERMS, **(terms or {}))
    done = int(observations_done)
    if not 0 <= done <= t["count"]:
        raise ValueError(f"observationsDone {done} outside 0..{t['count']}")
    if len(path_bps) < done:
        raise ValueError(f"pathBps has {len(path_bps)} fixings, observationsDone needs {done}")
    if not 0 < lead_secs < WEEK:
        raise ValueError("leadSecs must be in 1..604799 (the next observation lies within a week)")
    if spot_bps <= 0 or any(p <= 0 for p in path_bps[:done]):
        raise ValueError("fixings and spot must be > 0")
    if feed_name in addrs.get("feeds", {}):
        raise ValueError(f"feed name {feed_name} is taken")

    now = poke(chain, key)["time"]
    t_next = now + lead_secs
    strike = t_next - (done + 1) * WEEK
    feed = chain.deploy(key, bytecode("MockChainlinkFeed"), ["string"], [f"{feed_name} / USD (staged)"])
    f = chain.at("feed", feed)
    f.send(key, "pushRoundAt", initial, strike)
    for i in range(1, done + 1):
        f.send(key, "pushRoundAt", initial * path_bps[i - 1] // 10_000, strike + i * WEEK)
    if push_spot:
        f.send(key, "pushRound", initial * spot_bps // 10_000)

    factory = chain.at("factory", addrs["seriesFactory"])
    terms_t = {"feed": feed, "strikeTime": strike, "observationInterval": WEEK, "observationCount": t["count"],
               "kiBarrierBps": t["ki"], "acBarrierBps": t["ac"], "couponBpsPerPeriod": t["coupon"]}
    factory.send(key, "createSeries", terms_t)
    series = factory.call("seriesOf", factory.call("seriesId", terms_t))
    s = chain.at("series", series)
    recorder = factory.call("recorderOf", feed)
    rec = chain.at("recorder", recorder)
    for i in range(done + 1):
        rec.send(key, "recordFixing", strike + i * WEEK, ROUND_BASE + i + 1)
    s.send(key, "advance")
    addrs.setdefault("feeds", {})[feed_name] = feed
    if listing is not None:
        list_series(chain, key, addrs, series, feed, listing)
    return {"feed": feed, "feedName": feed_name, "series": series, "note": s.call("note"),
            "writer": s.call("writer"), "recorder": recorder, "strikeTime": strike, "nextObservation": t_next}


def list_series(chain: Chain, key: str, addrs: dict, series: str, feed: str, listing: dict) -> None:
    lst = dict(DEFAULT_LIST, **listing)
    desk = chain.at("desk", addrs["desk"])
    desk.send(key, "listSeries", series, addrs["surrogatePricer"], lst["volBps"], lst["capNotional"])
    desk.send(key, "setSpread", series, lst["bidBps"], lst["askBps"], lst["volBandBps"])
    desk.send(key, "setRiskBudget", feed, lst["riskBudgetBps"])


def default_scenario(chain: Chain, key: str, addrs: dict, lead_secs: int = DEFAULT_LEAD) -> dict:
    """16 observations remaining (or the pricer's nearest certified bound), as in the e2e script."""
    lo, hi = chain.at("pricer", addrs["surrogatePricer"]).call("certifiedRange", 8)
    rem = min(max(TARGET_REM, lo), hi)
    return stage(chain, key, addrs, FEED_NAME, PATH_BPS, SPOT_BPS, K2_TERMS["count"] - rem, lead_secs,
                 listing=dict(DEFAULT_LIST))


def check_quotable(chain: Chain, addrs: dict, series: str) -> tuple[int, int]:
    return chain.at("desk", addrs["desk"]).call("quoteBuy", series, USDG, 0)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def make_config(chain: Chain, core: dict, model_dir: Path, rpc_url: str, public_rpc_url: str,
                demo: bool = True) -> dict:
    blk = chain.block(core["deploymentBlock"])
    return {
        "network": "devnode" if chain.chain_id == cfgmod.DEVNODE_CHAIN_ID else f"chain-{chain.chain_id}",
        "chainId": chain.chain_id,
        "rpcUrl": rpc_url,
        "publicRpcUrl": public_rpc_url,
        "genesisHash": chain.block(0)["hash"],
        "deploymentBlock": core["deploymentBlock"],
        "deploymentBlockHash": blk["hash"],
        "deployedAt": blk["time"],
        "addresses": core["addresses"],
        "curator": address_of(DEV_KEY),
        "modelDir": str(model_dir.relative_to(ROOT)),
        "demo": {"enabled": demo, "keyEnv": "DEMO_KEY", "tokenEnv": "DEMO_TOKEN"},
        "devnode": {"container": "sp-devnode", "volume": "sp-devnode-data", "port": cfgmod.devnode_port()},
        "backendPort": cfgmod.backend_port(),
        "db": f"backend/data/{chain.chain_id}.sqlite",
        "pollSecs": 2,
        "historyStepSecs": 3600,
        "replayStepSecs": 3600,
        "testAccounts": {name: address_of(k) for name, k in TEST_KEYS.items()},
    }


def write_deployments(cfg: dict) -> Path:
    a = cfg["addresses"]
    out = {
        "chainId": cfg["chainId"], "usdg": a["usdg"], "seriesFactory": a["seriesFactory"],
        "noteQuoter": a["noteQuoter"], "desk": a["desk"], "surrogatePricer": a["surrogatePricer"],
        "curator": cfg["curator"], "feeds": a["feeds"], "deploymentBlock": cfg["deploymentBlock"],
        "pricerWeightsHash": json.loads((ROOT / cfg["modelDir"] / "student_export.json").read_text())["weightsHash"],
    }
    p = ROOT / "deployments" / f"{cfg['chainId']}.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(out, indent=2) + "\n")
    return p


def deployed(chain: Chain, cfg: dict) -> bool:
    """config.json describes a deployment that is on this chain."""
    try:
        if cfg.get("chainId") != chain.chain_id:
            return False
        if chain.block(cfg["deploymentBlock"])["hash"] != cfg["deploymentBlockHash"]:
            return False
        return chain.code(cfg["addresses"]["desk"]) not in (None, "0x")
    except Exception:
        return False


def deploy_all(rpc_url: str, public_rpc_url: str | None = None, model_dir: str = DEFAULT_MODEL_DIR,
               lead_secs: int = DEFAULT_LEAD, key: str = DEV_KEY, write: bool = True) -> dict:
    """Steps 1-6; returns the new config (written to backend/config.json when `write`).

    The contracts come from a fresh deployer key, funded by `key`: a new dev chain
    replays the same nonces, so deploying from `key` would give every reset the same
    addresses. `key` stays the curator (Desk owner), the feeds' owner and the house LP."""
    t0 = time.monotonic()
    chain = wait_for_node(rpc_url)
    mdir = (ROOT / model_dir).resolve()
    log(f"chain {chain.chain_id} at {rpc_url}, block {chain.block_number()}")
    deployer_key = "0x" + secrets.token_hex(32)
    chain.transfer_eth(key, address_of(deployer_key), DEPLOYER_ETH)
    log(f"1. Stylus pricer (deployer {address_of(deployer_key)})")
    pricer = deploy_pricer(rpc_url, deployer_key, mdir)
    log(f"   pricer {pricer}")
    log("2. Solidity contracts")
    core = deploy_core(chain, deployer_key, pricer, mdir, curator=address_of(key))
    for k, v in core["addresses"].items():
        if k != "feeds":
            log(f"   {k:16s} {v}")
    log("3. test accounts and vault seed")
    for name, k in TEST_KEYS.items():
        fund(chain, key, core["addresses"]["usdg"], address_of(k))
        log(f"   {name:7s} {address_of(k)}: {FUND_ETH / ETH:g} ETH, {FUND_USDG // USDG:,} USDG")
    seed_vault(chain, key, core["addresses"])
    log(f"   vault seeded with {LP_SEED // USDG:,} USDG by the dev account")
    log("4-5. default scenario, listed")
    st = default_scenario(chain, key, core["addresses"], lead_secs)
    cost, price = check_quotable(chain, core["addresses"], st["series"])
    log(f"   series {st['series']} on {st['feedName']} {st['feed']}; next observation in {lead_secs} s")
    log(f"   quoteBuy(1 NOTE) = {cost} USDG base units at {price} bps")
    cfg = make_config(chain, core, mdir, rpc_url, public_rpc_url or rpc_url.replace("127.0.0.1", "localhost"))
    cfg["deployer"] = address_of(deployer_key)
    if write:
        cfgmod.save(cfg)
        p = write_deployments(cfg)
        log(f"6. wrote {cfgmod.CONFIG_PATH.relative_to(ROOT)} and {p.relative_to(ROOT)}")
    log(f"done in {time.monotonic() - t0:.0f} s")
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    port = cfgmod.devnode_port()
    ap.add_argument("--rpc", default=f"http://127.0.0.1:{port}")
    ap.add_argument("--public-rpc", default=f"http://localhost:{port}", help="the RPC URL /config reports")
    ap.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    ap.add_argument("--lead-secs", type=int, default=int(os.environ.get("DEVNODE_LEAD_SECS", DEFAULT_LEAD)))
    ap.add_argument("--if-missing", action="store_true", help="do nothing if config.json's deployment is on chain")
    a = ap.parse_args()
    if a.if_missing and cfgmod.CONFIG_PATH.exists():
        chain = wait_for_node(a.rpc)
        if deployed(chain, cfgmod.load()):
            log(f"deployment in {cfgmod.CONFIG_PATH.relative_to(ROOT)} is on chain; nothing to do")
            return
    try:
        deploy_all(a.rpc, a.public_rpc, a.model_dir, a.lead_secs)
    except Revert as e:
        raise SystemExit(f"deploy failed: {e}")


if __name__ == "__main__":
    main()
