#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./scripts/start_network.sh 5
#
# Starts N nodes.
# Node 0 is the bootstrap / entry-friendly node.
# Other nodes bootstrap to node 0.
#
# Outputs:
#   .runtime/nodes/state/pids.txt
#   .runtime/nodes/state/known_nodes.txt
#   .runtime/nodes/logs/node_<i>.log

score_from_seed() {
  local seed="$1"
  local offset="$2"
  awk -v s="$seed" -v o="$offset" 'BEGIN {
    srand(s + o)
    printf "%.2f", rand()
  }'
}

NUM_NODES="${1:-5}"

if ! [[ "$NUM_NODES" =~ ^[0-9]+$ ]] || [ "$NUM_NODES" -lt 1 ]; then
  echo "Usage: $0 <num_nodes>=1.."
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/.runtime"
NODES_RUNTIME_DIR="$RUNTIME_DIR/nodes"
WEB_RUNTIME_DIR="$RUNTIME_DIR/web"
STATE_DIR="$NODES_RUNTIME_DIR/state"
WEB_CONFIG_DIR="$WEB_RUNTIME_DIR/config"
LOG_DIR="$NODES_RUNTIME_DIR/logs"
PID_FILE="$STATE_DIR/pids.txt"
NETWORK_FILE="$STATE_DIR/known_nodes.txt"
WEB_BOOTSTRAP_FILE="$WEB_CONFIG_DIR/bootstrap_nodes.txt"

mkdir -p "$STATE_DIR" "$WEB_CONFIG_DIR" "$LOG_DIR"

# Stop previous network if still running.
if [ -f "$PID_FILE" ]; then
  while read -r pid; do
    [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null || true
  done < "$PID_FILE"
fi
: > "$PID_FILE"
: > "$NETWORK_FILE"

# Capability pool.
CAPS_POOL=(
  "general"
  "math"
  "programming"
  "writing"
  "summarization"
  "research"
  "planning"
  "creative"
)


BASE_PORT=8002
API_BASE_PORT=9002
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:1.7b}"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

echo "Checking Ollama backend..."
if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required to check the Ollama API before starting nodes."
  exit 1
fi

if ! curl -fsS "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
  echo "ERROR: Ollama API is not reachable at ${OLLAMA_HOST}."
  echo "Start it with:"
  echo "  ollama serve"
  echo "Or open the Ollama desktop app, then rerun this script."
  exit 1
fi

if ! curl -fsS "${OLLAMA_HOST}/api/tags" \
  | python -c "import json,sys; data=json.load(sys.stdin); names={m.get('name') for m in data.get('models', [])}; sys.exit(0 if '${OLLAMA_MODEL}' in names else 1)" \
  >/dev/null 2>&1; then
  echo "ERROR: Ollama is running, but model '${OLLAMA_MODEL}' is not installed."
  echo "Install it with:"
  echo "  ollama pull ${OLLAMA_MODEL}"
  exit 1
fi

echo "Ollama ready: ${OLLAMA_HOST} model=${OLLAMA_MODEL}"
echo

echo "Starting $NUM_NODES nodes..."
echo

# Start node 0 first so others can bootstrap to it.
PORT0=$BASE_PORT
API_PORT0=$API_BASE_PORT
SEED0=1000
CAP0="general"
MODEL0="node-0-entry"
GENERAL0=$(score_from_seed "$SEED0" 1)
GENERAL0=$(awk -v x="$GENERAL0" 'BEGIN { printf "%.2f", 0.45 + x * 0.25 }')
CAPABILITY_SCORES0="{\"general\":${GENERAL0}}"

SYSTEM_PROMPT0="You are a helpful general assistant. Answer clearly and concisely."

