#!/usr/bin/env bash
# Starts the persistent Nitro dev node `sp-devnode` on 127.0.0.1:$DEVNODE_PORT (8647).
#
#   backend/devnode/up.sh              # create or start the container, wait for RPC, check it sequences
#   backend/devnode/up.sh --recreate   # throw the chain away first (new volume)
#
# Unlike contracts/script/e2e-devnode.sh: no --rm, the chain lives in the named
# volume `sp-devnode-data` (mounted where --dev keeps its data, /tmp/dev-test),
# archive mode (eth_call at past blocks, for the history backfill), CORS and
# vhosts open for a browser behind the SSH tunnel.
#
# Persistence: a graceful stop (docker stop/restart, down.sh, a host shutdown)
# keeps the chain. A hard kill can leave the dev sequencer unable to sequence
# ("wrong msgIdx"); this script detects that with a 0-value self-transfer and
# exits 3, and `--recreate` (or ops/start.sh, which then redeploys) starts over.
#
# Env: DEVNODE_PORT (8647), DEVNODE_NAME (sp-devnode), DEVNODE_VOLUME (sp-devnode-data).
set -euo pipefail

PORT="${DEVNODE_PORT:-8647}"
NAME="${DEVNODE_NAME:-sp-devnode}"
VOLUME="${DEVNODE_VOLUME:-sp-devnode-data}"
IMAGE="offchainlabs/nitro-node:v3.11.4-7d5ac27"
RPC="http://127.0.0.1:$PORT"
CAST="${CAST:-$(command -v cast || echo "$HOME/.foundry/bin/cast")}"
DEV_KEY=0xb6b15c8cb491557369f3c7d2c287b053eb229daa9c22138887752191c9520659
CHAIN_ID=412346

if [ "${1:-}" = "--recreate" ]; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME" >/dev/null 2>&1 || true
  echo "removed $NAME and its volume $VOLUME"
fi

if docker container inspect "$NAME" >/dev/null 2>&1; then
  state=$(docker container inspect -f '{{.State.Running}}' "$NAME")
else
  state=missing
fi
if [ "$state" = missing ]; then
  if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
    echo "port $PORT is taken by something else; set DEVNODE_PORT (and record it in backend/config.json)" >&2
    exit 2
  fi
  docker volume create "$VOLUME" >/dev/null
  # --dev keeps its data in /tmp/dev-test and runs as uid 1000: hand it the fresh volume
  docker run --rm -v "$VOLUME:/tmp/dev-test" --user root --entrypoint chown "$IMAGE" 1000:1000 /tmp/dev-test
  docker run -d --name "$NAME" --stop-timeout 60 \
    -v "$VOLUME:/tmp/dev-test" -p "127.0.0.1:$PORT:8547" "$IMAGE" \
    --dev --http.addr 0.0.0.0 --http.api=net,web3,eth,debug \
    --http.corsdomain='*' --http.vhosts='*' --execution.caching.archive >/dev/null
  echo "created $NAME ($IMAGE) on $RPC, data in volume $VOLUME"
elif [ "$state" = false ]; then
  docker start "$NAME" >/dev/null
  echo "started $NAME on $RPC"
else
  echo "$NAME already running on $RPC"
fi

for _ in $(seq 1 90); do
  "$CAST" chain-id --rpc-url "$RPC" >/dev/null 2>&1 && break || sleep 1
done
id=$("$CAST" chain-id --rpc-url "$RPC" 2>/dev/null || echo none)
[ "$id" = "$CHAIN_ID" ] || { echo "no dev node answering on $RPC (chain id $id)" >&2; exit 1; }

# can it still sequence? (a hard kill can break the dev sequencer while reads keep working)
DEV=$("$CAST" wallet address "$DEV_KEY")
if ! out=$(timeout 30 "$CAST" send --rpc-url "$RPC" --private-key "$DEV_KEY" "$DEV" --value 0 --json 2>&1); then
  echo "$NAME answers but cannot sequence transactions: $out" >&2
  echo "recreate it: backend/devnode/up.sh --recreate && backend/.venv/bin/python backend/devnode/deploy.py" >&2
  exit 3
fi
echo "chain $CHAIN_ID at block $("$CAST" block-number --rpc-url "$RPC"), sequencing ok"
