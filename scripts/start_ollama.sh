#!/usr/bin/env bash
set -euo pipefail

OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:1.7b}"
OLLAMA_HOST_URL="${OLLAMA_HOST_URL:-http://localhost:11434}"

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
