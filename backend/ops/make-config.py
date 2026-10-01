#!/usr/bin/env python3
"""Writes a service config from a deployments/<chainid>.json (the layout
contracts/script/Deploy.s.sol writes), e.g. the testnet variant:

    backend/.venv/bin/python backend/ops/make-config.py deployments/46630.json \\
        --rpc https://rpc.testnet.chain.robinhood.com --out backend/config.json

The demo routes are off unless --demo is given (they need the curator's key
in DEMO_KEY and mock feeds). The deployment block is found by bisecting
eth_getCode(seriesFactory) when the RPC keeps old state; otherwise pass
--deployment-block. The model dir is the one whose weightsHash the file's
`pricerWeightsHash` (or else the deployed pricer's `weightsHash()`) names, so a
k3 deployment gets model/k3 and its teacher v3; --model-dir overrides it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import config as cfgmod  # noqa: E402
from app.chain import Chain  # noqa: E402


def deployment_block(chain: Chain, factory: str) -> int:
    """The factory's creation block (both deploy scripts create it before the Desk)."""
    lo, hi = 0, chain.block_number()
    if chain.code(factory, hi) in (None, "0x"):
        raise SystemExit(f"no code at {factory} on this chain")
    while lo < hi:  # first block with the factory's code
        mid = (lo + hi) // 2
        if chain.code(factory, mid) in (None, "0x"):
            lo = mid + 1
        else:
            hi = mid
    return lo


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("deployments", help="deployments/<chainid>.json")
    ap.add_argument("--rpc", required=True, help="the RPC URL the service uses")
    ap.add_argument("--public-rpc", help="the RPC URL /config reports (default: --rpc)")
    ap.add_argument("--deployment-block", type=int)
    ap.add_argument("--demo", action="store_true", help="enable the demo routes")
    ap.add_argument("--port", type=int, default=cfgmod.backend_port())
    ap.add_argument("--out", default=str(cfgmod.CONFIG_PATH))
    ap.add_argument("--model-dir", help="model/k3, model/k2, ... (default: the one matching the pricer's weightsHash)")
    a = ap.parse_args()

    d = json.loads(Path(a.deployments).read_text())
    chain = Chain(a.rpc)
    if chain.chain_id != d["chainId"]:
        raise SystemExit(f"{a.rpc} is chain {chain.chain_id}, the file is for {d['chainId']}")
    feeds = d.get("feeds") or ({"RHTSLA": d["mockFeed"]} if d.get("mockFeed") else {})
    try:
        block = a.deployment_block if a.deployment_block is not None else deployment_block(chain, d["seriesFactory"])
    except Exception as e:
        raise SystemExit(f"can't find the deployment block ({e}); pass --deployment-block") from None
    b = chain.block(block)
    weights = d.get("pricerWeightsHash") or chain.at("pricer", d["surrogatePricer"]).call("weightsHash")
    model_dir = a.model_dir or next((m for m, e in _exports().items() if e == weights), None)
    if model_dir is None:
        raise SystemExit(f"no model/* has weightsHash {weights}; pass --model-dir")
    cfg = {
        "network": "devnode" if d["chainId"] == cfgmod.DEVNODE_CHAIN_ID else f"chain-{d['chainId']}",
        "chainId": d["chainId"],
        "rpcUrl": a.rpc,
        "publicRpcUrl": a.public_rpc or a.rpc,
        "genesisHash": chain.block(0)["hash"],
        "deploymentBlock": block,
        "deploymentBlockHash": b["hash"],
        "deployedAt": b["time"],
        "addresses": {k: d[k].lower() for k in ("usdg", "seriesFactory", "noteQuoter", "desk", "surrogatePricer")}
        | {"feeds": {k: v.lower() for k, v in feeds.items()}},
        "curator": (d.get("curator") or "").lower(),
        "modelDir": model_dir,
        "demo": {"enabled": bool(a.demo), "keyEnv": "DEMO_KEY", "tokenEnv": "DEMO_TOKEN"},
        "backendPort": a.port,
        "db": f"backend/data/{d['chainId']}.sqlite",
        "pollSecs": 2,
        "historyStepSecs": 3600,
        "replayStepSecs": 3600,
    }
    cfgmod.save(cfg, Path(a.out))
    print(f"wrote {a.out}: chain {cfg['chainId']}, desk {cfg['addresses']['desk']}, deployment block {block}, "
          f"demo {'on' if a.demo else 'off'}")


def _exports() -> dict[str, str]:
    out = {}
    for p in sorted((cfgmod.ROOT / "model").glob("*/student_export.json")):
        out[str(p.parent.relative_to(cfgmod.ROOT))] = json.loads(p.read_text())["weightsHash"]
    return out


if __name__ == "__main__":
    main()
