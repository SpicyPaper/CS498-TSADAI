from collections.abc import Awaitable, Callable
from dataclasses import asdict

import trio

from libp2p.abc import IHost
from libp2p.network.stream.net_stream import INetStream
from libp2p.peer.id import ID

from src.logging_utils import log
from src.models import NodeProfile
from src.network_utils import connect_to_peer
from src.protocols import PROFILE_PROTOCOL
from src.transport import TransportService


class ProfileService:
    """
    Direct profile RPC.

    The DHT is used only as a capability index. Full node metadata is fetched
    from the peer that owns it.
    """

    def __init__(
        self,
        host: IHost,
        transport: TransportService,
        profile_provider: Callable[[], Awaitable[NodeProfile]],
    ) -> None:
        self.host = host
        self.transport = transport
        self.profile_provider = profile_provider

    async def handle_stream(self, stream: INetStream) -> None:
        try:
            message = await self.transport.receive_message(stream, role="SERVER")

            if message.get("type") != "profile_request":
                reply = {
                    "type": "error",
                    "error": f"unexpected message type: {message.get('type')}",
                }
                await self.transport.send_message(stream, reply, role="SERVER")
                return

            profile = await self.profile_provider()
            reply = {
                "type": "profile_response",
                "profile": asdict(profile),
            }
            await self.transport.send_message(stream, reply, role="SERVER")

        except Exception as exc:
            log("PROFILE", f"Profile handler error: {exc}")
            raise
        finally:
            await stream.close()

    async def request_profile(
        self,
        peer_id: ID,
        addresses: list[str] | None = None,
        timeout_s: float = 3.0,
    ) -> NodeProfile | None:
        stream = None
        addresses = addresses or []

        try:
            with trio.fail_after(timeout_s):
                for address in addresses:
                    try:
                        await connect_to_peer(self.host, address)
                        break
                    except Exception as exc:
                        log(
                            "PROFILE",
                            f"Lazy connect failed peer={peer_id} "
                            f"addr={address}: {exc}",
                        )

                stream = await self.transport.open_stream(
                    self.host,
                    peer_id,
                    PROFILE_PROTOCOL,
                )
                await self.transport.send_message(
                    stream,
                    {"type": "profile_request"},
                    role="CLIENT",
                )
                reply = await self.transport.receive_message(stream, role="CLIENT")

            if reply.get("type") != "profile_response":
                log("PROFILE", f"Unexpected profile response type={reply.get('type')}")
                return None

            data = reply.get("profile")
            if not isinstance(data, dict):
                log("PROFILE", "Profile response did not contain a profile object")
                return None

            profile = NodeProfile(**data)
            expected_peer_id = peer_id.to_string()
            if profile.peer_id != expected_peer_id:
                log(
                    "PROFILE",
                    f"Profile peer_id mismatch expected={expected_peer_id} "
                    f"got={profile.peer_id}",
                )
                return None

            return profile

        except trio.TooSlowError:
            log("PROFILE", f"Profile request timed out peer={peer_id}")
            return None
        except Exception as exc:
            log("PROFILE", f"Profile request failed peer={peer_id}: {exc}")
            return None
        finally:
            if stream is not None:
                try:
                    await stream.close()
                except Exception:
                    pass
