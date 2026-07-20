"""Shared async HTTP helpers: client factory, retry/backoff, and a rate limiter."""

from __future__ import annotations

import asyncio
import time

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class ApiError(RuntimeError):
    """A non-recoverable error talking to an upstream API."""


def make_client(headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    """Create an AsyncClient with sane defaults and optional fixed headers."""
    return httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers or {})


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int = 3,
    **kwargs,
) -> httpx.Response:
    """Perform a request, retrying with exponential backoff on 429/5xx.

    Honors the ``Retry-After`` header when present. Raises :class:`ApiError` after
    exhausting retries or on a non-retryable 4xx (other than 429).
    """
    backoff = 1.0
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:  # network-level failure
            last_exc = exc
            if attempt == max_retries:
                raise ApiError(f"Network error calling {url}: {exc}") from exc
            await asyncio.sleep(backoff)
            backoff *= 2
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == max_retries:
                raise ApiError(
                    f"{resp.status_code} from {url} after {max_retries} retries: "
                    f"{resp.text[:300]}"
                )
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else backoff
            await asyncio.sleep(delay)
            backoff *= 2
            continue

        return resp

    # Unreachable, but keeps type-checkers happy.
    raise ApiError(f"Failed calling {url}: {last_exc}")


class RateLimiter:
    """A simple async token-bucket limiter (used for IGDB's 4 req/s cap)."""

    def __init__(self, rate_per_sec: float, burst: int | None = None):
        self._rate = rate_per_sec
        self._capacity = burst if burst is not None else max(1, int(rate_per_sec))
        self._tokens = float(self._capacity)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._last) * self._rate
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # Sleep just long enough for one token to refill.
                await asyncio.sleep((1.0 - self._tokens) / self._rate)
