#!/usr/bin/env bash
# The curator's steps on Robinhood Chain testnet, signed by the curator's own wallet on the
# curator's own machine (the backend holds no key there). The dev node does the same through
# the backend's /demo routes (backend/devnode/deploy.py `stage`).
#
#   contracts/script/curator.sh status [feed ..]       # series, listings, feeds (no wallet)
#   contracts/script/curator.sh feed RHTSLA            # 1. a mock feed the wallet owns
#   contracts/script/curator.sh stage <feed>           # 2. its price history, the series, the past fixings
#   contracts/script/curator.sh list <series>          # 3. listing, spread, risk budget (the Desk's owner)
#   contracts/script/curator.sh deposit 500            # 4. USDG into the Desk, in whole USDG (any wallet)
#   contracts/script/curator.sh push <feed> 212.50     # daily: a price in USD; quotes stop 26 h after the last
#   contracts/script/curator.sh fixing <series>        # after each observation: record its fixing
#   contracts/script/curator.sh keep                   # hourly: real prices, due fixings, a successor for each ended series
#   contracts/script/curator.sh delist <series>        # no more buys of it at the Desk; sells stay (the Desk's owner)
#
# WALLET holds cast's wallet flags, for every command but status:
#   WALLET="--account curator --password-file $HOME/.curator-pass"   # a keystore: `cast wallet import curator --interactive`
#   WALLET="--ledger"                                                 # confirm each transaction on the device
#   WALLET="--private-key 0x.."                                       # ends up in the shell history
#
# `stage` writes the dev node's default scenario on an empty feed: a round at the strike, one
# at each past observation (PATH_BPS, bps of the initial price) and the current spot; then the
# series whose strike lies one interval before the first of them, its fixings and `advance()`.
# The next observation is at NEXT_OBS, less than one interval ahead. Run again, it continues
# where it stopped (the strike is read back from the feed's first round).
# `fixing` takes the last round at or before the observation (at most 96 h old), else the
# first round after it (FixingsRecorder's rule).
# `keep` is one round over the Desk's active listings: each of their feeds gets the price of
# its real feed (SOURCES, by the name in the feed's description) as a new round, each
# observation that has passed its fixing, and each series that has ended a successor: the
# same terms, struck at the time the old one ended (a fixing that is recorded already),
# listed as the old one is. It does what the wallet may: a feed takes prices from its owner
# only and the Desk lists for its owner only, while anyone records fixings and creates
# series. What it couldn't do it says, and then exits 1. From cron:
#   0 * * * * WALLET="--account curator --password-file $HOME/.curator-pass" <repo>/contracts/script/curator.sh keep >>$HOME/curator-keep.log 2>&1
#
# Env: RPC (https://rpc.testnet.chain.robinhood.com), DEPLOYMENTS (<repo>/deployments/46630.json);
#      stage: INITIAL_USD (250), PATH_BPS ("9400 8800 5500 7200 8100 8600 9100 8300 8700 9000";
#             PATH_BPS="" stages a series with no past observation, struck one interval before NEXT_OBS),
#             SPOT_BPS (8500), NEXT_OBS (unix time; default LEAD_SECS, 3 days, from now),
#             KI (6000), AC (10000), COUPON (25), COUNT (26), INTERVAL (604800: the Desk lists weekly series only);
#      list:  VOL (5500), VOL_BAND (200), BID_BPS (20), ASK_BPS (30), CAP (100000 NOTE),
#             RISK_BUDGET_BPS (2000);
#      keep:  SOURCE_RPC (https://rpc.mainnet.chain.robinhood.com), SOURCES ("NAME=feed ..": the
#             Chainlink feeds of RHTSLA, RHNVDA, RHAAPL, ETH and BTC on Robinhood Chain mainnet).
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)" # contracts/
ROOT="$(cd "$HERE/.." && pwd)"
FORGE="${FORGE:-$(command -v forge || echo "$HOME/.foundry/bin/forge")}"
CAST="${CAST:-$(command -v cast || echo "$HOME/.foundry/bin/cast")}"
RPC="${RPC:-https://rpc.testnet.chain.robinhood.com}"
DEPLOYMENTS="${DEPLOYMENTS:-$ROOT/deployments/46630.json}"
WALLET="${WALLET:-}"
SOURCE_RPC="${SOURCE_RPC:-https://rpc.mainnet.chain.robinhood.com}"
SOURCES="${SOURCES:-RHTSLA=0x4A1166a659A55625345e9515b32adECea5547C38 RHNVDA=0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15 RHAAPL=0x6B22A786bAa607d76728168703a39Ea9C99f2cD0 ETH=0x78F3556b67E17Df817D51Ef5a990cDaF09E8d3A9 BTC=0xa2c5184bF03d373Dc9dE4876eb4Bce595B460251}"

