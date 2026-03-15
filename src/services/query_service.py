"""
Query service.

This service does two things:
- handle an incoming query stream and answer with llm answer
- initiate a query to another node and wait for its answer

NOTE: But for now, the answer is a temporary fake one, no llm is
involved currently.
"""

import uuid
import trio

from src.logging_utils import log
from src.models import QueryResult
from src.protocols import QUERY_PROTOCOL


class QueryService:
    def __init__(self, transport) -> None:
        self.transport = transport

    async def handle_stream(self, stream) -> None:
        """
        Called automatically when another node opens a stream
        using the query protocol.
        """
        log("SERVER", "Incoming query stream from remote peer")

        try:
            message = await self.transport.receive_message(stream, role="SERVER")

            # Check that protocole is used correctly and type is query
            if message.get("type") != "query":
                error_payload = {
                    "type": "error",
                    "error": f"unexpected message type: {message.get('type')}",
                }
                log("SERVER", "Received unexpected message on query protocol")
                await self.transport.send_message(stream, error_payload, role="SERVER")
                return

            prompt = message.get("prompt", "")
            query_id = message.get("query_id")

            log("SERVER", f"Received query query_id={query_id} prompt={prompt!r}")

            answer = f"Stub answer from remote node. Received prompt: {prompt}"

            reply = {
                "type": "response",
                "query_id": query_id,
                "answer": answer,
            }

            await self.transport.send_message(stream, reply, role="SERVER")
            log("SERVER", f"Sent response for query_id={query_id}")

        except Exception as exc:
            log("SERVER", f"Query handler error: {exc}")
            raise
        finally:
            log("SERVER", "Closing query stream")
            await stream.close()

    async def query_peer(
        self, host, peer_id, prompt: str, timeout_s: float = 10.0
    ) -> QueryResult:
        """
        Send one query request to a peer and wait for reply.
        """
        query_id = str(uuid.uuid4())
        stream = None

        try:
            # Send query to node with peer_id
            with trio.fail_after(timeout_s):
                stream = await self.transport.open_stream(host, peer_id, QUERY_PROTOCOL)

                payload = {
                    "type": "query",
                    "query_id": query_id,
                    "prompt": prompt,
                }

                await self.transport.send_message(stream, payload, role="CLIENT")
                log("CLIENT", f"Query sent to peer={peer_id} query_id={query_id}")

                reply = await self.transport.receive_message(stream, role="CLIENT")
                log(
                    "CLIENT",
                    f"Response received from peer={peer_id} query_id={query_id}",
                )

            # Check that the response is a response
            if reply.get("type") != "response":
                return QueryResult(
                    ok=False,
                    peer_id=str(peer_id),
                    answer=None,
                    error=f"unexpected response type: {reply.get('type')}",
                )

            # Check that the response has the same query_id
            if reply.get("query_id") != query_id:
                return QueryResult(
                    ok=False,
                    peer_id=str(peer_id),
                    answer=None,
                    error="query_id mismatch",
                )

            return QueryResult(
                ok=True,
                peer_id=str(peer_id),
                answer=reply.get("answer"),
                error=None,
            )

        except trio.TooSlowError:
            log("CLIENT", f"Query timeout after {timeout_s:.2f}s")
            return QueryResult(
                ok=False,
                peer_id=str(peer_id),
                answer=None,
                error=f"timeout after {timeout_s:.2f}s",
            )
        except Exception as exc:
            log("CLIENT", f"Query failed: {exc}")
            return QueryResult(
                ok=False,
                peer_id=str(peer_id),
                answer=None,
                error=str(exc),
            )
        finally:
            if stream is not None:
                try:
                    log("CLIENT", "Closing query stream")
                    await stream.close()
                except Exception:
                    pass
