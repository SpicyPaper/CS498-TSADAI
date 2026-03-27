from libp2p.abc import IHost
from libp2p.peer.id import ID

from src.network_utils import connect_to_peer
from src.logging_utils import log
from src.peer_registry import PeerRegistry
from src.services.ping_service import PingService
from src.models import PingResult


class HealthService:
    """
    Periodic peer health checks.

    This service uses PingService to keep the registry's
    liveness state updated.
    """

    def __init__(
        self,
        peer_registry: PeerRegistry,
        ping_service: PingService,
        local_peer_id: str,
    ) -> None:
        self.peer_registry = peer_registry
        self.ping_service = ping_service
        self.local_peer_id = local_peer_id

    async def check_peer(
        self, host: IHost, peer_id: ID, timeout_s: float = 3.0
    ) -> PingResult:
        """
        Check if a peer is alive or not.
        """
        known_addr = self.peer_registry.get_any_address(str(peer_id))

        if known_addr is not None:
            try:
                await connect_to_peer(host, known_addr)
            except Exception as exc:
                log("HEALTH", f"Lazy connect failed for peer_id={peer_id}: {exc}")

        result = await self.ping_service.ping_peer(host, peer_id, timeout_s=timeout_s)

        if result.ok:
            self.peer_registry.mark_peer_alive(str(peer_id), result.rtt_ms)
            log("HEALTH", f"Peer alive peer_id={peer_id} rtt_ms={result.rtt_ms:.2f}")
        else:
            self.peer_registry.mark_peer_unreachable(str(peer_id))
            log("HEALTH", f"Peer unreachable peer_id={peer_id} error={result.error}")

        return result
