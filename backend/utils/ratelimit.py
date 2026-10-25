"""Sliding-window rate limiter (per-instance)."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True
