#!/usr/bin/env bash
# End-to-end demo lifecycle on a local Nitro dev node, priced by the Stylus model.
#
#   contracts/script/e2e-devnode.sh                 # model/k2 (the default)
#   PRICER_MODEL_DIR=model/k1-r1 REQUIRE_MIDLIFE=0 contracts/script/e2e-devnode.sh
#
# Starts offchainlabs/nitro-node --dev (docker), deploys the Stylus pricer
# (cargo stylus deploy) built from PRICER_MODEL_DIR, a 6-decimal MockUSDG, a
# MockChainlinkFeed with staged history, the factory, the quoter and the Desk,
# then runs the lifecycle and prints the key numbers of each step:
#   series with a past strike -> past fixings recorded -> LP deposit -> listing,
#   spread and risk budget -> NOTE bought -> cover (WRITER) bought by a hedger
#   -> NOTE sold mid-life, all at the model's quote +- the spread -> the next
#   observation autocalls -> redeem, collect, LP withdraw.
# The strike lies in the past so mid-life is reachable in real time: the next
# observation is E2E_LEAD_SECS after staging, and the script waits for it.
# Exits 0 only if every step succeeds and every check holds.
#
# Env: PRICER_MODEL_DIR (default <repo>/model/k2), E2E_PORT (8647),
#      E2E_LEAD_SECS (240), REQUIRE_MIDLIFE (1: the sell must happen with
#      observationsRemaining < 26), KEEP_NODE (0: stop the container on exit),
#      CARGO_TARGET_DIR (default <repo>/stylus/pricer-model/target).
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)" # contracts/
ROOT="$(cd "$HERE/.." && pwd)"
FORGE="${FORGE:-$(command -v forge || echo "$HOME/.foundry/bin/forge")}"
CAST="${CAST:-$(command -v cast || echo "$HOME/.foundry/bin/cast")}"
export PATH="$HOME/.cargo/bin:$PATH"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$ROOT/stylus/pricer-model/target}"

MODEL_DIR="$(cd "$ROOT" && realpath -m "${PRICER_MODEL_DIR:-model/k2}")"
PORT="${E2E_PORT:-8647}"
RPC="http://127.0.0.1:$PORT"
LEAD="${E2E_LEAD_SECS:-240}"
REQUIRE_MIDLIFE="${REQUIRE_MIDLIFE:-1}"
KEEP_NODE="${KEEP_NODE:-0}"
IMAGE="offchainlabs/nitro-node:v3.11.4-7d5ac27"
CONTAINER="sp-e2e-devnode-$PORT"

# Nitro's well-known dev key (prefunded on --dev), and three anvil test keys for the LP, the buyer and the hedger.
DEV_KEY=0xb6b15c8cb491557369f3c7d2c287b053eb229daa9c22138887752191c9520659
LP_KEY=0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d
BUYER_KEY=0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a
HEDGER_KEY=0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6

WEEK=604800
VOL=5500
INITIAL=25000000000 # $250.00, 8 decimals
SPOT_BPS=8500       # current spot, bps of initial: outside every observation-day band
AC_FIX_BPS=10200    # the next observation fixes above the autocall barrier
LP_DEPOSIT=100000000000 # 100,000 USDG
BUY_NOTE=10000000000    # 10,000 NOTE
SELL_NOTE=4000000000    # 4,000 NOTE
BUY_COVER=12000000000   # 12,000 WRITER: 10,000 from the Desk's inventory, 2,000 from fresh pairs
FEE_BPS=20              # integrator fee on NOTE trades, of the notional
COVER_FEE_BPS=500       # integrator fee on cover trades, of the premium
BID_BPS=20              # the Desk buys NOTE / sells cover this far below the model's quote
ASK_BPS=30              # and sells NOTE / buys cover back this far above it
RISK_BUDGET_BPS=2000    # at most 20% of the vault at risk on this feed
MAX_BPS=10675           # maxPayoutPerNote in bps: a pair is worth this, so cover = MAX_BPS - NOTE
# observation fixings 1..25 (bps of initial): a knock-in at 3, then recovery; none reaches ac
PATH_BPS=(9400 8800 5500 7200 8100 8600 9100 8300 8700 9000 8200 7900 8400 8800 9300 8900 8500 8000 8600 9200 8700 8300 8800 9100 8900)

