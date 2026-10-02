#!/usr/bin/env bash
# Stops the service and the dev node (gracefully: the chain stays).
# With a config.json for another chain (the testnet) there is only the service to stop.
set -euo pipefail
BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
. "$BACKEND/ops/network.sh"
RUN="$BACKEND/run"
if systemctl --user cat sp-backend.service >/dev/null 2>&1; then
  systemctl --user stop sp-backend.service
  if devnode_config; then systemctl --user stop sp-devnode.service; fi
else
  if [ -f "$RUN/backend.pid" ]; then
    kill "$(cat "$RUN/backend.pid")" 2>/dev/null || true
    rm -f "$RUN/backend.pid"
  fi
  if devnode_config; then "$BACKEND/devnode/down.sh"; fi
fi
echo "stopped"
