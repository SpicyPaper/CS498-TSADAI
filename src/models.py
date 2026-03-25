"""
Small shared dataclasses for structured results.

These are local Python objects.
The actual network messages are JSON dictionaries.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NodeProfile:
    peer_id: str
    addresses: list[str]
    model_name: str
    capabilities: list[str] = field(default_factory=list)
    is_available: bool = True


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
    visited_peers: list[str]
    hop_count: int
    max_hops: int
