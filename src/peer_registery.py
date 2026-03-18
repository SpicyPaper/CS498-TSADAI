"""
Very small in-memory peer registry.

Stores peers manually configured at startup.
"""

from src.models import NodeProfile


class PeerRegistry:
    def __init__(self) -> None:
        self._profiles: dict[str, NodeProfile] = {}

    def add_profile(self, profile: NodeProfile) -> None:
        self._profiles[profile.peer_id] = profile

    def get_profile(self, peer_id: str) -> NodeProfile | None:
        return self._profiles.get(peer_id)

    def all_profiles(self) -> list[NodeProfile]:
        return list(self._profiles.values())

    def find_by_capability(self, capability: str) -> list[NodeProfile]:
        return [
            profile
            for profile in self._profiles.values()
            if capability in profile.capabilities and profile.is_available
        ]