TERMS_T="(address,uint40,uint32,uint8,uint16,uint16,uint16)"
STATE_T="((uint8,uint96,uint8,bool,bool,uint40,uint40,uint128))"
ROUND_T="(uint80,int256,uint256,uint256,uint80)"
LISTING_T="((bool,address,uint16,uint128,uint128))"
ZERO=0x0000000000000000000000000000000000000000
MAX_FEED_STALENESS=93600 # NoteQuoter: 26 h
MAX_FIX_AGE=345600       # FixingsRecorder: 96 h

fail() {
  echo "curator: $*" >&2
  exit 1
}
addr() { jq -er ".$1" "$DEPLOYMENTS" || fail "no $1 in $DEPLOYMENTS"; }
fields() { sed 's/ \[[^]]*\]//g' | tr -d '()[],'; } # cast's "(1, 250 [2.5e2], true)" as words
call() { "$CAST" call --rpc-url "$RPC" "$@"; }
# shellcheck disable=SC2086  # WALLET is a list of flags
send() { # send <to> <sig> [args...]; fails unless status 1
  [ -n "$WALLET" ] || fail "set WALLET to cast's wallet flags (see the head of this script)"
  local out
  out=$("$CAST" send --rpc-url "$RPC" $WALLET --json "$@") || fail "tx reverted: ${*:1:2}"
  [ "$(jq -r .status <<<"$out")" = "0x1" ] || fail "tx failed: ${*:1:2}"
}
# shellcheck disable=SC2086
me() {
  [ -n "$WALLET" ] || fail "set WALLET to cast's wallet flags (see the head of this script)"
  "$CAST" wallet address $WALLET
}
same() { [ "$(tr '[:upper:]' '[:lower:]' <<<"$1")" = "$(tr '[:upper:]' '[:lower:]' <<<"$2")" ]; }
now() { "$CAST" block latest -f timestamp --rpc-url "$RPC"; }
utc() { python3 -c "import sys, datetime; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))" "$1"; }
units() { python3 -c "import sys; from decimal import Decimal; print(int(Decimal(sys.argv[1]) * 10 ** int(sys.argv[2])))" "$1" "$2"; }
human() { python3 -c "import sys; print(f'{int(sys.argv[1]) / 10 ** int(sys.argv[2]):,.2f}')" "$1" "$2"; }
round_id() { python3 -c "import sys; print(2**64 + int(sys.argv[1]))" "$1"; }
rounds() { # rounds <feed>: how many it has
  local r
  r=$(call "$1" "latestRound()(uint256)" 2>/dev/null | fields) || r="" # reverts NoRounds while empty
  if [ -z "$r" ]; then echo 0; else python3 -c "import sys; print(int(sys.argv[1]) - 2**64)" "$r"; fi
}
round() { call "$1" "getRoundData(uint80)$ROUND_T" "$(round_id "$2")" | fields | xargs | awk '{print $2, $4}'; } # answer updatedAt
feed_of() { call "$1" "terms()($TERMS_T)" | fields | xargs | awk '{print $1}'; }
series_of() { # series_of <terms>: the factory's series with these terms, the zero address if none
  local factory
  factory=$(addr seriesFactory)
  call "$factory" "seriesOf(bytes32)(address)" "$(call "$factory" "seriesId($TERMS_T)(bytes32)" "$1")"
}

