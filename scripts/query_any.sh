
#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./scripts/query_any.sh 0 "do some math please"
#   ./scripts/query_any.sh 3 "write some python code"

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <node_index> <prompt>"
  exit 1
fi

NODE_INDEX="$1"
shift
PROMPT="$*"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NETWORK_FILE="$ROOT_DIR/.network/network_nodes.txt"

if [ ! -f "$NETWORK_FILE" ]; then
  echo "Network file not found: $NETWORK_FILE"
  echo "Start the network first."
  exit 1
fi

ENTRY_ADDR="$(awk -v idx="$NODE_INDEX" '$1 == idx {print $5}' "$NETWORK_FILE")"

if [ -z "$ENTRY_ADDR" ]; then
  echo "Could not find node index $NODE_INDEX in $NETWORK_FILE"
  exit 1
fi

if [[ "$ENTRY_ADDR" == *"<unknown-yet>"* ]]; then
  echo "Node $NODE_INDEX does not have a resolved peer id yet."
  exit 1
fi

python -m src.cli.client_query \
  --entry-node "$ENTRY_ADDR" \
  --prompt "$PROMPT"