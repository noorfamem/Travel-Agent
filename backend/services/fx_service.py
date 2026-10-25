"""Reference exchange rates (ECB via Frankfurter), used for '≈ MYR 123' displays and converting source prices."""
from __future__ import annotations

from typing import Optional

from ..config.settings import Settings
from ..utils.cache import TTLCache
from ..utils.errors import ProviderError
from ..utils.http_client import HttpClient

PROVIDER = "Frankfurter (ECB rates)"
ENDPOINT = "https://api.frankfurter.dev/v1/latest"


class FxService:
    def __init__(self, settings: Settings, http: HttpClient, cache: TTLCache):
        self.s, self.http, self.cache = settings, http, cache

    def rate(self, base: str, quote: str) -> Optional[float]:
        base, quote = base.upper(), quote.upper()
        if base == quote:
            return 1.0

        def fetch():
            return {"rate": float(self.http.request_json(PROVIDER, "GET", ENDPOINT, params={"base": base, "symbols": quote}, retries=1)["rates"][quote])}

        try:
            hit = self.cache.get_or_set(f"fx:{base}:{quote}", fetch, self.s.ttl_fx)
            return hit["rate"] if hit else None
        except (ProviderError, KeyError, TypeError, ValueError):
            return None  # unsupported currency or provider down: no approximation is shown
