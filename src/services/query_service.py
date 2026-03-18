import uuid

import trio

from src.logging_utils import log
from src.models import QueryResult
from src.protocols import QUERY_PROTOCOL


class QueryService:
    """
    This service can:
    - receive an incoming query
    - decide whether to answer locally or forward
    - send outgoing queries to other peers
    """

    def __init__(self, transport, local_agent, routing_service, host) -> None:
        self.transport = transport
        self.local_agent = local_agent
        self.routing_service = routing_service
        self.host = host

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

            decision = self.routing_service.route_query(prompt)

            if decision.execute_locally:
                log("SERVER", "Routing decision: execute locally")
                answer = await self.local_agent.generate(prompt)
            else:
                log(
                    "SERVER",
                    f"Routing decision: forward to peer={decision.target_peer_id}",
                )

                profile = self.routing_service.peer_registry.get_profile(
                    decision.target_peer_id
                )
                if profile is None:
                    raise RuntimeError(
                        f"unknown target peer: {decision.target_peer_id}"
                    )

                if not profile.addresses:
                    raise RuntimeError(
                        f"no known address for peer: {decision.target_peer_id}"
                    )

                info = await self.routing_service.connect_to_peer(
                    self.host, profile.addresses[0]
                )

                forwarded = await self.query_peer(
                    self.host,
                    info.peer_id,
                    prompt=prompt,
                    timeout_s=10.0,
                    query_id=query_id,
                )

                if not forwarded.ok:
                    answer = f"[FORWARD FAILED] {forwarded.error}"
                else:
                    answer = forwarded.answer

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
        self,
        host,
        peer_id,
        prompt: str,
        timeout_s: float = 10.0,
        query_id: str | None = None,
    ) -> QueryResult:
        """
        Send one query to another peer and wait for the response.
        """
        if query_id is None:
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
