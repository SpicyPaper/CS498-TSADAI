"""
Small console logging helpers.

This provides:
- readable prefixes
- immediate flush
- consistent formatting on client and server
"""

from datetime import datetime
import sys


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
    line = f"[{ts}] [{role}] {message}"
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    safe_line = line.encode(encoding, errors="backslashreplace").decode(encoding)
    print(safe_line, flush=True)
