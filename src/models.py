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
