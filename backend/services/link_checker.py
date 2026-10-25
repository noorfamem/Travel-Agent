"""Verifies that external links resolve (HEAD, then GET). Guards the 'no broken links' requirement."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..tools.link_tools import is_google
from ..utils.cache import TTLCache
from ..utils.logging_config import get_logger

log = get_logger("links")
_OK_BUT_BLOCKS_BOTS = {401, 403, 405, 406, 429, 999}


class LinkChecker:
    def __init__(self, enabled: bool = True, transport: Callable[..., Any] | None = None, cache: TTLCache | None = None):
        self.enabled, self._transport, self.cache = enabled, transport, cache or TTLCache()

    def _request(self, method: str, url: str) -> int:
        fn = self._transport
        if fn is None:
            import requests

            fn = requests.request
        resp = fn(method, url, timeout=6, allow_redirects=True, headers={"User-Agent": "Mozilla/5.0 TravelAIPro-linkcheck"})
        return int(getattr(resp, "status_code", 0))

    def _check_one(self, url: str) -> bool:
        cached = self.cache.get("lnk:" + url)
        if cached is not None:
            return bool(cached)
        try:
            code = self._request("HEAD", url)
            if code >= 400 and code not in _OK_BUT_BLOCKS_BOTS:
                code = self._request("GET", url)
            ok = code < 400 or code in _OK_BUT_BLOCKS_BOTS
        except Exception as exc:
            log.info("link check failed for %s: %s", url.split("?")[0], type(exc).__name__)
            ok = False
        self.cache.set("lnk:" + url, ok, 6 * 3600)
        return ok

    def check_many(self, urls: list[str]) -> dict[str, bool]:
        """Google-owned URLs (maps/flights/hotels) are trusted without a request. A disabled checker trusts everything."""
        urls = list(dict.fromkeys(u for u in urls if u))
        result = {u: True for u in urls if not self.enabled or is_google(u)}
        todo = [u for u in urls if u not in result]
        if todo:
            with ThreadPoolExecutor(max_workers=8) as ex:
                for u, ok in zip(todo, ex.map(self._check_one, todo)):
                    result[u] = ok
        return result
