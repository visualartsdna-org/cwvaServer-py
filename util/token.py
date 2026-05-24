"""Token validation for /cmd endpoint.

Replicates Java String.hashCode() * time_ms scheme exactly so existing
Java token-generation tooling works unchanged against this server.
Phrase is read from ~/.secrets.rson under cfg["secrets"]["phrase"].
"""

import time
from pathlib import Path
from util.rson import load


TOLERANCE_MS = 30000  # 30 seconds — matches Groovy server


def java_hashcode(s: str) -> int:
    """Exact replication of Java String.hashCode()."""
    h = 0
    for ch in s:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    return h


def _get_phrase() -> str:
    path = Path.home() / ".secrets.rson"
    cfg = load(str(path))
    return cfg["secrets"]["phrase"]


def _hc() -> int:
    return java_hashcode(_get_phrase())


def validate(token_str: str) -> bool:
    try:
        token = int(token_str)
        hc = _hc()
        if hc == 0:
            return False
        recovered_ms = token // hc
        now_ms = int(time.time() * 1000)
        return (now_ms - TOLERANCE_MS) < recovered_ms < (now_ms + TOLERANCE_MS)
    except Exception:
        return False


def get_time_token() -> str:
    """Generate a valid token for the current time (useful for testing)."""
    ms = int(time.time() * 1000)
    return str(ms * _hc())
