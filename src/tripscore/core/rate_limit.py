"""
Simple in-process rate limiting utilities.

These are used by long-running ingestion daemons to keep upstream API traffic stable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class TokenBucketRateLimiter:
    """Token bucket limiter for N events per minute (best-effort)."""

    max_per_minute: float
    burst: float | None = None

    def __post_init__(self) -> None:
        rpm = float(self.max_per_minute)
        if rpm <= 0:
            raise ValueError("max_per_minute must be > 0")
        self._capacity = float(self.burst) if self.burst is not None else float(rpm)
        self._tokens = self._capacity
        self._refill_per_sec = rpm / 60.0
        self._last = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed <= 0:
            return
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_sec)
        self._last = now

    def acquire(self, tokens: float = 1.0) -> None:
        need = float(tokens)
        if need <= 0:
            return
        while True:
            self._refill()
            if self._tokens >= need:
                self._tokens -= need
                return
            missing = need - self._tokens
            sleep_s = max(0.05, missing / max(1e-6, self._refill_per_sec))
            time.sleep(min(1.0, sleep_s))