LOG0="$LOG_DIR/node_0.log"
python -m src.cli.run_node \
  --port "$PORT0" \
  --api-port "$API_PORT0" \
  --seed "$SEED0" \
  --capabilities "$CAP0" \
  --capability-scores "$CAPABILITY_SCORES0" \
  --model-name "$MODEL0" \
  --advertise-address-mode ipv6_loopback \
  --agent-backend ollama \
  --ollama-model "$OLLAMA_MODEL" \
  --ollama-host "$OLLAMA_HOST" \
  --ollama-timeout "${OLLAMA_TIMEOUT:-300}" \
  --ollama-num-predict "${OLLAMA_NUM_PREDICT:-512}" \
  --ollama-system-prompt "$SYSTEM_PROMPT0" \
  --classifier-timeout "${CLASSIFIER_TIMEOUT:-60}" \
  --query-timeout "${QUERY_TIMEOUT:-60}" \
  --query-connect-timeout "${QUERY_CONNECT_TIMEOUT:-3}" \
  > "$LOG0" 2>&1 &

PID0=$!
echo "$PID0" >> "$PID_FILE"

echo "Started node 0 (pid=$PID0, port=$PORT0, api=$API_PORT0, caps=$CAP0)"
echo "Waiting for node 0 peer id..."

ENTRY_PEER_ID=""
for _ in $(seq 1 50); do
  if grep -q "^\[.*\] \[NODE\] I am " "$LOG0"; then
    ENTRY_PEER_ID="$(grep -m1 "^\[.*\] \[NODE\] I am " "$LOG0" | sed -E 's/.*I am ([^ ]+).*/\1/')"
    break
  fi
  sleep 0.5
done

if [ -z "$ENTRY_PEER_ID" ]; then
  echo "Failed to read node 0 peer id from $LOG0"
  exit 1
fi

ENTRY_ADDR="/ip6/::1/tcp/${PORT0}/p2p/${ENTRY_PEER_ID}"
echo "0 $PORT0 $CAP0 $MODEL0 $ENTRY_ADDR http://127.0.0.1:${API_PORT0}" >> "$NETWORK_FILE"
echo "http://127.0.0.1:${API_PORT0}" > "$WEB_BOOTSTRAP_FILE"

echo "Bootstrap node address:"
echo "  $ENTRY_ADDR"
echo "Bootstrap node API:"
echo "  http://127.0.0.1:${API_PORT0}/api/query"
echo

