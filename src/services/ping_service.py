"""
Ping service.

This service does two things:
- handle an incoming ping stream and answer with pong
- initiate a ping to another node and wait for pong
"""

import secrets
import time

import trio

from src.logging_utils import log
from src.models import PingResult
from src.protocols import PING_PROTOCOL


class PingService:
    def __init__(self, transport) -> None:
        self.transport = transport

    async def handle_stream(self, stream) -> None:
        """
        Called automatically when another node opens a stream
        using the ping protocol.
        """
        log("SERVER", "Incoming ping stream from remote peer")

        try:
            message = await self.transport.receive_message(stream, role="SERVER")

            # Check that protocole is used correctly and type is ping
            if message.get("type") != "ping":
                error_payload = {
                    "type": "error",
                    "error": f"unexpected message type: {message.get('type')}",
                }

                log("SERVER", "Received unexpected message on ping protocol")

                await self.transport.send_message(stream, error_payload, role="SERVER")
                return

            log("SERVER", f"Received ping with nonce={message.get('nonce')}")

            reply = {
                "type": "pong",
                "nonce": message.get("nonce"),
                "ts_ms": int(time.time() * 1000),
            }

            await self.transport.send_message(stream, reply, role="SERVER")

            log("SERVER", f"Sent pong with nonce={message.get('nonce')}")

        except Exception as exc:
            log("SERVER", f"Ping handler error: {exc}")
            raise
        finally:
            log("SERVER", "Closing ping stream")
            await stream.close()

    async def ping_peer(self, host, peer_id, timeout_s: float = 3.0) -> PingResult:
        """
        Send one ping request to a peer and wait for pong.
        """
        nonce = secrets.token_hex(8)
        start = time.perf_counter()
        stream = None

        try:
            # Send ping to node with peer_id
            with trio.fail_after(timeout_s):
                stream = await self.transport.open_stream(host, peer_id, PING_PROTOCOL)

                payload = {
                    "type": "ping",
                    "nonce": nonce,
                    "ts_ms": int(time.time() * 1000),
                }

                await self.transport.send_message(stream, payload, role="CLIENT")
                log("CLIENT", f"Ping sent to peer={peer_id}")

                reply = await self.transport.receive_message(stream, role="CLIENT")
                log("CLIENT", f"Pong received from peer={peer_id}")

            end = time.perf_counter()

            # Check that the response is a pong
            if reply.get("type") != "pong":
                return PingResult(
                    ok=False,
                    peer_id=str(peer_id),
                    rtt_ms=None,
                    error=f"unexpected response type: {reply.get('type')}",
                )

            # Check that the response has the same nonce
            if reply.get("nonce") != nonce:
                return PingResult(
                    ok=False,
                    peer_id=str(peer_id),
                    rtt_ms=None,
                    error="nonce mismatch",
                )

            return PingResult(
                ok=True,
                peer_id=str(peer_id),
                rtt_ms=(end - start) * 1000.0,
                error=None,
            )

        except trio.TooSlowError:
            log("CLIENT", f"Ping timeout after {timeout_s:.2f}s")
            return PingResult(
                ok=False,
                peer_id=str(peer_id),
                rtt_ms=None,
                error=f"timeout after {timeout_s:.2f}s",
            )
        except Exception as exc:
            log("CLIENT", f"Ping failed: {exc}")
            return PingResult(
                ok=False,
                peer_id=str(peer_id),
                rtt_ms=None,
                error=str(exc),
            )
        finally:
            if stream is not None:
                try:
                    log("CLIENT", "Closing ping stream")
                    await stream.close()
                except Exception:
                    pass
