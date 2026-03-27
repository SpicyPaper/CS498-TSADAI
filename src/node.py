import random
import secrets

import time
import trio
from libp2p import new_host
from libp2p.crypto.secp256k1 import create_new_key_pair
from libp2p.kad_dht.kad_dht import DHTMode
from libp2p.utils.address_validation import (
    find_free_port,
    get_available_interfaces,
)

from src.local_agent import DummyAgent
from src.network_utils import connect_to_bootstrap_peers
from src.logging_utils import log
from src.models import NodeProfile
from src.peer_registry import PeerRegistry
from src.protocols import PING_PROTOCOL, QUERY_PROTOCOL
from src.services.dht_service import DHTService
from src.services.health_service import HealthService
from src.services.ping_service import PingService
from src.services.pubsub_service import PubSubService
from src.services.query_service import QueryService
from src.services.routing_service import RoutingService
from src.transport import TransportService


class Node:
    """
    Center of logic.

    Manage the services (transport, routing, ping, query, etc.).
    Register and query the local agent LLM.
    Stores peers through the peer registery.
    """

    def __init__(
        self,
        port: int = 0,
        seed: int | None = None,
        model_name: str = "dummy-model",
        capabilities: list[str] | None = None,
        dht_mode: DHTMode = DHTMode.SERVER,
        advertise_address_mode: str = "ipv6_loopback",
    ) -> None:
        self.port = port if port > 0 else find_free_port()
        # List of addresses from which the host will accept incoming connection
        self.listen_addrs = get_available_interfaces(self.port)
        self.advertise_address_mode = advertise_address_mode

        # Seed is useful for tests
        if seed is not None:
            random.seed(seed)
            secret_number = random.getrandbits(32 * 8)
            secret = secret_number.to_bytes(length=32, byteorder="big")
        else:
            secret = secrets.token_bytes(32)

        self.host = new_host(key_pair=create_new_key_pair(secret))
        self.transport = TransportService()

        # Create the Profile of the current Node
        self.local_profile = NodeProfile(
            peer_id=self.host.get_id().to_string(),
            addresses=[],
            model_name=model_name,
            capabilities=capabilities or ["general"],
            is_available=True,
        )
        self.local_profile.addresses = self.all_shareable_addresses()

        self.peer_registry = PeerRegistry()
        self.local_agent = DummyAgent()

        self.ping_service = PingService(self.transport)

        self.dht_service = DHTService(self.host, mode=dht_mode)

        self.health_service = HealthService(
            self.peer_registry,
            self.ping_service,
            self.local_profile.peer_id,
        )

        self.routing_service = RoutingService(
            self.host,
            self.local_profile,
            self.peer_registry,
            self.dht_service,
            self.health_service,
        )

        self.query_service = QueryService(
            self.host,
            self.transport,
            self.local_agent,
            self.routing_service,
        )

        self.pubsub_service = PubSubService(
            self.host, self.dht_service, self.peer_registry, self.local_profile.peer_id
        )

    def advertised_addresses(self) -> list[str]:
        """
        Return the list of addresses that this node should advertise to others.

        For now we only support one explicit mode:
        - ipv6_loopback -> advertise only /ip6/::1/tcp/<port>/p2p/<peer_id>

        Fail fast if the requested address is not present in the listen addresses.
        """
        if self.advertise_address_mode == "ipv6_loopback":
            matches = [
                f"{addr}/p2p/{self.host.get_id()}"
                for addr in self.listen_addrs
                if str(addr).startswith("/ip6/::1/")
            ]
            if not matches:
                raise RuntimeError(
                    "advertise_address_mode='ipv6_loopback' was requested, "
                    "but no /ip6/::1 address is available in listen_addrs"
                )
            return matches

        raise ValueError(
            f"Unsupported advertise_address_mode: {self.advertise_address_mode}"
        )

    def all_shareable_addresses(self) -> list[str]:
        """
        Returns an array containing all the addresses (address + p2p + peer id)
        on which the local node can be reached.
        """
        return [
            f"{addr}/p2p/{self.local_profile.peer_id}" for addr in self.listen_addrs
        ]

    def print_addresses(self) -> None:
        """
        Prints node information.
        """
        log("NODE", f"I am {self.local_profile.peer_id}")
        log("NODE", f"Model: {self.local_profile.model_name}")
        log("NODE", f"Capabilities: {self.local_profile.capabilities}")
        log("NODE", f"Advertise address mode: {self.advertise_address_mode}")
        log("NODE", "Listening on:")
        for addr in self.local_profile.addresses:
            print(f"  {addr}", flush=True)

        log("NODE", "Advertised addresses:")
        for addr in self.advertised_addresses():
            print(f"  {addr}", flush=True)

    def register_protocol_handlers(self) -> None:
        """
        Register all stream handlers.
        """
        self.host.set_stream_handler(PING_PROTOCOL, self.ping_service.handle_stream)
        self.host.set_stream_handler(QUERY_PROTOCOL, self.query_service.handle_stream)
        log("NODE", f"Registered protocol handler: {PING_PROTOCOL}")
        log("NODE", f"Registered protocol handler: {QUERY_PROTOCOL}")

    async def publish_self(self) -> None:
        self.local_profile.addresses = self.advertised_addresses()
        self.local_profile.timestamp_ms = int(time.time() * 1000)

        log(
            "NODE",
            f"Publishing self profile peer_id={self.local_profile.peer_id} "
            f"model={self.local_profile.model_name} "
            f"caps={self.local_profile.capabilities} "
            f"addresses={self.local_profile.addresses} "
            f"ts={self.local_profile.timestamp_ms}",
        )

        await self.dht_service.publish_profile(self.local_profile)
        await self.dht_service.advertise_capabilities(self.local_profile)
        await self.pubsub_service.publish_profile_update(
            self.local_profile.peer_id,
            self.local_profile.timestamp_ms,
        )

    async def run_forever(self, bootstrap_addrs: list[str] | None = None) -> None:
        """
        Start the node and keep it running.
        """
        bootstrap_addrs = bootstrap_addrs or []
        self.local_profile.addresses = self.advertised_addresses()
        self.register_protocol_handlers()

        async with (
            self.host.run(listen_addrs=self.listen_addrs),
            trio.open_nursery() as nursery,
        ):
            nursery.start_soon(self.host.get_peerstore().start_cleanup_task, 60)

            async with self.dht_service.run():
                async with self.pubsub_service.run():
                    nursery.start_soon(self.pubsub_service.run_profile_update_listener)

                    await connect_to_bootstrap_peers(self.host, bootstrap_addrs)

                    await self.publish_self()

                    self.print_addresses()
                    log("NODE", "Node started. Waiting for incoming streams...")
                    await trio.sleep_forever()