step() { printf '\n== %s\n' "$*"; }
fail() {
  echo "E2E FAIL: $*" >&2
  exit 1
}
num() { awk '{print $1}'; } # strip cast's " [1.2e3]" suffix
call() { "$CAST" call --rpc-url "$RPC" "$@"; }
send() { # send <key> <to> <sig> [args...]; prints the tx hash, fails unless status 1
  local key=$1
  shift
  local out
  out=$("$CAST" send --rpc-url "$RPC" --private-key "$key" --json "$@") || fail "tx reverted: ${*:1:2}"
  [ "$(jq -r .status <<<"$out")" = "0x1" ] || fail "tx failed: ${*:1:2}"
  jq -r .transactionHash <<<"$out"
}
poke() { send "$DEV_KEY" "$DEV" --value 0 >/dev/null; } # the dev node makes blocks only on txs; views read the latest one
now() { "$CAST" block latest -f timestamp --rpc-url "$RPC"; }
create() { # create <path:Contract> [constructor args...]
  local target=$1
  shift
  local args=()
  [ $# -gt 0 ] && args=(--constructor-args "$@")
  (cd "$HERE" && "$FORGE" create --rpc-url "$RPC" --private-key "$DEV_KEY" --broadcast "$target" "${args[@]}") |
    awk '/Deployed to:/{print $3}'
}
event_data() { # event_data <txhash> <emitter> <topic0>
  "$CAST" receipt --rpc-url "$RPC" --json "$1" |
    jq -r --arg a "${2,,}" --arg t "$3" '.logs[] | select((.address|ascii_downcase)==$a and .topics[0]==$t) | .data'
}
usd() { python3 -c "import sys; print(f'{int(sys.argv[1]) / 1e6:,.6f}')" "$1"; }
round_id() { python3 -c "import sys; print(2**64 + int(sys.argv[1]))" "$1"; }

cleanup() {
  if [ "$KEEP_NODE" != 1 ]; then docker stop "$CONTAINER" >/dev/null 2>&1 || true; fi
}

# --- 0. node ----------------------------------------------------------------------
[ -f "$MODEL_DIR/student_export.json" ] || fail "no model at $MODEL_DIR (set PRICER_MODEL_DIR)"
step "0. Nitro dev node ($IMAGE) on $RPC"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --rm --name "$CONTAINER" -p "127.0.0.1:$PORT:8547" "$IMAGE" \
  --dev --http.addr 0.0.0.0 --http.api=net,web3,eth,debug >/dev/null
trap cleanup EXIT
for _ in $(seq 1 90); do "$CAST" chain-id --rpc-url "$RPC" >/dev/null 2>&1 && break || sleep 1; done
DEV=$("$CAST" wallet address "$DEV_KEY")
LP=$("$CAST" wallet address "$LP_KEY")
BUYER=$("$CAST" wallet address "$BUYER_KEY")
HEDGER=$("$CAST" wallet address "$HEDGER_KEY")
echo "chain id $("$CAST" chain-id --rpc-url "$RPC"), dev account $DEV ($("$CAST" balance --ether --rpc-url "$RPC" "$DEV") ETH)"

# --- 1. Stylus pricer -------------------------------------------------------------------
step "1. Stylus pricer from $MODEL_DIR"
DEPLOY_LOG="$(mktemp)"
(cd "$ROOT/stylus/pricer-model" && PRICER_MODEL_DIR="$MODEL_DIR" cargo stylus deploy \
  --endpoint "$RPC" --private-key "$DEV_KEY" --no-verify >"$DEPLOY_LOG" 2>&1) ||
  {
    tail -30 "$DEPLOY_LOG"
    fail "cargo stylus deploy"
  }
PRICER=$(sed 's/\x1b\[[0-9;]*m//g' "$DEPLOY_LOG" | grep -o 'deployed code at address: 0x[0-9a-fA-F]*' | awk '{print $NF}')
[ -n "$PRICER" ] || fail "no pricer address in the cargo stylus output"
WEIGHTS=$(call "$PRICER" "weightsHash()(bytes32)")
[ "$WEIGHTS" = "$(jq -r .weightsHash "$MODEL_DIR/student_export.json")" ] || fail "weightsHash differs from the export"
read -r OBS_MIN OBS_MAX < <(call "$PRICER" "certifiedRange(uint8)(int64,int64)" 8 | num | xargs)
echo "pricer $PRICER, model $(basename "$MODEL_DIR"), weightsHash $WEIGHTS"
echo "  $(sed 's/\x1b\[[0-9;]*m//g' "$DEPLOY_LOG" | grep -o 'contract size: .*' | sed -n 1p)"
echo "  certified observationsRemaining $OBS_MIN..$OBS_MAX, featureSpecVersion $(call "$PRICER" "featureSpecVersion()(uint16)")"

# --- 2. Solidity contracts ----------------------------------------------------------------
step "2. MockUSDG, MockChainlinkFeed, SeriesFactory, NoteQuoter, Desk"
USDG=$(create src/MockUSDG.sol:MockUSDG)
FEED=$(create src/MockChainlinkFeed.sol:MockChainlinkFeed "RHTSLA / USD (staged)")
FACTORY=$(create src/SeriesFactory.sol:SeriesFactory "$USDG")
QUOTER=$(create src/NoteQuoter.sol:NoteQuoter 93600)
# curator = dev account; 60 s pre-observation band so the demo can trade minutes before an observation
DESK=$(create src/Desk.sol:Desk "$USDG" "$FACTORY" "$QUOTER" "$DEV" 60)
for a in USDG FEED FACTORY QUOTER DESK; do
  [ -n "${!a}" ] || fail "deploy $a"
  printf '%-8s %s\n' "$a" "${!a}"
done

# --- 3. schedule and staged history ---------------------------------------------------------
# observationsRemaining at trading time: 16 if the model certifies it, else its nearest bound
TARGET_REM=16
[ "$TARGET_REM" -gt "$OBS_MAX" ] && TARGET_REM=$OBS_MAX
[ "$TARGET_REM" -lt "$OBS_MIN" ] && TARGET_REM=$OBS_MIN
DONE=$((26 - TARGET_REM))
poke
NOW=$(now)
T_NEXT=$((NOW + LEAD))
STRIKE=$((T_NEXT - (DONE + 1) * WEEK))
step "3. Staged feed history: strike $(date -u -d "@$STRIKE" '+%F %T') UTC, $DONE past observations, next at +${LEAD}s"
send "$DEV_KEY" "$FEED" "pushRoundAt(int256,uint40)" "$INITIAL" "$STRIKE" >/dev/null
for i in $(seq 1 "$DONE"); do
  send "$DEV_KEY" "$FEED" "pushRoundAt(int256,uint40)" $((INITIAL * PATH_BPS[i - 1] / 10000)) $((STRIKE + i * WEEK)) >/dev/null
done
send "$DEV_KEY" "$FEED" "pushRound(int256)" $((INITIAL * SPOT_BPS / 10000)) >/dev/null
echo "rounds $(call "$FEED" "latestRound()(uint256)" | num) (2^64 + n); current spot $SPOT_BPS bps of initial"

# --- 4. series with a past strike, past fixings recorded -------------------------------------
TERMS="($FEED,$STRIKE,$WEEK,26,6000,10000,25)"
TERMS_T="(address,uint40,uint32,uint8,uint16,uint16,uint16)"
step "4. Series (ki 60%, ac 100%, 25 bps/week, 26 weekly observations), past fixings"
send "$DEV_KEY" "$FACTORY" "createSeries($TERMS_T)" "$TERMS" >/dev/null
SERIES=$(call "$FACTORY" "seriesOf(bytes32)(address)" "$(call "$FACTORY" "seriesId($TERMS_T)(bytes32)" "$TERMS")")
NOTE=$(call "$SERIES" "note()(address)")
WRITER=$(call "$SERIES" "writer()(address)")
RECORDER=$(call "$FACTORY" "recorderOf(address)(address)" "$FEED")
echo "series $SERIES, NOTE $NOTE, recorder $RECORDER, maxPayoutPerNote $(call "$SERIES" "maxPayoutPerNote()(uint128)" | num)"
for i in $(seq 0 "$DONE"); do
  send "$DEV_KEY" "$RECORDER" "recordFixing(uint40,uint80)" $((STRIKE + i * WEEK)) "$(round_id $((i + 1)))" >/dev/null
done
send "$DEV_KEY" "$SERIES" "advance()" >/dev/null
STATE_T="((uint8,uint96,uint8,bool,bool,uint40,uint40,uint128))"
state() { call "$SERIES" "state()$STATE_T"; }
IFS=', ' read -r PHASE INIT OBS_DONE KNOCKED AUTOC NEXT_OBS _ _ < <(state | tr -d '()' | sed 's/ \[[^]]*\]//g')
echo "recorded strike + $DONE observations; phase $PHASE (1 = Live), initial $INIT, observationsDone $OBS_DONE, knockedIn $KNOCKED"
[ "$PHASE" = 1 ] && [ "$OBS_DONE" = "$DONE" ] && [ "$NEXT_OBS" = "$T_NEXT" ] || fail "series state"

# --- 5. LP deposit ------------------------------------------------------------------------
step "5. LP deposits $(usd $LP_DEPOSIT) USDG"
send "$DEV_KEY" "$LP" --value 1ether >/dev/null
send "$DEV_KEY" "$BUYER" --value 1ether >/dev/null
send "$DEV_KEY" "$HEDGER" --value 1ether >/dev/null
send "$LP_KEY" "$USDG" "mint(address,uint256)" "$LP" "$LP_DEPOSIT" >/dev/null
send "$LP_KEY" "$USDG" "approve(address,uint256)" "$DESK" "$LP_DEPOSIT" >/dev/null
send "$LP_KEY" "$DESK" "deposit(uint256,address)" "$LP_DEPOSIT" "$LP" >/dev/null
LP_SHARES=$(call "$DESK" "balanceOf(address)(uint256)" "$LP" | num)
echo "LP shares $LP_SHARES (12 decimals), Desk totalAssets $(usd "$(call "$DESK" "totalAssets()(uint256)" | num)")"

# --- 6. listing ------------------------------------------------------------------------------
step "6. Curator lists the series with the Stylus pricer, vol $VOL; spread $BID_BPS/$ASK_BPS bps, risk budget $RISK_BUDGET_BPS bps"
send "$DEV_KEY" "$DESK" "listSeries(address,address,uint16,uint128)" "$SERIES" "$PRICER" "$VOL" 50000000000 >/dev/null
send "$DEV_KEY" "$DESK" "setSpread(address,uint16,uint16,uint16)" "$SERIES" "$BID_BPS" "$ASK_BPS" 0 >/dev/null # k2's vol is pinned: no vol band
send "$DEV_KEY" "$DESK" "setRiskBudget(address,uint16)" "$FEED" "$RISK_BUDGET_BPS" >/dev/null
echo "listing: $(call "$DESK" "listing(address)((bool,address,uint16,uint128,uint128))" "$SERIES")"
desk_pos() { # the Desk's position and what it has at risk
  read -r AT_RISK LIMIT < <(call "$DESK" "risk(address)(uint256,uint256)" "$FEED" | num | xargs)
  echo "Desk holds NOTE $(usd "$(call "$NOTE" "balanceOf(address)(uint256)" "$DESK" | num)"), WRITER $(usd "$(call "$WRITER" "balanceOf(address)(uint256)" "$DESK" | num)"); at risk $(usd "$AT_RISK") of a budget of $(usd "$LIMIT") USDG"
}

# --- 7. buy NOTE --------------------------------------------------------------------------
IN_T="(uint16,int32,uint16,uint16,uint16,uint16,uint32,uint32,uint8,uint8)" # PricerInputs
TRADE_T="(address,uint256,uint16,uint256,uint16,address,bytes32)"
check_trade() { # check_trade <tx> <topic0> <side>: the trade price is the quoter's at that block, moved by the spread
  local tx=$1 topic=$2 side=$3 block data inputs clean quoted want
  block=$("$CAST" receipt --rpc-url "$RPC" "$tx" blockNumber)
  data=$(event_data "$tx" "$DESK" "$topic")
  [ -n "$data" ] || fail "no trade event in $tx"
  mapfile -t T < <("$CAST" decode-abi "f()$TRADE_T" "$data" | num)
  inputs=$(call --block "$block" "$QUOTER" "inputs(address,uint16)($IN_T)" "$SERIES" "$VOL" | sed 's/ \[[^]]*\]//g')
  clean=$(call --block "$block" "$PRICER" "priceBps($IN_T)(uint16)" "$inputs" | num)
  quoted=$(call --block "$block" "$QUOTER" "notePriceBps(address,address,uint16)(uint16,bytes32)" "$SERIES" "$PRICER" "$VOL" | sed -n 1p | num)
  REM=$(tr -d '()' <<<"$inputs" | awk -F', ' '{print $9}')
  echo "  model saw $inputs"
  case $side in
    buyNote) want=$((quoted + ASK_BPS)) ;;
    sellNote) want=$((quoted - BID_BPS)) ;;
    buyCover) want=$((MAX_BPS - (quoted - BID_BPS))) ;;
    sellCover) want=$((MAX_BPS - (quoted + ASK_BPS))) ;;
  esac
  echo "  model clean price $clean bps + accrued coupon $((quoted - clean)) bps = NOTE quote $quoted bps; $side traded at ${T[2]} bps"
  echo "  amount $(usd "${T[1]}"), USDG $(usd "${T[3]}"), fee ${T[4]} bps to ${T[5]}, weightsHash ${T[6]}"
  local rj g l1
  rj=$("$CAST" receipt --rpc-url "$RPC" --json "$tx")
  g=$("$CAST" to-dec "$(jq -r .gasUsed <<<"$rj")")
  l1=$("$CAST" to-dec "$(jq -r '.gasUsedForL1 // "0x0"' <<<"$rj")")
  echo "  gas used $g, of which L1 data $l1, L2 execution $((g - l1)) (quote incl. the Stylus model, mint or unwind, transfers)"
  [ "${T[2]}" = "$want" ] || fail "$side price ${T[2]} != $want (model quote $quoted, spread $BID_BPS/$ASK_BPS)"
  [ "${T[6]}" = "$WEIGHTS" ] || fail "event weightsHash"
}
step "7. Buyer buys $(usd $BUY_NOTE) NOTE (fee $FEE_BPS bps to the integrator)"
send "$BUYER_KEY" "$USDG" "mint(address,uint256)" "$BUYER" 20000000000 >/dev/null
poke
read -r COST PRICE < <(call "$DESK" "quoteBuy(address,uint256,uint16)(uint256,uint16)" "$SERIES" "$BUY_NOTE" "$FEE_BPS" | num | xargs)
MAX_COST=$((COST + COST / 200))
echo "quoteBuy: $(usd "$COST") USDG at $PRICE bps; maxCost $(usd $MAX_COST)"
send "$BUYER_KEY" "$USDG" "approve(address,uint256)" "$DESK" "$MAX_COST" >/dev/null
TX=$(send "$BUYER_KEY" "$DESK" "buy(address,uint256,uint256,uint16,address,address)" "$SERIES" "$BUY_NOTE" "$MAX_COST" "$FEE_BPS" "$DEV" "$BUYER")
check_trade "$TX" "$("$CAST" keccak "NoteBought(address,address,address,uint256,uint16,uint256,uint16,address,bytes32)")" buyNote
echo "buyer NOTE $(usd "$(call "$NOTE" "balanceOf(address)(uint256)" "$BUYER" | num)")"
desk_pos

