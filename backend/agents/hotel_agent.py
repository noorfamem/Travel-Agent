"""AGENT 2 - Hotel Search Agent.

Live rates come from a hotel-price provider when configured. Directory listings (OpenStreetMap)
and Tavily-found official websites fill gaps so Indian states still return bookable hotel pages.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from ..models.travel_models import DataKind, HotelOption
from ..providers.hotel_provider import HotelProvider, ProviderHotels
from ..services.location_service import LocationService
from ..tools.booking_links import booking_hotel_name_url, booking_hotels_url, google_hotel_name_url, google_hotels_open_url
from ..tools.budget import dedupe_hotels, hotel_stay_total, split_hotels_by_budget
from ..tools.link_tools import is_ota_url
from ..tools.ranking import rank_hotels
from ..utils.errors import ProviderError
from ..utils.logging_config import get_logger
from .common import clean_link, status_for_error
from .schemas import HotelAgentRequest
from .store import SelectionStore

log = get_logger("hotel_agent")

_AIRPORT_TAIL = re.compile(r"(?i)\s*(international\s+)?(airport|airstrip|aerodrome)\s*$")
_SKIP_OFFICIAL = (
    "booking.com", "makemytrip.com", "agoda.com", "tripadvisor", "expedia", "hotels.com",
    "goibibo.com", "yatra.com", "cleartrip.com", "kayak.com", "trivago.com", "google.com",
    "skyscanner", "ixigo.com",
)


class HotelAgent:
    def __init__(self, locations: LocationService, provider: HotelProvider, store: SelectionStore, tavily: Any = None):
        self.locations, self.provider, self.store, self.tavily = locations, provider, store, tavily

    def _normalize_destination(self, raw: str):
        text = (raw or "").strip()
        text = _AIRPORT_TAIL.sub("", text).strip(" ,-") or text
        loc = self.locations.resolve(text)
        if loc.kind == "airport" and loc.city:
            loc = self.locations.resolve(loc.city)
        if loc.kind in {"unknown", "ambiguous"} and len(text) == 3:
            ap = self.locations.airport(text.upper())
            if ap and ap.city:
                loc = self.locations.resolve(ap.city)
                text = ap.city
        return loc, text

    def _find_official_website(self, hotel_name: str, place: str) -> Optional[str]:
        if not self.tavily or not getattr(self.tavily, "configured", False):
            return None
        try:
            res = self.tavily.search(f"{hotel_name} {place} India official website", max_results=4)
            for r in res.results:
                url = clean_link(r.url)
                if not url:
                    continue
                low = url.lower()
                if any(d in low for d in _SKIP_OFFICIAL) or is_ota_url(url):
                    continue
                return url
        except Exception:
            pass
        return None

    def search(self, req: HotelAgentRequest) -> dict[str, Any]:
        nights = (req.check_out - req.check_in).days if req.check_in else 1
        base: dict[str, Any] = {"agent": "hotel", "provider": self.provider.name, "currency": req.currency, "nights": nights, "rooms": req.rooms,
                                "check_in": req.check_in.isoformat() if req.check_in else None, "check_out": req.check_out.isoformat(),
                                "budget": {"amount": req.budget, "scope": req.budget_scope}, "options": [], "over_budget": [], "excluded": [], "notes": []}
        if not req.destination or not req.destination.strip():
            return {**base, "status": "needs_clarification", "data_kind": DataKind.UNAVAILABLE.value,
                    "message": "Please enter a destination (city, state, or country) before searching for hotels."}
        loc, typed = self._normalize_destination(req.destination)
        if loc.kind == "ambiguous":
            return {**base, "status": "needs_clarification", "data_kind": DataKind.UNAVAILABLE.value, "message": f"'{req.destination}' could mean several places. Please choose one.", "candidates": loc.candidates[:8]}
        from ..config.settings import get_settings
        settings = get_settings()
        if loc.country_code and loc.country_code != "IN" and settings.domestic_only:
            return {**base, "status": "invalid_location", "data_kind": DataKind.UNAVAILABLE.value, "message": f"'{req.destination}' is outside India."}
        if loc.kind == "unknown":
            base["notes"].append("Destination not found in the reference dataset; searching providers by the name you typed.")
        if loc.kind in {"city", "state", "region", "airport"} and loc.name:
            place = loc.city or loc.name
        else:
            place = typed
        country = loc.country or "India" if not settings.domestic_only else "India"
        query = f"{place}, {country}"
        search_url = google_hotels_open_url(query, req.check_in, req.check_out, req.adults)
        booking_search = booking_hotels_url(place, req.check_in, req.check_out, req.adults, req.rooms, req.children)
        base.update(search_url=search_url, booking_search_url=booking_search,
                    search_url_label="Search hotels on Google Hotels (a search page, NOT an offer or booking link)",
                    resolved_place=place)
        if not self.provider.configured:
            return {**base, "status": "unavailable", "data_kind": DataKind.UNAVAILABLE.value,
                    "message": "No hotel provider is configured, so no hotel prices are shown (none are invented). Use the search link."}
        try:
            got = self.provider.search(query, req.check_in, req.check_out, req.adults, req.children, req.rooms, req.currency, req.stars)
        except ProviderError as e:
            log.warning("hotel provider failed: %s", e.kind.value)
            return {**base, "status": status_for_error(e), "data_kind": DataKind.UNAVAILABLE.value, "message": e.user_message(), "error_kind": e.kind.value}
        except Exception as exc:
            log.exception("unexpected %s provider failure: %s", "hotel", type(exc).__name__)
            return {**base, "status": "error", "data_kind": DataKind.UNAVAILABLE.value, "message": "The provider returned data in an unexpected format.", "error_kind": "invalid_response"}
        if not isinstance(got, ProviderHotels):
            return {**base, "status": "error", "data_kind": DataKind.UNAVAILABLE.value, "message": "The provider returned data in an unexpected format.", "error_kind": "invalid_response"}
        base.update(provider=got.provider, data_kind=got.data_kind.value, retrieved_at=got.retrieved_at)
        base["notes"].extend(got.notes)
        opts = dedupe_hotels(got.options)
        filtered = list(opts)
        if req.stars:
            starred = [h for h in filtered if h.star_rating in req.stars]
            if starred:
                filtered = starred
            else:
                base["notes"].append("Star filter matched no hotels; showing all listings.")
        for am in req.amenities:
            am_hit = [h for h in filtered if any(am.lower() in a.lower() for a in h.amenities)]
            if am_hit:
                filtered = am_hit
            else:
                base["notes"].append(f"Amenity filter '{am}' matched none; ignored.")
        if req.free_cancellation:
            free = [h for h in filtered if h.cancellation and "free cancel" in h.cancellation.lower()]
            if free:
                filtered = free
            else:
                base["notes"].append("Free-cancellation filter matched none; showing hotels without that flag.")
        if not filtered:
            return {**base, "status": "no_results", "message": "No hotels matched. Try relaxing filters or use the Google Hotels / Booking.com search links."}
        within, over, excluded = split_hotels_by_budget(filtered, req.budget, req.budget_scope, nights, req.rooms, req.currency)
        unpriced = [h for h in filtered if hotel_stay_total(h, nights, req.rooms) is None]
        display = list(within)
        for h in unpriced:
            if h not in display:
                h.labels = [*h.labels, "PRICE ON WEBSITE"]
                display.append(h)
        total_budget = (req.budget * nights * req.rooms if req.budget_scope == "per_night" else req.budget) if req.budget else None
        centre = (loc.lat, loc.lon) if loc.lat is not None and loc.lon is not None else None
        top = rank_hotels(display, req.style, total_budget, nights, req.rooms, req.stars, centre, top_n=8) if display else []
        near = sorted(over, key=lambda h: hotel_stay_total(h, nights, req.rooms) or float("inf"))[:3]
        for h in top + near:
            self._finish(h, got.retrieved_at, search_url, place, req)
            if not h.official_website:
                found = self._find_official_website(h.name, place)
                if found:
                    h.official_website = found
                    h.official_website_label = "Official Hotel Website"
            self.store.put("hotel", h, key=h.id)
        for h in near:
            h.labels = [*h.labels, "OVER BUDGET"]
        msg = "Rates and availability may change. Confirm taxes, occupancy and cancellation terms with the provider. Live inventory is only shown when a hotel-price API returned it; other listings are directory or web-research pages."
        if not top and near:
            msg = "No hotel fits your budget. The nearest over-budget alternatives are listed separately."
        elif not top:
            msg = "No priced hotels were usable (see 'excluded')."
        elif any("PRICE ON WEBSITE" in (h.labels or []) for h in top):
            msg = "Some hotels are directory listings with official websites (no live room rate). Confirm price on the hotel site."
        return {**base, "status": "ok" if top else "no_results", "message": msg, "options": [h.model_dump(mode="json") for h in top],
                "over_budget": [h.model_dump(mode="json") for h in near], "excluded": excluded}

    @staticmethod
    def _finish(h: HotelOption, retrieved_at, search_url: str, place: str, req: HotelAgentRequest) -> None:
        h.booking_url = clean_link(h.booking_url)
        h.official_website = clean_link(h.official_website)
        if h.booking_url and is_ota_url(h.booking_url) and not h.official_website:
            # Aggregator pages are search/booking sites, not the hotel's official website.
            pass
        if h.booking_url and not is_ota_url(h.booking_url) and not h.official_website:
            h.official_website = h.booking_url
            h.official_website_label = "Official Hotel Website"
            h.booking_url = None
        h.link_kind = "offer" if h.booking_url else ("search" if (h.official_website or search_url) else "none")
        h.search_url = google_hotel_name_url(h.name, place, req.check_in, req.check_out)
        h.booking_search_url = booking_hotel_name_url(h.name, place, req.check_in, req.check_out, req.adults, req.rooms)
        h.retrieved_at = h.retrieved_at or retrieved_at
        if h.official_website:
            h.official_website_label = h.official_website_label or "Official Hotel Website"
