"""The one service object: the indexer (which owns config, chain and db) plus
what the routes share. Errors are `ApiError` -> {"error": name, "args": {...}}."""

from __future__ import annotations

import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from . import config as cfgmod
from .indexer import Indexer, Snapshot

ROOT = cfgmod.ROOT
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


class ApiError(Exception):
    def __init__(self, status: int, name: str, args: dict | None = None):
        super().__init__(name)
        self.status = status
        self.name = name
        self.fields = args or {}

    def to_json(self) -> dict:
        return {"error": self.name, "args": self.fields}


def addr(s: str, what: str = "address") -> str:
    if not isinstance(s, str) or not ADDRESS.match(s):
        raise ApiError(400, "BadAddress", {what: s})
    return s.lower()


@lru_cache(maxsize=1)
def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return "unknown"


@lru_cache(maxsize=8)
def model_exports() -> dict[str, tuple[str, dict]]:
    """weightsHash -> (model dir relative to the repo, student_export dict), for every model/*."""
    out = {}
    for p in sorted((ROOT / "model").glob("*/student_export.json")):
        e = json.loads(p.read_text())
        out[e["weightsHash"]] = (str(p.parent.relative_to(ROOT)), e)
    return out


class Service:
    def __init__(self, config_path: Path | None = None):
        self.indexer = Indexer(config_path=config_path or cfgmod.CONFIG_PATH)

    def start(self) -> None:
        self.indexer.start()

    def stop(self) -> None:
        self.indexer.stop()

    def snap(self) -> Snapshot:
        """The current snapshot; 503 while the indexer has no head or lost the deployment."""
        s = self.indexer.snap
        if s is None or s.head is None:
            raise ApiError(503, "NotReady", {"status": s.status if s else "starting", "error": self.indexer.error})
        if s.status == "DeploymentMissing":
            # the public URL: rpcUrl may carry an API key
            raise ApiError(503, "DeploymentMissing", {"rpcUrl": s.cfg.get("publicRpcUrl", s.cfg["rpcUrl"]),
                                                      "deploymentBlock": s.cfg["deploymentBlock"]})
        return s

    def export_for(self, weights_hash: str) -> tuple[str, dict]:
        e = model_exports().get(weights_hash)
        if e is None:
            raise ApiError(404, "UnknownModel", {"weightsHash": weights_hash})
        return e


SERVICE = Service()
