#!/usr/bin/env bash
# Stops the dev node gracefully (the chain stays in its volume; up.sh resumes it).
#
#   backend/devnode/down.sh           # docker stop, 60 s for Nitro to flush
#   backend/devnode/down.sh --purge   # also remove the container and the volume (the chain is gone)
set -euo pipefail
NAME="${DEVNODE_NAME:-sp-devnode}"
VOLUME="${DEVNODE_VOLUME:-sp-devnode-data}"

if docker inspect "$NAME" >/dev/null 2>&1; then
  docker stop -t 60 "$NAME" >/dev/null
  echo "stopped $NAME"
else
  echo "no container $NAME"
fi
if [ "${1:-}" = "--purge" ]; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME" >/dev/null 2>&1 || true
  echo "removed $NAME and volume $VOLUME"
fi
