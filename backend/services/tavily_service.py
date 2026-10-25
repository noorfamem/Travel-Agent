"""Tavily web research (search + images). Results are labelled WEB_RESEARCH and cached (research changes slowly)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from ..config.settings import Settings
from ..tools.link_tools import safe_url
from ..utils.cache import TTLCache
from ..utils.errors import ErrorKind, ProviderError
from ..utils.http_client import HttpClient

PROVIDER = "Tavily"
ENDPOINT = "https://api.tavily.com/search"


@dataclass
class WebResult:
    title: str
    url: str
    content: str
    score: float = 0.0


@dataclass
class WebSearch:
    query: str
    answer: str = ""
    results: list[WebResult] = field(default_factory=list)


class TavilyService:
    def __init__(self, settings: Settings, http: HttpClient, cache: TTLCache):
        self.s, self.http, self.cache = settings, http, cache

    @property
    def configured(self) -> bool:
        return bool(self.s.tavily_api_key)

    def search(self, query: str, max_results: int = 5) -> WebSearch:
        if not self.configured:
            raise ProviderError(PROVIDER, ErrorKind.NOT_CONFIGURED, "TAVILY_API_KEY missing")
        payload = {"query": query[:380], "search_depth": "basic", "max_results": max_results, "include_answer": True}
        key = "tav:" + hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()

        def fetch() -> dict:
            data = self.http.request_json(PROVIDER, "POST", ENDPOINT, json=payload, headers={"Authorization": f"Bearer {self.s.tavily_api_key}"})
            if not isinstance(data, dict) or "results" not in data:
                raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, "missing 'results'")
            return data

        data = self.cache.get_or_set(key, fetch, self.s.ttl_research)
        results = []
        for r in data.get("results", []):
            url = safe_url(r.get("url"))
            if url and r.get("content"):
                results.append(WebResult(title=str(r.get("title", ""))[:200], url=url, content=str(r["content"]), score=float(r.get("score") or 0)))
        return WebSearch(query=query, answer=str(data.get("answer") or ""), results=results)