cmd_feed() {
  local name=${1:?feed NAME, e.g. RHTSLA} feed
  [ -n "$WALLET" ] || fail "set WALLET to cast's wallet flags (see the head of this script)"
  # shellcheck disable=SC2086
  feed=$(cd "$HERE" && "$FORGE" create --rpc-url "$RPC" $WALLET --broadcast \
    src/mocks/MockChainlinkFeed.sol:MockChainlinkFeed --constructor-args "$name / USD (staged)" |
    awk '/Deployed to:/{print $3}')
  [ -n "$feed" ] || fail "deploy MockChainlinkFeed"
  echo "feed $name: $feed (owner $(call "$feed" "owner()(address)"))"
  echo "next: $0 stage $feed; and name it in the backend: config.json addresses.feeds.$name"
}

cmd_stage() {
  local feed=${1:?stage FEED} initial path spot interval count i n t ts ans have
  local strike t_next done_ series recorder who
  who=$(me)
  initial=$(units "${INITIAL_USD:-250}" 8)
  read -r -a path <<<"${PATH_BPS-9400 8800 5500 7200 8100 8600 9100 8300 8700 9000}"
  spot=${SPOT_BPS:-8500}
  interval=${INTERVAL:-604800}
  count=${COUNT:-26}
  done_=${#path[@]}
  [ "$done_" -le "$count" ] || fail "PATH_BPS has $done_ fixings, the series only $count observations"
  same "$(call "$feed" "owner()(address)")" "$who" || fail "the wallet $who does not own feed $feed"

  n=$(rounds "$feed")
  t=$(now)
  if [ "$n" -gt 0 ]; then # continue: the strike is the first round
    strike=$(round "$feed" 1 | awk '{print $2}')
    t_next=$((strike + (done_ + 1) * interval))
    [ -z "${NEXT_OBS:-}" ] || [ "$NEXT_OBS" = "$t_next" ] || fail "feed $feed is staged for NEXT_OBS=$t_next"
  else
    t_next=${NEXT_OBS:-$((t + ${LEAD_SECS:-259200}))}
    strike=$((t_next - (done_ + 1) * interval))
    [ "$t_next" -gt "$t" ] && [ $((t_next - t)) -lt "$interval" ] ||
      fail "the next observation must lie less than one interval ($interval s) ahead"
  fi
  echo "strike $(utc "$strike"), $done_ past observations, next observation $(utc "$t_next") (NEXT_OBS=$t_next)"

  for i in $(seq 0 "$done_"); do
    ts=$((strike + i * interval))
    ans=$initial
    [ "$i" -gt 0 ] && ans=$((initial * path[i - 1] / 10000))
    if [ "$i" -lt "$n" ]; then
      have=$(round "$feed" $((i + 1)))
      [ "$have" = "$ans $ts" ] || fail "round $((i + 1)) of $feed is ($have), not ($ans $ts): stage a fresh feed"
    else
      send "$feed" "pushRoundAt(int256,uint40)" "$ans" "$ts"
      echo "  round $((i + 1)): $(human "$ans" 8) USD at $(utc "$ts")"
    fi
  done
  if [ "$n" -le $((done_ + 1)) ]; then
    send "$feed" "pushRound(int256)" $((initial * spot / 10000))
    echo "  round $((done_ + 2)): $(human $((initial * spot / 10000)) 8) USD now"
  fi

  local factory terms
  factory=$(addr seriesFactory)
  terms="($feed,$strike,$interval,$count,${KI:-6000},${AC:-10000},${COUPON:-25})"
  series=$(series_of "$terms")
  if same "$series" "$ZERO"; then
    send "$factory" "createSeries($TERMS_T)" "$terms"
    series=$(series_of "$terms")
  fi
  recorder=$(call "$factory" "recorderOf(address)(address)" "$feed")
  for i in $(seq 0 "$done_"); do
    ts=$((strike + i * interval))
    [ "$(call "$recorder" "isRecorded(uint40)(bool)" "$ts")" = true ] ||
      send "$recorder" "recordFixing(uint40,uint80)" "$ts" "$(round_id $((i + 1)))"
  done
  send "$series" "advance()"
  echo "series $series (NOTE $(call "$series" "note()(address)"), WRITER $(call "$series" "writer()(address)"))"
  series_line "$series"
  echo "next: $0 list $series"
}

cmd_list() {
  local series=${1:?list SERIES} desk feed who
  who=$(me)
  desk=$(addr desk)
  same "$(call "$desk" "owner()(address)")" "$who" || fail "the wallet $who is not the Desk's owner (the curator)"
  feed=$(feed_of "$series")
  send "$desk" "listSeries(address,address,uint16,uint128)" "$series" "$(addr surrogatePricer)" "${VOL:-5500}" "$(units "${CAP:-100000}" 6)"
  send "$desk" "setSpread(address,uint16,uint16,uint16)" "$series" "${BID_BPS:-20}" "${ASK_BPS:-30}" "${VOL_BAND:-200}"
  send "$desk" "setRiskBudget(address,uint16)" "$feed" "${RISK_BUDGET_BPS:-2000}"
  listing_line "$series"
}

cmd_delist() {
  local series=${1:?delist SERIES} desk who
  who=$(me)
  desk=$(addr desk)
  same "$(call "$desk" "owner()(address)")" "$who" || fail "the wallet $who is not the Desk's owner (the curator)"
  send "$desk" "delistSeries(address)" "$series"
  listing_line "$series"
}

cmd_deposit() {
  local amount desk usdg who
  amount=$(units "${1:?deposit USDG_AMOUNT}" 6)
  desk=$(addr desk)
  usdg=$(addr usdg)
  who=$(me)
  send "$usdg" "approve(address,uint256)" "$desk" "$amount"
  send "$desk" "deposit(uint256,address)" "$amount" "$who"
  echo "Desk totalAssets $(human "$(call "$desk" "totalAssets()(uint256)" | fields)" 6) USDG"
}

cmd_push() {
  local feed=${1:?push FEED PRICE_USD} price who
  price=$(units "${2:?push FEED PRICE_USD}" 8)
  who=$(me)
  same "$(call "$feed" "owner()(address)")" "$who" || fail "the wallet $who does not own feed $feed"
  send "$feed" "pushRound(int256)" "$price"
  feed_line "$feed"
}

cmd_fixing() {
  local series=${1:?fixing SERIES} feed recorder pending obs n k updated before age pick
  feed=$(feed_of "$series")
  recorder=$(call "$series" "recorder()(address)")
  while read -r pending obs < <(call "$series" "pendingObservation()((bool,uint40))" | fields | xargs) && [ "$pending" = true ]; do
    n=$(rounds "$feed")
    before=0 # the last round at or before the observation
    age=0
    for k in $(seq "$n" -1 1); do
      updated=$(round "$feed" "$k" | awk '{print $2}')
      if [ "$updated" -le "$obs" ]; then
        before=$k
        age=$((obs - updated))
        break
      fi
    done
    if [ "$before" -gt 0 ] && [ "$age" -le "$MAX_FIX_AGE" ]; then
      pick=$before
    elif [ "$before" -lt "$n" ]; then # nothing fresh before it: the first round after it
      pick=$((before + 1))
    else
      fail "no price within 96 h before $(utc "$obs"): push one, the first price after the observation becomes its fixing"
    fi
    send "$recorder" "recordFixing(uint40,uint80)" "$obs" "$(round_id "$pick")"
    echo "fixing for $(utc "$obs"): $(human "$(round "$feed" "$pick" | awk '{print $1}')" 8) USD (round $pick)"
  done
  send "$series" "advance()"
  series_line "$series"
}

FAILED=0
step() { # step <function> [args..]: in a shell of its own, so that a failure ends this step only
  local rc
  set +e
  (
    set -e
    "$@"
  )
  rc=$?
  set -e
  [ "$rc" -eq 0 ] || FAILED=$((FAILED + 1))
}

keep_price() { # keep_price <feed> <wallet>: the real feed's price as a new round
  local feed=$1 name pair src="" owner price
  name=$(call "$feed" "description()(string)" | tr -d '"')
  name=${name%% / *} # "RHTSLA / USD (staged)"
  for pair in $SOURCES; do
    case $pair in "$name="*) src=${pair#*=} ;; esac
  done
  [ -n "$src" ] || fail "feed $feed: no real feed for $name in SOURCES"
  owner=$(call "$feed" "owner()(address)")
  same "$owner" "$2" || fail "feed $feed ($name): no price pushed, only its owner $owner can"
  price=$("$CAST" call --rpc-url "$SOURCE_RPC" "$src" "latestRoundData()$ROUND_T" | fields | xargs | awk '{print $2}')
  [ "$price" -gt 0 ] || fail "feed $feed ($name): the real feed $src answers $price"
  send "$feed" "pushRound(int256)" "$price"
  echo "$name: $(feed_line "$feed")"
}

keep_fixings() { # keep_fixings <series>: the fixings of its observations that have passed
  local pending
  pending=$(call "$1" "pendingObservation()((bool,uint40))" | fields | xargs)
  case $pending in true*) cmd_fixing "$1" ;; esac
}

