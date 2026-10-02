#!/usr/bin/env bash
# End-to-end lifecycle of the v2 perpetual note on a local Nitro dev node, priced by the
# closed form plus the Stylus student (docs/v2-perpetual-note.md, docs/v2-spec.md).
#
#   contracts/script/e2e-perp-devnode.sh                              # model/p1
#   PRICER_MODEL_DIR=model/synthetic-p contracts/script/e2e-perp-devnode.sh
#   PERP_PRICER=formula contracts/script/e2e-perp-devnode.sh          # no Stylus: PerpFormulaPricer
#
# Starts offchainlabs/nitro-node --dev (docker), deploys the Stylus PerpPricer (cargo stylus
# deploy) built from PRICER_MODEL_DIR, a 6-decimal MockUSDG, a MockChainlinkFeed with staged
# history, the v1 factory (it owns the recorders), PerpFactory, PerpQuoter, PerpDesk and a
# PerpWrapper, then runs the lifecycle and prints the key numbers of each step:
#   series with a past first fixing -> five fixings recorded (one ratchets the reference)
#   -> LP deposit -> listing (model, vol, earnings date), spread, risk budget
#   -> NOTE bought -> cover (WRITER) bought by a hedger -> part of the NOTE wrapped
#   -> NOTE sold, all at closed form + student +- the spread, every quote checked against
#      the Python integer twins (tools/perp_formula.py, tools/pricer_quant.py)
#   -> the next fixing knocks the note in (the LP queues a redemption while it is pending)
#   -> holders claim what the fixing released, the Desk collects, the wrapper reinvests
#   -> everybody unwinds, the queue is paid, the LP withdraws; USDG is conserved.
# The first fixing lies in the past so a later week is reachable in real time: the next
# fixing is E2E_LEAD_SECS after staging, and the script waits for it.
# Exits 0 only if every step succeeds and every check holds.
#
# Env: PRICER_MODEL_DIR (default <repo>/model/p1), PERP_PRICER (stylus | formula),
#      E2E_PORT (8657: NOT the live dev node's 8647), E2E_LEAD_SECS (240), KEEP_NODE (0),
#      CARGO_TARGET_DIR (default <repo>/stylus/pricer-model/target), E2E_IMAGE,
#      PYTHON (default <repo>/tools/.venv/bin/python: numpy + pycryptodome).
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)" # contracts/
ROOT="$(cd "$HERE/.." && pwd)"
FORGE="${FORGE:-$(command -v forge || echo "$HOME/.foundry/bin/forge")}"
CAST="${CAST:-$(command -v cast || echo "$HOME/.foundry/bin/cast")}"
PYTHON="${PYTHON:-$ROOT/tools/.venv/bin/python}"
export PATH="$HOME/.cargo/bin:$PATH"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$ROOT/stylus/pricer-model/target}"
export PYTHONDONTWRITEBYTECODE=1

MODE="${PERP_PRICER:-stylus}"
MODEL_DIR="$(cd "$ROOT" && realpath -m "${PRICER_MODEL_DIR:-model/p1}")"
PORT="${E2E_PORT:-8657}"
RPC="http://127.0.0.1:$PORT"
LEAD="${E2E_LEAD_SECS:-240}"
KEEP_NODE="${KEEP_NODE:-0}"
IMAGE="${E2E_IMAGE:-offchainlabs/nitro-node:v3.11.4-7d5ac27}"
CONTAINER="sp-perp-e2e-$PORT"

# Nitro's well-known dev key (prefunded on --dev), and three anvil test keys for the LP, the buyer and the hedger.
DEV_KEY=0xb6b15c8cb491557369f3c7d2c287b053eb229daa9c22138887752191c9520659
LP_KEY=0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d
BUYER_KEY=0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a
HEDGER_KEY=0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6

WEEK=604800
# the P1 product (docs/v2-spec.md): k 60%, phi 1/yr at weekly fixings; coupon reserve R = 23.5%
KI_BPS=6000
MELT=18995352771274247
RESERVE=235000
PAIR_BPS=12350      # (1e6 + RESERVE) / 100: a pair is worth this per unit of notional, so cover = PAIR_BPS - NOTE
VOL="${E2E_VOL:-5500}"
VOL_BAND="${E2E_VOL_BAND:-200}"
FIRST_PRICE=25000000000 # $250.00, 8 decimals
# fixings 1..5, bps of the first fixing: the second one is a new high (the reference ratchets to 104%)
PATH_BPS=(9600 10400 9900 9300 8800)
REFERENCE_BPS=10400
SPOT_BPS=8840       # current spot: 85% of the reference
KI_FIX_BPS=5980     # the next fixing: 57.5% of the reference, below the 60% barrier
AFTER_BPS=6240      # spot after it: 60% of the reference
LP_DEPOSIT=100000000000 # 100,000 USDG
BUY_NOTE=10000000000    # 10,000 NOTE tokens
WRAP_NOTE=3000000000    # of which 3,000 go into the wrapper
SELL_NOTE=2000000000    # 2,000 sold back before the fixing
BUY_COVER=12000000000   # 12,000 WRITER: 10,000 from the Desk's inventory, 2,000 from fresh pairs
FEE_BPS=20              # integrator fee on NOTE trades, of the notional
COVER_FEE_BPS=500       # integrator fee on cover trades, of the premium
BID_BPS=20
ASK_BPS=30
RISK_BUDGET_BPS=2000
RAY=1000000000000000000000000000

