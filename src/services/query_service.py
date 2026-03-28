import uuid

import trio

from libp2p.abc import IHost
from libp2p.peer.id import ID
from libp2p.network.stream.net_stream import INetStream

from src.network_utils import connect_to_peer
from src.logging_utils import log
from src.models import QueryResult, QueryContext
from src.protocols import QUERY_PROTOCOL
from src.transport import TransportService
from src.services.routing_service import RoutingService
from src.local_agent import LocalAgent


class QueryService:
    """
    Query service provides:
    - local execution
    - safe forwarding
    - query context propagation
    - loop prevention
    - max hop bounding
    """

    def __init__(
        self,
        host: IHost,
        transport: TransportService,
        local_agent: LocalAgent,
        routing_service: RoutingService,
    ) -> None:
        self.host = host
        self.transport = transport
        self.local_agent = local_agent
        self.routing_service = routing_service

    def _parse_context_from_message(self, message: dict) -> QueryContext:
        raw = message.get("query_context", {})

        return QueryContext(
            origin_peer_id=raw.get("origin_peer_id", "unknown"),
            visited_peers=list(raw.get("visited_peers", [])),
            hop_count=int(raw.get("hop_count", 0)),
            max_hops=int(raw.get("max_hops", 3)),
        )

    async def handle_stream(self, stream: INetStream) -> None:
        """
        Registered on node startup.
        Called automatically when another node opens a stream
        using the ping protocol.
        """
        log("SERVER", "Incoming query stream from remote peer")

        try:
            message = await self.transport.receive_message(stream, role="SERVER")

            # Check that the protocole is used correctly and the type is query
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
            context = self._parse_context_from_message(message)

            log(
                "SERVER",
                f"Received query query_id={query_id} prompt={prompt!r} "
                f"hop_count={context.hop_count} visited={context.visited_peers}",
            )

            # Detect direct loops.
            if self.host.get_id().to_string() in context.visited_peers:
                answer = "[ROUTING ERROR] Loop detected: local peer already visited."
                reply = {
                    "type": "response",
                    "query_id": query_id,
                    "answer": answer,
                }
                await self.transport.send_message(stream, reply, role="SERVER")
                return

            # Build the context that this node will use / propagate further.
            next_context = QueryContext(
                origin_peer_id=context.origin_peer_id,
                visited_peers=context.visited_peers + [self.host.get_id().to_string()],
                hop_count=context.hop_count + 1,
                max_hops=context.max_hops,
            )

            decision = await self.routing_service.route_query(prompt, next_context)
            log("SERVER", f"Routing decision: {decision.reason}")

            # Bounded search ended, no suitable node found here
            if decision.no_suitable_node:
                log("SERVER", "Routing decision: no suitable node found")

                reply = {
                    "type": "response",
                    "query_id": query_id,
                    "status": "no_suitable_node",
                    "answer": None,
                }
                await self.transport.send_message(stream, reply, role="SERVER")
                log("SERVER", f"Sent no_suitable_node response for query_id={query_id}")
                return

            # Execute locally
            if decision.execute_locally:
                log("SERVER", "Routing decision: execute locally")
                answer = await self.local_agent.generate(prompt)
            # Forward to another peer
            else:
                log(
                    "SERVER",
                    f"Routing decision: forward to peer={decision.target_peer_id}",
                )

                forwarded = await self.query_peer(
                    ID.from_base58(decision.target_peer_id),
                    prompt=prompt,
                    timeout_s=10.0,
                    query_id=query_id,
                    query_context=next_context,
                )

                if forwarded.ok:
                    self.routing_service.peer_registry.mark_peer_alive(
                        decision.target_peer_id,
                        rtt_ms=None,
                    )
                    # If downstream says no suitable node was found,
                    # only the entry node should fallback locally.
                    if forwarded.status == "no_suitable_node":
                        if context.origin_peer_id == "external-client":
                            log(
                                "SERVER",
                                "No suitable remote node found; entry node falls back locally",
                            )
                            answer = await self.local_agent.generate(prompt)
                        else:
                            reply = {
                                "type": "response",
                                "query_id": query_id,
                                "status": "no_suitable_node",
                                "answer": None,
                            }
                            await self.transport.send_message(
                                stream, reply, role="SERVER"
                            )
                            log(
                                "SERVER",
                                f"Propagated no_suitable_node for query_id={query_id}",
                            )
                            return
                    else:
                        answer = forwarded.answer
                else:
                    self.routing_service.peer_registry.mark_peer_unreachable(
                        decision.target_peer_id
                    )
                    log(
                        "SERVER",
                        f"Forwarding failed, fallback to local: {forwarded.error}",
                    )
                    answer = await self.local_agent.generate(prompt)

            reply = {
                "type": "response",
                "query_id": query_id,
                "status": "ok",
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
        peer_id: ID,
        prompt: str,
        timeout_s: float = 10.0,
        query_id: str | None = None,
        query_context: QueryContext | None = None,
    ) -> QueryResult:
        """
        Send one query to another peer and wait for the response.
        """
        if query_id is None:
            query_id = str(uuid.uuid4())

        if query_context is None:
            query_context = QueryContext(
                origin_peer_id=self.host.get_id().to_string(),
                visited_peers=[],
                hop_count=0,
                max_hops=3,
            )

        stream = None

        try:
            # Send query to node with peer_id
            with trio.fail_after(timeout_s):
                known_addr = self.routing_service.peer_registry.get_any_address(
                    str(peer_id)
                )

                if known_addr is not None:
                    try:
                        await connect_to_peer(self.host, known_addr)
                    except Exception as exc:
                        log("CLIENT", f"Lazy connect failed for peer={peer_id}: {exc}")

                stream = await self.transport.open_stream(
                    self.host, peer_id, QUERY_PROTOCOL
                )

                payload = {
                    "type": "query",
                    "query_id": query_id,
                    "prompt": prompt,
                    "query_context": {
                        "origin_peer_id": query_context.origin_peer_id,
                        "visited_peers": query_context.visited_peers,
                        "hop_count": query_context.hop_count,
                        "max_hops": query_context.max_hops,
                    },
                }

                await self.transport.send_message(stream, payload, role="CLIENT")
                log(
                    "CLIENT",
                    f"Query sent to peer={peer_id} query_id={query_id} "
                    f"hop_count={query_context.hop_count} visited={query_context.visited_peers}",
                )

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
                    status="error",
                )

            # Check that the response has the same query_id
            if reply.get("query_id") != query_id:
                return QueryResult(
                    ok=False,
                    peer_id=str(peer_id),
                    answer=None,
                    error="query_id mismatch",
                    status="error",
                )

            return QueryResult(
                ok=True,
                peer_id=str(peer_id),
                answer=reply.get("answer"),
                error=None,
                status=reply.get("status", "ok"),
            )

        except trio.TooSlowError:
            log("CLIENT", f"Query timeout after {timeout_s:.2f}s")
            return QueryResult(
                ok=False,
                peer_id=str(peer_id),
                answer=None,
                error=f"timeout after {timeout_s:.2f}s",
                status="error",
            )
        except Exception as exc:
            log("CLIENT", f"Query failed: {exc}")
            return QueryResult(
                ok=False,
                peer_id=str(peer_id),
                answer=None,
                error=str(exc),
                status="error",
            )
        finally:
            if stream is not None:
                try:
                    log("CLIENT", "Closing query stream")
                    await stream.close()
                except Exception:
                    pass
