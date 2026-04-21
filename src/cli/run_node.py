"""
Start one node and keep it alive.
"""

import argparse
import sys
import traceback

import trio
from libp2p.kad_dht.kad_dht import DHTMode

from src.node import Node


async def async_main(args):
    capabilities = [c.strip() for c in args.capabilities.split(",") if c.strip()]

    dht_mode = DHTMode.SERVER if args.dht_mode == "server" else DHTMode.CLIENT
    node = Node(
        port=args.port,
        seed=args.seed,
        model_name=args.model_name,
        capabilities=capabilities,
        dht_mode=dht_mode,
        advertise_address_mode=args.advertise_address_mode,
        enable_gossip=args.enable_gossip,
        agent_backend=args.agent_backend,
        llm_model_id=args.llm_model_id,
        llm_max_new_tokens=args.llm_max_new_tokens,
        llm_enable_thinking=args.llm_enable_thinking,
        ollama_model=args.ollama_model,
        ollama_host=args.ollama_host,
        ollama_num_predict=args.ollama_num_predict,
        ollama_system_prompt=args.ollama_system_prompt,
    )

    await node.run_forever(bootstrap_addrs=args.bootstrap)


def main():
    parser = argparse.ArgumentParser(description="Run one libp2p node.")
    # General args
    parser.add_argument("-p", "--port", type=int, default=0)
    parser.add_argument("-s", "--seed", type=int, default=None)
    parser.add_argument("--model-name", type=str, default="dummy-model")
    parser.add_argument("--capabilities", type=str, default="general")
    parser.add_argument("--dht-mode", choices=["server", "client"], default="server")
    parser.add_argument("--bootstrap", nargs="*", default=[])
    parser.add_argument(
        "--advertise-address-mode",
        choices=["ipv6_loopback"],
        default="ipv6_loopback",
        help="Which address family/mode to advertise in the node profile.",
    )
    parser.add_argument(
        "--enable-gossip",
        action="store_true",
        help="Run the node with GossipSub. DHT discovery still works.",
    )

    # Models args
    parser.add_argument(
        "--agent-backend",
        choices=["dummy", "ollama", "qwen"],
        default="dummy",
    )
    parser.add_argument(
        "--llm-model-id",
        type=str,
        default="Qwen/Qwen3-0.6B",
    )
    parser.add_argument(
        "--llm-max-new-tokens",
        type=int,
        default=128,
    )
    parser.add_argument(
        "--llm-enable-thinking",
        action="store_true",
        help="Enable Qwen3 thinking mode.",
    )
    parser.add_argument(
        "--ollama-model",
        type=str,
        default="qwen3:0.6b",
    )
    parser.add_argument(
        "--ollama-host",
        type=str,
        default="http://localhost:11434",
    )
    parser.add_argument(
        "--ollama-num-predict",
        type=int,
        default=128,
    )
    parser.add_argument(
        "--ollama-system-prompt",
        type=str,
        default=None,
    )

    args = parser.parse_args()

    try:
        trio.run(async_main, args)
    except BaseException as exc:
        print("\n=== FULL EXCEPTION ===", file=sys.stderr, flush=True)
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        sys.exit(1)


if __name__ == "__main__":
    main()