step() { printf '\n== %s\n' "$*"; }
fail() {
  echo "E2E FAIL: $*" >&2
  exit 1
}
num() { awk '{print $1}'; } # strip cast's " [1.2e3]" suffix
py() { python3 -c "$1" "${@:2}"; }
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
usd() { py "import sys; print(f'{int(sys.argv[1]) / 1e6:,.6f}')" "$1"; }
round_id() { py "import sys; print(2**64 + int(sys.argv[1]))" "$1"; }
bal() { call "$1" "balanceOf(address)(uint256)" "$2" | num; }
gas_of() { # L2 execution gas of a tx
  local rj g l1
  rj=$("$CAST" receipt --rpc-url "$RPC" --json "$1")
  g=$("$CAST" to-dec "$(jq -r .gasUsed <<<"$rj")")
  l1=$("$CAST" to-dec "$(jq -r '.gasUsedForL1 // "0x0"' <<<"$rj")")
  echo $((g - l1))
}

cleanup() {
  if [ "$KEEP_NODE" != 1 ]; then docker stop "$CONTAINER" >/dev/null 2>&1 || true; fi
}

# --- 0. node ----------------------------------------------------------------------
if [ "$MODE" = stylus ]; then
  [ -f "$MODEL_DIR/student_export.json" ] || fail "no model at $MODEL_DIR (set PRICER_MODEL_DIR, or PERP_PRICER=formula)"
  [ "$(jq -r .featureSpecVersion "$MODEL_DIR/student_export.json")" = 2 ] || fail "$MODEL_DIR is not a feature spec 2 model"
fi
"$PYTHON" -c "import Crypto, numpy" 2>/dev/null || fail "PYTHON=$PYTHON needs numpy and pycryptodome (the integer twins)"
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

# --- 1. pricer --------------------------------------------------------------------------
PRODUCT="($KI_BPS,$MELT,$WEEK,400,0)" # P1: drift 4%, discount 0
if [ "$MODE" = stylus ]; then
  step "1. Stylus PerpPricer from $MODEL_DIR"
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
  echo "pricer $PRICER, model $(basename "$MODEL_DIR"), weightsHash $WEIGHTS"
  echo "  $(sed 's/\x1b\[[0-9;]*m//g' "$DEPLOY_LOG" | grep -o 'contract size: .*' | sed -n 1p)"
  TWIN_MODEL=(--model "$MODEL_DIR")
else
  step "1. PerpFormulaPricer (no student: the closed form and the fixing accrual)"
  # model/p1's two fixing bands and vol range: 6 h, knock-in +-10%, reference +-5%, vol 20-90%
  PRICER=$(create src/PerpFormulaPricer.sol:PerpFormulaPricer "$PRODUCT" "(21600,1000,500,2000,9000)")
  WEIGHTS=$(call "$PRICER" "weightsHash()(bytes32)")
  echo "pricer $PRICER, weightsHash $WEIGHTS"
  TWIN_MODEL=()
fi
[ "$(call "$PRICER" "featureSpecVersion()(uint16)")" = 2 ] || fail "featureSpecVersion"
GOT_PRODUCT=$(call "$PRICER" "product()((uint16,uint64,uint32,int16,int16))" | sed 's/ \[[^]]*\]//g; s/ //g')
echo "  product (knock-in bps, melt share, interval, drift bps, discount bps) $GOT_PRODUCT"
[ "$GOT_PRODUCT" = "$PRODUCT" ] || fail "the model is not pinned to the P1 product $PRODUCT"
read -r VOL_MIN VOL_MAX < <(call "$PRICER" "certifiedRange(uint8)(int64,int64)" 1 | num | xargs)
echo "  certified vol $VOL_MIN..$VOL_MAX bps"

