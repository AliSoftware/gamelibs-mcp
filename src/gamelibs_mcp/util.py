"""Small shared helpers: pagination, timestamps, caching.

We return plain dicts from tools (FastMCP serializes them directly) rather than heavy
model classes — this keeps payloads lean and the code compact, matching the project's
"lean tool responses" goal.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

T = TypeVar("T")

# Cap how many ids a *_get_*_details tool will process in one call, to bound response size.
MAX_DETAIL_IDS = 50


def epoch_to_iso(epoch: int | None) -> str | None:
    """Convert a unix-seconds timestamp to an ISO-8601 UTC string (or None)."""
    if not epoch:
        return None
    return datetime.fromtimestamp(int(epoch), tz=UTC).isoformat()


def paginate(
    items: list[T],
    limit: int,
    offset: int,
    to_dict: Callable[[T], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Slice ``items`` into a standard paginated envelope.

    Returns ``{total, limit, offset, count, games}`` where ``total`` is the full
    (pre-slice) count so callers know whether more pages exist.
    """
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    window = items[offset : offset + limit]
    if to_dict is not None:
        window = [to_dict(i) for i in window]
    return {
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "count": len(window),
        "games": window,
    }


class TTLCache:
    """A trivial single-value TTL cache (per process). Not thread-shared across events."""

    def __init__(self, ttl_seconds: float = 300.0):
        self._ttl = ttl_seconds
        self._value: Any = None
        self._stamp: float = 0.0

    def get(self) -> Any | None:
        if self._value is not None and (time.monotonic() - self._stamp) < self._ttl:
            return self._value
        return None

    def set(self, value: Any) -> None:
        self._value = value
        self._stamp = time.monotonic()

    def clear(self) -> None:
        self._value = None
        self._stamp = 0.0
