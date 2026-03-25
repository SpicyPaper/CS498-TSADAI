import time

from src.logging_utils import log

from src.models import NodeProfile, PeerStatus


class PeerRegistry:
    """
    Small in-memory peer registry.

    Stores peers manually configured at startup.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, NodeProfile] = {}
        self._statuses: dict[str, PeerStatus] = {}

    def add_profile(self, profile: NodeProfile) -> None:
        self._profiles[profile.peer_id] = profile
        if profile.peer_id not in self._statuses:
            self._statuses[profile.peer_id] = PeerStatus(peer_id=profile.peer_id)

    def get_profile(self, peer_id: str) -> NodeProfile | None:
        return self._profiles.get(peer_id)

    def all_profiles(self) -> list[NodeProfile]:
        return list(self._profiles.values())

    def get_status(self, peer_id: str) -> PeerStatus | None:
        return self._statuses.get(peer_id)

    def mark_peer_alive(self, peer_id: str, rtt_ms: float | None) -> None:
        status = self._statuses.setdefault(peer_id, PeerStatus(peer_id=peer_id))
        status.is_alive = True
        status.last_rtt_ms = rtt_ms
        status.last_checked_ts_ms = int(time.time() * 1000)
        status.consecutive_failures = 0

        profile = self._profiles.get(peer_id)
        if profile is not None:
            profile.is_available = True

    def mark_peer_unreachable(self, peer_id: str) -> None:
        status = self._statuses.setdefault(peer_id, PeerStatus(peer_id=peer_id))
        status.is_alive = False
        status.last_checked_ts_ms = int(time.time() * 1000)
        status.consecutive_failures += 1

        profile = self._profiles.get(peer_id)
        if profile is not None:
            profile.is_available = False

    def all_live_profiles(self) -> list[NodeProfile]:
        live_profiles: list[NodeProfile] = []
        for peer_id, profile in self._profiles.items():
            status = self._statuses.get(peer_id)
            if status is not None and status.is_alive:
                live_profiles.append(profile)
        return live_profiles

    def get_any_address(self, peer_id: str) -> str | None:
        profile = self._profiles.get(peer_id)
        if profile is None or not profile.addresses:
            return None
        return profile.addresses[0]

    def find_live_by_capability(
        self,
        capability: str,
        *,
        exclude_peer_ids: set[str] | None = None,
    ) -> list[NodeProfile]:
        exclude_peer_ids = exclude_peer_ids or set()

        results: list[NodeProfile] = []
        for peer_id, profile in self._profiles.items():
            if peer_id in exclude_peer_ids:
                continue
            if capability not in profile.capabilities:
                continue

            status = self._statuses.get(peer_id)
            if status is None or not status.is_alive:
                continue

            results.append(profile)

        return results
