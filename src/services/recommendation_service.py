from libp2p.abc import IHost
from libp2p.peer.id import ID
from libp2p.network.stream.net_stream import INetStream

from src.logging_utils import log
from src.models import NodeProfile
from src.network_utils import connect_to_peer
from src.peer_registry import PeerRegistry
from src.protocols import RECOMMEND_PROTOCOL
from src.transport import TransportService
from src.services.dht_service import DHTService


class RecommendationService:
    """
    Small RPC service for asking a peer:
    which peers do you know for capability X?
    """

    def __init__(
        self,
        host: IHost,
        transport: TransportService,
        peer_registry: PeerRegistry,
        local_peer_id: str,
        dht_service: DHTService,
    ) -> None:
        self.host = host
        self.transport = transport
        self.peer_registry = peer_registry
        self.local_peer_id = local_peer_id
        self.dht_service = dht_service

    async def _refresh_candidates_from_dht(self, capability: str) -> None:
        if self.dht_service is None:
            return

        profiles = await self.dht_service.fetch_capability_profiles(
            capability,
            exclude_peer_ids={self.local_peer_id},
        )

        for profile in profiles:
            self.peer_registry.upsert_profile(profile)

    def _select_recommendations(
        self,
        capability: str,
        limit: int,
        exclude_peer_ids: set[str],
    ) -> list[str]:
        candidates: list[NodeProfile] = []

        for profile in self.peer_registry.all_profiles():
            if profile.peer_id == self.local_peer_id:
                continue
            if profile.peer_id in exclude_peer_ids:
                continue
            if capability not in profile.capabilities:
                continue

            candidates.append(profile)

        candidates.sort(
            key=lambda profile: profile.capability_scores.get(capability, 0.0),
            reverse=True,
        )

        return [profile.peer_id for profile in candidates[:limit]]

    async def handle_stream(self, stream: INetStream) -> None:
        try:
            message = await self.transport.receive_message(stream, role="SERVER")

            if message.get("type") != "recommend_request":
                reply = {
                    "type": "error",
                    "error": f"unexpected message type: {message.get('type')}",
                }
                await self.transport.send_message(stream, reply, role="SERVER")
                return

            capability = message.get("capability", "")
            limit = int(message.get("limit", 3))
            exclude_peer_ids = set(message.get("exclude_peer_ids", []))
            exclude_peer_ids.add(self.local_peer_id)

            await self._refresh_candidates_from_dht(capability)

            peer_ids = self._select_recommendations(
                capability=capability,
                limit=limit,
                exclude_peer_ids=exclude_peer_ids,
            )

            known_capable_peer_ids = [
                profile.peer_id
                for profile in self.peer_registry.all_profiles()
                if profile.peer_id != self.local_peer_id
                and capability in profile.capabilities
            ]

            reply = {
                "type": "recommend_response",
                "capability": capability,
                "peer_ids": peer_ids,
            }
            await self.transport.send_message(stream, reply, role="SERVER")

            log(
                "RECOMMEND",
                f"Recommendation selection capability={capability} "
                f"known_capable_peer_ids={known_capable_peer_ids} "
                f"excluded_peer_ids={sorted(exclude_peer_ids)} "
                f"returned={peer_ids}",
            )

        except Exception as exc:
            log("RECOMMEND", f"Recommendation handler error: {exc}")
            raise
        finally:
            await stream.close()

    async def request_recommendations(
        self,
        peer_id: ID,
        capability: str,
        limit: int,
        exclude_peer_ids: list[str],
    ) -> list[str]:
        stream = None

        try:
            known_addr = self.peer_registry.get_any_address(str(peer_id))
            if known_addr is not None:
                try:
                    await connect_to_peer(self.host, known_addr)
                except Exception as exc:
                    log("RECOMMEND", f"Lazy connect failed for peer={peer_id}: {exc}")

            stream = await self.transport.open_stream(
                self.host,
                peer_id,
                RECOMMEND_PROTOCOL,
            )

            payload = {
                "type": "recommend_request",
                "capability": capability,
                "limit": limit,
                "exclude_peer_ids": exclude_peer_ids,
            }

            await self.transport.send_message(stream, payload, role="CLIENT")
            reply = await self.transport.receive_message(stream, role="CLIENT")

            if reply.get("type") != "recommend_response":
                log(
                    "RECOMMEND",
                    f"Unexpected recommendation response type={reply.get('type')}",
                )
                return []

            if reply.get("capability") != capability:
                log(
                    "RECOMMEND",
                    f"Capability mismatch in recommendation response: "
                    f"expected={capability} got={reply.get('capability')}",
                )
                return []

            peer_ids = reply.get("peer_ids", [])
            if not isinstance(peer_ids, list):
                return []

            return [str(pid) for pid in peer_ids]

        except Exception as exc:
            log("RECOMMEND", f"Recommendation request failed peer={peer_id}: {exc}")
            return []
        finally:
            if stream is not None:
                try:
                    await stream.close()
                except Exception:
                    pass
