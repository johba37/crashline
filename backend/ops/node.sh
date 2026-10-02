#!/usr/bin/env bash
# The dev node's start logic (the sp-devnode unit's ExecStart, and start.sh without systemd):
# wait for docker, start or create sp-devnode (up.sh), recreate it if a hard kill broke
# its sequencer (up.sh exits 3), and deploy + stage if config.json's deployment is not on it.
set -euo pipefail
BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.foundry/bin:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
if [ -f "$BACKEND/.env" ]; then set -a; . "$BACKEND/.env"; set +a; fi
. "$BACKEND/ops/network.sh"
# never deploy over a testnet config: deploy.py would rewrite config.json for the dev node
devnode_config || { echo "config.json is for another chain: no dev node to start"; exit 0; }

for i in $(seq 1 150); do docker info >/dev/null 2>&1 && break; [ "$i" = 150 ] && { echo "docker not available" >&2; exit 1; }; sleep 2; done

set +e
"$BACKEND/devnode/up.sh"
rc=$?
set -e
if [ "$rc" = 3 ]; then
  echo "the node cannot sequence (hard kill?): recreating it and redeploying"
  "$BACKEND/devnode/up.sh" --recreate
elif [ "$rc" != 0 ]; then
  exit "$rc"
fi
"$BACKEND/.venv/bin/python" "$BACKEND/devnode/deploy.py" --if-missing
