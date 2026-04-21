"""
Inspect DHT capability discovery.

This debug CLI:
- starts a temporary libp2p host
- connects to one or more bootstrap peers
- asks the DHT for providers of a capability
- fetches and prints their profiles
- exits
"""

import argparse
import sys
import traceback

import trio
from libp2p.kad_dht.kad_dht import DHTMode

from src.node import Node
from src.network_utils import connect_to_bootstrap_peers


async def async_main(args) -> None:
    node = Node(
        port=args.port,
        model_name="dht-debug-client",
        capabilities=["debug"],
        dht_mode=DHTMode.CLIENT,
        enable_gossip=False,
        advertise_address_mode=args.advertise_address_mode,
    )

    async with node.host.run(listen_addrs=node.listen_addrs):
        async with node.dht_service.run():
            await connect_to_bootstrap_peers(node.host, args.bootstrap)

            provider_ids = await node.dht_service.find_capability_provider_ids(
                args.capability
            )

            print()
            print(
                f"Found {len(provider_ids)} provider id(s) "
                f"for capability={args.capability!r}"
            )

            for peer_id in provider_ids:
                print()
                print(f"peer_id: {peer_id}")

                profile = await node.dht_service.get_profile(peer_id)
                if profile is None:
                    print("profile: <not found in DHT>")
                    continue

                print(f"model: {profile.model_name}")
                print(f"capabilities: {profile.capabilities}")
                print(f"is_available: {profile.is_available}")
                print(f"timestamp_ms: {profile.timestamp_ms}")
                print("addresses:")
                for address in profile.addresses:
                    print(f"  {address}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find nodes advertising a capability through the DHT."
    )
    parser.add_argument(
        "--bootstrap",
        nargs="+",
        required=True,
        help="One or more bootstrap peer multiaddrs.",
    )
    parser.add_argument(
        "--capability",
        required=True,
        help="Capability to search for, for example: general, math, code.",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=0,
        help="Temporary local port for the debug host.",
    )
    parser.add_argument(
        "--advertise-address-mode",
        choices=["ipv6_loopback"],
        default="ipv6_loopback",
        help="Address mode for the temporary debug host.",
    )

    args = parser.parse_args()

    try:
        trio.run(async_main, args)
    except KeyboardInterrupt:
        print("\nStopped.")
    except BaseException as exc:
        print("\n=== FULL EXCEPTION ===", file=sys.stderr, flush=True)
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        sys.exit(1)


if __name__ == "__main__":
    main()
