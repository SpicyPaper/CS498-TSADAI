"""
Central place for protocol IDs.

A protocol ID identifies the "kind" of stream/message exchanged over libp2p.
Each service listens on its own protocol.
"""

from libp2p.custom_types import TProtocol

PING_PROTOCOL = TProtocol("/tsadai/ping/1.0.0")
QUERY_PROTOCOL = TProtocol("/tsadai/query/1.0.0")
