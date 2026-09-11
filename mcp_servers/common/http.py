"""Shared outbound HTTP client for every MCP server.

All upstream traffic goes through here so that four policies are applied in
exactly one place:

1. **Identifying User-Agent.** Nominatim rejects anonymous clients outright,
   and Overpass throttles them. ``OSM_USER_AGENT`` must contain real contact
   details per the OSM tile/API usage policy.
2. **Per-host rate limiting.** Nominatim allows 1 req/s, Overpass roughly
   0.5 req/s. Limits are keyed by host so a slow Overpass query never delays
   an unrelated Open-Meteo call.
3. **Bounded retries with backoff.** Public endpoints return sporadic 429/5xx
   under load; we retry idempotent GETs a few times and then give up cleanly.
4. **Response caching.** See :mod:`mcp_servers.common.cache`.
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import Mapping
from typing import Any

import httpx

from .cache import cached_json
from .ratelimit import RateLimiter

DEFAULT_USER_AGENT = os.getenv(
    "OSM_USER_AGENT", "WayfarerTravelAssistant/1.0 (contact: set-OSM_USER_AGENT)"
)
CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("HTTP_READ_TIMEOUT", "45"))
MAX_ATTEMPTS = int(os.getenv("HTTP_MAX_ATTEMPTS", "3"))

# Hosts with a documented rate limit. Anything else gets a generous default.
_HOST_LIMITS: dict[str, float] = {
    "nominatim.openstreetmap.org": float(os.getenv("NOMINATIM_RATE_LIMIT_RPS", "1.0")),
    "overpass-api.de": float(os.getenv("OVERPASS_RATE_LIMIT_RPS", "0.5")),
    "overpass.kumi.systems": float(os.getenv("OVERPASS_RATE_LIMIT_RPS", "0.5")),
}
_DEFAULT_LIMIT = float(os.getenv("DEFAULT_RATE_LIMIT_RPS", "8.0"))

_limiters: dict[str, RateLimiter] = {}
_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


class HttpError(RuntimeError):
    """Raised when an upstream request fails after all retries.

    Carries ``status`` so callers can distinguish "no data for this place"
    (404) from "the service is unwell" (5xx) and degrade accordingly.
    """

    def __init__(self, message: str, status: int | None = None, url: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.url = url


def _limiter_for(url: str) -> RateLimiter:
    host = httpx.URL(url).host or "unknown"
    if host not in _limiters:
        rate = _HOST_LIMITS.get(host, _DEFAULT_LIMIT)
        # A burst of 1 for OSM hosts enforces true serialisation.
        burst = 1 if host in _HOST_LIMITS else 4
        _limiters[host] = RateLimiter(rate, burst=burst)
    return _limiters[host]


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
                    headers={
                        "User-Agent": DEFAULT_USER_AGENT,
                        "Accept-Encoding": "gzip, deflate",
                    },
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
                )
    return _client


async def shutdown_http() -> None:
    """Close the shared client. Called from each server's shutdown path."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _request(
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    data: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> httpx.Response:
    client = await _get_client()
    limiter = _limiter_for(url)
    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        await limiter.acquire()
        try:
            response = await client.request(
                method, url, params=params, content=data, headers=dict(headers or {})
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
        else:
            if response.status_code < 400:
                return response
            # 429 and 5xx are transient; 4xx (other than 429) will not improve.
            if response.status_code != 429 and response.status_code < 500:
                raise HttpError(
                    f"{method} {url} failed with HTTP {response.status_code}: "
                    f"{response.text[:200]}",
                    status=response.status_code,
                    url=url,
                )
            last_error = HttpError(
                f"HTTP {response.status_code} from {url}",
                status=response.status_code,
                url=url,
            )

        if attempt < MAX_ATTEMPTS:
            # Exponential backoff with jitter, so parallel agents do not
            # synchronise their retries into a thundering herd.
            await asyncio.sleep(min(8.0, 0.75 * 2 ** (attempt - 1)) * (0.7 + random.random() * 0.6))

    if isinstance(last_error, HttpError):
        raise last_error
    raise HttpError(f"{method} {url} failed after {MAX_ATTEMPTS} attempts: {last_error}", url=url)


async def get_json(
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    cache_ttl: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> Any:
    """GET a URL and parse JSON, with caching and retries."""

    async def produce() -> Any:
        response = await _request("GET", url, params=params, headers=headers)
        try:
            return response.json()
        except ValueError as exc:
            raise HttpError(f"{url} returned non-JSON content", url=url) from exc

    if cache_ttl == 0:
        return await produce()
    return await cached_json("GET", [url, dict(params or {})], produce, ttl=cache_ttl)


async def get_text(url: str, *, cache_ttl: int | None = None) -> str:
    """GET a URL as text (used for the OurAirports CSV feeds)."""

    async def produce() -> str:
        response = await _request("GET", url)
        return response.text

    if cache_ttl == 0:
        return await produce()
    return await cached_json("GET_TEXT", [url], produce, ttl=cache_ttl)


async def post_form_json(
    url: str,
    body: str,
    *,
    cache_ttl: int | None = None,
    content_type: str = "application/x-www-form-urlencoded",
) -> Any:
    """POST a body and parse JSON. Overpass QL queries are sent this way."""

    async def produce() -> Any:
        response = await _request(
            "POST", url, data=body, headers={"Content-Type": content_type}
        )
        try:
            return response.json()
        except ValueError as exc:
            raise HttpError(f"{url} returned non-JSON content", url=url) from exc

    if cache_ttl == 0:
        return await produce()
    return await cached_json("POST", [url, body], produce, ttl=cache_ttl)
