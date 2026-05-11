# CS498 TSADAI

Trustworthy and Scalable Architectures for Decentralized AI Systems.

Nodes advertise their capabilities through a libp2p Kademlia DHT, discover suitable peers, and forward queries to capable nodes. The default setup uses Ollama with Qwen3 1.7B; the optional local Hugging Face and AIaaS backends also default to Qwen3 1.7B, while the dummy backend is available for tests.

## Features

- libp2p peer-to-peer networking
- Kademlia DHT for capability discovery
- capability routing: general, math, programming, writing, summarization, research, planning, creative
- LLM-based capability classification at the entry node
- local peer registry with liveness tracking
- ping-based health checks
- optional GossipSub profile updates, disabled by default
- dummy, Ollama, local Hugging Face, and AIaaS agent backends
- local network scripts and DHT inspection CLI

## Requirements

- Python >= 3.13
- uv
- Ollama for the recommended LLM backend

## Setup

```bash
pip install uv
uv sync
source .venv/Scripts/activate
```

Install Ollama from:

```text
https://ollama.com/download
```

Pull the default model:

```bash
ollama pull qwen3:1.7b
```

Check that Ollama is running:

```bash
curl http://localhost:11434/api/tags
```

## Run a Local Network

Start the local network with the node count configured by `NUM_NODES` in `.env`:

```bash
./scripts/start_network.sh
```

Change `NUM_NODES` in `.env` when you need a smaller or larger network.

## Configuration

Create your local configuration by copying `.env.example` to `.env`:

```bash
cp .env.example .env
```

Then open `.env` and choose how you want to run the project by setting
`REQUEST_BACKEND` and `CLASSIFIER_BACKEND` directly there:

```text
ollama  local Ollama server
aiass   EPFL AIaaS API
local   local Hugging Face model through transformers
dummy   placeholder backend for tests
```

If either backend is `aiass`, set `AIASS_API_KEY` in `.env`. The request model
and classifier model can be configured separately for Ollama, local
Transformers, and AIaaS. Change the other fields only if you want different
ports, output lengths, or timeouts. The scripts and Python CLIs load `.env`
automatically.

For local development, the default ports in `.env.example` mean:

```text
BASE_PORT=8002       node 0 listens on libp2p port 8002, node 1 on 8003, etc.
API_BASE_PORT=9002   node 0 exposes HTTP on 9002, node 1 on 9003, etc.
API_HOST=127.0.0.1   node HTTP APIs are reachable only from this machine
WEB_HOST=127.0.0.1   the web UI is reachable only from this machine
WEB_PORT=8000        open the web UI at http://127.0.0.1:8000
```

Forwarding uses two peer timeouts: `QUERY_CONNECT_TIMEOUT` controls opening the
libp2p stream, and `QUERY_TIMEOUT` controls how long the router waits for a
selected node to generate its response. The UI and one-shot client use
`CLIENT_QUERY_TIMEOUT`; it should always be larger than the worst expected
network request.

With the values in `.env.example`, one classification plus two forwarding
attempts gives:

```text
CLASSIFIER_TIMEOUT + 2 * (QUERY_CONNECT_TIMEOUT + QUERY_TIMEOUT)
= 60 + 2 * (2 + 60)
= 184 seconds
```

So `CLIENT_QUERY_TIMEOUT=300` gives enough room for classification, one failed
or slow routed node, one reroute, and some overhead without making the UI wait
forever.

Runtime files are written to:

```text
.runtime/nodes/state/pids.txt
.runtime/nodes/state/known_nodes.txt
.runtime/nodes/logs/node_<i>.log
.runtime/web/config/bootstrap_nodes.txt
.runtime/web/conversations.json
```

For local networks started by the script, each node also exposes a small HTTP
query API. The libp2p ports start at `BASE_PORT`; the HTTP API ports start at
`API_BASE_PORT`.
The node state file uses this format:

