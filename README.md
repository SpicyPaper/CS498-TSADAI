# CS498 TSADAI

Trustworthy and Scalable Architectures for Decentralized AI Systems.

This project runs a local network of libp2p nodes. Each node advertises its
capabilities through a Kademlia DHT; an entry node classifies a query, discovers
suitable peers, and either answers locally or forwards the query to a better
matching node.

The recommended backend is Ollama with Qwen3 1.7B. The project can also use the
EPFL AIaaS API, local Hugging Face Transformers models, or a dummy backend for
tests.

## Features

- libp2p peer-to-peer networking with Kademlia DHT discovery
- capability routing for general, math, programming, writing, summarization,
  research, planning, and creative tasks
- LLM-based capability classification at the entry node
- simulated per-node capability scores for predictable local demos
- Ollama, AIaaS, local Hugging Face, and dummy backends
- HTTP APIs, a small web UI, and CLI scripts for local testing

## Requirements

- Python >= 3.13
- uv
- Ollama, for the recommended backend

Install Ollama from:

```text
https://ollama.com/download
```

Start Ollama before running the network by opening the Ollama app or running:

```bash
ollama serve
```

Then check that the API responds:

```bash
curl http://localhost:11434/api/tags
```

If an Ollama model is missing, `scripts/start_network.sh` pulls it automatically
from the running Ollama server.

## Setup

```bash
pip install uv
uv sync
source .venv/Scripts/activate
cp .env.example .env
```

Open `.env` and choose backends:

```text
REQUEST_BACKEND=ollama
CLASSIFIER_BACKEND=ollama
```

Available backends:

```text
ollama  local Ollama server
aiass   EPFL AIaaS OpenAI-compatible API
local   local Hugging Face model through Transformers
dummy   placeholder backend for tests
```

If either backend is `aiass`, set `AIASS_API_KEY`.

If either backend is `local`, each node process loads its own LLM. Keep
`NUM_NODES` small, especially with larger models, or use a shared backend such
as Ollama or AIaaS.

Useful `.env` values:

```text
NUM_NODES=8         number of local nodes started by the script
BASE_PORT=8002      first libp2p port
API_BASE_PORT=9002  first node HTTP API port
WEB_PORT=8000       web UI port
```

## Run

Start the local network:

```bash
./scripts/start_network.sh
```

Start a specific number of nodes:

```bash
./scripts/start_network.sh 4
```

Stop the network:

```bash
./scripts/stop_network.sh
```

Runtime files are written under `.runtime/`, especially:

```text
.runtime/nodes/state/known_nodes.txt
.runtime/nodes/logs/node_<i>.log
.runtime/web/config/bootstrap_nodes.txt
```

## Send Queries

Send a query through node 0:

```bash
./scripts/query_any.sh 0 "Solve 2x + 4 = 10."
```

More examples:

```bash
./scripts/query_any.sh 0 "Write a Python function that reverses a list."
./scripts/query_any.sh 0 "Rewrite this sentence to sound professional: send me the file now."
./scripts/query_any.sh 0 "Make me a 3-step study plan for distributed systems."
```

The entry node classifies the query, scores itself and discovered peers, then
answers locally or forwards the request.

## Web UI

Start the FastAPI web gateway:

```bash
python -m src.ui.web_app
```

Then open:

```text
http://127.0.0.1:8000
```

The web UI reads entry-node API URLs from
`.runtime/web/config/bootstrap_nodes.txt`. The local network script writes the
first node API there automatically when the file is empty.

## Useful Commands

View started nodes:

```bash
cat .runtime/nodes/state/known_nodes.txt
```

Find providers for a capability:

```bash
python -m src.cli.find_nodes \
  --bootstrap "$(awk '$1 == 0 {print $5}' .runtime/nodes/state/known_nodes.txt)" \
  --capability math
```

Run one node manually:

```bash
python -m src.cli.run_node \
  --port 8003 \
  --api-port 9003 \
  --seed 1001 \
  --model-name node-1-math \
  --capabilities math \
  --dht-mode server \
  --bootstrap "<node-0-address>" \
  --system-prompt "You are a concise mathematics specialist."
```

Other CLIs:

```text
python -m src.cli.client_query  send one query to an entry node
python -m src.cli.send_message  low-level ping/query test
```

## Notes

- Nodes advertise only their top scored capabilities in the DHT. Routers fetch a
  peer's full profile directly when they need detailed scores.
- GossipSub support exists but is still experimental; leave it disabled for
  normal local runs.
- If startup fails, check `.runtime/nodes/logs/node_<i>.log` first.