# --- 2. Solidity contracts ----------------------------------------------------------------
step "2. MockUSDG, MockChainlinkFeed, SeriesFactory (recorders), PerpFactory, PerpQuoter, PerpDesk"
USDG=$(create src/MockUSDG.sol:MockUSDG)
FEED=$(create src/MockChainlinkFeed.sol:MockChainlinkFeed "RHTSLA / USD (staged)")
V1_FACTORY=$(create src/SeriesFactory.sol:SeriesFactory "$USDG")
FACTORY=$(create src/PerpFactory.sol:PerpFactory "$USDG" "$V1_FACTORY")
QUOTER=$(create src/PerpQuoter.sol:PerpQuoter 93600)
# curator = dev account; a 60 s band before each fixing so the demo can trade minutes before it
DESK=$(create src/PerpDesk.sol:PerpDesk "$USDG" "$FACTORY" "$QUOTER" "$DEV" 60)
for a in USDG FEED V1_FACTORY FACTORY QUOTER DESK; do
  [ -n "${!a}" ] || fail "deploy $a"
  printf '%-10s %s\n' "$a" "${!a}"
done

# --- 3. schedule and staged history ---------------------------------------------------------
DONE=${#PATH_BPS[@]}
poke
NOW=$(now)
T_NEXT=$((NOW + LEAD))
FIRST=$((T_NEXT - (DONE + 1) * WEEK))
step "3. Staged feed history: first fixing $(date -u -d "@$FIRST" '+%F %T') UTC, $DONE fixings since, the next at +${LEAD}s"
send "$DEV_KEY" "$FEED" "pushRoundAt(int256,uint40)" "$FIRST_PRICE" "$FIRST" >/dev/null
for i in $(seq 1 "$DONE"); do
  send "$DEV_KEY" "$FEED" "pushRoundAt(int256,uint40)" $((FIRST_PRICE * PATH_BPS[i - 1] / 10000)) $((FIRST + i * WEEK)) >/dev/null
done
send "$DEV_KEY" "$FEED" "pushRound(int256)" $((FIRST_PRICE * SPOT_BPS / 10000)) >/dev/null
echo "rounds $(call "$FEED" "latestRound()(uint256)" | num) (2^64 + n); current spot $SPOT_BPS bps of the first fixing"

# --- 4. the perpetual series ---------------------------------------------------------------
TERMS="($FEED,$FIRST,$WEEK,$KI_BPS,$MELT,$RESERVE)"
TERMS_T="(address,uint40,uint32,uint16,uint64,uint64)"
step "4. Perpetual series (k 60%, melt share 1.8995% per weekly fixing, coupon reserve 23.5%), past fixings"
send "$DEV_KEY" "$FACTORY" "createSeries($TERMS_T)" "$TERMS" >/dev/null
SERIES=$(call "$FACTORY" "seriesOf(bytes32)(address)" "$(call "$FACTORY" "seriesId($TERMS_T)(bytes32)" "$TERMS")")
NOTE=$(call "$SERIES" "note()(address)")
WRITER=$(call "$SERIES" "writer()(address)")
RECORDER=$(call "$FACTORY" "recorderOf(address)(address)" "$FEED")
echo "series $SERIES, NOTE $NOTE, WRITER $WRITER, recorder $RECORDER"
for i in $(seq 0 "$DONE"); do
  send "$DEV_KEY" "$RECORDER" "recordFixing(uint40,uint80)" $((FIRST + i * WEEK)) "$(round_id $((i + 1)))" >/dev/null
done
send "$DEV_KEY" "$SERIES" "advance()" >/dev/null
STATE_T="((uint8,uint96,uint96,bool,uint8,uint32,uint40,uint256,uint256,uint256))"
state() { call "$SERIES" "state()$STATE_T" | tr -d '()' | sed 's/ \[[^]]*\]//g'; }
IFS=', ' read -r PHASE REFERENCE LAST KNOCKED MISSED FIX_DONE NEXT_FIX PER_TOKEN NOTE_IDX WRITER_IDX < <(state)
echo "phase $PHASE (1 = Live), reference $REFERENCE (the high at fixing 2), fixingsDone $FIX_DONE, knockedIn $KNOCKED"
echo "notional per token $(py "import sys; print(int(sys.argv[1]) / 1e27)" "$PER_TOKEN") (= (1 - a)^$DONE), NOTE index $NOTE_IDX, WRITER index $WRITER_IDX"
[ "$PHASE" = 1 ] && [ "$FIX_DONE" = "$DONE" ] && [ "$NEXT_FIX" = "$T_NEXT" ] && [ "$KNOCKED" = false ] || fail "series state"
[ "$REFERENCE" = $((FIRST_PRICE * REFERENCE_BPS / 10000)) ] || fail "the reference did not ratchet"
[ "$WRITER_IDX" = 0 ] || fail "a clean note released USDG to WRITER"
# the twin of the fixing rule gives the same state
"$PYTHON" - "$ROOT" "$KI_BPS" "$MELT" "$RESERVE" "$FIRST_PRICE" "$PER_TOKEN" "$NOTE_IDX" "${PATH_BPS[@]}" <<'PY' || fail "series state differs from tools/perp_vectors.py"
import sys
sys.path.insert(0, sys.argv[1] + "/tools")
import perp_vectors as pv
ki, melt, reserve, first, per_token, note_idx = (int(x) for x in sys.argv[2:8])
r = pv.run(ki, melt, reserve, first, [first * int(b) // 10_000 for b in sys.argv[8:]])
assert (r["notionalPerToken"], r["noteIndex"], r["writerIndex"]) == (per_token, note_idx, 0), r
PY
echo "  = tools/perp_vectors.py (the Python twin of the fixing rule)"

# --- 5. LP deposit ------------------------------------------------------------------------
step "5. LP deposits $(usd $LP_DEPOSIT) USDG"
send "$DEV_KEY" "$LP" --value 1ether >/dev/null
send "$DEV_KEY" "$BUYER" --value 1ether >/dev/null
send "$DEV_KEY" "$HEDGER" --value 1ether >/dev/null
send "$LP_KEY" "$USDG" "mint(address,uint256)" "$LP" "$LP_DEPOSIT" >/dev/null
send "$LP_KEY" "$USDG" "approve(address,uint256)" "$DESK" "$LP_DEPOSIT" >/dev/null
send "$LP_KEY" "$DESK" "deposit(uint256,address)" "$LP_DEPOSIT" "$LP" >/dev/null
LP_SHARES=$(bal "$DESK" "$LP")
echo "LP shares $LP_SHARES (12 decimals), Desk totalAssets $(usd "$(call "$DESK" "totalAssets()(uint256)" | num)")"
MINTED=$LP_DEPOSIT # every USDG that exists, for the conservation check at the end

# --- 6. listing ------------------------------------------------------------------------------
[ "$MODE" = formula ] || { [ $((VOL - VOL_BAND)) -ge "$VOL_MIN" ] && [ $((VOL + VOL_BAND)) -le "$VOL_MAX" ]; } ||
  fail "vol $VOL +- $VOL_BAND is outside the certified $VOL_MIN..$VOL_MAX"
EARNINGS=$((NOW + 30 * 86400))
step "6. Curator lists the series: vol $VOL +- $VOL_BAND bps, next earnings $(date -u -d "@$EARNINGS" '+%F'); spread $BID_BPS/$ASK_BPS bps, risk budget $RISK_BUDGET_BPS bps"
send "$DEV_KEY" "$DESK" "listSeries(address,address,uint16,uint40,uint128)" "$SERIES" "$PRICER" "$VOL" "$EARNINGS" 50000000000 >/dev/null
send "$DEV_KEY" "$DESK" "setSpread(address,uint16,uint16,uint16)" "$SERIES" "$BID_BPS" "$ASK_BPS" "$VOL_BAND" >/dev/null
send "$DEV_KEY" "$DESK" "setRiskBudget(address,uint16)" "$FEED" "$RISK_BUDGET_BPS" >/dev/null
echo "listing: $(call "$DESK" "listing(address)((bool,address,uint16,uint40,uint128))" "$SERIES")"
desk_pos() { # the Desk's position and what it has at risk
  read -r AT_RISK LIMIT < <(call "$DESK" "risk(address)(uint256,uint256)" "$FEED" | num | xargs)
  echo "Desk holds NOTE $(usd "$(bal "$NOTE" "$DESK")"), WRITER $(usd "$(bal "$WRITER" "$DESK")"); released to it $(usd "$(call "$SERIES" "claimable(address)(uint256)" "$DESK" | num)"); at risk $(usd "$AT_RISK") of a budget of $(usd "$LIMIT") USDG"
}

# --- trades: every price is checked against the integer twins --------------------------------
IN_T="(uint16,uint16,uint32,uint8,uint8)" # PerpPricerInputs
QUOTE_T="((uint16,uint16,int16,uint16,bytes32))"
TRADED_TOPIC=$("$CAST" keccak "Traded(address,address,uint8,address,uint256,uint256,uint16,uint256,uint16,address,bytes32)")
TRADED_T="(address,uint256,uint256,uint16,uint256,uint16,address,bytes32)"
twin_quote() { # twin_quote <block> <vol>: sets Q_PRICE (and prints the parts) after checking chain == twin
  local block=$1 vol=$2 inputs chain twin
  inputs=$(call --block "$block" "$QUOTER" "inputs(address,uint16,uint40)($IN_T)" "$SERIES" "$vol" "$EARNINGS" | sed 's/ \[[^]]*\]//g')
  chain=$(call --block "$block" "$QUOTER" "quote(address,address,uint16,uint40)$QUOTE_T" "$SERIES" "$PRICER" "$vol" "$EARNINGS" |
    tr -d '()' | sed 's/ \[[^]]*\]//g')
  IFS=', ' read -r Q_PRICE Q_FORMULA Q_CORRECTION Q_COUPON Q_HASH <<<"$chain"
  twin=$("$PYTHON" "$ROOT/tools/perp_quoter_vectors.py" "${TWIN_MODEL[@]}" --inputs "$(tr -d '() ' <<<"$inputs")" --reserve "$RESERVE")
  echo "  vol $vol: inputs $inputs -> formula $Q_FORMULA bps, correction $Q_CORRECTION bps, coupon $Q_COUPON bps = NOTE $Q_PRICE bps"
  [ "$twin" = "$Q_FORMULA $Q_CORRECTION $Q_COUPON $Q_PRICE" ] || fail "chain quote ($Q_FORMULA $Q_CORRECTION $Q_COUPON $Q_PRICE) != Python twins ($twin)"
  [ "$Q_HASH" = "$WEIGHTS" ] || fail "quote weightsHash"
  if [ "$MODE" = stylus ]; then # the Stylus contract itself, asked directly
    [ "$(call --block "$block" "$PRICER" "correctionBps($IN_T)(int16)" "$inputs" | num)" = "$Q_CORRECTION" ] || fail "pricer correction"
  fi
}
check_trade() { # check_trade <tx> <side>: the trade price is the quote at that block, moved by the spread
  local tx=$1 side=$2 block data q_lo q_hi note want
  block=$("$CAST" receipt --rpc-url "$RPC" "$tx" blockNumber)
  data=$(event_data "$tx" "$DESK" "$TRADED_TOPIC")
  [ -n "$data" ] || fail "no Traded event in $tx"
  mapfile -t T < <("$CAST" decode-abi "f()$TRADED_T" "$data" | num)
  twin_quote "$block" $((VOL - VOL_BAND))
  q_hi=$Q_PRICE # the note is usually worth more at the lower vol
  q_lo=$Q_PRICE
  if [ "$VOL_BAND" != 0 ]; then
    twin_quote "$block" $((VOL + VOL_BAND))
    q_lo=$Q_PRICE
    [ "$q_hi" -lt "$q_lo" ] && read -r q_lo q_hi <<<"$q_hi $q_lo"
  fi
  [ "$q_hi" -gt "$PAIR_BPS" ] && q_hi=$PAIR_BPS
  [ "$q_lo" -gt "$PAIR_BPS" ] && q_lo=$PAIR_BPS
  case $side in
    buyNote | sellCover)
      note=$((q_hi + ASK_BPS))
      [ "$note" -gt "$PAIR_BPS" ] && note=$PAIR_BPS
      ;;
    sellNote | buyCover)
      note=$((q_lo - BID_BPS))
      [ "$note" -lt 0 ] && note=0
      ;;
  esac
  case $side in
    buyNote | sellNote) want=$note ;;
    buyCover | sellCover) want=$((PAIR_BPS - note)) ;;
  esac
  echo "  NOTE quotes lo $q_lo, hi $q_hi bps; $side traded at ${T[3]} bps: ${T[1]} tokens = $(usd "${T[2]}") USDG of notional, paid $(usd "${T[4]}") USDG"
  echo "  L2 execution gas $(gas_of "$tx") (the quotes: closed form in Solidity + the model, twice with a vol band; mint or unwind; transfers)"
  [ "${T[3]}" = "$want" ] || fail "$side price ${T[3]} != $want"
  [ "${T[7]}" = "$WEIGHTS" ] || fail "event weightsHash"
}
desk_quote() { # desk_quote <side 0..3> <amount> <feeBps> -> PAID PRICE
  read -r PAID PRICE < <(call "$DESK" "quote(address,uint8,uint256,uint16)(uint256,uint16)" "$SERIES" "$1" "$2" "$3" | num | xargs)
}