# --- 7b. buy cover ------------------------------------------------------------------------
step "7b. Hedger buys $(usd $BUY_COVER) WRITER as cover: pays the premium, the Desk funds the pairs"
read -r AT_RISK LIMIT < <(call "$DESK" "risk(address)(uint256,uint256)" "$FEED" | num | xargs)
echo "risk budget first (quotes don't check it): $(usd $((LIMIT - AT_RISK))) USDG of room"
send "$HEDGER_KEY" "$USDG" "mint(address,uint256)" "$HEDGER" 5000000000 >/dev/null
poke
read -r COST PRICE < <(call "$DESK" "quoteBuyCover(address,uint256,uint16)(uint256,uint16)" "$SERIES" "$BUY_COVER" "$COVER_FEE_BPS" | num | xargs)
MAX_COST=$((COST + COST / 50))
echo "quoteBuyCover: $(usd "$COST") USDG at $PRICE bps incl. a fee of $COVER_FEE_BPS bps of the premium (a pair locks $(usd $((BUY_COVER * MAX_BPS / 10000)))); maxCost $(usd $MAX_COST)"
send "$HEDGER_KEY" "$USDG" "approve(address,uint256)" "$DESK" "$MAX_COST" >/dev/null
TX=$(send "$HEDGER_KEY" "$DESK" "buyCover(address,uint256,uint256,uint16,address,address)" "$SERIES" "$BUY_COVER" "$MAX_COST" "$COVER_FEE_BPS" "$DEV" "$HEDGER")
check_trade "$TX" "$("$CAST" keccak "CoverBought(address,address,address,uint256,uint16,uint256,uint16,address,bytes32)")" buyCover
[ "$(call "$WRITER" "balanceOf(address)(uint256)" "$HEDGER" | num)" = "$BUY_COVER" ] || fail "hedger WRITER"
desk_pos
[ "$(call "$NOTE" "balanceOf(address)(uint256)" "$DESK" | num)" = $((BUY_COVER - BUY_NOTE)) ] || fail "Desk NOTE after the cover sale"

