"""
Very small routing service.

Does the following:
- decide whether to execute locally or forward
- choose a remote peer based on a simple keyword/capability rule
"""

import multiaddr

from libp2p.peer.peerinfo import info_from_p2p_addr

from src.models import NodeProfile
from src.peer_registery import PeerRegistry
from src.logging_utils import log


class RoutingDecision:
    def __init__(
        self, execute_locally: bool, target_peer_id: str | None = None
    ) -> None:
        self.execute_locally = execute_locally
        self.target_peer_id = target_peer_id


class RoutingService:
    def __init__(self, local_profile: NodeProfile, peer_registry: PeerRegistry) -> None:
        self.local_profile = local_profile
        self.peer_registry = peer_registry

    def route_query(self, prompt: str) -> RoutingDecision:
        """
        Minimal routing rule:
        - if prompt contains 'math', try a peer with 'math' capability
        - otherwise execute locally
        """

        prompt_lower = prompt.lower()

        if "math" in prompt_lower:
            candidates = self.peer_registry.find_by_capability("math")
        elif "general" in prompt_lower:
            candidates = self.peer_registry.find_by_capability("general")

        for candidate in candidates:
            if candidate.peer_id != self.local_profile.peer_id:
                return RoutingDecision(
                    execute_locally=False,
                    target_peer_id=candidate.peer_id,
                )

        return RoutingDecision(execute_locally=True)

    async def connect_to_peer(self, host, destination_multiaddr: str):
        """
        Connect to a remote peer using its full /p2p/... multiaddr.
        """
        log("CLIENT", f"Connecting to {destination_multiaddr}")
        # Parse the address (string to obj)
        maddr = multiaddr.Multiaddr(destination_multiaddr)
        # Convert address to peer info
        info = info_from_p2p_addr(maddr)
        await host.connect(info)
        log("CLIENT", f"Connected to peer_id={info.peer_id}")
        return info