step "7. Buyer buys $(usd $BUY_NOTE) NOTE tokens (fee $FEE_BPS bps to the integrator)"
send "$BUYER_KEY" "$USDG" "mint(address,uint256)" "$BUYER" 20000000000 >/dev/null
MINTED=$((MINTED + 20000000000))
poke
desk_quote 0 "$BUY_NOTE" "$FEE_BPS"
MAX_COST=$((PAID + PAID / 200))
echo "quote: $(usd "$PAID") USDG at $PRICE bps of notional; maxCost $(usd $MAX_COST)"
send "$BUYER_KEY" "$USDG" "approve(address,uint256)" "$DESK" "$MAX_COST" >/dev/null
TX=$(send "$BUYER_KEY" "$DESK" "buy(address,uint256,uint256,uint16,address,address)" "$SERIES" "$BUY_NOTE" "$MAX_COST" "$FEE_BPS" "$DEV" "$BUYER")
check_trade "$TX" buyNote
desk_pos

step "7b. Hedger buys $(usd $BUY_COVER) WRITER tokens as cover: pays the premium, the Desk funds the pairs"
read -r AT_RISK LIMIT < <(call "$DESK" "risk(address)(uint256,uint256)" "$FEED" | num | xargs)
echo "risk budget first (quotes don't check it): $(usd $((LIMIT - AT_RISK))) USDG of room"
send "$HEDGER_KEY" "$USDG" "mint(address,uint256)" "$HEDGER" 5000000000 >/dev/null
MINTED=$((MINTED + 5000000000))
poke
desk_quote 2 "$BUY_COVER" "$COVER_FEE_BPS"
MAX_COST=$((PAID + PAID / 50))
echo "quote: $(usd "$PAID") USDG at $PRICE bps incl. a fee of $COVER_FEE_BPS bps of the premium; maxCost $(usd $MAX_COST)"
send "$HEDGER_KEY" "$USDG" "approve(address,uint256)" "$DESK" "$MAX_COST" >/dev/null
TX=$(send "$HEDGER_KEY" "$DESK" "buyCover(address,uint256,uint256,uint16,address,address)" "$SERIES" "$BUY_COVER" "$MAX_COST" "$COVER_FEE_BPS" "$DEV" "$HEDGER")
check_trade "$TX" buyCover
[ "$(bal "$WRITER" "$HEDGER")" = "$BUY_COVER" ] || fail "hedger WRITER"
desk_pos
[ "$(bal "$NOTE" "$DESK")" = $((BUY_COVER - BUY_NOTE)) ] || fail "Desk NOTE after the cover sale"

