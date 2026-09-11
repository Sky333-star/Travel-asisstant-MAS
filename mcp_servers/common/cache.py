"""Two-tier (memory + disk) TTL cache for keyless upstream APIs.

The public APIs this project uses are free but politely rate-limited, so
caching is not an optimisation -- it is a requirement for being a good
citizen. Responses are also stable enough (weather forecasts refresh hourly,
OSM data far less often) that a one-hour default TTL is safe.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

DEFAULT_TTL = int(os.getenv("HTTP_CACHE_TTL_SECONDS", "3600"))
CACHE_DIR = Path(os.getenv("HTTP_CACHE_DIR", "data/cache"))


def _key(namespace: str, payload: Any) -> str:
    raw = json.dumps([namespace, payload], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


class TTLCache:
    """In-process cache with an optional JSON file mirror.

    The disk mirror means a restarted MCP server (or a second server process)
    reuses work already paid for, which matters most for the multi-megabyte
    OurAirports dataset and for Overpass queries that take several seconds.
    """

    def __init__(self, ttl: int = DEFAULT_TTL, directory: Path | None = None) -> None:
        self.ttl = ttl
        self.directory = directory or CACHE_DIR
        self._mem: dict[str, tuple[float, Any]] = {}

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, namespace: str, payload: Any) -> Any | None:
        key = _key(namespace, payload)
        now = time.time()

        hit = self._mem.get(key)
        if hit and hit[0] > now:
            return hit[1]

        path = self._path(key)
        try:
            if path.is_file():
                with path.open("r", encoding="utf-8") as fh:
                    envelope = json.load(fh)
                if float(envelope.get("expires", 0)) > now:
                    value = envelope.get("value")
                    self._mem[key] = (float(envelope["expires"]), value)
                    return value
                path.unlink(missing_ok=True)
        except (OSError, ValueError):
            # A corrupt cache entry must never break a request.
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return None

    def set(self, namespace: str, payload: Any, value: Any, ttl: int | None = None) -> None:
        key = _key(namespace, payload)
        expires = time.time() + (ttl if ttl is not None else self.ttl)
        self._mem[key] = (expires, value)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = self._path(key).with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump({"expires": expires, "value": value}, fh)
            tmp.replace(self._path(key))
        except (OSError, TypeError):
            # Disk mirroring is best-effort; the memory tier still works.
            pass

    def clear(self) -> None:
        self._mem.clear()


_shared = TTLCache()


async def cached_json(
    namespace: str,
    payload: Any,
    producer: Callable[[], Awaitable[Any]],
    ttl: int | None = None,
) -> Any:
    """Return a cached value, or await ``producer`` and cache its result."""
    hit = _shared.get(namespace, payload)
    if hit is not None:
        return hit
    value = await producer()
    if value is not None:
        _shared.set(namespace, payload, value, ttl=ttl)
    return value
