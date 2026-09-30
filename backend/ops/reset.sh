#!/usr/bin/env bash
# Resets the dev node to a fresh chain with the default scenario (the sp-reset unit's ExecStart):
# through the service's /demo/reset when it answers, else directly (up.sh --recreate + deploy.py;
# a running service follows config.json by itself).
set -euo pipefail
BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.foundry/bin:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
if [ -f "$BACKEND/.env" ]; then set -a; . "$BACKEND/.env"; set +a; fi
PORT="${BACKEND_PORT:-8650}"
TOKEN="${DEMO_TOKEN:-sp-devnode-demo}"
if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  curl -sf --max-time 600 -X POST -H "X-Demo-Token: $TOKEN" -H 'content-type: application/json' -d '{}' \
    "http://127.0.0.1:$PORT/demo/reset"
  echo
else
  "$BACKEND/devnode/up.sh" --recreate
  "$BACKEND/.venv/bin/python" "$BACKEND/devnode/deploy.py"
fi