# --- 8. sell NOTE mid-life -------------------------------------------------------------------------
step "8. Buyer sells $(usd $SELL_NOTE) NOTE back mid-life (early exit)"
poke
read -r PROCEEDS PRICE < <(call "$DESK" "quoteSell(address,uint256,uint16)(uint256,uint16)" "$SERIES" "$SELL_NOTE" "$FEE_BPS" | num | xargs)
MIN_PROCEEDS=$((PROCEEDS - PROCEEDS / 200))
echo "quoteSell: $(usd "$PROCEEDS") USDG at $PRICE bps; minProceeds $(usd $MIN_PROCEEDS)"
send "$BUYER_KEY" "$NOTE" "approve(address,uint256)" "$DESK" "$SELL_NOTE" >/dev/null
TX=$(send "$BUYER_KEY" "$DESK" "sell(address,uint256,uint256,uint16,address,address)" "$SERIES" "$SELL_NOTE" "$MIN_PROCEEDS" "$FEE_BPS" "$DEV" "$BUYER")
check_trade "$TX" "$("$CAST" keccak "NoteSold(address,address,address,uint256,uint16,uint256,uint16,address,bytes32)")" sellNote
desk_pos
SELL_REM=$REM
echo "sold with observationsRemaining $SELL_REM"
if [ "$REQUIRE_MIDLIFE" = 1 ] && [ "$SELL_REM" -ge 26 ]; then
  fail "the sell was not mid-life (observationsRemaining $SELL_REM): model $(basename "$MODEL_DIR") certifies $OBS_MIN..$OBS_MAX"
