#!/usr/bin/env bash
# Brings up the dev node and the service and waits until /config answers.
# With the systemd user units installed (install.sh) it starts them; otherwise it runs
# node.sh and run.sh under nohup with a pid file in backend/run/.
# With a config.json for another chain (the testnet) it starts the service alone.
set -euo pipefail
BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$BACKEND/.env" ]; then set -a; . "$BACKEND/.env"; set +a; fi
. "$BACKEND/ops/network.sh"
PORT="${BACKEND_PORT:-8650}"
RUN="$BACKEND/run"
mkdir -p "$RUN"
if systemctl --user cat sp-backend.service >/dev/null 2>&1; then
  if devnode_config; then
    systemctl --user start sp-devnode.service
    # the node unit stays "active" after its start script; if the container died since, run it again
    if [ "$(docker container inspect -f '{{.State.Running}}' "${DEVNODE_NAME:-sp-devnode}" 2>/dev/null)" != true ]; then
      systemctl --user restart sp-devnode.service
    fi
  fi
  systemctl --user start sp-backend.service
else
  "$BACKEND/ops/node.sh"
  if [ -f "$RUN/backend.pid" ] && kill -0 "$(cat "$RUN/backend.pid")" 2>/dev/null; then
    echo "service already running (pid $(cat "$RUN/backend.pid"))"
  else
    nohup "$BACKEND/run.sh" >>"$RUN/backend.log" 2>&1 &
    echo $! >"$RUN/backend.pid"
  fi
fi
for _ in $(seq 1 120); do
  if out=$(curl -sf "http://127.0.0.1:$PORT/config"); then
    echo "service up on 127.0.0.1:$PORT: $(python3 -c 'import json,sys; c=json.load(sys.stdin); print("chain", c["chainId"], "block", c["block"], "desk", c["addresses"]["desk"])' <<<"$out")"
    exit 0
  fi
  sleep 1
done
echo "the service did not answer on $PORT (journalctl --user -u sp-backend, or $RUN/backend.log)" >&2
exit 1
