"""Wikipedia / Wikidata lookups (no API key) used to VERIFY places: real coordinates, real images, official websites."""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from ..config.settings import Settings
from ..tools.link_tools import safe_url
from ..utils.cache import TTLCache
from ..utils.errors import ProviderError
from ..utils.http_client import HttpClient
from ..utils.logging_config import get_logger

log = get_logger("wikipedia")
PROVIDER = "Wikipedia"
API = "https://en.wikipedia.org/w/api.php"
REST = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKIDATA = "https://www.wikidata.org/wiki/Special:EntityData/"


class WikipediaService:
    def __init__(self, settings: Settings, http: HttpClient, cache: TTLCache):
        self.s, self.http, self.cache = settings, http, cache
        self.headers = {"User-Agent": "TravelAIPro/1.0 (student travel planner project)", "Accept": "application/json"}

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
        key = f"wiki:{url}:{sorted((params or {}).items())}"
        try:
            return self.cache.get_or_set(key, lambda: self.http.request_json(PROVIDER, "GET", url, params=params, headers=self.headers, retries=1), self.s.ttl_wiki)
        except ProviderError as exc:
            log.info("wikipedia lookup skipped (%s)", exc.kind.value)
            return None

    def nearby_pages(self, lat: float, lon: float, radius_m: int = 10000, limit: int = 50) -> list[dict]:
        """One request returning notable articles near a point, with description, image, extract, coordinates and wikidata id."""
        data = self._get(API, {"action": "query", "generator": "geosearch", "ggscoord": f"{lat}|{lon}", "ggsradius": min(radius_m, 10000), "ggslimit": limit,
                               "prop": "coordinates|pageimages|description|extracts|pageprops", "ppprop": "wikibase_item", "piprop": "thumbnail", "pithumbsize": 640,
                               "exintro": 1, "explaintext": 1, "exchars": 280, "exlimit": "max", "colimit": "max", "format": "json", "formatversion": 2})
        out = []
        for p in ((data or {}).get("query") or {}).get("pages", []) or []:
            title = p.get("title")
            if not title:
                continue
            c = (p.get("coordinates") or [{}])[0]
            out.append({"title": title, "description": p.get("description", "") or "", "extract": p.get("extract", "") or "", "image": safe_url((p.get("thumbnail") or {}).get("source")),
                        "lat": c.get("lat"), "lon": c.get("lon"), "url": "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_")), "index": p.get("index", 99),
                        "wikibase": (p.get("pageprops") or {}).get("wikibase_item")})
        return sorted(out, key=lambda r: r["index"])

    def search_titles(self, query: str, limit: int = 3) -> list[str]:
        data = self._get(API, {"action": "query", "list": "search", "srsearch": query, "srlimit": limit, "format": "json", "formatversion": 2})
        return [r["title"] for r in ((data or {}).get("query") or {}).get("search", []) if r.get("title")]

    def summary(self, title: str) -> Optional[dict]:
        data = self._get(REST + quote(title.replace(" ", "_"), safe=""))
        if not data or data.get("type") not in {"standard", None}:
            return None
        return {"title": data.get("title", title), "description": data.get("description", "") or "", "extract": data.get("extract", "") or "",
                "image": safe_url((data.get("thumbnail") or {}).get("source")), "lat": (data.get("coordinates") or {}).get("lat"), "lon": (data.get("coordinates") or {}).get("lon"),
                "url": safe_url(((data.get("content_urls") or {}).get("desktop") or {}).get("page")), "wikibase": data.get("wikibase_item")}

    def official_website(self, wikibase_id: Optional[str]) -> Optional[str]:
        if not wikibase_id:
            return None
        data = self._get(f"{WIKIDATA}{wikibase_id}.json")
        try:
            return safe_url(data["entities"][wikibase_id]["claims"]["P856"][0]["mainsnak"]["datavalue"]["value"])
        except (TypeError, KeyError, IndexError):
            return None
