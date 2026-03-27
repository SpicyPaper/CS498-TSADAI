from libp2p.abc import IHost
from libp2p.peer.id import ID

from src.logging_utils import log
from src.models import NodeProfile, QueryContext
from src.peer_registry import PeerRegistry
from src.services.dht_service import DHTService
from src.services.health_service import HealthService


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
    Routing service.

    Treats the local registry as a cache and uses DHT
    to discover providers dynamically when needed.
    """

    def __init__(
        self,
        host: IHost,
        local_profile: NodeProfile,
        peer_registry: PeerRegistry,
        dht_service: DHTService,
        health_service: HealthService | None = None,
    ) -> None:
        self.host = host
        self.local_profile = local_profile
        self.peer_registry = peer_registry
        self.dht_service = dht_service
        self.health_service = health_service

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

    async def refresh_candidates_from_dht(self, capability: str) -> None:
        """
        Ask the DHT for providers of a capability, fetch their profiles,
        and store them in the local cache.
        """
        if self.dht_service is None:
            return

        provider_ids = await self.dht_service.find_capability_provider_ids(capability)

        for peer_id in provider_ids:
            if peer_id == self.local_profile.peer_id:
                continue

            old_profile = self.peer_registry.get_profile(peer_id)

            profile = await self.dht_service.get_profile(peer_id)
            if profile is not None:
                self.peer_registry.upsert_profile(profile)

                if old_profile is None:
                    log(
                        "ROUTING",
                        f"Discovered new peer from DHT peer_id={peer_id} "
                        f"caps={profile.capabilities} addresses={profile.addresses}",
                    )
                else:
                    log(
                        "ROUTING",
                        f"Refreshed cached peer from DHT peer_id={peer_id} "
                        f"caps={profile.capabilities} addresses={profile.addresses}",
                    )

    async def _pick_reachable_candidate(
        self,
        candidates: list[NodeProfile],
        timeout_s: float = 2.0,
    ) -> NodeProfile | None:
        """
        Validate cached candidates on demand with ping.
        """
        if not candidates:
            return None

        if self.health_service is None:
            return candidates[0]

        log("DEBUG", "_pick_reachable_candidate")
        for candidate in candidates:
            log("DEBUG", f"{candidate.peer_id}")
            result = await self.health_service.check_peer(
                self.host,
                ID.from_base58(candidate.peer_id),
                timeout_s=timeout_s,
            )
            if result.ok:
                log("DEBUG", "selected")
                return candidate

        return None

    async def route_query(self, prompt: str, context: QueryContext) -> RoutingDecision:
        """
        Minimal routing rule:
        - infer required capability
        - prefer local
        - refresh from DHT
        - prefer recently known live peers
        - otherwise validate cached candidates on demand with ping
        - fallback locally
        """
        required_capability = self.infer_required_capability(prompt)

        # If local node supports it, execute locally.
        if required_capability in self.local_profile.capabilities:
            return RoutingDecision(
                execute_locally=True,
                reason=f"local node supports capability={required_capability}",
            )

        # Prevent infinite forwarding.
        if context.hop_count >= context.max_hops:
            return RoutingDecision(
                execute_locally=True,
                reason="max hops reached, fallback to local execution",
            )

        # Refresh cache from DHT.
        await self.refresh_candidates_from_dht(required_capability)

        excluded = set(context.visited_peers)
        excluded.add(self.local_profile.peer_id)

        # Check if a peer already marked as live is capable to answer the query
        live_candidates = self.peer_registry.find_live_by_capability(
            required_capability,
            exclude_peer_ids=excluded,
        )
        if live_candidates:
            return RoutingDecision(
                execute_locally=False,
                target_peer_id=live_candidates[0].peer_id,
                reason=f"forward to live peer with capability={required_capability}",
            )

        # Otherwise, get peers that are capable but with unknown live status
        cached_candidates = [
            profile
            for profile in self.peer_registry.all_profiles()
            if profile.peer_id not in excluded
            and required_capability in profile.capabilities
        ]

        # Test reachability of candidate and pick a live candidate
        reachable = await self._pick_reachable_candidate(cached_candidates)
        if reachable is not None:
            return RoutingDecision(
                execute_locally=False,
                target_peer_id=reachable.peer_id,
                reason=f"forward to on-demand checked peer with capability={required_capability}",
            )

        # No candidates were reachable, fallback locally
        return RoutingDecision(
            execute_locally=True,
            reason=f"no reachable peer found for capability={required_capability}, fallback locally",
        )
