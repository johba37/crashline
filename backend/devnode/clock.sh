#!/usr/bin/env bash
# DEV ONLY. Moves the dev node's block clock forward, on the patched image
# sp-nitro-node:v3.11.4-7d5ac27-clock started by `DEVNODE_CLOCK=1 up.sh` (see
# docs/backend.md, "Dev clock"). Block timestamp = wall clock + offset; the
# offset lives in the container's volume (/tmp/dev-test/clock-offset), so it
# survives restarts and timestamps never go back. It only ever grows.
#
#   backend/devnode/clock.sh show                 # offset and latest block timestamp
#   backend/devnode/clock.sh advance 604800       # +1 week, then mine a block at the new time
#   backend/devnode/clock.sh set-absolute <unix>  # the next block at <unix> (not before the current clock)
#
# `advance` and `set-absolute` send a 0-value self-transfer from the dev key so
# `latest` carries the new time, print the old and new latest block timestamps,
# and exit 1 unless the timestamp moved by at least the requested amount.
#
# Env: DEVNODE_NAME (sp-devnode), DEVNODE_PORT (8647).
set -euo pipefail

NAME="${DEVNODE_NAME:-sp-devnode}"
PORT="${DEVNODE_PORT:-8647}"
RPC="http://127.0.0.1:$PORT"
CAST="${CAST:-$(command -v cast || echo "$HOME/.foundry/bin/cast")}"
DEV_KEY=0xb6b15c8cb491557369f3c7d2c287b053eb229daa9c22138887752191c9520659
OFFSET_FILE=/tmp/dev-test/clock-offset # in the volume; up.sh passes it as SP_CLOCK_OFFSET_FILE

usage() {
  sed -n '8,10p' "$0" | sed 's/^# *//' >&2
  exit 2
}
die() {
  echo "clock.sh: $*" >&2
  exit 1
}

# the container must run the clock image with the offset file wired in
check_container() {
  local envs
  envs=$(docker container inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$NAME" 2>/dev/null) ||
    die "no container $NAME"
  grep -qx "SP_CLOCK_OFFSET_FILE=$OFFSET_FILE" <<<"$envs" ||
    die "$NAME has no SP_CLOCK_OFFSET_FILE=$OFFSET_FILE: start it with DEVNODE_CLOCK=1 (or DEVNODE_IMAGE=sp-nitro-node:v3.11.4-7d5ac27-clock) backend/devnode/up.sh --recreate"
}
get_offset() { # the file the node reads; missing or garbage means 0, like the node
  local v
  v=$(docker exec "$NAME" cat "$OFFSET_FILE" 2>/dev/null | tr -d '[:space:]') || true
  [[ "$v" =~ ^[0-9]+$ ]] && echo "$v" || echo 0
}
put_offset() { # atomic replace, as the node's user (uid 1000 owns the volume)
  docker exec "$NAME" sh -c "printf '%s\n' '$1' > '$OFFSET_FILE.tmp' && mv '$OFFSET_FILE.tmp' '$OFFSET_FILE'"
}
latest_ts() { "$CAST" block latest -f timestamp --rpc-url "$RPC"; }
mine() { # one block: a 0-value self-transfer from the dev key
  local dev out
  dev=$("$CAST" wallet address "$DEV_KEY")
  out=$(timeout 60 "$CAST" send --rpc-url "$RPC" --private-key "$DEV_KEY" "$dev" --value 0 --json) ||
    die "could not send the block-producing tx: $out"
  [ "$(jq -r .status <<<"$out")" = 0x1 ] || die "block-producing tx failed: $out"
}
# shift <secs>: raise the offset by secs, mine, and check latest moved by >= secs
shift_clock() {
  local secs=$1 old_off new_off old_ts new_ts
  old_off=$(get_offset)
  old_ts=$(latest_ts)
  new_off=$((old_off + secs))
  put_offset "$new_off"
  [ "$(get_offset)" = "$new_off" ] || die "could not write $OFFSET_FILE in $NAME"
  mine
  new_ts=$(latest_ts)
  echo "offset:           $old_off -> $new_off s"
  echo "latest timestamp: $old_ts ($(date -u -d "@$old_ts" '+%F %T') UTC) -> $new_ts ($(date -u -d "@$new_ts" '+%F %T') UTC), +$((new_ts - old_ts)) s"
  [ $((new_ts - old_ts)) -ge "$secs" ] || die "latest block timestamp moved by $((new_ts - old_ts)) s, less than $secs s"
}

cmd="${1:-}"
case "$cmd" in
show)
  check_container
  off=$(get_offset)
  ts=$(latest_ts)
  now=$(date +%s)
  echo "container:        $NAME ($RPC)"
  echo "offset:           $off s ($((off / 86400)) d $(((off % 86400) / 3600)) h)"
  echo "latest block:     $("$CAST" block-number --rpc-url "$RPC") at $ts ($(date -u -d "@$ts" '+%F %T') UTC)"
  echo "next block at ~:  $((now + off)) (wall clock $now + offset)"
  ;;
advance)
  [[ "${2:-}" =~ ^[0-9]+$ ]] || usage
  check_container
  shift_clock "$2"
  ;;
set-absolute)
  [[ "${2:-}" =~ ^[0-9]+$ ]] || usage
  check_container
  target=$2
  off=$(get_offset)
  secs=$((target - $(date +%s) - off)) # what to add to the offset so the next block lands at target
  ts=$(latest_ts)
  [ "$secs" -ge 0 ] && [ "$target" -ge "$ts" ] ||
    die "$target is before the chain's clock (latest block $ts, next ~$(($(date +%s) + off))); the offset never decreases"
  shift_clock "$secs"
  [ "$(latest_ts)" -ge "$target" ] || die "latest block timestamp is before $target"
  ;;
*) usage ;;
esac
