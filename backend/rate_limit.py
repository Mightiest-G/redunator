"""Simple in-memory per-client rate limiting (SDD: 100 req/min).

Sliding-window counter keyed by client IP, kept in process memory.
No Redis/Postgres is involved. /health, /docs, /openapi.json and /redoc
are EXEMPT (see backend/app.py).
"""
from __future__ import annotations

import time
from collections import deque

from . import config

_WINDOW_S = 60.0


class RateLimiter:
    def __init__(self, per_min: int | None = None):
        self.per_min = per_min if per_min is not None else config.RATE_LIMIT_PER_MIN
        self._hits: dict[str, deque[float]] = {}

    def check(self, client: str) -> tuple[bool, int, float]:
        """Return (allowed, remaining, reset_in_seconds)."""
        now = time.monotonic()
        q = self._hits.setdefault(client, deque())
        while q and q[0] <= now - _WINDOW_S:
            q.popleft()
        if len(q) >= self.per_min:
            reset = max(0.0, _WINDOW_S - (now - q[0]))
            return False, 0, reset
        q.append(now)
        return True, self.per_min - len(q), _WINDOW_S - (now - q[0])

    def reset(self) -> None:
        self._hits.clear()


limiter = RateLimiter()
