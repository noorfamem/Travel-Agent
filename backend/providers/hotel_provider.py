"""Hotel-offer provider interface.

SerpAPI (Google Hotels) supplies live rates when a key is present.
OpenStreetMap (Overpass) and Tavily supply hotel names + official websites for any Indian state/city.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol
from urllib.parse import urlsplit

from ..models.travel_models import DataKind, HotelOption
from ..services.overpass_service import OverpassHotelService
from ..services.serpapi_service import SerpApiService
from ..services.tavily_service import TavilyService
from ..tools.booking_links import google_hotel_name_url
from ..tools.link_tools import is_ota_url, safe_url
from ..utils.errors import ProviderError
from ..utils.logging_config import get_logger

log = get_logger("hotel_provider")
_SKIP_HOSTS = {"google.com", "google.co.in", "youtube.com", "facebook.com", "instagram.com", "x.com", "twitter.com"}


@dataclass
class ProviderHotels:
    options: list[HotelOption]
    provider: str
    data_kind: DataKind
    retrieved_at: Optional[str] = None
    notes: list[str] = field(default_factory=list)


class HotelProvider(Protocol):
    name: str
    @property
    def configured(self) -> bool: ...
    def search(self, destination: str, check_in: dt.date, check_out: dt.date, adults: int, children: int, rooms: int, currency: str, stars: list[int]) -> ProviderHotels: ...


class SerpApiHotelProvider:
    name = "SerpAPI (Google Hotels)"

    def __init__(self, svc: SerpApiService):
        self.svc = svc

    @property
    def configured(self) -> bool:
        return self.svc.configured

    def search(self, destination, check_in, check_out, adults, children, rooms, currency, stars) -> ProviderHotels:
        raw = self.svc.search_hotels(destination, check_in, check_out, adults, children, [], currency, stars)
        generic = self.svc.google_hotels_url(destination, check_in, check_out)
        opts = self.svc.parse_hotels(raw, currency, check_in, check_out, rooms, generic)
        for h in opts:
            if h.booking_url == generic:
                h.booking_url = None
        return ProviderHotels(opts, self.name, DataKind.LIVE, (raw.get("_meta") or {}).get("retrieved_at"))


class OverpassHotelProvider:
    name = "OpenStreetMap (Overpass)"

    def __init__(self, svc: OverpassHotelService, locations: Any):
        self.svc, self.locations = svc, locations

    @property
    def configured(self) -> bool:
        return True

    def search(self, destination, check_in, check_out, adults, children, rooms, currency, stars) -> ProviderHotels:
        loc = self.locations.resolve(re.sub(r"(?i)^hotels?\s+in\s+", "", destination.replace(", India", "")))
        if loc.kind == "airport" and loc.city:
            loc = self.locations.resolve(loc.city)
        if loc.lat is None or loc.lon is None:
            return ProviderHotels([], self.name, DataKind.VERIFIED, notes=["No map coordinates for this place."])
        radius = 60000 if loc.kind in {"state", "region"} else 15000
        opts = self.svc.search_hotels(loc.lat, loc.lon, check_in, check_out, rooms, currency, radius_m=radius)
        return ProviderHotels(opts, self.name, DataKind.VERIFIED)


class TavilyHotelProvider:
    name = "Tavily (hotel websites)"

    def __init__(self, svc: TavilyService):
        self.svc = svc

    @property
    def configured(self) -> bool:
        return self.svc.configured

    def search(self, destination, check_in, check_out, adults, children, rooms, currency, stars) -> ProviderHotels:
        place = re.sub(r"(?i)^hotels?\s+in\s+", "", destination).replace(", India", "").strip()
        nights = max(1, (check_out - check_in).days)
        found = self.svc.search(f"best hotels in {place} India official website", max_results=8)
        extra = self.svc.search(f"{place} India luxury and budget hotels official site", max_results=6)
        results = list(found.results) + list(extra.results)
        out: list[HotelOption] = []
        seen: set[str] = set()
        for r in results:
            url = safe_url(r.url)
            if not url:
                continue
            host = urlsplit(url).netloc.lower().removeprefix("www.")
            if any(host == d or host.endswith("." + d) for d in _SKIP_HOSTS):
                continue
            name = _hotel_name_from_title(r.title, place)
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            ota = is_ota_url(url)
            out.append(HotelOption(
                id="tav-" + hashlib.sha1(f"{name}{url}".encode()).hexdigest()[:10],
                name=name,
                location=place,
                nights=nights,
                check_in=check_in,
                check_out=check_out,
                currency=currency,
                source=self.name,
                data_kind=DataKind.WEB,
                booking_url=url if ota else None,
                official_website=None if ota else url,
                official_website_label=None if ota else "Official Hotel Website",
                search_url=google_hotel_name_url(name, place, check_in, check_out),
                price_note="Website found by Tavily web research — live room rates are not included. Confirm price on the hotel site.",
            ))
            if len(out) >= 10:
                break
        return ProviderHotels(out, self.name, DataKind.WEB)


class ChainedHotelProvider:
    """Try live rates first, then directory/website sources so every Indian state still returns hotels."""

    def __init__(self, providers: list[Any]):
        self._providers = providers

    @property
    def name(self) -> str:
        names = [p.name for p in self._providers if p.configured]
        return " / ".join(names) if names else "No hotel provider configured"

    @property
    def configured(self) -> bool:
        return any(p.configured for p in self._providers)

    def search(self, destination, check_in, check_out, adults, children, rooms, currency, stars) -> ProviderHotels:
        merged: list[HotelOption] = []
        notes: list[str] = []
        names_used: list[str] = []
        last_err: Optional[ProviderError] = None
        kind = DataKind.LIVE
        retrieved = None
        for p in self._providers:
            if not p.configured:
                continue
            try:
                got = p.search(destination, check_in, check_out, adults, children, rooms, currency, stars)
            except ProviderError as e:
                log.warning("hotel provider %s failed: %s", p.name, e.kind.value)
                last_err = e
                notes.append(f"{p.name} unavailable ({e.kind.value}).")
                continue
            names_used.append(p.name)
            if got.retrieved_at and not retrieved:
                retrieved = got.retrieved_at
            if got.data_kind != DataKind.LIVE:
                kind = got.data_kind if not merged else DataKind.WEB
            merged = _merge_hotels(merged, got.options)
            notes.extend(got.notes)
            # Keep merging directory + official-website sources even after live rates,
            # so Indian states still get hotel websites when Google Hotels is thin.
        if not merged and last_err and not names_used:
            raise last_err
        return ProviderHotels(merged, " / ".join(names_used) or self.name, kind if merged else DataKind.UNAVAILABLE, retrieved, notes)


def _hotel_name_from_title(title: str, place: str) -> str:
    t = (title or "").split("|")[0].split(" - ")[0].strip()
    t = re.sub(r"(?i)\b(official\s+website|book\s+now|reviews?|prices?)\b", "", t)
    t = re.sub(r"(?i)\b(hotels?\s+in\s+" + re.escape(place) + r")\b", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -|")
    return (t[:80] or f"Hotel in {place}").strip()


def _merge_hotels(base: list[HotelOption], extra: list[HotelOption]) -> list[HotelOption]:
    by = {re.sub(r"\s+", " ", h.name.lower()): h for h in base}
    out = list(base)
    for h in extra:
        key = re.sub(r"\s+", " ", h.name.lower())
        cur = by.get(key)
        if cur is None:
            by[key] = h
            out.append(h)
            continue
        if not cur.booking_url and h.booking_url:
            cur.booking_url = h.booking_url
        if not cur.official_website and h.official_website:
            cur.official_website = h.official_website
            cur.official_website_label = h.official_website_label or "Official Hotel Website"
        if cur.price_per_night is None and h.price_per_night is not None:
            cur.price_per_night = h.price_per_night
            cur.total_price = h.total_price
            cur.source = h.source
            cur.data_kind = h.data_kind
        if not cur.thumbnail and h.thumbnail:
            cur.thumbnail = h.thumbnail
        if not cur.amenities and h.amenities:
            cur.amenities = h.amenities
    return out
