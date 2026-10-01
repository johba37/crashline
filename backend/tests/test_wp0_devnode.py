"""WP0: the persistent dev node and its deployment.

Acceptance: `up.sh && deploy.py` from a clean state ends with a quotable listed
series; `cast call` from the host works; a browser `fetch` gets CORS headers;
`docker restart sp-devnode` keeps the chain.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from conftest import BACKEND, ROOT, RPC, node_up
from app import config as cfgmod
from app.chain import Chain
from devnode import deploy as dp

CAST = shutil.which("cast") or str(Path.home() / ".foundry/bin/cast")


def default_series(chain: Chain, cfg: dict) -> str:
    factory = chain.at("factory", cfg["addresses"]["seriesFactory"])
    feed = cfg["addresses"]["feeds"]["RHTSLA"]
    return next(s for s in factory.call("allSeries") if chain.at("series", s).call("terms")["feed"] == feed)


def check_deployment(chain: Chain, cfg: dict) -> None:
    a = cfg["addresses"]
    assert cfg["chainId"] == 412346
    assert chain.block(cfg["deploymentBlock"])["hash"] == cfg["deploymentBlockHash"]
    pricer = chain.at("pricer", a["surrogatePricer"])
    export = json.loads((ROOT / cfg["modelDir"] / "student_export.json").read_text())
    assert pricer.call("weightsHash") == export["weightsHash"]
    series = default_series(chain, cfg)
    desk = chain.at("desk", a["desk"])
    listing = desk.call("listing", series)
    assert listing["active"] and listing["pricer"] == a["surrogatePricer"]
    assert listing["volBpsAnnual"] == 5500 and listing["capNotional"] == 100_000 * 10**6
    assert desk.call("spread", series) == {"bidBps": 20, "askBps": 30, "volBandBps": 0}
    assert desk.call("riskBudgetBps", a["feeds"]["RHTSLA"]) == 2000
    state = chain.at("series", series).call("state")
    assert state["phase"] == 1 and state["observationsDone"] == 10  # 16 remaining
    assert state["knockedIn"]  # the e2e path knocks in at observation 3
    dp.poke(chain, dp.DEV_KEY)
    cost, price = desk.call("quoteBuy", series, 10**6, 0)  # quotable now
    assert 0 < price < 10_675 and cost == price * 100
    # the test accounts are funded
    usdg = chain.at("usdg", a["usdg"])
    for name, addr in cfg["testAccounts"].items():
        assert chain.balance(addr) > 0, name
        assert usdg.call("balanceOf", addr) > 0, name
    assert desk.call("totalAssets") >= dp.LP_SEED


def test_deployment_is_quotable(chain, cfg):
    check_deployment(chain, cfg)
    dep = json.loads((ROOT / "deployments/412346.json").read_text())
    assert dep["desk"] == cfg["addresses"]["desk"] and dep["feeds"] == cfg["addresses"]["feeds"]


def test_cast_call_from_host(cfg):
    a = cfg["addresses"]
    out = subprocess.run([CAST, "call", "--rpc-url", RPC, a["desk"], "MAX_FEE_BPS()(uint16)"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == "200"
    out = subprocess.run([CAST, "call", "--rpc-url", RPC, a["surrogatePricer"], "featureSpecVersion()(uint16)"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == "1"


def test_cors_for_browser_fetch():
    origin = "http://localhost:5173"
    pre = httpx.options(RPC, headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                      "Access-Control-Request-Headers": "content-type"})
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] in ("*", origin)
    assert "POST" in pre.headers["access-control-allow-methods"]
    r = httpx.post(RPC, headers={"Origin": origin}, json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId"})
    assert r.headers["access-control-allow-origin"] in ("*", origin)
    assert r.json()["result"] == hex(412346)
    # vhosts '*': a Host header other than localhost (as behind a tunnel) is accepted
    r = httpx.post(RPC, headers={"Host": "devnode.example"}, json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId"})
    assert r.status_code == 200 and r.json()["result"] == hex(412346)


def _wait_node(timeout: float = 90) -> None:
    t0 = time.monotonic()
    while not node_up():
        assert time.monotonic() - t0 < timeout, "node did not come back"
        time.sleep(1)


def test_docker_restart_keeps_chain(chain, cfg):
    dp.poke(chain, dp.DEV_KEY)
    head = chain.block("latest")
    subprocess.run(["docker", "restart", cfg["devnode"]["container"]], check=True, capture_output=True)
    _wait_node()
    c2 = Chain(RPC)
    assert c2.block(head["number"])["hash"] == head["hash"]
    assert c2.block_number() >= head["number"]
    # it still sequences, and the deployment is intact
    dp.poke(c2, dp.DEV_KEY)
    assert c2.block_number() > head["number"]
    check_deployment(c2, cfgmod.load())


def test_node_has_archive_state(chain, cfg):
    """eth_call at the deployment block works (the history backfill needs it)."""
    desk = chain.at("desk", cfg["addresses"]["desk"])
    assert desk.call("MAX_FEE_BPS", block=cfg["deploymentBlock"] + 5) == 200
    usdg = chain.at("usdg", cfg["addresses"]["usdg"])
    assert usdg.call("totalSupply", block=cfg["deploymentBlock"]) == 0


@pytest.mark.destructive
@pytest.mark.skipif(os.environ.get("SP_CLEAN") != "1", reason="set SP_CLEAN=1: recreates the chain from scratch")
def test_up_and_deploy_from_clean_state():
    subprocess.run([str(BACKEND / "devnode/up.sh"), "--recreate"], check=True, capture_output=True)
    subprocess.run([str(BACKEND / ".venv/bin/python"), str(BACKEND / "devnode/deploy.py")], check=True,
                   capture_output=True)
    check_deployment(Chain(RPC), cfgmod.load())
