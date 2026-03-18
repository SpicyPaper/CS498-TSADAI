"""
Start one node and keep it alive.
"""

import argparse
import sys

import trio

from src.node import Node


async def async_main(args):
    capabilities = [c.strip() for c in args.capabilities.split(",") if c.strip()]

    node = Node(
        port=args.port,
        seed=args.seed,
        model_name=args.model_name,
        capabilities=capabilities,
    )

    # Optional manually known peer
    if args.known_peer_id and args.known_peer_addr:
        peer_capabilities = [
            c.strip() for c in args.known_peer_capabilities.split(",") if c.strip()
        ]
        node.register_known_peer(
            peer_id=args.known_peer_id,
            addresses=[args.known_peer_addr],
            model_name=args.known_peer_model,
            capabilities=peer_capabilities,
        )

    await node.run_forever()


def main():
    parser = argparse.ArgumentParser(description="Run one libp2p node.")
    parser.add_argument("-p", "--port", type=int, default=0)
    parser.add_argument("-s", "--seed", type=int, default=None)
    parser.add_argument("--model-name", type=str, default="dummy-model")
    parser.add_argument("--capabilities", type=str, default="general")

    parser.add_argument("--known-peer-id", type=str, default=None)
    parser.add_argument("--known-peer-addr", type=str, default=None)
    parser.add_argument("--known-peer-model", type=str, default="remote-model")
    parser.add_argument("--known-peer-capabilities", type=str, default="general")

    args = parser.parse_args()

    try:
        trio.run(async_main, args)
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