```text
index libp2p_port capability model multiaddr api_url
```

The web app calls:

```text
POST http://<API_HOST>:<api-port>/api/query
GET  http://<API_HOST>:<api-port>/api/query/progress/<query_id>
```

Stop the network:

```bash
./scripts/stop_network.sh
```

## Scripts and CLIs

Main scripts:

```text
scripts/start_network.sh   start a local network
scripts/stop_network.sh    stop the local network
scripts/query_any.sh       send a query to a node index
scripts/start_ollama.sh    check Ollama and the default model
```

Useful CLIs:

```
python -m src.cli.run_node      run one node manually
python -m src.cli.client_query  send one query to an entry node
python -m src.cli.find_nodes    inspect DHT capability providers
python -m src.cli.send_message  low-level ping/query test
```

FastAPI web gateway and UI:

```bash
python -m src.ui.web_app
```

Then open:

```text
http://<WEB_HOST>:<WEB_PORT>
```

The web UI reads bootstrap entry nodes from `.runtime/web/config/bootstrap_nodes.txt`,
lets you select one, and sends chat prompts through that node HTTP API.
Conversations are saved locally in
`.runtime/web/conversations.json`; the network still receives bounded context for
the active conversation instead of the full chat history. While a request is
running, the web UI polls the gateway, which relays live progress events exposed
by the selected node API. The full routing trace is still shown when the response
arrives. The web UI renders common Markdown elements such as headings, lists,
inline code, and code blocks.

Web bootstrap file format:

```text
http://<API_HOST>:<API_BASE_PORT>
```

The local network script writes the first node API URL there for convenience.
DHT discovery remains internal to the existing routing path.

## Send Queries

Send a math query through node 0:

```bash
./scripts/query_any.sh 0 "Solve 2x + 4 = 10."
```

Send a programming query:

```bash
./scripts/query_any.sh 0 "python: write a function that reverses a list"
```

The entry node classifies the query capability, then may answer locally or forward the query to a better capability match.

Nodes advertise only their strong `advertised_capabilities` in the DHT. When a
router needs more detail, it opens a direct profile request to the provider and
fetches the full profile, including `capability_scores`.

Useful capability test prompts:

```bash
./scripts/query_any.sh 0 "What is the capital of France?"
./scripts/query_any.sh 0 "Write a Python function that reverses a list."
./scripts/query_any.sh 0 "Rewrite this sentence to sound professional: send me the file now."
./scripts/query_any.sh 0 "Summarize this in one sentence: DHTs distribute records across peers so no central registry is needed."
./scripts/query_any.sh 0 "Compare DHT and GossipSub briefly."
./scripts/query_any.sh 0 "Make me a 3-step study plan for distributed systems."
./scripts/query_any.sh 0 "Write a short sci-fi story about a lost satellite."
./scripts/query_any.sh 0 "Solve 2x + 4 = 10."
```

## Inspect the DHT

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

## Manual Node Options

Run one node manually:
(`--bootstrap "<node-0-address>"` can be omitted if it's the first node that you run)

The backend, models, timeouts, and address mode come from `.env`.

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

Enable GossipSub manually:
(WIP feature for the moment, do not run with it)

```bash
--enable-gossip
```

Available agent backends:

```text
dummy
ollama
local
aiass
```

## Routing Summary

For each query, the entry node classifies the required capability with
`CLASSIFIER_BACKEND`, stores it in the query context, queries the DHT for
providers, caches candidates locally, checks liveness, and forwards to a
suitable peer. The selected node answers with `REQUEST_BACKEND`. Forwarded nodes
reuse the capability from the query context instead of classifying again.

The DHT returns providers for advertised capabilities. Routing then opens a
direct profile request to each provider and scores candidates with the full
`capability_scores` map returned by the peer.

## Debugging

To check if there's no processes running on those ports:

```bash
netstat -ano | grep -E "8002|8003|8004|8005|8006"
```