fi
NAV=$(call "$DESK" "totalAssets()(uint256)" | num)
echo "Desk totalAssets $(usd "$NAV") USDG (NOTE/WRITER marked at the model's quote)"

# --- 9. next observation: autocall ------------------------------------------------------------------
WAIT=$((T_NEXT + 2 - $(date +%s)))
step "9. Waiting ${WAIT}s for observation $((DONE + 1)) at $(date -u -d "@$T_NEXT" '+%T') UTC"
[ "$WAIT" -gt 0 ] && sleep "$WAIT"
poke
send "$DEV_KEY" "$FEED" "pushRoundAt(int256,uint40)" $((INITIAL * AC_FIX_BPS / 10000)) "$T_NEXT" >/dev/null
send "$DEV_KEY" "$RECORDER" "recordFixing(uint40,uint80)" "$T_NEXT" "$(round_id $((DONE + 3)))" >/dev/null
TX=$(send "$DEV_KEY" "$SERIES" "advance()")
IFS=', ' read -r PHASE _ OBS_DONE KNOCKED AUTOC _ _ PAYOUT < <(state | tr -d '()' | sed 's/ \[[^]]*\]//g')
echo "fixing $AC_FIX_BPS bps of initial -> phase $PHASE (2 = Settled), autocalled $AUTOC, knockedIn $KNOCKED, observationsDone $OBS_DONE"
echo "payoutPerNote $PAYOUT (= 1 + 25 bps x $((DONE + 1)))"
[ "$PHASE" = 2 ] && [ "$AUTOC" = true ] || fail "no autocall"
[ "$PAYOUT" = $((1000000 + 2500 * (DONE + 1))) ] || fail "payout"

