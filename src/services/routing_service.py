from libp2p.abc import IHost
from libp2p.peer.id import ID

from src.logging_utils import log
from src.models import NodeProfile, QueryContext
from src.peer_registry import PeerRegistry
from src.services.dht_service import DHTService
from src.services.health_service import HealthService
from src.services.recommendation_service import RecommendationService
from src.services.capability_classifier import CapabilityClassifier

MIN_UTILITY_THRESHOLD = 0.65
MAX_RECOMMENDATIONS_PER_PEER = 3


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
        recommendation_service: RecommendationService | None = None,
    ) -> None:
        self.host = host
        self.local_profile = local_profile
        self.peer_registry = peer_registry
        self.dht_service = dht_service
        self.health_service = health_service
        self.live_ttl_ms = live_ttl_ms
        self.capability_classifier = capability_classifier
        self.recommendation_service = recommendation_service

    async def refresh_candidates_from_dht(self, capability: str) -> None:
        """
        Ask the DHT for providers of a capability, fetch their profiles,
        and store them in the local cache.
        """
        if self.dht_service is None:
            return

        profiles = await self.dht_service.fetch_capability_profiles(
            capability,
            exclude_peer_ids={self.local_profile.peer_id},
        )

        for profile in profiles:
            old_profile = self.peer_registry.get_profile(profile.peer_id)
            self.peer_registry.upsert_profile(profile)

            if old_profile is None:
                log(
                    "ROUTING",
                    f"Discovered new peer from DHT peer_id={profile.peer_id} "
                    f"caps={profile.capabilities} addresses={profile.addresses}",
                )

    async def route_query(self, prompt: str, context: QueryContext) -> RoutingDecision:
        """
        Routing rule:
        1. classify the required capability
        2. search the best candidate for that capability
        3. if none is found, retry the same search for the general capability
        4. if still none is found, fallback to local general if available
        5. otherwise return no_suitable_node
        """
        # Resolve the required capability once and carry it through forwarding
        if context.required_capability is None:
            if self.capability_classifier is None:
                raise RuntimeError("Routing requires a capability classifier")

            required_capability = await self.capability_classifier.classify(prompt)
            log("ROUTING", f"LLM classified query capability={required_capability}")

            context.required_capability = required_capability
        else:
            required_capability = context.required_capability

        excluded = set(context.visited_peers)
        excluded.add(self.local_profile.peer_id)
        used_general_fallback = False

        best_candidate = await self._search_capability(required_capability, excluded)

        if best_candidate is None and required_capability != "general":
            used_general_fallback = True
            log(
                "ROUTING",
                f"No candidate found for capability={required_capability}, "
                f"trying general fallback",
            )
            best_candidate = await self._search_capability("general", excluded)

        if best_candidate is None:
            log(
                "ROUTING",
                f"No candidate found for capability={required_capability} "
                f"and no candidate found for general fallback",
            )
            return RoutingDecision(
                execute_locally=False,
                target_peer_id=None,
                reason=(
                    f"no valid candidate found for capability={required_capability} "
                    f"or general"
                ),
                no_suitable_node=True,
            )

        kind, profile, utility, capability_used = best_candidate

        if kind == "local":
            if used_general_fallback:
                return RoutingDecision(
                    execute_locally=True,
                    reason=f"local chosen from general fallback utility={utility:.2f}",
                )
            return RoutingDecision(
                execute_locally=True,
                reason=(
                    f"local chosen for capability={capability_used} "
                    f"utility={utility:.2f}"
                ),
            )

        if used_general_fallback:
            return RoutingDecision(
                execute_locally=False,
                target_peer_id=profile.peer_id,
                reason=(
                    f"forward to peer={profile.peer_id} from general fallback "
                    f"quality={profile.capability_scores.get(capability_used, 0.0):.2f} "
                    f"utility={utility:.2f}"
                ),
            )

        return RoutingDecision(
            execute_locally=False,
            target_peer_id=profile.peer_id,
            reason=(
                f"forward to peer={profile.peer_id} "
                f"capability={capability_used} "
                f"quality={profile.capability_scores.get(capability_used, 0.0):.2f} "
                f"utility={utility:.2f}"
            ),
        )

    async def _search_capability(
        self,
        capability: str,
        excluded_peer_ids: set[str],
    ) -> tuple[str, NodeProfile | None, float, str] | None:
        local_is_capable = capability in self.local_profile.capabilities
        local_utility = (
            self.local_profile.capability_scores.get(capability, 0.0)
            if local_is_capable
            else None
        )
        await self.refresh_candidates_from_dht(capability)

        direct_capable_candidates = [
            profile
            for profile in self.peer_registry.all_profiles()
            if profile.peer_id not in excluded_peer_ids
            and capability in profile.capabilities
        ]

        log(
            "ROUTING",
            f"Scoring candidates capability={capability} "
            f"local_available={local_utility is not None} "
            f"direct_count={len(direct_capable_candidates)}",
        )

        ranked_direct_candidates = await self._rank_candidates(
            direct_capable_candidates,
            capability,
        )

        evaluated_remote_candidates = list(ranked_direct_candidates)

        passing_candidates: list[tuple[str, NodeProfile | None, float]] = []

        if local_utility is not None and local_utility >= MIN_UTILITY_THRESHOLD:
            passing_candidates.append(("local", None, local_utility))

        for profile, utility in ranked_direct_candidates:
            if utility >= MIN_UTILITY_THRESHOLD:
                passing_candidates.append(("remote", profile, utility))

        log(
            "ROUTING",
            f"Threshold-passing candidates capability={capability} "
            f"count={len(passing_candidates)} threshold={MIN_UTILITY_THRESHOLD:.2f}",
        )

        if passing_candidates:
            passing_candidates.sort(key=lambda item: item[2], reverse=True)
            kind, profile, utility = passing_candidates[0]
            return (kind, profile, utility, capability)

        recommendation_sources = [profile for profile, _ in ranked_direct_candidates]
        recommendation_excluded_peer_ids = set(excluded_peer_ids)
        recommendation_excluded_peer_ids.update(
            profile.peer_id for profile in direct_capable_candidates
        )

        log(
            "ROUTING",
            f"Asking recommendations capability={capability} "
            f"source_peer_ids={[profile.peer_id for profile in recommendation_sources]} "
            f"excluded_peer_ids={sorted(recommendation_excluded_peer_ids)}",
        )
        recommendation_groups = await self._get_recommendation_groups(
            recommendation_sources,
            capability,
            excluded_peer_ids=recommendation_excluded_peer_ids,
        )

        recommended_hit = await self._search_recommendation_rounds(
            recommendation_groups,
            capability,
            evaluated_remote_candidates,
        )

        if recommended_hit is not None:
            chosen, utility = recommended_hit
            return ("remote", chosen, utility, capability)

        overall_candidates: list[tuple[str, NodeProfile | None, float]] = []

        if local_utility is not None:
            overall_candidates.append(("local", None, local_utility))

        overall_candidates.extend(
            ("remote", profile, utility)
            for profile, utility in evaluated_remote_candidates
        )

        if not overall_candidates:
            return None

        overall_candidates.sort(key=lambda item: item[2], reverse=True)
        kind, profile, utility = overall_candidates[0]
        return (kind, profile, utility, capability)

    def _candidate_utility(
        self,
        profile: NodeProfile,
        capability: str,
    ) -> float:
        """
        Utility combines static quality with dynamic local observations so we
        do not route only by model quality on paper
        """
        quality = profile.capability_scores.get(capability, 0.0)

        status = self.peer_registry.get_status(profile.peer_id)

        freshness_score = 0.0
        failure_score = 0.0
        latency_score = 0.0

        if status is not None:
            if self.peer_registry.is_peer_fresh_live(profile.peer_id, self.live_ttl_ms):
                freshness_score = 1.0

            failure_score = min(status.consecutive_failures / 3.0, 1.0)

            if status.last_rtt_ms is not None:
                latency_score = min(status.last_rtt_ms / 1000.0, 1.0)

        utility = (
            0.75 * quality
            + 0.15 * freshness_score
            - 0.07 * failure_score
            - 0.08 * latency_score
        )
        utility = max(0.0, min(1.0, utility))

        log(
            "ROUTING",
            f"Utility breakdown peer_id={profile.peer_id} "
            f"capability={capability} "
            f"quality={quality:.2f} freshness={freshness_score:.2f} "
            f"failure={failure_score:.2f} latency={latency_score:.2f} "
            f"utility={utility:.2f}",
        )

        return utility

    async def _evaluate_candidate(
        self,
        profile: NodeProfile,
        capability: str,
        timeout_s: float = 2.0,
    ) -> tuple[NodeProfile, float] | None:
        if self.health_service is None:
            utility = self._candidate_utility(profile, capability)
            return profile, utility

        result = await self.health_service.check_peer(
            self.host,
            ID.from_base58(profile.peer_id),
            timeout_s=timeout_s,
        )

        if not result.ok:
            return None

        utility = self._candidate_utility(profile, capability)

        return profile, utility

    async def _rank_candidates(
        self,
        candidates: list[NodeProfile],
        capability: str,
        timeout_s: float = 2.0,
    ) -> list[tuple[NodeProfile, float]]:
        """
        Evaluate every candidate for this query, discard unreachable peers,
        then sort reachable ones by descending utility
        """
        ranked: list[tuple[NodeProfile, float]] = []

        for profile in candidates:
            evaluated = await self._evaluate_candidate(
                profile,
                capability,
                timeout_s=timeout_s,
            )
            if evaluated is not None:
                ranked.append(evaluated)

        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked

    async def _get_recommendation_groups(
        self,
        source_profiles: list[NodeProfile],
        capability: str,
        excluded_peer_ids: set[str],
    ) -> list[list[NodeProfile]]:
        groups: list[list[NodeProfile]] = []

        if self.recommendation_service is None:
            return groups

        for profile in source_profiles:
            recommended_peer_ids = (
                await self.recommendation_service.request_recommendations(
                    peer_id=ID.from_base58(profile.peer_id),
                    capability=capability,
                    limit=MAX_RECOMMENDATIONS_PER_PEER,
                    exclude_peer_ids=list(excluded_peer_ids),
                )
            )

            recommended_profiles: list[NodeProfile] = []
            for peer_id in recommended_peer_ids:
                if peer_id in excluded_peer_ids:
                    continue

                cached = self.peer_registry.get_profile(peer_id)
                if cached is None and self.dht_service is not None:
                    fetched = await self.dht_service.get_profile(peer_id)
                    if fetched is not None:
                        self.peer_registry.upsert_profile(fetched)
                        cached = fetched

                if cached is not None:
                    recommended_profiles.append(cached)
                    excluded_peer_ids.add(peer_id)

            if recommended_profiles:
                groups.append(recommended_profiles)

        return groups

    async def _search_recommendation_rounds(
        self,
        recommendation_groups: list[list[NodeProfile]],
        capability: str,
        best_seen: list[tuple[NodeProfile, float]],
    ) -> tuple[NodeProfile, float] | None:
        round_index = 0

        while True:
            round_candidates: list[NodeProfile] = []

            for group in recommendation_groups:
                if round_index < len(group):
                    round_candidates.append(group[round_index])

            if not round_candidates:
                break

            log(
                "ROUTING",
                f"Recommendation round={round_index + 1} "
                f"capability={capability} candidates={len(round_candidates)}",
            )

            ranked_round = await self._rank_candidates(round_candidates, capability)
            if ranked_round:
                best_profile, best_utility = ranked_round[0]
                log(
                    "ROUTING",
                    f"Best recommendation round candidate peer_id={best_profile.peer_id} "
                    f"capability={capability} utility={best_utility:.2f}",
                )

            best_seen.extend(ranked_round)

            if ranked_round and ranked_round[0][1] >= MIN_UTILITY_THRESHOLD:
                return ranked_round[0]

            round_index += 1

        return None
