"""
Start one node and keep it alive.
"""

import argparse
import sys

import trio

from src.node import Node


async def async_main(args):
    node = Node(port=args.port, seed=args.seed)
    await node.run_forever()


def main():
    parser = argparse.ArgumentParser(description="Start one libp2p node.")
    parser.add_argument("-p", "--port", type=int, default=0, help="Listening port")
    parser.add_argument(
        "-s", "--seed", type=int, default=None, help="Optional deterministic seed"
    )
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
