#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$ROOT_DIR/.env" ]; then
  echo "ERROR: missing .env file at $ROOT_DIR/.env"
  echo "Create it before preparing Ollama."
  exit 1
fi

set -a
# shellcheck disable=SC1091
. "$ROOT_DIR/.env"
set +a

if [ -z "${OLLAMA_MODEL:-}" ]; then
  echo "ERROR: missing OLLAMA_MODEL in .env"
  exit 1
fi

if [ -z "${OLLAMA_HOST:-}" ]; then
  echo "ERROR: missing OLLAMA_HOST in .env"
  exit 1
fi

OLLAMA_HOST_URL="${OLLAMA_HOST_URL:-$OLLAMA_HOST}"

echo "Checking Ollama..."

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is not installed or not in PATH."
  echo "Install it from: https://ollama.com/download"
  exit 1
fi

echo "Ollama found."

echo "Checking if Ollama API is running at $OLLAMA_HOST_URL..."

if ! curl -fsS "$OLLAMA_HOST_URL/api/tags" >/dev/null 2>&1; then
  echo "Ollama API is not responding."
  echo
  echo "Start Ollama in another terminal with:"
  echo "  ollama serve"
  echo
  echo "Or open the Ollama desktop app if you installed it that way."
  exit 1
fi

echo "Ollama API is running."

echo "Checking model: $OLLAMA_MODEL"

if ! ollama list | awk '{print $1}' | grep -qx "$OLLAMA_MODEL"; then
  echo "Model not found locally. Pulling $OLLAMA_MODEL..."
  ollama pull "$OLLAMA_MODEL"
else
  echo "Model already installed."
fi

echo
echo "Testing $OLLAMA_MODEL..."

curl -fsS "$OLLAMA_HOST_URL/api/generate" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$OLLAMA_MODEL\",
    \"prompt\": \"Answer in one short sentence: what is a DHT?\",
    \"stream\": false,
    \"think\": false,
    \"options\": {
      \"num_predict\": 80,
      \"temperature\": 0.7,
      \"top_p\": 0.8
    }
  }" | python -m json.tool

echo
echo "Ollama backend ready:"
echo "  model: $OLLAMA_MODEL"
echo "  api:   $OLLAMA_HOST_URL/api/generate"