keep_series() { # keep_series <series> <wallet>: its due fixings and, once it has ended, its successor
  local s=$1 out phase done_ autocalled maturity
  keep_fixings "$s"
  out=$(call "$s" "state()$STATE_T" | fields | xargs)
  read -r phase _ done_ _ autocalled _ maturity _ <<<"$out"
  [ "$phase" = 2 ] || return 0

  local feed strike interval count ki ac coupon ended terms next
  out=$(call "$s" "terms()($TERMS_T)" | fields | xargs)
  read -r feed strike interval count ki ac coupon <<<"$out"
  ended=$maturity
  if [ "$autocalled" = true ]; then ended=$((strike + done_ * interval)); fi
  terms="($feed,$ended,$interval,$count,$ki,$ac,$coupon)"
  next=$(series_of "$terms")
  if same "$next" "$ZERO"; then
    send "$(addr seriesFactory)" "createSeries($TERMS_T)" "$terms"
    next=$(series_of "$terms")
    echo "series $s ended at $(utc "$ended"): its successor is $next"
  fi
  keep_fixings "$next" # the strike, where the old series ended on the fallback and left it unrecorded

  local desk owner pricer vol cap bid ask band
  desk=$(addr desk)
  out=$(call "$desk" "listing(address)$LISTING_T" "$next" | fields | xargs)
  read -r _ pricer _ <<<"$out"
  same "$pricer" "$ZERO" || return 0 # listed already, or delisted since
  owner=$(call "$desk" "owner()(address)")
  same "$owner" "$2" || fail "series $next, the successor of $s, is not listed: only the Desk's owner $owner can"
  out=$(call "$desk" "listing(address)$LISTING_T" "$s" | fields | xargs)
  read -r _ pricer vol cap _ <<<"$out"
  out=$(call "$desk" "spread(address)((uint16,uint16,uint16))" "$s" | fields | xargs)
  read -r bid ask band <<<"$out"
  send "$desk" "listSeries(address,address,uint16,uint128)" "$next" "$pricer" "$vol" "$cap"
  send "$desk" "setSpread(address,uint16,uint16,uint16)" "$next" "$bid" "$ask" "$band"
  series_line "$next"
  listing_line "$next"
}

