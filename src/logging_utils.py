"""
Small console logging helpers.

This provides:
- readable prefixes
- immediate flush
- consistent formatting on client and server
"""

from datetime import datetime


def log(role: str, message: str) -> None:
    """
    role examples:
    - NODE
    - SERVER
    - CLIENT
    - PING
    - QUERY
    """
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{role}] {message}", flush=True)
