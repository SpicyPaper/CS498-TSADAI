import multiaddr

from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.abc import IHost

from src.models import NodeProfile, QueryContext
from src.peer_registry import PeerRegistry
from src.logging_utils import log


class RoutingDecision:
    def __init__(
        self,
        execute_locally: bool,
        target_peer_id: str | None = None,
        reason: str = "",
    ) -> None:
        self.execute_locally = execute_locally
        self.target_peer_id = target_peer_id
        self.reason = reason


class RoutingService:
    """
    Minimal routing service.

    Responsibilities:
    - infer a required capability from the prompt
    - decide whether forwarding is allowed
    - choose a live peer
    - fallback to local execution when needed
    """

    def __init__(self, local_profile: NodeProfile, peer_registry: PeerRegistry) -> None:
        self.local_profile = local_profile
        self.peer_registry = peer_registry

    def infer_required_capability(self, prompt: str) -> str:
        """
        Very simple capability inference for now.
        You can later replace this with better classification logic.
        """
        prompt_lower = prompt.lower()

        if "math" in prompt_lower:
            return "math"
        if "code" in prompt_lower or "python" in prompt_lower:
            return "code"
        return "general"

    def route_query(self, prompt: str, context: QueryContext) -> RoutingDecision:
        """
        Minimal routing rule:
        - if prompt contains "tag", try a peer with "tag" capability
        - otherwise execute locally
        """
        required_capability = self.infer_required_capability(prompt)

        # If local node has the needed capability, prefer local execution.
        if required_capability in self.local_profile.capabilities:
            return RoutingDecision(
                execute_locally=True,
                reason=f"local node supports capability={required_capability}",
            )

        # Do not forward forever.
        if context.hop_count >= context.max_hops:
            return RoutingDecision(
                execute_locally=True,
                reason="max hops reached, fallback to local execution",
            )

        excluded = set(context.visited_peers)
        excluded.add(self.local_profile.peer_id)

        candidates = self.peer_registry.find_live_by_capability(
            required_capability,
            exclude_peer_ids=excluded,
        )

        if not candidates:
            return RoutingDecision(
                execute_locally=True,
                reason=f"no live remote peer found for capability={required_capability}",
            )

        # Pick first live matching candidate.
        target = candidates[0]

        return RoutingDecision(
            execute_locally=False,
            target_peer_id=target.peer_id,
            reason=f"forward to peer with capability={required_capability}",
        )

    async def connect_to_peer(self, host: IHost, destination_multiaddr: str):
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