step "7c. Buyer wraps $(usd $WRAP_NOTE) NOTE: shares for a holder that never claims"
WRAPPER=$(create src/PerpWrapper.sol:PerpWrapper "$DESK" "$SERIES" true)
[ -n "$WRAPPER" ] || fail "deploy PerpWrapper"
send "$BUYER_KEY" "$NOTE" "approve(address,uint256)" "$WRAPPER" "$WRAP_NOTE" >/dev/null
send "$BUYER_KEY" "$WRAPPER" "wrap(uint256,address)" "$WRAP_NOTE" "$BUYER" >/dev/null
SHARES=$(bal "$WRAPPER" "$BUYER")
echo "wrapper $WRAPPER ($(call "$WRAPPER" "symbol()(string)")), shares $SHARES (12 decimals) for $(usd "$(call "$WRAPPER" "totalTokens()(uint256)" | num)") NOTE"
[ "$SHARES" = $((WRAP_NOTE * 1000000)) ] || fail "wrapper shares"

step "8. Buyer sells $(usd $SELL_NOTE) NOTE tokens back"
poke
desk_quote 1 "$SELL_NOTE" "$FEE_BPS"
MIN_PROCEEDS=$((PAID - PAID / 200))
echo "quote: $(usd "$PAID") USDG at $PRICE bps; minProceeds $(usd $MIN_PROCEEDS)"
send "$BUYER_KEY" "$NOTE" "approve(address,uint256)" "$DESK" "$SELL_NOTE" >/dev/null
TX=$(send "$BUYER_KEY" "$DESK" "sell(address,uint256,uint256,uint16,address,address)" "$SERIES" "$SELL_NOTE" "$MIN_PROCEEDS" "$FEE_BPS" "$DEV" "$BUYER")
check_trade "$TX" sellNote
desk_pos
echo "Desk totalAssets $(usd "$(call "$DESK" "totalAssets()(uint256)" | num)") USDG (NOTE and WRITER marked at the quote)"

