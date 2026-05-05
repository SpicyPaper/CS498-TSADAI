from libp2p.abc import IHost
from libp2p.peer.id import ID

from src.logging_utils import log
from src.models import NodeProfile, QueryContext
from src.peer_registry import PeerRegistry
from src.services.dht_service import DHTService
from src.services.health_service import HealthService
from src.services.capability_classifier import CAPABILITIES, CapabilityClassifier
from src.services.recommendation_service import RecommendationService

MIN_UTILITY_THRESHOLD = 0.65
DHT_DISCOVERY_MIN_DEMAND = 0.3
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

    Treats the local registry as a cache, asks the DHT for providers of the
    important requested capabilities, then scores every reachable node against
    the whole capability mix for the query.
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
            if not profile.is_available:
                log(
                    "ROUTING",
                    f"Ignored unavailable DHT profile peer_id={profile.peer_id}",
                )
                continue

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
        1. get the scored capability needs from the query or forwarded context
        2. refresh DHT candidates for important requested capabilities
        3. score local and direct DHT candidates against the whole capability mix
        4. if no direct candidate is good enough, ask direct peers for recommendations
        5. if no recommended candidate is good enough, use the best candidate seen
        6. if no candidate exists, retry with the general capability
        7. otherwise return no_suitable_node
        """
        # Resolve scored capabilities once and carry them through forwarding.
        if context.required_capabilities is None:
            if self.capability_classifier is None:
                raise RuntimeError("Routing requires a capability classifier")

            required_capabilities = await self.capability_classifier.classify_scores(
                prompt
            )
            context.required_capabilities = required_capabilities
            log(
                "ROUTING",
                f"LLM classified query capability_scores={required_capabilities}",
            )
        else:
            required_capabilities = self._normalize_capability_scores(
                context.required_capabilities
            )
            context.required_capabilities = required_capabilities

        excluded = set(context.visited_peers)
        excluded.add(self.local_profile.peer_id)

        candidate = await self._search_scored_capabilities(
            required_capabilities,
            excluded,
        )

        if candidate is None and "general" not in required_capabilities:
            log(
                "ROUTING",
                f"No candidate found for capability_scores={required_capabilities}, "
                f"trying general fallback",
            )
            candidate = await self._search_scored_capabilities(
                {"general": 0.5},
                excluded,
            )

        if candidate is None:
            log(
                "ROUTING",
                f"No candidate found for capability_scores={required_capabilities} "
                f"and no candidate found for general fallback",
            )
            return RoutingDecision(
                execute_locally=False,
                target_peer_id=None,
                reason=(
                    f"no valid candidate found for capability_scores="
                    f"{required_capabilities} "
                    f"or general"
                ),
                no_suitable_node=True,
            )

        kind, profile, route_utility, weighted_quality = candidate

        if kind == "local":
            return RoutingDecision(
                execute_locally=True,
                reason=(
                    f"local chosen for capability_scores={required_capabilities} "
                    f"weighted_quality={weighted_quality:.2f} "
                    f"utility={route_utility:.2f}"
                ),
            )

        return RoutingDecision(
            execute_locally=False,
            target_peer_id=profile.peer_id,
            reason=(
                f"forward to peer={profile.peer_id} "
                f"capability_scores={required_capabilities} "
                f"node_scores={self._profile_scores_for(profile, required_capabilities)} "
                f"weighted_quality={weighted_quality:.2f} "
                f"utility={route_utility:.2f}"
            ),
        )

    def _normalize_capability_scores(self, scores: dict[str, float]) -> dict[str, float]:
        """Keep at most three known capabilities with scores in [0, 1]."""
        if not isinstance(scores, dict):
            return {"general": 0.6}

        normalized: dict[str, float] = {}
        for capability, score in scores.items():
            capability = str(capability).strip().lower()
            if capability not in CAPABILITIES:
                continue

            try:
                value = float(score)
            except (TypeError, ValueError):
                continue

            value = max(0.0, min(1.0, value))
            if value > 0.0:
                normalized[capability] = value

        return dict(
            sorted(
                normalized.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:3]
        ) or {"general": 0.6}

    def _discovery_capabilities(self, capability_scores: dict[str, float]) -> list[str]:
        """
        Use only meaningful needs for DHT lookups, but keep all needs for scoring.

        If every score is below the discovery threshold, use the top capability
        so routing still has at least one DHT entry point.
        """
        capabilities = [
            capability
            for capability, score in capability_scores.items()
            if score >= DHT_DISCOVERY_MIN_DEMAND
        ]

        if capabilities:
            return capabilities

        return [next(iter(capability_scores))]

    async def _search_scored_capabilities(
        self,
        capability_scores: dict[str, float],
        excluded_peer_ids: set[str],
    ) -> tuple[str, NodeProfile | None, float, float] | None:
        """
        Refresh DHT candidates for important requested capabilities, then choose
        the node with the best cumulative utility.

        The routing keeps the original two-stage behavior: first try local and
        direct DHT candidates, then ask recommendations only if no direct
        candidate is good enough.
        """
        capability_scores = self._normalize_capability_scores(capability_scores)
        discovery_capabilities = self._discovery_capabilities(capability_scores)

        for capability in discovery_capabilities:
            await self.refresh_candidates_from_dht(capability)

        direct_remote_candidates = [
            profile
            for profile in self.peer_registry.all_profiles()
            if profile.peer_id not in excluded_peer_ids
            and profile.is_available
            and self._has_any_requested_capability(profile, capability_scores)
        ]

        log(
            "ROUTING",
            f"Scoring cumulative candidates capability_scores={capability_scores} "
            f"discovery_capabilities={discovery_capabilities} "
            f"local_available={self._has_any_requested_capability(self.local_profile, capability_scores)} "
            f"direct_count={len(direct_remote_candidates)}",
        )

        direct_candidates = await self._evaluate_candidate_pool(
            direct_remote_candidates,
            capability_scores,
            include_local=True,
        )
        best_direct = self._best_candidate(direct_candidates)

        if best_direct is not None and best_direct[2] >= MIN_UTILITY_THRESHOLD:
            log(
                "ROUTING",
                f"Selected direct candidate utility={best_direct[2]:.2f} "
                f"threshold={MIN_UTILITY_THRESHOLD:.2f}",
            )
            return best_direct

        if best_direct is None:
            log("ROUTING", "No direct candidates available")
        else:
            log(
                "ROUTING",
                f"No direct candidate above threshold "
                f"best_utility={best_direct[2]:.2f} "
                f"threshold={MIN_UTILITY_THRESHOLD:.2f}",
            )

        recommended_remote_candidates = await self._get_recommended_candidates(
            direct_remote_candidates,
            capability_scores,
            excluded_peer_ids,
        )
        log(
            "ROUTING",
            f"Recommendation candidate count={len(recommended_remote_candidates)}",
        )

        recommended_candidates = await self._evaluate_candidate_pool(
            recommended_remote_candidates,
            capability_scores,
            include_local=False,
        )
        best_recommended = self._best_candidate(recommended_candidates)

        if (
            best_recommended is not None
            and best_recommended[2] >= MIN_UTILITY_THRESHOLD
        ):
            log(
                "ROUTING",
                f"Selected recommended candidate utility={best_recommended[2]:.2f} "
                f"threshold={MIN_UTILITY_THRESHOLD:.2f}",
            )
            return best_recommended

        best_seen = self._best_candidate(direct_candidates + recommended_candidates)
        if best_seen is not None:
            log(
                "ROUTING",
                f"No candidate above threshold, using best seen "
                f"utility={best_seen[2]:.2f} threshold={MIN_UTILITY_THRESHOLD:.2f}",
            )

        return best_seen

    async def _evaluate_candidate_pool(
        self,
        candidates: list[NodeProfile],
        capability_scores: dict[str, float],
        include_local: bool,
    ) -> list[tuple[str, NodeProfile | None, float, float]]:
        evaluated: list[tuple[str, NodeProfile | None, float, float]] = []

        if include_local and self._has_any_requested_capability(
            self.local_profile,
            capability_scores,
        ):
            local_utility, local_weighted_quality = self._scored_candidate_utility(
                self.local_profile,
                capability_scores,
            )
            evaluated.append(("local", None, local_utility, local_weighted_quality))

        for profile in candidates:
            candidate = await self._evaluate_scored_candidate(
                profile,
                capability_scores,
            )
            if candidate is not None:
                evaluated.append(candidate)

        return evaluated

    def _best_candidate(
        self,
        candidates: list[tuple[str, NodeProfile | None, float, float]],
    ) -> tuple[str, NodeProfile | None, float, float] | None:
        if not candidates:
            return None

        return max(candidates, key=lambda candidate: candidate[2])

    async def _get_recommended_candidates(
        self,
        sources: list[NodeProfile],
        capability_scores: dict[str, float],
        excluded_peer_ids: set[str],
    ) -> list[NodeProfile]:
        """Ask direct candidates for extra peers, without changing the ranking rule."""
        if self.recommendation_service is None:
            return []

        recommended_by_id: dict[str, NodeProfile] = {}
        recommendation_excluded_peer_ids = set(excluded_peer_ids)
        recommendation_excluded_peer_ids.update(profile.peer_id for profile in sources)

        for capability in capability_scores:
            capable_sources = [
                profile for profile in sources if capability in profile.capabilities
            ]

            log(
                "ROUTING",
                f"Asking recommendations capability={capability} "
                f"source_peer_ids={[profile.peer_id for profile in capable_sources]} "
                f"excluded_peer_ids={sorted(recommendation_excluded_peer_ids)}",
            )

            for source in capable_sources:
                recommended_peer_ids = (
                    await self.recommendation_service.request_recommendations(
                        peer_id=ID.from_base58(source.peer_id),
                        capability=capability,
                        limit=MAX_RECOMMENDATIONS_PER_PEER,
                        exclude_peer_ids=list(recommendation_excluded_peer_ids),
                    )
                )

                for peer_id in recommended_peer_ids:
                    if peer_id in recommendation_excluded_peer_ids:
                        continue

                    profile = self.peer_registry.get_profile(peer_id)
                    if profile is None and self.dht_service is not None:
                        profile = await self.dht_service.get_profile(peer_id)
                        if profile is not None:
                            self.peer_registry.upsert_profile(profile)

                    if (
                        profile is not None
                        and profile.is_available
                        and self._has_any_requested_capability(
                            profile,
                            capability_scores,
                        )
                    ):
                        recommended_by_id[profile.peer_id] = profile
                        recommendation_excluded_peer_ids.add(profile.peer_id)

        return list(recommended_by_id.values())

    def _has_any_requested_capability(
        self,
        profile: NodeProfile,
        capability_scores: dict[str, float],
    ) -> bool:
        return any(
            profile.capability_scores.get(capability, 0.0) > 0.0
            for capability in capability_scores
        )

    def _profile_scores_for(
        self,
        profile: NodeProfile,
        capability_scores: dict[str, float],
    ) -> dict[str, float]:
        return {
            capability: profile.capability_scores.get(capability, 0.0)
            for capability in capability_scores
        }

    def _weighted_capability_quality(
        self,
        profile: NodeProfile,
        capability_scores: dict[str, float],
    ) -> float:
        """
        Weighted average of node quality for the requested capabilities.

        Missing node scores count as 0, and the final quality is clamped to
        [0, 1] so it stays comparable with the rest of the utility formula.
        """
        total_demand = sum(capability_scores.values())
        if total_demand <= 0.0:
            return 0.0

        weighted_quality = sum(
            demand_score * profile.capability_scores.get(capability, 0.0)
            for capability, demand_score in capability_scores.items()
        ) / total_demand

        return max(0.0, min(1.0, weighted_quality))

    def _scored_candidate_utility(
        self,
        profile: NodeProfile,
        capability_scores: dict[str, float],
    ) -> tuple[float, float]:
        """
        Combine static multi-capability quality with local health observations.
        """
        weighted_quality = self._weighted_capability_quality(profile, capability_scores)

        status = self.peer_registry.get_status(profile.peer_id)

        freshness_score = 0.0
        failure_score = 0.0
        latency_score = 0.0

        if profile.peer_id == self.local_profile.peer_id:
            freshness_score = 1.0
        elif status is not None:
            if self.peer_registry.is_peer_fresh_live(profile.peer_id, self.live_ttl_ms):
                freshness_score = 1.0

            failure_score = min(status.consecutive_failures / 3.0, 1.0)

            if status.last_rtt_ms is not None:
                latency_score = min(status.last_rtt_ms / 1000.0, 1.0)

        utility = (
            0.75 * weighted_quality
            + 0.15 * freshness_score
            - 0.07 * failure_score
            - 0.08 * latency_score
        )
        utility = max(0.0, min(1.0, utility))

        log(
            "ROUTING",
            f"Utility breakdown peer_id={profile.peer_id} "
            f"capability_scores={capability_scores} "
            f"node_scores={self._profile_scores_for(profile, capability_scores)} "
            f"weighted_quality={weighted_quality:.2f} "
            f"freshness={freshness_score:.2f} "
            f"failure={failure_score:.2f} latency={latency_score:.2f} "
            f"utility={utility:.2f}",
        )

        return utility, weighted_quality

    async def _evaluate_scored_candidate(
        self,
        profile: NodeProfile,
        capability_scores: dict[str, float],
        timeout_s: float = 2.0,
    ) -> tuple[str, NodeProfile, float, float] | None:
        if self.health_service is not None:
            result = await self.health_service.check_peer(
                self.host,
                ID.from_base58(profile.peer_id),
                timeout_s=timeout_s,
            )

            if not result.ok:
                return None

        utility, weighted_quality = self._scored_candidate_utility(
            profile,
            capability_scores,
        )

        return "remote", profile, utility, weighted_quality