cmd_keep() {
  local desk who all s out feed feeds="" listed=""
  desk=$(addr desk)
  who=$(me)
  echo "keep at $(utc "$(now)"), wallet $who"
  all=$(call "$desk" "listedSeries()(address[])" | fields)
  for s in $all; do
    out=$(call "$desk" "listing(address)$LISTING_T" "$s" | fields | xargs)
    case $out in true*) ;; *) continue ;; esac
    listed="$listed $s"
    feed=$(feed_of "$s")
    case " $feeds " in *" $feed "*) ;; *) feeds="$feeds $feed" ;; esac
  done
  for feed in $feeds; do step keep_price "$feed" "$who"; done
  for s in $listed; do step keep_series "$s" "$who"; done
  [ "$FAILED" -eq 0 ] || fail "keep: $FAILED of its steps failed, see above"
}

feed_line() { # feed_line <feed>
  local n answer updated t
  n=$(rounds "$1")
  [ "$n" -gt 0 ] || { echo "feed $1: no rounds"; return; }
  read -r answer updated < <(round "$1" "$n")
  t=$(now)
  if [ $((t - updated)) -gt "$MAX_FEED_STALENESS" ]; then
    echo "feed $1: $n rounds, $(human "$answer" 8) USD at $(utc "$updated"), STALE: push a price"
  else
    echo "feed $1: $n rounds, $(human "$answer" 8) USD at $(utc "$updated"), quotes until $(utc $((updated + MAX_FEED_STALENESS)))"
  fi
}

