import json
from contextlib import asynccontextmanager


from libp2p.abc import IHost
from libp2p.pubsub.gossipsub import (
    GossipSub,
    PROTOCOL_ID,
    PROTOCOL_ID_V11,
    PROTOCOL_ID_V12,
)
from libp2p.pubsub.pubsub import Pubsub
from libp2p.tools.async_service import background_trio_service

from src.logging_utils import log
from src.services.dht_service import DHTService
from src.peer_registry import PeerRegistry


PROFILE_UPDATES_TOPIC = "tsadai.profile.updates"


class PubSubService:
    """
    GossipSub service.

    Use gossip only for fast notifications, not as the source of truth.
    When a profile update is received, refresh from DHT.
    """

    def __init__(
        self,
        host: IHost,
        dht_service: DHTService,
        peer_registry: PeerRegistry,
        local_peer_id: str,
    ) -> None:
        self.host = host
        self.dht_service = dht_service
        self.peer_registry = peer_registry
        self.local_peer_id = local_peer_id

        self.router = GossipSub(
            protocols=[PROTOCOL_ID, PROTOCOL_ID_V11, PROTOCOL_ID_V12],
            degree=8,
            degree_low=6,
            degree_high=12,
        )
        self.pubsub = Pubsub(host, self.router)
        self.subscription = None

    @asynccontextmanager
    async def run(self):
        async with background_trio_service(self.pubsub):
            await self.pubsub.wait_until_ready()
            self.subscription = await self.pubsub.subscribe(PROFILE_UPDATES_TOPIC)
            log("GOSSIP", f"Subscribed to topic={PROFILE_UPDATES_TOPIC}")
            yield self

    async def publish_profile_update(self, peer_id: str, timestamp_ms: int) -> None:
        payload = {
            "event": "profile_updated",
            "peer_id": peer_id,
            "timestamp_ms": timestamp_ms,
        }
        await self.pubsub.publish(
            PROFILE_UPDATES_TOPIC, json.dumps(payload).encode("utf-8")
        )
        log("GOSSIP", f"Published profile update for peer_id={peer_id}")

    async def run_profile_update_listener(self) -> None:
        if self.subscription is None:
            raise RuntimeError("PubSub subscription not initialized")

        while True:
            msg = await self.subscription.get()
            payload = json.loads(bytes(msg.data).decode("utf-8"))

            if payload.get("event") != "profile_updated":
                continue

            peer_id = payload.get("peer_id")
            if not peer_id:
                continue

            log("GOSSIP", f"Received gossip payload: {payload}")
            log("GOSSIP", f"Received profile_updated for peer_id={peer_id}")

            # Ignore our own profile update
            if peer_id == self.local_peer_id:
                log("GOSSIP", f"Ignoring self profile update for peer_id={peer_id}")
                continue

            profile = await self.dht_service.get_profile(peer_id)
            if profile is None:
                log("GOSSIP", f"No DHT profile found for peer_id={peer_id}")
                continue

            self.peer_registry.upsert_profile(profile)
            log("GOSSIP", f"Refreshed profile from DHT for peer_id={peer_id}")
