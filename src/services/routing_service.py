import random

from libp2p.abc import IHost
from libp2p.peer.id import ID

from src.logging_utils import log
from src.models import NodeProfile, QueryContext
from src.peer_registry import PeerRegistry
from src.services.dht_service import DHTService
from src.services.health_service import HealthService
from src.services.capability_classifier import CapabilityClassifier

EXPLORE_PROBABILITY = 0.20


class RoutingDecision:
    def __init__(
        self,
        execute_locally: bool,
        target_peer_id: str | None = None,
        reason: str = "",
        no_suitable_node: bool = False,
    ) -> None:
        self.execute_locally = execute_locally
        self.target_peer_id = target_peer_id
        self.reason = reason
        self.no_suitable_node = no_suitable_node


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
        live_ttl_ms: int,
        health_service: HealthService | None = None,
        capability_classifier: CapabilityClassifier | None = None,
    ) -> None:
        self.host = host
        self.local_profile = local_profile
        self.peer_registry = peer_registry
        self.dht_service = dht_service
        self.health_service = health_service
        self.live_ttl_ms = live_ttl_ms
        self.capability_classifier = capability_classifier

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
        Randomize order so we do not always try the same peer first.
        """
        if not candidates:
            return None

        randomized = list(candidates)
        random.shuffle(randomized)

        if self.health_service is None:
            return randomized[0]

        for candidate in randomized:
            result = await self.health_service.check_peer(
                self.host,
                ID.from_base58(candidate.peer_id),
                timeout_s=timeout_s,
            )
            if result.ok:
                return candidate

        return None

    async def route_query(self, prompt: str, context: QueryContext) -> RoutingDecision:
        """
        Routing rule:
        1. local if capable
        2. fresh live capable peer
        3. cached capable peer checked on demand
        4. random other known peer
        5. fallback local
        """
        if context.required_capability is None:
            if self.capability_classifier is None:
                raise RuntimeError("Routing requires a capability classifier")

            required_capability = await self.capability_classifier.classify(prompt)
            log("ROUTING", f"LLM classified query capability={required_capability}")

            context.required_capability = required_capability
        else:
            required_capability = context.required_capability

        # 1) If local node supports it, execute locally.
        if required_capability in self.local_profile.capabilities:
            return RoutingDecision(
                execute_locally=True,
                reason=f"local node supports capability={required_capability}",
            )

        # Prevent infinite forwarding.
        if context.hop_count >= context.max_hops:
            return RoutingDecision(
                execute_locally=False,
                target_peer_id=None,
                reason="max hops reached, no suitable node found",
                no_suitable_node=True,
            )

        excluded = set(context.visited_peers)
        excluded.add(self.local_profile.peer_id)

        # Refresh cache from DHT for the required capability.
        await self.refresh_candidates_from_dht(required_capability)

        # 2) Collect capable candidates from the local registry.
        fresh_live_candidates = self.peer_registry.find_fresh_live_by_capability(
            required_capability,
            max_age_ms=self.live_ttl_ms,
            exclude_peer_ids=excluded,
        )

        cached_capable_candidates = [
            profile
            for profile in self.peer_registry.all_profiles()
            if profile.peer_id not in excluded
            and required_capability in profile.capabilities
        ]

        fresh_live_peer_ids = {profile.peer_id for profile in fresh_live_candidates}
        exploration_candidates = [
            profile
            for profile in cached_capable_candidates
            if profile.peer_id not in fresh_live_peer_ids
        ]

        # 3) Usually exploit fresh live peers, but sometimes explore another capable peer.
        if fresh_live_candidates and (
            not exploration_candidates or random.random() >= EXPLORE_PROBABILITY
        ):
            chosen = random.choice(fresh_live_candidates)
            return RoutingDecision(
                execute_locally=False,
                target_peer_id=chosen.peer_id,
                reason=f"forward to fresh live peer with capability={required_capability}",
            )

        if exploration_candidates:
            reachable_exploration = await self._pick_reachable_candidate(
                exploration_candidates
            )
            if reachable_exploration is not None:
                return RoutingDecision(
                    execute_locally=False,
                    target_peer_id=reachable_exploration.peer_id,
                    reason=(
                        f"explored reachable peer with capability={required_capability}"
                    ),
                )

        # 4) If exploration failed or there were no fresh peers, probe any capable peer.
        reachable_capable = await self._pick_reachable_candidate(
            cached_capable_candidates
        )
        if reachable_capable is not None:
            return RoutingDecision(
                execute_locally=False,
                target_peer_id=reachable_capable.peer_id,
                reason=f"forward to reachable checked peer with capability={required_capability}",
            )

        # 5) No capable peer worked: try one random other known peer.
        capable_peer_ids = {profile.peer_id for profile in cached_capable_candidates}
        capable_peer_ids.update(profile.peer_id for profile in fresh_live_candidates)

        other_candidates = [
            profile
            for profile in self.peer_registry.all_profiles()
            if profile.peer_id not in excluded
            and profile.peer_id not in capable_peer_ids
        ]

        reachable_other = await self._pick_reachable_candidate(other_candidates)
        if reachable_other is not None:
            return RoutingDecision(
                execute_locally=False,
                target_peer_id=reachable_other.peer_id,
                reason="forward to random other known peer",
            )

        # 6) Fallback local.
        return RoutingDecision(
            execute_locally=True,
            reason=f"no reachable peer found for capability={required_capability}, fallback locally",
        )