series_line() { # series_line <series>
  local phase initial obs_done knocked autocalled next payout pending obs
  read -r phase initial obs_done knocked autocalled next _ payout < <(call "$1" "state()$STATE_T" | fields | xargs)
  read -r pending obs < <(call "$1" "pendingObservation()((bool,uint40))" | fields | xargs)
  case $phase in
  0) echo "series $1: pending, strike not recorded" ;;
  1)
    echo "series $1: live, initial $(human "$initial" 8) USD, $obs_done observations done, knocked in $knocked, next $(utc "$next")"
    [ "$pending" = true ] && echo "  the observation of $(utc "$obs") has passed: $0 fixing $1"
    ;;
  *) echo "series $1: settled (autocalled $autocalled, knocked in $knocked), $(human "$payout" 6) USDG per NOTE" ;;
  esac
  return 0
}

listing_line() { # listing_line <series>
  local desk active pricer vol cap sold bid ask band
  desk=$(addr desk)
  read -r active pricer vol cap sold < <(call "$desk" "listing(address)$LISTING_T" "$1" | fields | xargs)
  if same "$pricer" "$ZERO"; then
    echo "  not listed"
    return
  fi
  read -r bid ask band < <(call "$desk" "spread(address)((uint16,uint16,uint16))" "$1" | fields | xargs)
  echo "  listed (active $active): vol $vol +- $band bps, spread $bid/$ask bps, cap $(human "$cap" 6) NOTE, sold $(human "$sold" 6)," \
    "risk budget $(call "$desk" "riskBudgetBps(address)(uint16)" "$(feed_of "$1")") bps"
}

cmd_status() {
  local desk factory s feed seen=""
  desk=$(addr desk)
  factory=$(addr seriesFactory)
  echo "chain $("$CAST" chain-id --rpc-url "$RPC"), $(utc "$(now)")"
  echo "Desk $desk: owner $(call "$desk" "owner()(address)"), totalAssets $(human "$(call "$desk" "totalAssets()(uint256)" | fields)" 6) USDG"
  for s in $(call "$factory" "allSeries()(address[])" | fields); do
    series_line "$s"
    listing_line "$s"
    feed=$(feed_of "$s")
    case " $seen " in *" $feed "*) ;; *)
      seen="$seen $feed"
      echo "  $(feed_line "$feed")"
      ;;
    esac
  done
  [ -n "$seen" ] || echo "no series yet"
  for feed in "$@"; do feed_line "$feed"; done
}

[ -f "$DEPLOYMENTS" ] || fail "no $DEPLOYMENTS (set DEPLOYMENTS)"
[ "$("$CAST" chain-id --rpc-url "$RPC")" = "$(addr chainId)" ] || fail "$RPC is not chain $(addr chainId)"
cmd=${1:-}
[ $# -gt 0 ] && shift
case $cmd in
status | feed | stage | list | delist | deposit | push | fixing | keep) "cmd_$cmd" "$@" ;;
*) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
