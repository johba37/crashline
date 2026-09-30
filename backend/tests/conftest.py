"""Shared fixtures: the dev node from WP0 (every test is skipped when it is not up)
and, for the route tests, a service instance of our own.

The service under test runs as `backend/run.sh` on TEST_BACKEND_PORT (8651) with
its own SQLite file, so a service already running on 8650 is left alone.
Destructive tests (they recreate the chain) are marked `destructive` and run last.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import config as cfgmod  # noqa: E402
from app.chain import Chain  # noqa: E402
from devnode import deploy as dp  # noqa: E402

TEST_BACKEND_PORT = int(os.environ.get("TEST_BACKEND_PORT", 8651))
RPC = os.environ.get("TEST_RPC", f"http://127.0.0.1:{cfgmod.devnode_port()}")


def node_up() -> bool:
    try:
        return Chain(RPC, timeout=3).chain_id == cfgmod.DEVNODE_CHAIN_ID
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    if not node_up():
        skip = pytest.mark.skip(reason=f"dev node not up at {RPC} (backend/devnode/up.sh)")
        for item in items:
            item.add_marker(skip)
    # destructive tests (they replace the chain) after everything else
    items.sort(key=lambda i: 1 if i.get_closest_marker("destructive") else 0)


def pytest_configure(config):
    config.addinivalue_line("markers", "destructive: replaces the dev node's chain (runs last)")


@pytest.fixture(scope="session")
def chain() -> Chain:
    return Chain(RPC)


@pytest.fixture
def cfg() -> dict:
    """Re-read per test: /demo/reset rewrites it."""
    return cfgmod.load()


@pytest.fixture(scope="session")
def keys() -> dict:
    return dict(dp.TEST_KEYS, dev=dp.DEV_KEY)


class Service:
    """backend/run.sh on the test port with a private database."""

    def __init__(self, port: int, db: str):
        self.port = port
        self.db = db
        self.url = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None
        self.client = httpx.Client(base_url=self.url, timeout=120)
        self.log = Path(db).with_suffix(".log")

    def start(self, timeout: float = 60) -> None:
        env = dict(os.environ, BACKEND_PORT=str(self.port), BACKEND_DB=self.db)
        self.proc = subprocess.Popen([str(BACKEND / "run.sh")], env=env, stdout=open(self.log, "ab"),
                                     stderr=subprocess.STDOUT, start_new_session=True)
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"service exited: {self.log.read_text()[-3000:]}")
            try:
                if self.client.get("/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise RuntimeError(f"service did not come up: {self.log.read_text()[-3000:]}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def get(self, path: str, **params) -> dict:
        r = self.client.get(path, params=params or None)
        assert r.status_code == 200, f"GET {path}: {r.status_code} {r.text}"
        return r.json()

    def wait_for(self, fn, timeout: float = 10, poll: float = 0.25):
        """Poll `fn()` until it returns something truthy."""
        t0 = time.monotonic()
        while True:
            v = fn()
            if v:
                return v
            if time.monotonic() - t0 > timeout:
                raise AssertionError(f"condition not met within {timeout} s")
            time.sleep(poll)

    def synced(self, chain: Chain, timeout: float = 10) -> dict:
        """Wait until the service reflects the chain head."""
        head = chain.block_number()
        return self.wait_for(lambda: (lambda c: c if c["block"] >= head else None)(self.get("/config")), timeout)


@pytest.fixture(scope="session")
def service():
    tmp = tempfile.mkdtemp(prefix="sp-backend-test-")
    svc = Service(TEST_BACKEND_PORT, os.path.join(tmp, "test.sqlite"))
    svc.start()
    yield svc
    svc.stop()
