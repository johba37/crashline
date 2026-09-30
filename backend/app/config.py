"""The service's single config file, `backend/config.json`.

Written by `backend/devnode/deploy.py` (and rewritten by /demo/stage and
/demo/reset); for the testnet, the same file with the addresses from
`deployments/46630.json` and `demo.enabled: false`. Env overrides:
BACKEND_CONFIG (path), BACKEND_PORT, DEVNODE_PORT (rewrites the RPC ports).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
CONFIG_PATH = Path(os.environ.get("BACKEND_CONFIG", BACKEND / "config.json"))

DEVNODE_CHAIN_ID = 412346
DEFAULT_DEVNODE_PORT = 8647
DEFAULT_BACKEND_PORT = 8650
# Nitro's well-known dev key: prefunded on --dev, the curator and the demo signer on the dev node
NITRO_DEV_KEY = "0xb6b15c8cb491557369f3c7d2c287b053eb229daa9c22138887752191c9520659"
DEFAULT_DEMO_TOKEN = "sp-devnode-demo"


def devnode_port() -> int:
    return int(os.environ.get("DEVNODE_PORT", DEFAULT_DEVNODE_PORT))


def backend_port() -> int:
    return int(os.environ.get("BACKEND_PORT", DEFAULT_BACKEND_PORT))


def load(path: Path | None = None) -> dict:
    p = Path(path or CONFIG_PATH)
    if not p.exists():
        raise FileNotFoundError(f"{p} not found: run backend/devnode/up.sh && backend/devnode/deploy.py first")
    return json.loads(p.read_text())


def save(cfg: dict, path: Path | None = None) -> None:
    """Atomic write (the service may read it while /demo/reset rewrites it)."""
    p = Path(path or CONFIG_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".config.", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, p)


def demo_key(cfg: dict) -> str | None:
    """The demo signer's key, from the env var the config names; on the dev node
    it falls back to Nitro's public dev key."""
    env = cfg.get("demo", {}).get("keyEnv", "DEMO_KEY")
    key = os.environ.get(env)
    if not key and cfg.get("chainId") == DEVNODE_CHAIN_ID:
        key = NITRO_DEV_KEY
    return key


def demo_token(cfg: dict) -> str | None:
    env = cfg.get("demo", {}).get("tokenEnv", "DEMO_TOKEN")
    tok = os.environ.get(env)
    if not tok and cfg.get("chainId") == DEVNODE_CHAIN_ID:
        tok = DEFAULT_DEMO_TOKEN
    return tok


def db_path(cfg: dict) -> Path:
    p = cfg.get("db") or f"backend/data/{cfg['chainId']}.sqlite"
    return (ROOT / p) if not os.path.isabs(p) else Path(p)