# --- 10. redeem, collect, LP withdraw -------------------------------------------------------------
step "10. Redeem, collect, LP withdraw"
LEFT=$(call "$NOTE" "balanceOf(address)(uint256)" "$BUYER" | num)
B0=$(call "$USDG" "balanceOf(address)(uint256)" "$BUYER" | num)
send "$BUYER_KEY" "$SERIES" "redeem(uint256,uint256,address)" "$LEFT" 0 "$BUYER" >/dev/null
B1=$(call "$USDG" "balanceOf(address)(uint256)" "$BUYER" | num)
echo "buyer redeemed $(usd "$LEFT") NOTE for $(usd $((B1 - B0))) USDG"
[ $((B1 - B0)) = $((LEFT * PAYOUT / 1000000)) ] || fail "redeem amount"
H0=$(call "$USDG" "balanceOf(address)(uint256)" "$HEDGER" | num)
send "$HEDGER_KEY" "$SERIES" "redeem(uint256,uint256,address)" 0 "$BUY_COVER" "$HEDGER" >/dev/null
H1=$(call "$USDG" "balanceOf(address)(uint256)" "$HEDGER" | num)
echo "hedger redeemed $(usd "$BUY_COVER") WRITER for $(usd $((H1 - H0))) USDG (the coupons the autocall left unpaid)"
[ $((H1 - H0)) = $((BUY_COVER * (MAX_BPS * 100 - PAYOUT) / 1000000)) ] || fail "WRITER redeem amount"
TX=$(send "$DEV_KEY" "$DESK" "collect(address)" "$SERIES")
COLLECTED=$("$CAST" decode-abi "f()(uint256)" "$(event_data "$TX" "$DESK" "$("$CAST" keccak "Collected(address,uint256)")")" | num)
echo "Desk collected $(usd "$COLLECTED") USDG for its NOTE"
[ "$COLLECTED" = $(((BUY_COVER - BUY_NOTE + SELL_NOTE) * PAYOUT / 1000000)) ] || fail "collect amount"
MAX_REDEEM=$(call "$DESK" "maxRedeem(address)(uint256)" "$LP" | num)
L0=$(call "$USDG" "balanceOf(address)(uint256)" "$LP" | num)
send "$LP_KEY" "$DESK" "redeem(uint256,address,address)" "$MAX_REDEEM" "$LP" "$LP" >/dev/null
L1=$(call "$USDG" "balanceOf(address)(uint256)" "$LP" | num)
echo "LP redeemed $MAX_REDEEM of $LP_SHARES shares for $(usd $((L1 - L0))) USDG (deposited $(usd $LP_DEPOSIT); P&L $(usd $((L1 - L0 - LP_DEPOSIT))))"
DUST_DESK=$(call "$USDG" "balanceOf(address)(uint256)" "$DESK" | num)
DUST_SERIES=$(call "$USDG" "balanceOf(address)(uint256)" "$SERIES" | num)
echo "left over: Desk $DUST_DESK, series escrow $DUST_SERIES base units"
[ "$DUST_DESK" -le 1 ] || fail "Desk not emptied"
[ "$DUST_SERIES" -le 3 ] || fail "series escrow not emptied"

step "E2E PASS: model $(basename "$MODEL_DIR") ($WEIGHTS), sell at observationsRemaining $SELL_REM"
