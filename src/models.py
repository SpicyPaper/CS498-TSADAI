"""
Small shared dataclasses for structured results.

These are local Python objects.
The actual network messages are JSON dictionaries.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional
import json
import time


@dataclass
class PingResult:
    ok: bool
    peer_id: Optional[str]
    rtt_ms: Optional[float]
    error: Optional[str]


@dataclass
class QueryResult:
    ok: bool
    peer_id: Optional[str]
    answer: Optional[str]
    error: Optional[str]
    status: str = "ok"
    routing_trace: Optional[dict] = None


@dataclass
class NodeProfile:
    peer_id: str
    addresses: list[str]
    model_name: str
    advertised_capabilities: list[str] = field(default_factory=list)
    capability_scores: dict[str, float] = field(default_factory=dict)
    is_available: bool = True
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_json_bytes(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> "NodeProfile":
        data = json.loads(raw.decode("utf-8"))
        return cls(**data)


@dataclass
class PeerStatus:
    peer_id: str
    is_alive: bool = False
    last_rtt_ms: Optional[float] = None
    last_checked_ts_ms: Optional[int] = None
    consecutive_failures: int = 0


@dataclass
class QueryContext:
    origin_peer_id: str
    excluded_peer_ids: list[str] = field(default_factory=list)
    required_capabilities: dict[str, float] | None = None
    routed_by_peer_id: str | None = None
