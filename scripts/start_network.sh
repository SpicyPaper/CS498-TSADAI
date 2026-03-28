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
#   .network/pids.txt
#   .network/network_nodes.txt
#   .network/logs/node_<i>.log

NUM_NODES="${1:-5}"

if ! [[ "$NUM_NODES" =~ ^[0-9]+$ ]] || [ "$NUM_NODES" -lt 1 ]; then
  echo "Usage: $0 <num_nodes>=1.."
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NETWORK_DIR="$ROOT_DIR/.network"
LOG_DIR="$NETWORK_DIR/logs"

mkdir -p "$LOG_DIR"
: > "$NETWORK_DIR/pids.txt"
: > "$NETWORK_DIR/network_nodes.txt"

# Stop previous network if still running.
if [ -f "$NETWORK_DIR/pids.txt" ]; then
  while read -r pid; do
    [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null || true
  done < "$NETWORK_DIR/pids.txt"
fi
: > "$NETWORK_DIR/pids.txt"

# Capability pool.
# Duplicates are intentional so you can have 1-2+ nodes with the same capability.
CAPS_POOL=(
  "general"
  "math"
  "code"
  "general"
  "math"
  "code"
  "general"
  "reasoning"
  "general"
)

BASE_PORT=8002

echo "Starting $NUM_NODES nodes..."
echo

# Start node 0 first so others can bootstrap to it.
PORT0=$BASE_PORT
SEED0=1000
CAP0="general"
MODEL0="node-0-entry"

LOG0="$LOG_DIR/node_0.log"
python -m src.cli.run_node \
  --port "$PORT0" \
  --seed "$SEED0" \
  --capabilities "$CAP0" \
  --model-name "$MODEL0" \
  --advertise-address-mode ipv6_loopback \
  > "$LOG0" 2>&1 &

PID0=$!
echo "$PID0" >> "$NETWORK_DIR/pids.txt"

echo "Started node 0 (pid=$PID0, port=$PORT0, caps=$CAP0)"
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
echo "0 $PORT0 $CAP0 $MODEL0 $ENTRY_ADDR" >> "$NETWORK_DIR/network_nodes.txt"

echo "Bootstrap node address:"
echo "  $ENTRY_ADDR"
echo

# Start remaining nodes.
for ((i=1; i<NUM_NODES; i++)); do
  PORT=$((BASE_PORT + i))
  SEED=$((1000 + i))
  CAP="${CAPS_POOL[$((i % ${#CAPS_POOL[@]}))]}"
  MODEL="node-${i}-${CAP}"

  LOG_FILE="$LOG_DIR/node_${i}.log"

  python -m src.cli.run_node \
    --port "$PORT" \
    --seed "$SEED" \
    --capabilities "$CAP" \
    --model-name "$MODEL" \
    --bootstrap "$ENTRY_ADDR" \
    --advertise-address-mode ipv6_loopback \
    > "$LOG_FILE" 2>&1 &

  PID=$!
  echo "$PID" >> "$NETWORK_DIR/pids.txt"

  echo "Started node $i (pid=$PID, port=$PORT, caps=$CAP)"

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

  echo "$i $PORT $CAP $MODEL $ADDR" >> "$NETWORK_DIR/network_nodes.txt"
done

echo
echo "Network started."
echo
echo "Available nodes:"
column -t "$NETWORK_DIR/network_nodes.txt" || cat "$NETWORK_DIR/network_nodes.txt"
echo
echo "Logs:"
echo "  $LOG_DIR"
echo
echo "PIDs:"
echo "  $NETWORK_DIR/pids.txt"