# Start remaining nodes.
for ((i=1; i<NUM_NODES; i++)); do
  PORT=$((BASE_PORT + i))
  API_PORT=$((API_BASE_PORT + i))
  SEED=$((1000 + i))
  CAP="${CAPS_POOL[$((i % ${#CAPS_POOL[@]}))]}"
  MODEL="node-${i}-${CAP}"

  MAIN_RAND=$(score_from_seed "$SEED" 1)
  GENERAL_RAND=$(score_from_seed "$SEED" 2)
  SECONDARY_RAND=$(score_from_seed "$SEED" 3)

  MAIN_SCORE=$(awk -v x="$MAIN_RAND" 'BEGIN { printf "%.2f", 0.78 + x * 0.17 }')
  GENERAL_SCORE=$(awk -v x="$GENERAL_RAND" 'BEGIN { printf "%.2f", 0.45 + x * 0.20 }')
  SECONDARY_SCORE=$(awk -v x="$SECONDARY_RAND" 'BEGIN { printf "%.2f", 0.20 + x * 0.25 }')

  case "$CAP" in
    math)
      CAPABILITY_SCORES="{\"math\":${MAIN_SCORE},\"general\":${GENERAL_SCORE},\"research\":${SECONDARY_SCORE}}"
      SYSTEM_PROMPT="You are a concise mathematics specialist. Show the key reasoning steps and avoid unsupported claims."
      ;;
    programming)
      CAPABILITY_SCORES="{\"programming\":${MAIN_SCORE},\"general\":${GENERAL_SCORE},\"research\":${SECONDARY_SCORE}}"
      SYSTEM_PROMPT="You are a careful programming assistant. Prefer correct, minimal code and mention important assumptions."
      ;;
    writing)
      CAPABILITY_SCORES="{\"writing\":${MAIN_SCORE},\"general\":${GENERAL_SCORE},\"summarization\":${SECONDARY_SCORE}}"
      SYSTEM_PROMPT="You are a writing specialist. Improve clarity, tone, structure, and correctness."
      ;;
    summarization)
      CAPABILITY_SCORES="{\"summarization\":${MAIN_SCORE},\"general\":${GENERAL_SCORE},\"writing\":${SECONDARY_SCORE}}"
      SYSTEM_PROMPT="You are a summarization specialist. Preserve key facts and remove unnecessary detail."
      ;;
    research)
      CAPABILITY_SCORES="{\"research\":${MAIN_SCORE},\"general\":${GENERAL_SCORE},\"writing\":${SECONDARY_SCORE}}"
      SYSTEM_PROMPT="You are a research assistant. Be factual, structured, and explicit about uncertainty."
      ;;
    planning)
      CAPABILITY_SCORES="{\"planning\":${MAIN_SCORE},\"general\":${GENERAL_SCORE},\"research\":${SECONDARY_SCORE}}"
      SYSTEM_PROMPT="You are a planning specialist. Break goals into clear, practical steps."
      ;;
    creative)
      CAPABILITY_SCORES="{\"creative\":${MAIN_SCORE},\"general\":${GENERAL_SCORE},\"writing\":${SECONDARY_SCORE}}"
      SYSTEM_PROMPT="You are a creative assistant. Generate vivid, original, coherent ideas."
      ;;
    *)
      CAPABILITY_SCORES="{\"general\":${GENERAL_SCORE}}"
      SYSTEM_PROMPT="You are a helpful general assistant. Answer clearly and concisely."
      ;;
  esac

  LOG_FILE="$LOG_DIR/node_${i}.log"

  python -m src.cli.run_node \
    --port "$PORT" \
    --api-port "$API_PORT" \
    --seed "$SEED" \
    --capabilities "$CAP" \
    --capability-scores "$CAPABILITY_SCORES" \
    --model-name "$MODEL" \
    --bootstrap "$ENTRY_ADDR" \
    --advertise-address-mode ipv6_loopback \
    --agent-backend ollama \
    --ollama-model "$OLLAMA_MODEL" \
    --ollama-host "$OLLAMA_HOST" \
    --ollama-timeout "${OLLAMA_TIMEOUT:-300}" \
    --ollama-num-predict "${OLLAMA_NUM_PREDICT:-512}" \
    --ollama-system-prompt "$SYSTEM_PROMPT" \
    --classifier-timeout "${CLASSIFIER_TIMEOUT:-60}" \
    --query-timeout "${QUERY_TIMEOUT:-60}" \
    --query-connect-timeout "${QUERY_CONNECT_TIMEOUT:-3}" \
    > "$LOG_FILE" 2>&1 &

  PID=$!
  echo "$PID" >> "$PID_FILE"

  echo "Started node $i (pid=$PID, port=$PORT, api=$API_PORT, caps=$CAP)"

  PEER_ID=""
  for _ in $(seq 1 50); do
    if grep -q "^\[.*\] \[NODE\] I am " "$LOG_FILE"; then
      PEER_ID="$(grep -m1 "^\[.*\] \[NODE\] I am " "$LOG_FILE" | sed -E 's/.*I am ([^ ]+).*/\1/')"
      break
    fi
    sleep 0.5
  done

  if [ -z "$PEER_ID" ]; then
    echo "Warning: could not read peer id for node $i yet"
    ADDR="/ip6/::1/tcp/${PORT}/p2p/<unknown-yet>"
  else
    ADDR="/ip6/::1/tcp/${PORT}/p2p/${PEER_ID}"
  fi

  echo "$i $PORT $CAP $MODEL $ADDR http://127.0.0.1:${API_PORT}" >> "$NETWORK_FILE"
done

echo
echo "Network started."
echo
echo "Available nodes:"
column -t "$NETWORK_FILE" || cat "$NETWORK_FILE"
echo
echo "Logs:"
echo "  $LOG_DIR"
echo
echo "PIDs:"
echo "  $PID_FILE"
