import random
import secrets

import multiaddr
import trio
from libp2p import new_host
from libp2p.crypto.secp256k1 import create_new_key_pair
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.utils.address_validation import (
    find_free_port,
    get_available_interfaces,
)

from src.local_agent import DummyAgent
from src.logging_utils import log
from src.models import NodeProfile
from src.peer_registry import PeerRegistry
from src.protocols import PING_PROTOCOL, QUERY_PROTOCOL
from src.services.health_service import HealthService
from src.services.ping_service import PingService
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
    ) -> None:
        self.port = port if port > 0 else find_free_port()
        # List of addresses from which the host will accept incoming connection
        self.listen_addrs = get_available_interfaces(self.port)

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
        self.routing_service = RoutingService(self.local_profile, self.peer_registry)

        self.ping_service = PingService(self.transport)
        self.query_service = QueryService(
            self.host,
            self.transport,
            self.local_agent,
            self.routing_service,
        )

        self.health_service = HealthService(
            self.peer_registry,
            self.ping_service,
            self.routing_service,
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
        log("NODE", "Listening on:")
        for addr in self.local_profile.addresses:
            print(f"  {addr}", flush=True)

    def register_protocol_handlers(self) -> None:
        """
        Register all stream handlers.
        """
        self.host.set_stream_handler(PING_PROTOCOL, self.ping_service.handle_stream)
        self.host.set_stream_handler(QUERY_PROTOCOL, self.query_service.handle_stream)
        log("NODE", f"Registered protocol handler: {PING_PROTOCOL}")
        log("NODE", f"Registered protocol handler: {QUERY_PROTOCOL}")

    def register_known_peer(
        self,
        peer_id: str,
        addresses: list[str],
        model_name: str,
        capabilities: list[str],
    ) -> None:
        """
        Register a known peer in the peer registry.
        """
        profile = NodeProfile(
            peer_id=peer_id,
            addresses=addresses,
            model_name=model_name,
            capabilities=capabilities,
            is_available=True,
        )
        self.peer_registry.add_profile(profile)
        log("NODE", f"Registered known peer {peer_id} with capabilities={capabilities}")

    async def run_forever(self) -> None:
        """
        Start the node and keep it running.
        """
        self.register_protocol_handlers()

        async with (
            self.host.run(listen_addrs=self.listen_addrs),
            trio.open_nursery() as nursery,
        ):
            nursery.start_soon(self.host.get_peerstore().start_cleanup_task, 60)
            nursery.start_soon(
                self.health_service.run_periodic_checks,
                self.host,
                10.0,
                3.0,
            )

            self.print_addresses()
            log("NODE", "Node started. Waiting for incoming streams...")
            await trio.sleep_forever()
