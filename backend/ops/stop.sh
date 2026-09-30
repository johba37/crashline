#!/usr/bin/env bash
# Stops the service and the dev node (gracefully: the chain stays).
set -euo pipefail
BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$BACKEND/run"
if systemctl --user cat sp-backend.service >/dev/null 2>&1; then
  systemctl --user stop sp-backend.service sp-devnode.service
else
  if [ -f "$RUN/backend.pid" ]; then
    kill "$(cat "$RUN/backend.pid")" 2>/dev/null || true
    rm -f "$RUN/backend.pid"
  fi
  "$BACKEND/devnode/down.sh"
fi
echo "stopped"
