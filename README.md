# CS498 TSADAI

Trustworthy and Scalable Architectures for Decentralized AI Systems.

Nodes advertise their capabilities through a libp2p Kademlia DHT, discover suitable peers, and forward queries to capable nodes. The default setup uses Ollama with Qwen3 1.7B, while dummy and direct Qwen backends are also available.

## Features

- libp2p peer-to-peer networking
- Kademlia DHT for profile and capability discovery
- capability routing: general, math, programming, writing, summarization, research, planning, creative
- Ollama-based capability classification at the entry node
- local peer registry with liveness tracking
- ping-based health checks
- optional GossipSub profile updates, disabled by default
- dummy, Ollama, and direct Qwen agent backends
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

Start 5 nodes:

```bash
./scripts/start_network.sh 5
```

Later for bigger tests more nodes can be started (add OLLAMA_NUM_PREDICT=32 to get shorter model output results):

```bash
OLLAMA_NUM_PREDICT=32 ./scripts/start_network.sh 50
```

The default Ollama model can be overridden:

```bash
OLLAMA_MODEL=qwen3:1.7b ./scripts/start_network.sh 8
```

Runtime files are written to:

```text
.runtime/config/bootstrap_nodes.txt
.runtime/state/pids.txt
.runtime/state/network_nodes.txt
.runtime/logs/nodes/
.runtime/ui/conversations.json
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

Simple web UI:

```bash
python -m src.ui.web_app
```

Then open:

```text
http://127.0.0.1:8000
```

Simple desktop UI:

```bash
python -m src.ui.chat_app
```

Both UIs read bootstrap peers from `.runtime/config/bootstrap_nodes.txt`, let you select
an entry node, and sends chat prompts through the same client path as
`scripts/query_any.sh`. Conversations are saved locally in
`.runtime/ui/conversations.json`; the network still receives bounded context for
the active conversation instead of the full chat history. The web UI renders common
Markdown elements such as headings, lists, inline code, and code blocks.

Bootstrap file format:

```text
/ip6/::1/tcp/8002/p2p/<peer-id>
```

The first UI run can seed `.runtime/config/bootstrap_nodes.txt` from the local
`.runtime/state/network_nodes.txt` file for convenience. After that, the UI uses the
bootstrap file as its entry-node source. DHT discovery remains internal to the
existing routing path.

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
cat .runtime/state/network_nodes.txt
```

Find providers for a capability:

```bash
python -m src.cli.find_nodes \
  --bootstrap "$(awk '$1 == 0 {print $5}' .runtime/state/network_nodes.txt)" \
  --capability math
```

## Manual Node Options

Run one node manually with Ollama:
(`--bootstrap "<node-0-address>"` can be omitted if it's the first node that you run)

```bash
python -m src.cli.run_node \
  --port 8003 \
  --seed 1001 \
  --model-name node-1-math \
  --capabilities math \
  --dht-mode server \
  --bootstrap "<node-0-address>" \
  --advertise-address-mode ipv6_loopback \
  --agent-backend ollama \
  --ollama-model qwen3:1.7b \
  --ollama-host http://localhost:11434 \
  --ollama-system-prompt "You are a concise mathematics specialist."
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
qwen
```

## Routing Summary

For each query, the entry node classifies the required capability with Ollama, stores it in the query context, queries the DHT for providers, caches candidates locally, checks liveness, and forwards to a suitable peer. Forwarded nodes reuse the capability from the query context instead of classifying again.

The DHT returns candidate providers, not necessarily every matching node.

## Debugging

To check if there's no processes running on those ports:

```bash
netstat -ano | grep -E "8002|8003|8004|8005|8006"
```
