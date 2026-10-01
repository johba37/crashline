"""WP6: operations.

Acceptance: after a host reboot, start.sh (or the units) bring both up and
/config answers. A reboot can't run inside a test: `stop.sh` takes both down
the way a shutdown does (the units' ExecStop: graceful), `start.sh` runs the
units' start path (what the user manager runs at boot with lingering).
"""

from __future__ import annotations

import sqlite3
import subprocess
import time

import httpx
import pytest

from conftest import BACKEND, RPC, node_up
from app import config as cfgmod
from app.chain import Chain
from devnode import deploy as dp

OPS = BACKEND / "ops"
PORT = cfgmod.DEFAULT_BACKEND_PORT


def units_installed() -> bool:
    return subprocess.run(["systemctl", "--user", "cat", "sp-backend.service"], capture_output=True).returncode == 0


def running(name: str = "sp-devnode") -> bool:
    out = subprocess.run(["docker", "container", "inspect", "-f", "{{.State.Running}}", name], capture_output=True,
                         text=True).stdout.strip()
    return out == "true"


def config_8650() -> dict | None:
    try:
        r = httpx.get(f"http://127.0.0.1:{PORT}/config", timeout=5)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


@pytest.mark.skipif(not units_installed(), reason="systemd user units not installed (backend/ops/install.sh)")
def test_units_installed_and_enabled():
    for u in ("sp-devnode.service", "sp-backend.service"):
        out = subprocess.run(["systemctl", "--user", "is-enabled", u], capture_output=True, text=True).stdout.strip()
        assert out == "enabled", u
        text = subprocess.run(["systemctl", "--user", "cat", u], capture_output=True, text=True).stdout
        assert str(BACKEND) in text and "WantedBy=default.target" in text
    assert subprocess.run(["systemctl", "--user", "cat", "sp-reset.service"], capture_output=True).returncode == 0
    linger = subprocess.run(["loginctl", "show-user", subprocess.run(["id", "-un"], capture_output=True, text=True)
                             .stdout.strip(), "-p", "Linger", "--value"], capture_output=True, text=True).stdout
    assert linger.strip() == "yes"  # the user manager, and so the units, start at boot


def test_stop_then_start_brings_both_up():
    cfg0 = cfgmod.load()
    subprocess.run([str(OPS / "start.sh")], check=True, capture_output=True, timeout=900)
    head = Chain(RPC).block("latest")
    subprocess.run([str(OPS / "stop.sh")], check=True, capture_output=True, timeout=300)
    assert not running() and not node_up() and config_8650() is None
    t0 = time.monotonic()
    out = subprocess.run([str(OPS / "start.sh")], check=True, capture_output=True, text=True, timeout=900)
    assert "service up" in out.stdout
    c = config_8650()
    assert c and c["chainId"] == 412346 and c["addresses"]["desk"] == cfg0["addresses"]["desk"]
    chain = Chain(RPC)
    assert chain.block(head["number"])["hash"] == head["hash"]  # the chain survived
    dp.poke(chain, dp.DEV_KEY)  # and sequences
    print(f"start.sh took {time.monotonic() - t0:.0f} s")


def test_indexer_rolls_back_lost_blocks(service, chain, cfg, keys):
    """A block the chain no longer has (as after a hard kill): the rows indexed from it go,
    the real ones come back."""
    from helpers import USDG, buy, default_series
    s0 = default_series(chain, cfg)
    b = buy(chain, cfg, keys["buyer"], s0, 10 * USDG)
    service.synced(chain)
    before = service.get("/trades", limit=500)["trades"]
    rollbacks = service.get("/health")["rollbacks"]
    db = sqlite3.connect(service.db, timeout=30)
    head = db.execute("SELECT MAX(number) FROM blocks").fetchone()[0]
    db.execute("UPDATE blocks SET hash = ? WHERE number = ?", ("0x" + "de" * 32, head))
    db.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("0x" + "ab" * 32, 0, head, 0, s0, "buy", "0x" + "11" * 20, "0x" + "11" * 20, "1", 1, "1", 0,
                "0x" + "00" * 20, "0x" + "00" * 32))
    db.commit()
    dp.poke(chain, dp.DEV_KEY)
    service.wait_for(lambda: service.get("/health")["rollbacks"] > rollbacks, timeout=10)
    service.synced(chain)
    after = service.get("/trades", limit=500)["trades"]
    assert "0x" + "ab" * 32 not in {t["txHash"] for t in after}
    assert after == before and any(t["txHash"] == b["tx"] for t in after)
    rows = db.execute("SELECT number, hash FROM blocks ORDER BY number DESC LIMIT 20").fetchall()
    assert all(chain.block(n)["hash"] == h for n, h in rows)


@pytest.mark.destructive
def test_start_recovers_from_a_hard_kill():
    """SIGKILL can leave the dev sequencer broken; start.sh (node.sh) recreates and redeploys if so."""
    subprocess.run([str(OPS / "start.sh")], check=True, capture_output=True, timeout=900)
    subprocess.run(["docker", "kill", "-s", "KILL", "sp-devnode"], check=True, capture_output=True)
    assert not running()
    subprocess.run([str(OPS / "start.sh")], check=True, capture_output=True, timeout=900)
    chain = Chain(RPC)
    dp.poke(chain, dp.DEV_KEY)  # sequences again
    cfg = cfgmod.load()
    assert dp.deployed(chain, cfg)  # config.json's deployment is on the chain (kept or redeployed)
    t0 = time.monotonic()
    while True:  # the service follows (a new deployment means a rescan)
        c = config_8650()
        if c and c["addresses"]["desk"] == cfg["addresses"]["desk"]:
            break
        assert time.monotonic() - t0 < 120
        time.sleep(1)
    series = httpx.get(f"http://127.0.0.1:{PORT}/series", timeout=30).json()["series"]
    assert any(s["feedName"] == "RHTSLA" and s["quotable"]["ok"] for s in series)
    # the service on 8650 holds only blocks the chain still has (it rolled back if blocks were lost)
    db = sqlite3.connect(cfgmod.db_path(cfg), timeout=30)
    rows = db.execute("SELECT number, hash FROM blocks ORDER BY number DESC LIMIT 20").fetchall()
    assert rows and all(chain.block(n)["hash"] == h for n, h in rows)
