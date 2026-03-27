from contextlib import asynccontextmanager

from libp2p.abc import IHost
from libp2p.kad_dht.kad_dht import DHTMode, KadDHT
from libp2p.records.validator import Validator
from libp2p.tools.async_service import background_trio_service

from src.logging_utils import log
from src.models import NodeProfile


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
        self.dht = KadDHT(host, mode)
        self.dht.register_validator(self.PROFILE_NAMESPACE, ProfileValidator())

    def profile_key(self, peer_id: str) -> str:
        return f"/{self.PROFILE_NAMESPACE}/{peer_id}"

    def capability_key(self, capability: str) -> str:
        return f"capability:{capability}"

    @asynccontextmanager
    async def run(self):
        # Add currently known peers to routing table
        for peer_id in self.host.get_peerstore().peer_ids():
            await self.dht.routing_table.add_peer(peer_id)

        # Start DHT in background manually
        async with background_trio_service(self.dht):
            log("DHT", f"DHT service started in mode={self.mode}")
            yield self

    async def publish_profile(self, profile: NodeProfile) -> None:
        key = self.profile_key(profile.peer_id)
        value = profile.to_json_bytes()
        await self.dht.put_value(key, value)
        log("DHT", f"Published profile key={key}")

    async def get_profile(self, peer_id: str) -> NodeProfile | None:
        key = self.profile_key(peer_id)
        raw = await self.dht.get_value(key)
        if not raw:
            return None
        profile = NodeProfile.from_json_bytes(raw)
        log("DHT", f"Fetched profile for peer_id={peer_id}")
        return profile

    async def advertise_capabilities(self, profile: NodeProfile) -> None:
        for capability in profile.capabilities:
            ok = await self.dht.provide(self.capability_key(capability))
            log("DHT", f"Advertised capability={capability} ok={ok}")

    async def find_capability_providers(self, capability: str):
        providers = await self.dht.find_providers(self.capability_key(capability))
        log("DHT", f"Found {len(providers)} providers for capability={capability}")
        return providers

    async def find_capability_provider_ids(self, capability: str) -> list[str]:
        """
        Return provider peer IDs as strings for a given capability.
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

        log(
            "DHT",
            f"Provider ID list for capability={capability}: peer_ids={peer_ids}",
        )
        return peer_ids
