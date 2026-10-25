"""Server-side store of agent outputs. Agents hand each other IDs, not client-supplied objects, so provenance cannot be forged."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Optional


class SelectionStore:
    def __init__(self, ttl: int = 7200, max_items: int = 1000):
        self._d: dict[str, tuple[float, str, Any]] = {}
        self.ttl, self.max_items, self._lock = ttl, max_items, threading.Lock()

    def put(self, kind: str, obj: Any, key: Optional[str] = None) -> str:
        key = key or f"{kind[:3]}-{uuid.uuid4().hex[:12]}"
        with self._lock:
            now = time.time()
            for k in [k for k, (t, _, _) in self._d.items() if now - t > self.ttl]:
                del self._d[k]
            while len(self._d) >= self.max_items:
                del self._d[min(self._d, key=lambda k: self._d[k][0])]
            self._d[key] = (now, kind, obj)
        return key

    def get(self, key: Optional[str], kind: str) -> Optional[Any]:
        if not key:
            return None
        with self._lock:
            hit = self._d.get(key)
        if not hit or time.time() - hit[0] > self.ttl or hit[1] != kind:
            return None
        return hit[2]
