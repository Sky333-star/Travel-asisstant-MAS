"""Async token-bucket rate limiter.

OpenStreetMap's Nominatim usage policy caps clients at 1 request/second and
Overpass asks for similar restraint. Violating those limits gets an IP banned,
so every outbound call in this project funnels through a limiter.
"""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Token bucket allowing ``rate`` requests per second with a small burst.

    The limiter is cooperative: :meth:`acquire` sleeps until a token is
    available rather than raising, which keeps call sites simple.
    """

    def __init__(self, rate: float, burst: int = 1) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.burst = max(1, burst)
        self._tokens = float(self.burst)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated
                self._updated = now
                self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # Sleep exactly long enough for one more token to appear.
                await asyncio.sleep((1.0 - self._tokens) / self.rate)
