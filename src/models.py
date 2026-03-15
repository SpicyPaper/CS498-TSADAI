"""
Small shared dataclasses for structured results.

These are local Python objects.
The actual network messages are JSON dictionaries.
"""

from dataclasses import dataclass
from typing import Optional


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
