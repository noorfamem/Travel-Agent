"""Small thread-safe TTL cache (per-instance, no global state)."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


class TTLCache:
    def __init__(self, max_items: int = 3000):
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._max = max_items
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item and item[0] > time.time():
                self.hits += 1
                return item[1]
            self._data.pop(key, None)
            self.misses += 1
            return None

    def set(self, key: str, value: Any, ttl: int) -> None:
        with self._lock:
            if len(self._data) >= self._max:
                for k, _ in sorted(self._data.items(), key=lambda kv: kv[1][0])[: max(1, self._max // 10)]:
                    self._data.pop(k, None)
            self._data[key] = (time.time() + ttl, value)

    def get_or_set(self, key: str, producer: Callable[[], Any], ttl: int) -> Any:
        hit = self.get(key)
        if hit is not None:
            return hit
        value = producer()
        if value is not None:
            self.set(key, value, ttl)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
