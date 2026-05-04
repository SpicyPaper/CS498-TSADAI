from contextlib import asynccontextmanager

import trio
from libp2p.abc import IHost
from libp2p.kad_dht.kad_dht import DHTMode, KadDHT
from libp2p.peer.peerinfo import PeerInfo
from libp2p.records.validator import Validator
from libp2p.tools.async_service import background_trio_service

from src.logging_utils import log
from src.models import NodeProfile


DHT_OPERATION_TIMEOUT_S = 30.0


class ProfileValidator(Validator):
    """
    Minimal validator for the /profile namespace.

    For now we just require non-empty bytes.
    """

    def validate(self, key: str, value: bytes) -> None:
        if not value:
            raise ValueError("profile value cannot be empty")

    def select(self, key: str, values: list[bytes]) -> int:
        # Keep first value for now.
        return 0


class DHTService:
    """
    Kad-DHT service for:
    - publishing node profiles
    - retrieving profiles by peer_id
    - advertising capability providers
    - finding providers for a capability
    """

    PROFILE_NAMESPACE = "profile"

    def __init__(self, host: IHost, mode: DHTMode = DHTMode.SERVER) -> None:
        self.host = host
        self.mode = mode
        self.dht = KadDHT(host, mode, enable_random_walk=(mode == DHTMode.SERVER))
        self.dht.register_validator(self.PROFILE_NAMESPACE, ProfileValidator())

    def profile_key(self, peer_id: str) -> str:
        return f"/{self.PROFILE_NAMESPACE}/{peer_id}"

    def capability_key(self, capability: str) -> str:
        return f"capability:{capability}"

    @asynccontextmanager
    async def run(self):
        # Seed the DHT with any peers that were already known before startup.
        for peer_id in self.host.get_peerstore().peer_ids():
            added = await self.dht.add_peer(peer_id)
            log("DHT", f"Seeded known peer peer_id={peer_id} added={added}")

        # Start DHT in background manually
        async with background_trio_service(self.dht):
            log("DHT", f"DHT service started in mode={self.mode}")
            yield self

    async def add_bootstrap_peers(self, bootstrap_peers: list[PeerInfo]) -> None:
        """
        Seed the DHT routing table with peers we successfully bootstrapped to.
        """
        for info in bootstrap_peers:
            added = await self.dht.add_peer(info.peer_id)
            log("DHT", f"Added bootstrap peer peer_id={info.peer_id} added={added}")

        if bootstrap_peers:
            await self.refresh_routing_table()

    async def refresh_routing_table(
        self,
        timeout_s: float = DHT_OPERATION_TIMEOUT_S,
    ) -> bool:
        with trio.move_on_after(timeout_s) as cancel_scope:
            await self.dht.refresh_routing_table()

        if cancel_scope.cancelled_caught:
            log("DHT", f"Routing table refresh timed out after {timeout_s:.1f}s")
            return False

        return True

    async def publish_profile(
        self,
        profile: NodeProfile,
        timeout_s: float = DHT_OPERATION_TIMEOUT_S,
    ) -> bool:
        key = self.profile_key(profile.peer_id)
        value = profile.to_json_bytes()

        with trio.move_on_after(timeout_s) as cancel_scope:
            await self.dht.put_value(key, value)

        if cancel_scope.cancelled_caught:
            log("DHT", f"Publish profile timed out key={key} after {timeout_s:.1f}s")
            return False

        log("DHT", f"Published profile key={key}")
        return True

    async def get_profile(
        self,
        peer_id: str,
        timeout_s: float = DHT_OPERATION_TIMEOUT_S,
    ) -> NodeProfile | None:
        key = self.profile_key(peer_id)
        raw = None

        with trio.move_on_after(timeout_s) as cancel_scope:
            raw = await self.dht.get_value(key)

        if cancel_scope.cancelled_caught:
            log("DHT", f"Get profile timed out key={key} after {timeout_s:.1f}s")
            return None

        if not raw:
            return None
        profile = NodeProfile.from_json_bytes(raw)
        log("DHT", f"Fetched profile for peer_id={peer_id}")
        return profile

    async def advertise_capabilities(
        self,
        profile: NodeProfile,
        timeout_s: float = DHT_OPERATION_TIMEOUT_S,
    ) -> dict[str, bool]:
        results: dict[str, bool] = {}

        for capability in profile.capabilities:
            key = self.capability_key(capability)
            ok = False

            with trio.move_on_after(timeout_s) as cancel_scope:
                ok = await self.dht.provide(key)

            if cancel_scope.cancelled_caught:
                log(
                    "DHT",
                    f"Advertise capability timed out capability={capability} "
                    f"after {timeout_s:.1f}s",
                )
                results[capability] = False
                continue

            log("DHT", f"Advertised capability={capability} ok={ok}")
            results[capability] = ok

        return results

    async def find_capability_providers(
        self,
        capability: str,
        timeout_s: float = DHT_OPERATION_TIMEOUT_S,
    ):
        key = self.capability_key(capability)
        providers = []

        with trio.move_on_after(timeout_s) as cancel_scope:
            providers = await self.dht.find_providers(key)

        if cancel_scope.cancelled_caught:
            log(
                "DHT",
                f"Find providers timed out capability={capability} "
                f"after {timeout_s:.1f}s",
            )
            return []

        return providers

    async def find_capability_provider_ids(self, capability: str) -> list[str]:
        """
        Return unique provider peer IDs as strings for a given capability.
        """
        providers = await self.find_capability_providers(capability)

        peer_ids: list[str] = []
        for provider in providers:
            try:
                if hasattr(provider.peer_id, "to_string"):
                    peer_ids.append(provider.peer_id.to_string())
                else:
                    peer_ids.append(str(provider.peer_id))
            except Exception:
                continue

        peer_ids = list(dict.fromkeys(peer_ids))

        log(
            "DHT",
            f"Provider ID list for capability={capability}: peer_ids={peer_ids}",
        )
        return peer_ids

    async def fetch_capability_profiles(
        self,
        capability: str,
        exclude_peer_ids: set[str] | None = None,
    ) -> list[NodeProfile]:
        exclude_peer_ids = exclude_peer_ids or set()

        provider_ids = await self.find_capability_provider_ids(capability)

        profiles: list[NodeProfile] = []
        for peer_id in provider_ids:
            if peer_id in exclude_peer_ids:
                continue

            profile = await self.get_profile(peer_id)
            if profile is None:
                continue

            profiles.append(profile)

        return profiles
