#!/usr/bin/env bash
# The notes on Robinhood Chain testnet: four feeds at real prices and six series on them,
# staged and listed through curator.sh (same WALLET, RPC and DEPLOYMENTS; see its head).
#
#   contracts/script/testnet-notes.sh create   # once, before the first check (2026-10-06 20:00 UTC)
#   contracts/script/testnet-notes.sh prices   # daily: each feed's price now, and the fixings that are due
#
# Every series is the product model/k3 prices (knock-in 60 %, autocall 100 %, 25 bps a week,
# 26 weekly checks), so the notes differ in what they are on, the vol they are listed at and
# when they started. A feed's later notes are struck at one of its first note's checks: they
# share its rounds and fixings.
#
# Prices are Yahoo Finance's (the chart API ml/data/fetch_tsla.py reads). NOTES holds each
# feed's close at its weekly checks as read on 2026-10-02 (stocks at 20:00 UTC, the US close;
# coins at 00:00 UTC), none of them at or above its strike, so every note is still running.
# The price now is read at each run.
#
# `create` keeps each feed's address in DEPLOYMENTS (.feeds.<NAME>, which also names the feed
# for the backend: backend/ops/make-config.py), so a second run continues where the first
# stopped. Then: contracts/script/curator.sh deposit <USDG>.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)" # contracts/script/
CAST="${CAST:-$(command -v cast || echo "$HOME/.foundry/bin/cast")}"
export RPC="${RPC:-https://rpc.testnet.chain.robinhood.com}"
export DEPLOYMENTS="${DEPLOYMENTS:-$HERE/../../deployments/46630.json}"
WALLET="${WALLET:-}"

TERMS_T="(address,uint40,uint32,uint8,uint16,uint16,uint16)"
WEEK=604800
ZERO=0x0000000000000000000000000000000000000000

# feed | Yahoo symbol | listing vol, bps | next check, UTC | first strike, USD | the closes at
# its checks since, bps of that strike | further notes, struck at these checks (1 = the first)
NOTES="RHTSLA|TSLA|5500|2026-10-06 20:00|433.59|9773 9149 9333 8801 9700 9292 9137 8739 7091 7550 7676 7769 8078 8213 8491 8224 8739 8138|5 17
RHNVDA|NVDA|4500|2026-10-08 20:00|235.74|9312 9088 9275 8691 8937 8303 8265 8602 8798 8856 8274 9289 9557 9199 9671 9691 9263 9304 9527 9793|
ETH|ETH-USD|6500|2026-10-07 00:00|2752.63|9724|
BTC|BTC-USD|4500|2026-10-07 00:00|86172.28|9704|"

fail() {
  echo "testnet-notes: $*" >&2
  exit 1
}
curator() { "$HERE/curator.sh" "$@"; }
call() { "$CAST" call --rpc-url "$RPC" "$@"; }
# shellcheck disable=SC2086  # WALLET is a list of flags
send() { # send <to> <sig> [args...]; fails unless status 1
  local out
  out=$("$CAST" send --rpc-url "$RPC" $WALLET --json "$@") || fail "tx reverted: ${*:1:2}"
  [ "$(jq -r .status <<<"$out")" = "0x1" ] || fail "tx failed: ${*:1:2}"
}
unix() { python3 -c "import sys, datetime; print(int(datetime.datetime.strptime(sys.argv[1], '%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone.utc).timestamp()))" "$1"; }
price() { # price <Yahoo symbol>: its last price, in USD
  curl -fsS -m 30 -A "Mozilla/5.0" "https://query1.finance.yahoo.com/v8/finance/chart/$1?interval=1d&range=1d" |
    jq -er '.chart.result[0].meta.regularMarketPrice' || fail "no price for $1"
}
first_strike() { # first_strike <next check, UTC> <closes>: when the feed's first note was struck
  local closes
  read -r -a closes <<<"$2"
  echo $(($(unix "$1") - (${#closes[@]} + 1) * WEEK))
}
terms() { echo "($1,$2,$WEEK,26,6000,10000,25)"; } # terms <feed> <strike>
series_of() { call "$FACTORY" "seriesOf(bytes32)(address)" "$(call "$FACTORY" "seriesId($TERMS_T)(bytes32)" "$(terms "$1" "$2")")"; }

cmd_create() {
  local name symbol vol next initial closes further feed spot strike i series
  while IFS='|' read -r name symbol vol next initial closes further <&3; do
    feed=$(jq -r --arg n "$name" '.feeds[$n] // empty' "$DEPLOYMENTS")
    if [ -z "$feed" ]; then
      feed=$(curator feed "$name" | awk '/^feed /{print $3}')
      [ -n "$feed" ] || fail "no feed $name"
      jq --arg n "$name" --arg a "$feed" '.feeds[$n] = $a' "$DEPLOYMENTS" >"$DEPLOYMENTS.tmp" && mv "$DEPLOYMENTS.tmp" "$DEPLOYMENTS"
    fi
    echo "== $name: feed $feed"
    spot=$(python3 -c "import sys; print(round(float(sys.argv[1]) * 10000 / float(sys.argv[2])))" "$(price "$symbol")" "$initial")
    INITIAL_USD=$initial PATH_BPS=$closes SPOT_BPS=$spot NEXT_OBS=$(unix "$next") curator stage "$feed"
    strike=$(first_strike "$next" "$closes")
    for i in 0 $further; do
      series=$(series_of "$feed" $((strike + i * WEEK)))
      if [ "$series" = "$ZERO" ]; then # a further note: its strike and checks are fixings the stage recorded
        send "$FACTORY" "createSeries($TERMS_T)" "$(terms "$feed" $((strike + i * WEEK)))"
        series=$(series_of "$feed" $((strike + i * WEEK)))
        send "$series" "advance()"
      fi
      VOL=$vol curator list "$series"
    done
  done 3<<<"$NOTES"
  curator status
  echo "next: $HERE/curator.sh deposit <USDG>; and daily: $0 prices"
}

cmd_prices() {
  local name symbol vol next initial closes further feed strike i series pending
  while IFS='|' read -r name symbol vol next initial closes further <&3; do
    feed=$(jq -er --arg n "$name" '.feeds[$n]' "$DEPLOYMENTS") || fail "no feed $name in $DEPLOYMENTS: $0 create"
    curator push "$feed" "$(price "$symbol")"
    strike=$(first_strike "$next" "$closes")
    for i in 0 $further; do
      series=$(series_of "$feed" $((strike + i * WEEK)))
      read -r pending _ < <(call "$series" "pendingObservation()((bool,uint40))" | tr -d '(),')
      [ "$pending" != true ] || curator fixing "$series"
    done
  done 3<<<"$NOTES"
}

[ -f "$DEPLOYMENTS" ] || fail "no $DEPLOYMENTS (set DEPLOYMENTS)"
FACTORY=$(jq -er .seriesFactory "$DEPLOYMENTS") || fail "no seriesFactory in $DEPLOYMENTS"
case ${1:-} in
create | prices) "cmd_$1" ;;
*) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
