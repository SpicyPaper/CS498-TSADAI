"""
Reusable Node runtime.

This class owns:
- the libp2p host
- the registered protocol handlers
- connection helpers
- the services (ping/query)
"""

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

from src.logging_utils import log
from src.protocols import PING_PROTOCOL, QUERY_PROTOCOL
from src.services.ping_service import PingService
from src.services.query_service import QueryService
from src.transport import TransportService


class Node:
    def __init__(self, port: int = 0, seed: int | None = None) -> None:
        self.port = port if port > 0 else find_free_port()
        # List of addresses that host will accept incoming connections
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
        self.ping_service = PingService(self.transport)
        self.query_service = QueryService(self.transport)

    def peer_id(self) -> str:
        return self.host.get_id().to_string()

    def print_addresses(self) -> None:
        """
        Prints useful startup information
        """
        log("NODE", f"I am {self.peer_id()}")
        log("NODE", "Listening on:")

        for addr in self.listen_addrs:
            print(f"  {addr}/p2p/{self.peer_id()}", flush=True)

    def register_protocol_handlers(self) -> None:
        """
        Register all stream handlers.
        """
        self.host.set_stream_handler(PING_PROTOCOL, self.ping_service.handle_stream)
        self.host.set_stream_handler(QUERY_PROTOCOL, self.query_service.handle_stream)

        log("NODE", f"Registered protocol handler: {PING_PROTOCOL}")
        log("NODE", f"Registered protocol handler: {QUERY_PROTOCOL}")

    async def connect_to_peer(self, destination_multiaddr: str):
        """
        Connect to a remote peer using its full /p2p/... multiaddr.
        """
        log("CLIENT", f"Connecting to {destination_multiaddr}")

        # Parse the address (string to obj)
        maddr = multiaddr.Multiaddr(destination_multiaddr)
        # Convert address to peer info
        info = info_from_p2p_addr(maddr)
        await self.host.connect(info)

        log("CLIENT", f"Connected to peer_id={info.peer_id}")

        return info

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
            self.print_addresses()
            log("NODE", "Node started. Waiting for incoming streams...")
            await trio.sleep_forever()
