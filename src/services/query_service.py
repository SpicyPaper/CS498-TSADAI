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


DEFAULT_QUERY_TIMEOUT_S = 330.0


class QueryService:
    """
    Query service provides:
    - local execution
    - one-hop forwarding to the selected node
    - query context propagation
    - rerouting after failed forwards
    """

    def __init__(
        self,
        host: IHost,
        transport: TransportService,
        local_agent: LocalAgent,
        routing_service: RoutingService,
        query_timeout_s: float = DEFAULT_QUERY_TIMEOUT_S,
    ) -> None:
        self.host = host
        self.transport = transport
        self.local_agent = local_agent
        self.routing_service = routing_service
        self.query_timeout_s = query_timeout_s

    def _parse_context_from_message(self, message: dict) -> QueryContext:
        raw = message.get("query_context", {})
        excluded_peer_ids = raw.get("excluded_peer_ids", raw.get("visited_peers", []))
        routed_by_peer_id = raw.get("routed_by_peer_id")
        if routed_by_peer_id is None and raw.get("visited_peers"):
            routed_by_peer_id = str(raw["visited_peers"][-1])

        return QueryContext(
            origin_peer_id=raw.get("origin_peer_id", "unknown"),
            excluded_peer_ids=list(excluded_peer_ids),
            required_capabilities=raw.get("required_capabilities"),
            routed_by_peer_id=routed_by_peer_id,
        )

    def _node_summary(self) -> dict:
        profile = self.routing_service.local_profile
        return {
            "peer_id": profile.peer_id,
            "model_name": profile.model_name,
            "capabilities": profile.capabilities,
            "capability_scores": profile.capability_scores,
        }

    def _response_trace(
        self,
        query_id: str,
        decision_trace: dict | None,
        action: str,
        answered_by: dict | None = None,
        downstream_trace: dict | None = None,
    ) -> dict:
        hop = dict(decision_trace or {})
        hop["node"] = self._node_summary()
        hop["action"] = action

        if downstream_trace and isinstance(downstream_trace.get("hops"), list):
            hops = [hop] + downstream_trace["hops"]
            final_answered_by = downstream_trace.get("answered_by") or answered_by
        else:
            hops = [hop]
            final_answered_by = answered_by

        return {
            "query_id": query_id,
            "answered_by": final_answered_by,
            "hops": hops,
        }

    async def answer_query(
        self,
        prompt: str,
        query_id: str | None = None,
        context: QueryContext | None = None,
    ) -> dict:
        """
        Answer one query using the same routing path as the libp2p query protocol.

        This is the core query behavior. It is used by both:
        - handle_stream, when the query comes from another libp2p node
        - the optional HTTP API, when the query comes from a UI/client
        """
        if query_id is None:
            query_id = str(uuid.uuid4())

        if context is None:
            context = QueryContext(
                origin_peer_id="external-client",
            )

        # A forwarded query is an execution request for this selected node.
        if context.routed_by_peer_id is not None:
            if context.routed_by_peer_id == self.host.get_id().to_string():
                return {
                    "type": "response",
                    "query_id": query_id,
                    "status": "routing_error",
                    "answer": "[ROUTING ERROR] Node received a query routed by itself.",
                    "routing_trace": {
                        "query_id": query_id,
                        "answered_by": None,
                        "hops": [
                            {
                                "node": self._node_summary(),
                                "action": "loop_detected",
                                "routed_by_peer_id": context.routed_by_peer_id,
                            }
                        ],
                    },
                }

            try:
                answer = await self.local_agent.generate(prompt)
            except Exception as exc:
                log("SERVER", f"Forwarded local generation failed: {exc}")
                return {
                    "type": "response",
                    "query_id": query_id,
                    "status": "generation_error",
                    "answer": f"[GENERATION ERROR] {exc}",
                    "routing_trace": {
                        "query_id": query_id,
                        "answered_by": None,
                        "hops": [
                            {
                                "node": self._node_summary(),
                                "action": "forwarded_generation_error",
                                "required_capabilities": context.required_capabilities,
                                "routed_by_peer_id": context.routed_by_peer_id,
                                "error": str(exc),
                            }
                        ],
                    },
                }

            return {
                "type": "response",
                "query_id": query_id,
                "status": "ok",
                "answer": answer,
                "routing_trace": {
                    "query_id": query_id,
                    "answered_by": self._node_summary(),
                    "hops": [
                        {
                            "node": self._node_summary(),
                            "action": "execute_forwarded_request",
                            "required_capabilities": context.required_capabilities,
                            "routed_by_peer_id": context.routed_by_peer_id,
                        }
                    ],
                },
            }

        # Build the routing context used only by the entry/rerouting node.
        routing_context = QueryContext(
            origin_peer_id=context.origin_peer_id,
            excluded_peer_ids=context.excluded_peer_ids,
            required_capabilities=context.required_capabilities,
        )

        try:
            decision = await self.routing_service.route_query(prompt, routing_context)
        except Exception as exc:
            log("SERVER", f"Routing failed: {exc}")
            return {
                "type": "response",
                "query_id": query_id,
                "status": "routing_error",
                "answer": f"[ROUTING ERROR] {exc}",
                "routing_trace": {
                    "query_id": query_id,
                    "answered_by": None,
                    "hops": [
                        {
                            "node": self._node_summary(),
                            "action": "routing_error",
                            "error": str(exc),
                        }
                    ],
                },
            }
        log("SERVER", f"Routing decision: {decision.reason}")

        # Bounded search ended, no suitable node found here.
        if decision.no_suitable_node:
            return {
                "type": "response",
                "query_id": query_id,
                "status": "no_suitable_node",
                "answer": None,
                "routing_trace": self._response_trace(
                    query_id,
                    decision.routing_trace,
                    "no_suitable_node",
                ),
            }

        # Execute locally.
        if decision.execute_locally:
            try:
                answer = await self.local_agent.generate(prompt)
            except Exception as exc:
                log("SERVER", f"Local generation failed: {exc}")
                return {
                    "type": "response",
                    "query_id": query_id,
                    "status": "generation_error",
                    "answer": f"[GENERATION ERROR] {exc}",
                    "routing_trace": self._response_trace(
                        query_id,
                        decision.routing_trace,
                        "generation_error",
                    ),
                }
            routing_trace = self._response_trace(
                query_id,
                decision.routing_trace,
                "execute_local",
                answered_by=self._node_summary(),
            )
        # Forward to another peer.
        else:
            answer = None
            routing_trace = None
            excluded_peer_ids = list(routing_context.excluded_peer_ids)

            for attempt in range(3):
                log(
                    "SERVER",
                    f"Routing decision: forward to peer={decision.target_peer_id} "
                    f"attempt={attempt + 1}",
                )

                forwarded = await self.query_peer(
                    ID.from_base58(decision.target_peer_id),
                    prompt=prompt,
                    timeout_s=self.query_timeout_s,
                    query_id=query_id,
                    query_context=QueryContext(
                        origin_peer_id=routing_context.origin_peer_id,
                        required_capabilities=routing_context.required_capabilities,
                        routed_by_peer_id=self.host.get_id().to_string(),
                    ),
                )

                if forwarded.ok:
                    self.routing_service.peer_registry.mark_peer_alive(
                        decision.target_peer_id,
                        rtt_ms=None,
                    )

                    if forwarded.status == "no_suitable_node":
                        return {
                            "type": "response",
                            "query_id": query_id,
                            "status": "no_suitable_node",
                            "answer": None,
                            "routing_trace": self._response_trace(
                                query_id,
                                decision.routing_trace,
                                "forward_no_suitable_node",
                                downstream_trace=forwarded.routing_trace,
                            ),
                        }

                    if forwarded.status != "ok":
                        return {
                            "type": "response",
                            "query_id": query_id,
                            "status": forwarded.status,
                            "answer": forwarded.answer,
                            "routing_trace": self._response_trace(
                                query_id,
                                decision.routing_trace,
                                "forward_error",
                                downstream_trace=forwarded.routing_trace,
                            ),
                        }

                    answer = forwarded.answer
                    routing_trace = self._response_trace(
                        query_id,
                        decision.routing_trace,
                        "forward",
                        downstream_trace=forwarded.routing_trace,
                    )
                    break

                self.routing_service.peer_registry.mark_peer_unreachable(
                    decision.target_peer_id
                )
                log(
                    "SERVER",
                    f"Forwarding failed to peer={decision.target_peer_id}: {forwarded.error}",
                )

                excluded_peer_ids.append(decision.target_peer_id)

                # Ask the existing router again after excluding the failed peer.
                decision = await self.routing_service.route_query(
                    prompt,
                    QueryContext(
                        origin_peer_id=routing_context.origin_peer_id,
                        excluded_peer_ids=excluded_peer_ids,
                        required_capabilities=routing_context.required_capabilities,
                    ),
                )

                if decision.no_suitable_node:
                    return {
                        "type": "response",
                        "query_id": query_id,
                        "status": "no_suitable_node",
                        "answer": None,
                        "routing_trace": self._response_trace(
                            query_id,
                            decision.routing_trace,
                            "no_suitable_node_after_forward_failure",
                        ),
                    }

                if decision.execute_locally:
                    log("SERVER", "Re-routing decision after failure: execute locally")
                    answer = await self.local_agent.generate(prompt)
                    routing_trace = self._response_trace(
                        query_id,
                        decision.routing_trace,
                        "execute_local_after_forward_failure",
                        answered_by=self._node_summary(),
                    )
                    break

            if answer is None:
                return {
                    "type": "response",
                    "query_id": query_id,
                    "status": "no_suitable_node",
                    "answer": None,
                    "routing_trace": self._response_trace(
                        query_id,
                        decision.routing_trace,
                        "forwarding_exhausted",
                    ),
                }

        return {
            "type": "response",
            "query_id": query_id,
            "status": "ok",
            "answer": answer,
            "routing_trace": routing_trace,
        }

    async def handle_stream(self, stream: INetStream) -> None:
        """
        Registered on node startup.
        Called automatically when another node opens a stream
        using the query protocol.
        """
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
                f"Received query query_id={query_id} "
                f"required_capabilities={context.required_capabilities} "
                f"routed_by={context.routed_by_peer_id} "
                f"excluded={len(context.excluded_peer_ids)} prompt={prompt!r}",
            )

            reply = await self.answer_query(
                prompt=prompt,
                query_id=query_id,
                context=context,
            )

            await self.transport.send_message(stream, reply, role="SERVER")
            log(
                "SERVER",
                f"Sent response for query_id={query_id} "
                f"status={reply.get('status', 'ok')}",
            )

        except Exception as exc:
            log("SERVER", f"Query handler error: {exc}")
            raise
        finally:
            await stream.close()

    async def query_peer(
        self,
        peer_id: ID,
        prompt: str,
        timeout_s: float = DEFAULT_QUERY_TIMEOUT_S,
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
                        "routed_by_peer_id": query_context.routed_by_peer_id,
                        "excluded_peer_ids": query_context.excluded_peer_ids,
                        "required_capabilities": query_context.required_capabilities,
                    },
                }

                await self.transport.send_message(stream, payload, role="CLIENT")
                log("CLIENT", f"Query sent to peer={peer_id} query_id={query_id} ")

                reply = await self.transport.receive_message(stream, role="CLIENT")
                log(
                    "CLIENT",
                    f"Response received from peer={peer_id} query_id={query_id} "
                    f"status={reply.get('status', 'ok')}",
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
                routing_trace=reply.get("routing_trace"),
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
                    await stream.close()
                except Exception:
                    pass
