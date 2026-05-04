import random
import secrets

import time
import trio
from libp2p import new_host
from libp2p.crypto.secp256k1 import create_new_key_pair
from libp2p.kad_dht.kad_dht import DHTMode
from libp2p.utils.address_validation import (
    find_free_port,
    get_available_interfaces,
)

from src.local_agent import DummyAgent, QwenAgent, OllamaAgent
from src.network_utils import connect_to_bootstrap_peers
from src.logging_utils import log
from src.models import NodeProfile, QueryContext
from src.peer_registry import PeerRegistry
from src.protocols import PING_PROTOCOL, QUERY_PROTOCOL, RECOMMEND_PROTOCOL
from src.services.dht_service import DHTService
from src.services.health_service import HealthService
from src.services.ping_service import PingService
from src.services.pubsub_service import PubSubService
from src.services.query_service import QueryService
from src.services.routing_service import RoutingService
from src.services.recommendation_service import RecommendationService
from src.services.capability_classifier import CAPABILITIES, CapabilityClassifier
from src.transport import TransportService


class Node:
    """
    Center of logic.

    Manage the services (transport, routing, ping, query, etc.).
    Register and query the local agent LLM.
    Stores peers through the peer registery.
    """

    def __init__(
        self,
        port: int = 0,
        seed: int | None = None,
        model_name: str = "dummy-model",
        capabilities: list[str] | None = None,
        capability_scores: dict[str, float] | None = None,
        dht_mode: DHTMode = DHTMode.SERVER,
        advertise_address_mode: str = "ipv6_loopback",
        enable_gossip: bool = False,
        agent_backend: str = "dummy",
        llm_model_id: str = "Qwen/Qwen3-0.6B",
        llm_max_new_tokens: int = 512,
        llm_enable_thinking: bool = False,
        ollama_model: str = "qwen3:1.7b",
        ollama_host: str = "http://localhost:11434",
        ollama_timeout: float = 300.0,
        ollama_num_predict: int = 512,
        ollama_system_prompt: str | None = None,
        classifier_timeout: float = 60.0,
        query_timeout: float = 330.0,
    ) -> None:
        self.port = port if port > 0 else find_free_port()
        # List of addresses from which the host will accept incoming connection
        self.listen_addrs = get_available_interfaces(self.port)
        self.advertise_address_mode = advertise_address_mode

        self.enable_gossip = enable_gossip
        self.agent_backend = agent_backend
        self.ollama_model = ollama_model
        self.ollama_host = ollama_host
        self.ollama_timeout = ollama_timeout
        self.classifier_timeout = classifier_timeout
        self.query_timeout = query_timeout

        # Seed is useful for tests
        if seed is not None:
            random.seed(seed)
            secret_number = random.getrandbits(32 * 8)
            secret = secret_number.to_bytes(length=32, byteorder="big")
        else:
            secret = secrets.token_bytes(32)

        self.host = new_host(key_pair=create_new_key_pair(secret))
        self.transport = TransportService()

        # Create the Profile of the current Node
        resolved_capabilities = capabilities or ["general"]
        resolved_scores = capability_scores or {
            cap: 1.0 for cap in resolved_capabilities
        }

        self.local_profile = NodeProfile(
            peer_id=self.host.get_id().to_string(),
            addresses=[],
            model_name=model_name,
            capabilities=resolved_capabilities,
            capability_scores=resolved_scores,
            is_available=True,
        )
        self.local_profile.addresses = self.all_shareable_addresses()

        self.peer_registry = PeerRegistry()

        # Init the model backend
        if agent_backend == "dummy":
            self.local_agent = DummyAgent()
        elif agent_backend == "ollama":
            self.local_agent = OllamaAgent(
                model=ollama_model,
                host=ollama_host,
                timeout_s=ollama_timeout,
                system_prompt=ollama_system_prompt,
                num_predict=ollama_num_predict,
            )
        elif agent_backend == "qwen":
            self.local_agent = QwenAgent(
                model_id=llm_model_id,
                max_new_tokens=llm_max_new_tokens,
                enable_thinking=llm_enable_thinking,
            )
        else:
            raise ValueError(f"Unsupported agent_backend: {agent_backend}")

        self.ping_service = PingService(self.transport)

        self.dht_service = DHTService(self.host, mode=dht_mode)

        self.health_service = HealthService(
            self.peer_registry,
            self.ping_service,
            self.local_profile.peer_id,
        )

        self.recommendation_service = RecommendationService(
            self.host,
            self.transport,
            self.peer_registry,
            self.local_profile.peer_id,
            self.dht_service,
        )

        capability_classifier = CapabilityClassifier(
            model=ollama_model,
            host=ollama_host,
            timeout_s=classifier_timeout,
        )

        self.routing_service = RoutingService(
            self.host,
            self.local_profile,
            self.peer_registry,
            self.dht_service,
            300 * 1000,  # 5min
            self.health_service,
            capability_classifier,
            self.recommendation_service,
        )

        self.query_service = QueryService(
            self.host,
            self.transport,
            self.local_agent,
            self.routing_service,
            query_timeout_s=query_timeout,
        )

        self.pubsub_service = None
        self._trio_token = None
        self.api_url: str | None = None

        if self.enable_gossip:
            self.pubsub_service = PubSubService(
                self.host,
                self.dht_service,
                self.peer_registry,
                self.local_profile.peer_id,
            )

    def advertised_addresses(self) -> list[str]:
        """
        Return the list of addresses that this node should advertise to others.

        For now we only support one explicit mode:
        - ipv6_loopback -> advertise only /ip6/::1/tcp/<port>/p2p/<peer_id>

        Fail fast if the requested address is not present in the listen addresses.
        """
        if self.advertise_address_mode == "ipv6_loopback":
            matches = [
                f"{addr}/p2p/{self.host.get_id()}"
                for addr in self.listen_addrs
                if str(addr).startswith("/ip6/::1/")
            ]
            if not matches:
                raise RuntimeError(
                    "advertise_address_mode='ipv6_loopback' was requested, "
                    "but no /ip6/::1 address is available in listen_addrs"
                )
            return matches

        raise ValueError(
            f"Unsupported advertise_address_mode: {self.advertise_address_mode}"
        )

    async def periodically_publish_self_to_dht(self, interval_s: float = 60.0) -> None:
        while True:
            await trio.sleep(interval_s)

            try:
                await self.publish_self_to_dht()
                if self.enable_gossip:
                    await self.announce_self_via_gossip()
            except Exception as exc:
                log("DHT", f"Periodic self publish failed: {exc}")

    def all_shareable_addresses(self) -> list[str]:
        """
        Returns an array containing all the addresses (address + p2p + peer id)
        on which the local node can be reached.
        """
        return [
            f"{addr}/p2p/{self.local_profile.peer_id}" for addr in self.listen_addrs
        ]

    def print_addresses(self) -> None:
        """
        Prints node information.
        """
        log("NODE", f"I am {self.local_profile.peer_id}")
        log("NODE", f"Model: {self.local_profile.model_name}")
        log("NODE", f"Capabilities: {self.local_profile.capabilities}")
        log("NODE", f"Capability scores: {self.local_profile.capability_scores}")
        log("NODE", f"Agent backend: {self.agent_backend}")
        if self.agent_backend == "ollama":
            log(
                "NODE",
                f"Ollama answer backend model={self.ollama_model} "
                f"host={self.ollama_host} timeout={self.ollama_timeout:.0f}s",
            )
        log(
            "ROUTING",
            f"Capability classifier: ollama model={self.ollama_model} "
            f"host={self.ollama_host} timeout={self.classifier_timeout:.0f}s "
            f"allowed_capabilities={CAPABILITIES}",
        )
        log("NODE", f"Advertise address mode: {self.advertise_address_mode}")
        log("NODE", f"Peer query timeout: {self.query_timeout:.0f}s")
        if self.api_url is not None:
            log("NODE", f"HTTP API: {self.api_url}")
            log("NODE", f"HTTP query endpoint: POST {self.api_url}/api/query")
        else:
            log("NODE", "HTTP API: disabled")
        log("NODE", "Listening on:")
        for addr in self.local_profile.addresses:
            print(f"  {addr}", flush=True)

        log("NODE", "Advertised addresses:")
        for addr in self.advertised_addresses():
            print(f"  {addr}", flush=True)

    def register_protocol_handlers(self) -> None:
        """
        Register all stream handlers.
        """
        self.host.set_stream_handler(PING_PROTOCOL, self.ping_service.handle_stream)
        log("NODE", f"Registered protocol handler: {PING_PROTOCOL}")
        self.host.set_stream_handler(QUERY_PROTOCOL, self.query_service.handle_stream)
        log("NODE", f"Registered protocol handler: {QUERY_PROTOCOL}")
        self.host.set_stream_handler(
            RECOMMEND_PROTOCOL, self.recommendation_service.handle_stream
        )
        log("NODE", f"Registered protocol handler: {RECOMMEND_PROTOCOL}")

    async def refresh_local_profile(self) -> None:
        self.local_profile.addresses = self.advertised_addresses()
        self.local_profile.timestamp_ms = int(time.time() * 1000)

        log(
            "NODE",
            f"Publishing self profile peer_id={self.local_profile.peer_id} "
            f"model={self.local_profile.model_name} "
            f"caps={self.local_profile.capabilities} "
            f"addresses={self.local_profile.addresses} "
            f"ts={self.local_profile.timestamp_ms}",
        )

    async def publish_self_to_dht(self) -> None:
        await self.refresh_local_profile()
        await self.dht_service.publish_profile(self.local_profile)
        await self.dht_service.advertise_capabilities(self.local_profile)

    async def announce_self_via_gossip(self) -> None:
        if self.pubsub_service is None:
            return

        await self.pubsub_service.publish_profile_update(
            self.local_profile.peer_id,
            self.local_profile.timestamp_ms,
        )

    async def _start_node_logic(
        self,
        bootstrap_addrs: list[str],
    ) -> None:
        bootstrap_peers = await connect_to_bootstrap_peers(
            self.host,
            bootstrap_addrs,
        )
        await self.dht_service.add_bootstrap_peers(bootstrap_peers)

        await self.publish_self_to_dht()

        if self.enable_gossip:
            await self.announce_self_via_gossip()

        self.print_addresses()
        log("NODE", "Node started. Waiting for incoming streams...")
        await trio.sleep_forever()

    async def run_forever(
        self,
        bootstrap_addrs: list[str] | None = None,
        api_host: str | None = None,
        api_port: int | None = None,
    ) -> None:
        bootstrap_addrs = bootstrap_addrs or []
        self._trio_token = trio.lowlevel.current_trio_token()
        if api_port is not None:
            self.api_url = f"http://{api_host or '127.0.0.1'}:{api_port}"
        else:
            self.api_url = None
        self.local_profile.addresses = self.advertised_addresses()
        self.register_protocol_handlers()

        async with (
            self.host.run(listen_addrs=self.listen_addrs),
            trio.open_nursery() as nursery,
        ):
            async with self.dht_service.run():
                nursery.start_soon(self.periodically_publish_self_to_dht)
                if api_port is not None:
                    from src.services.node_api_service import NodeAPIService

                    node_api = NodeAPIService(
                        self,
                        api_host or "127.0.0.1",
                        api_port,
                    )
                    nursery.start_soon(node_api.run)

                if self.enable_gossip:
                    if self.pubsub_service is None:
                        raise RuntimeError(
                            "Gossip is enabled but PubSubService was not initialized"
                        )

                    async with self.pubsub_service.run():
                        nursery.start_soon(
                            self.pubsub_service.run_profile_update_listener
                        )
                        await self._start_node_logic(bootstrap_addrs)
                else:
                    log("GOSSIP", "Gossip disabled")
                    await self._start_node_logic(bootstrap_addrs)

    def answer_query_from_api(
        self,
        prompt: str,
        query_id: str | None = None,
        required_capability: str | None = None,
    ) -> dict:
        if self._trio_token is None:
            raise RuntimeError("Node is not running yet")

        context = QueryContext(
            origin_peer_id="http-api",
            visited_peers=[],
            required_capability=required_capability,
        )

        return trio.from_thread.run(
            self.query_service.answer_query,
            prompt,
            query_id,
            context,
            trio_token=self._trio_token,
        )