# --- 9. the next fixing knocks the note in -----------------------------------------------------
WAIT=$((T_NEXT + 2 - $(date +%s)))
step "9. Waiting ${WAIT}s for fixing $((DONE + 1)) at $(date -u -d "@$T_NEXT" '+%T') UTC"
[ "$WAIT" -gt 0 ] && sleep "$WAIT"
poke
# the fixing time has passed and its fixing isn't recorded: no share price, so no ERC-4626 withdrawal
[ "$(call "$DESK" "maxRedeem(address)(uint256)" "$LP" | num)" = 0 ] || fail "maxRedeem should be 0 while the fixing is pending"
QUEUED=$((LP_SHARES / 5))
send "$LP_KEY" "$DESK" "requestRedeem(uint256)" "$QUEUED" >/dev/null
echo "fixing pending: maxRedeem 0, but the LP queues $QUEUED shares (20%) for redemption"
BUYER_NOTE=$(bal "$NOTE" "$BUYER")
DESK_NOTE=$(bal "$NOTE" "$DESK")
KI_PRICE=$((FIRST_PRICE * KI_FIX_BPS / 10000))
send "$DEV_KEY" "$FEED" "pushRoundAt(int256,uint40)" "$KI_PRICE" "$T_NEXT" >/dev/null
LATEST=$(call "$FEED" "latestRound()(uint256)" | num)
send "$DEV_KEY" "$RECORDER" "recordFixing(uint40,uint80)" "$T_NEXT" "$LATEST" >/dev/null
OLD_PER_TOKEN=$PER_TOKEN
OLD_NOTE_IDX=$NOTE_IDX
send "$DEV_KEY" "$SERIES" "advance()" >/dev/null
IFS=', ' read -r PHASE REFERENCE LAST KNOCKED MISSED FIX_DONE NEXT_FIX PER_TOKEN NOTE_IDX WRITER_IDX < <(state)
echo "fixing at $KI_FIX_BPS bps of the first fixing = $(py "import sys; print(round(int(sys.argv[1]) / int(sys.argv[2]) * 100, 2))" "$KI_PRICE" "$REFERENCE")% of the reference: knockedIn $KNOCKED, fixingsDone $FIX_DONE, nothing settles"
[ "$PHASE" = 1 ] && [ "$KNOCKED" = true ] && [ "$FIX_DONE" = $((DONE + 1)) ] || fail "no knock-in"
# the release of this fixing, from the rule: m = floor(s * a / 1e18); NOTE m * (x + R), WRITER the rest of m * (1 + R)
read -r WANT_PER_TOKEN WANT_NOTE WANT_WRITER < <(py "
import sys
s, a, R, f, h = (int(x) for x in sys.argv[1:6])
m = s * a // 10**18
total = m * (10**6 + R) // 10**6
note = m * (f * 10**6 + R * h) // (h * 10**6)
print(s - m, note, total - note)" "$OLD_PER_TOKEN" "$MELT" "$RESERVE" "$KI_PRICE" "$REFERENCE")
[ "$PER_TOKEN" = "$WANT_PER_TOKEN" ] || fail "notional per token"
[ "$NOTE_IDX" = "$(py "import sys; print(int(sys.argv[1]) + int(sys.argv[2]))" "$OLD_NOTE_IDX" "$WANT_NOTE")" ] || fail "NOTE index"
[ "$WRITER_IDX" = "$WANT_WRITER" ] || fail "WRITER index"
echo "released per token (1e27): NOTE $WANT_NOTE (a x (0.575 + R)), WRITER $WANT_WRITER (a x 0.425): the hedger's cover pays"

# --- 10. released cash: claim, collect, reinvest --------------------------------------------------
step "10. The fixing's USDG: holders claim, the Desk collects, the wrapper reinvests"
claim_check() { # claim_check <name> <key> <address> <token balance> <release per token>
  local name=$1 key=$2 who=$3 balance=$4 per=$5 want b0 b1
  want=$(py "import sys; print(int(sys.argv[1]) * int(sys.argv[2]) // 10**27)" "$balance" "$per")
  [ "$(call "$SERIES" "claimable(address)(uint256)" "$who" | num)" = "$want" ] || fail "$name claimable"
  b0=$(bal "$USDG" "$who")
  send "$key" "$SERIES" "claim(address)" "$who" >/dev/null
  b1=$(bal "$USDG" "$who")
  [ $((b1 - b0)) = "$want" ] || fail "$name claim"
  echo "$name claimed $(usd "$want") USDG for $(usd "$balance") tokens"
}
claim_check "buyer (NOTE)" "$BUYER_KEY" "$BUYER" "$BUYER_NOTE" "$WANT_NOTE"
claim_check "hedger (WRITER)" "$HEDGER_KEY" "$HEDGER" "$BUY_COVER" "$WANT_WRITER"
D0=$(bal "$USDG" "$DESK")
send "$DEV_KEY" "$DESK" "collect(address)" "$SERIES" >/dev/null
D1=$(bal "$USDG" "$DESK")
echo "Desk collected $(usd $((D1 - D0))) USDG for its $(usd "$DESK_NOTE") NOTE"
[ $((D1 - D0)) = "$(py "import sys; print(int(sys.argv[1]) * int(sys.argv[2]) // 10**27)" "$DESK_NOTE" "$WANT_NOTE")" ] || fail "Desk collect"
# a fresh round after the fixing, then the wrapper reinvests what the fixing released to it
send "$DEV_KEY" "$FEED" "pushRound(int256)" $((FIRST_PRICE * AFTER_BPS / 10000)) >/dev/null
PENDING=$(call "$WRAPPER" "pendingCash()(uint256)" | num)
send "$DEV_KEY" "$WRAPPER" "compound()" >/dev/null # anyone can
WRAPPED=$(call "$WRAPPER" "totalTokens()(uint256)" | num)
echo "wrapper: $(usd "$PENDING") USDG released, reinvested at the Desk: now $(usd "$WRAPPED") NOTE for the same $SHARES shares"
[ "$WRAPPED" -gt "$WRAP_NOTE" ] || fail "the wrapper did not reinvest"
[ "$(bal "$USDG" "$WRAPPER")" -le 2 ] || fail "wrapper cash left"
desk_pos

# --- 11. unwind ---------------------------------------------------------------------------------
step "11. Unwind: unwrap, sell everything back, queue paid, LP out"
send "$BUYER_KEY" "$WRAPPER" "unwrap(uint256,address)" "$SHARES" "$BUYER" >/dev/null
LEFT=$(bal "$NOTE" "$BUYER")
echo "buyer unwrapped $SHARES shares; holds $(usd "$LEFT") NOTE"
poke
desk_quote 1 "$LEFT" 0
send "$BUYER_KEY" "$NOTE" "approve(address,uint256)" "$DESK" "$LEFT" >/dev/null
TX=$(send "$BUYER_KEY" "$DESK" "sell(address,uint256,uint256,uint16,address,address)" "$SERIES" "$LEFT" 0 0 "$DEV" "$BUYER")
check_trade "$TX" sellNote
poke
desk_quote 3 "$BUY_COVER" 0
send "$HEDGER_KEY" "$WRITER" "approve(address,uint256)" "$DESK" "$BUY_COVER" >/dev/null
TX=$(send "$HEDGER_KEY" "$DESK" "sellCover(address,uint256,uint256,uint16,address,address)" "$SERIES" "$BUY_COVER" 0 0 "$DEV" "$HEDGER")
check_trade "$TX" sellCover
send "$DEV_KEY" "$DESK" "collect(address)" "$SERIES" >/dev/null
# the wrapper's virtual shares keep a base unit of NOTE; the Desk holds the WRITER of that pair
W_DUST=$(bal "$NOTE" "$WRAPPER")
[ "$W_DUST" -le 1 ] || fail "wrapper NOTE left"
[ "$(bal "$NOTE" "$DESK")" = 0 ] && [ "$(bal "$WRITER" "$DESK")" = "$W_DUST" ] || fail "the Desk still holds tokens"
[ "$(call "$NOTE" "totalSupply()(uint256)" | num)" = "$W_DUST" ] || fail "NOTE supply"
L0=$(bal "$USDG" "$LP")
send "$DEV_KEY" "$DESK" "processQueue(uint256)" 10 >/dev/null # anyone can
CLAIMABLE=$(call "$DESK" "claimableAssets(address)(uint256)" "$LP" | num)
send "$LP_KEY" "$DESK" "claim(address)" "$LP" >/dev/null
echo "queue processed: $QUEUED shares filled for $(usd "$CLAIMABLE") USDG, claimed by the LP"
[ "$(call "$DESK" "queuedShares()(uint256)" | num)" = 0 ] || fail "queue not emptied"
MAX_REDEEM=$(call "$DESK" "maxRedeem(address)(uint256)" "$LP" | num)
send "$LP_KEY" "$DESK" "redeem(uint256,address,address)" "$MAX_REDEEM" "$LP" "$LP" >/dev/null
L1=$(bal "$USDG" "$LP")
# the Desk's last base unit of WRITER keeps a rounding sliver of shares from redeeming: under 2 base units of USDG
SHARES_LEFT=$((LP_SHARES - MAX_REDEEM - QUEUED))
[ "$SHARES_LEFT" -ge 0 ] && [ "$SHARES_LEFT" -lt 2000000 ] || fail "LP shares left: $SHARES_LEFT"
echo "LP redeemed the other $MAX_REDEEM of $LP_SHARES shares ($SHARES_LEFT left, a rounding sliver); in total $(usd $((L1 - L0))) USDG (deposited $(usd $LP_DEPOSIT); P&L $(usd $((L1 - L0 - LP_DEPOSIT))))"
B=$(bal "$USDG" "$BUYER")
H=$(bal "$USDG" "$HEDGER")
I=$(bal "$USDG" "$DEV")
echo "buyer P&L $(usd $((B - 20000000000))), hedger P&L $(usd $((H - 5000000000))), integrator fees $(usd "$I") USDG"
DUST_DESK=$(bal "$USDG" "$DESK")
DUST_SERIES=$(bal "$USDG" "$SERIES")
DUST_WRAPPER=$(bal "$USDG" "$WRAPPER")
echo "left over: Desk $DUST_DESK, series escrow $DUST_SERIES, wrapper $DUST_WRAPPER base units"
[ $((L1 + B + H + I + DUST_DESK + DUST_SERIES + DUST_WRAPPER)) = "$MINTED" ] || fail "USDG not conserved"
[ "$DUST_DESK" -le 3 ] || fail "Desk not emptied"
[ "$DUST_SERIES" -le 20 ] || fail "series escrow not emptied"
echo "USDG conserved: every base unit minted is with the LP, the buyer, the hedger, the integrator or in that dust"

step "E2E PASS: perpetual note, pricer $MODE $([ "$MODE" = stylus ] && basename "$MODEL_DIR") ($WEIGHTS)"
