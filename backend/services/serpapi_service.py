"""Real-time flights and hotels through SerpAPI (Google Flights / Google Hotels engines).

Why SerpAPI: AviationStack and Tavily do not return fares or hotel inventory. Google Flights/Hotels (via SerpAPI) returns live prices,
ratings, layovers and booking/search links. Without an API key the app reports 'unavailable' - it never fakes results."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from typing import Any, Optional
from ..config.settings import Settings
from ..models.travel_models import Cabin, DataKind, FlightOption, FlightSegment, HotelOption, Layover
from ..tools.booking_links import google_flights_open_url, google_hotels_open_url
from ..tools.link_tools import safe_url
from ..tools.time_tools import day_offset, fmt_local, localize, minutes_between
from ..utils.cache import TTLCache
from ..utils.errors import ErrorKind, ProviderError
from ..utils.http_client import HttpClient
from ..utils.logging_config import get_logger
from .location_service import LocationService

log = get_logger("serpapi")
PROVIDER = "SerpAPI (Google Flights/Hotels)"
ENDPOINT = "https://serpapi.com/search.json"
CABIN_CODE = {Cabin.ECONOMY: 1, Cabin.PREMIUM_ECONOMY: 2, Cabin.BUSINESS: 3, Cabin.FIRST: 4}


def classify_error(message: str) -> ErrorKind:
    m = message.lower()
    if "hasn't returned any results" in m or "no results" in m or "returned no" in m:
        return ErrorKind.NO_RESULTS
    if "invalid api key" in m or ("api key" in m and "missing" in m):
        return ErrorKind.AUTH
    if "run out of searches" in m or "rate limit" in m or "too many" in m or ("plan" in m and "limit" in m):
        return ErrorKind.RATE_LIMITED
    return ErrorKind.BAD_REQUEST


def google_flights_url(origin: str, dest: str, outbound: dt.date, ret: Optional[dt.date]) -> str:
    return google_flights_open_url(origin, dest, outbound, ret)


def google_hotels_url(query: str, check_in: dt.date, check_out: dt.date) -> str:
    return google_hotels_open_url(query, check_in, check_out)


class SerpApiService:
    def __init__(self, settings: Settings, http: HttpClient, cache: TTLCache, locations: LocationService):
        self.s, self.http, self.cache, self.locations = settings, http, cache, locations

    @staticmethod
    def google_flights_url(origin: str, dest: str, outbound: dt.date, ret: Optional[dt.date] = None) -> str:
        return google_flights_url(origin, dest, outbound, ret)

    @staticmethod
    def google_hotels_url(query: str, check_in: dt.date, check_out: dt.date) -> str:
        return google_hotels_url(query, check_in, check_out)

    @property
    def configured(self) -> bool:
        return bool(self.s.serpapi_api_key)

    def _call(self, params: dict, ttl: int) -> dict:
        """Returns the provider payload plus a '_meta' block (data-origin proof: latency, cache hit, provider search id)."""
        if not self.configured:
            raise ProviderError(PROVIDER, ErrorKind.NOT_CONFIGURED, "FLIGHT_API_KEY / HOTEL_API_KEY missing")
        key = "serp:" + hashlib.sha1(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()
        t0 = time.perf_counter()
        cached = self.cache.get(key)
        if cached is None:
            data = self.http.request_json(PROVIDER, "GET", ENDPOINT, params={**params, "api_key": self.s.serpapi_api_key})
            if not isinstance(data, dict):
                raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, "unexpected payload")
            if data.get("error"):
                raise ProviderError(PROVIDER, classify_error(str(data["error"])), str(data["error"])[:200])
            self.cache.set(key, data, ttl)
            cached, hit = data, False
        else:
            hit = True
        sm = cached.get("search_metadata") or {}
        meta = {"provider": PROVIDER, "endpoint": f"{ENDPOINT} (engine={params.get('engine')})", "params": {k: v for k, v in params.items() if k != "api_key"},
                "provider_search_id": sm.get("id"), "provider_status": sm.get("status"), "provider_created_at": sm.get("created_at"),
                "provider_time_taken_s": sm.get("total_time_taken"), "latency_ms": int((time.perf_counter() - t0) * 1000), "served_from_cache": hit,
                "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        return {**cached, "_meta": meta}

    # ------------------------------------------------------------------ flights
    def search_flights(self, dep_ids: list[str], arr_ids: list[str], outbound: dt.date, ret: Optional[dt.date], cabin: Cabin, currency: str,
                       departure_token: Optional[str] = None) -> dict:
        """Fares are requested for ONE adult so every price is unambiguously per person."""
        params: dict[str, Any] = {"engine": "google_flights", "departure_id": ",".join(dep_ids), "arrival_id": ",".join(arr_ids), "outbound_date": outbound.isoformat(),
                                  "type": 1 if ret else 2, "travel_class": CABIN_CODE[cabin], "adults": 1, "currency": currency, "hl": self.s.serpapi_language, "gl": self.s.serpapi_country}
        if ret:
            params["return_date"] = ret.isoformat()
        if departure_token:
            params["departure_token"] = departure_token
        return self._call(params, self.s.ttl_flights)

    def _tz(self, iata: str | None) -> str:
        ap = self.locations.airport(iata or "")
        return ap.tz if ap else ""

    def parse_flights(self, data: dict, direction: str, currency: str, cabin: Cabin, round_trip: bool, search_url: Optional[str]) -> list[FlightOption]:
        if not isinstance(data, dict):
            return []
        as_list = lambda v: [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []  # noqa: E731 - provider data is untrusted
        items = as_list(data.get("best_flights")) + as_list(data.get("other_flights"))
        retrieved = (data.get("_meta") or {}).get("retrieved_at")
        # Prefer SerpAPI's own Google Flights URL (it is the live search page Google returned).
        provider_url = safe_url((data.get("search_metadata") or {}).get("google_flights_url"))
        url = provider_url or search_url
        out: list[FlightOption] = []
        seen: set[str] = set()
        for it in items:
            try:
                opt = self._parse_one(it, direction, currency, cabin, round_trip, url, retrieved)
            except Exception as exc:  # one malformed item must not kill the batch
                log.warning("skipping malformed flight item: %s", type(exc).__name__)
                continue
            if opt and opt.id not in seen:
                seen.add(opt.id)
                out.append(opt)
        return out

    def _parse_one(self, it: dict, direction: str, currency: str, cabin: Cabin, round_trip: bool, url: Optional[str], retrieved: Optional[str]) -> Optional[FlightOption]:
        raw = it.get("flights") or []
        if not raw:
            return None
        segs: list[FlightSegment] = []
        for s in raw:
            da, aa = s.get("departure_airport") or {}, s.get("arrival_airport") or {}
            dtz, atz = self._tz(da.get("id")), self._tz(aa.get("id"))
            segs.append(FlightSegment(airline=s.get("airline", ""), flight_number=str(s.get("flight_number", "")), airplane=s.get("airplane", "") or "", origin_iata=da.get("id", ""),
                                      origin_name=da.get("name", ""), destination_iata=aa.get("id", ""), destination_name=aa.get("name", ""),
                                      departs_at=localize(da.get("time", ""), dtz) if dtz else None, arrives_at=localize(aa.get("time", ""), atz) if atz else None,
                                      duration_min=s.get("duration"), cabin=s.get("travel_class", "") or "", overnight=bool(s.get("overnight"))))
        first, last = segs[0], segs[-1]
        if first.departs_at is None or last.arrives_at is None:
            return None  # without a trustworthy timezone we refuse to show a time rather than guess one
        computed = minutes_between(first.departs_at, last.arrives_at)
        if computed <= 0:
            return None
        provider_total = it.get("total_duration")
        if provider_total and abs(provider_total - computed) > 15:
            log.warning("duration mismatch for %s: provider=%s computed=%s", first.flight_number, provider_total, computed)
        layovers = [Layover(iata=l.get("id", ""), name=l.get("name", ""), duration_min=l.get("duration"), overnight=bool(l.get("overnight"))) for l in it.get("layovers") or []]
        price = it.get("price")
        price = float(price) if isinstance(price, (int, float)) else None
        bag = [e for e in (it.get("extensions") or []) if "bag" in str(e).lower()]
        for s in raw:
            bag += [e for e in (s.get("extensions") or []) if "bag" in str(e).lower()]
        airlines = list(dict.fromkeys(s.airline for s in segs if s.airline))
        numbers = [s.flight_number for s in segs if s.flight_number]
        fid = direction[:3] + "-" + hashlib.sha1(f"{numbers}{first.departs_at.isoformat()}{price}".encode()).hexdigest()[:10]
        return FlightOption(
            id=fid, direction=direction, airlines=airlines, flight_numbers=numbers, airline_logo=safe_url(it.get("airline_logo")), origin_iata=first.origin_iata,
            origin_name=first.origin_name, destination_iata=last.destination_iata, destination_name=last.destination_name, origin_tz=self._tz(first.origin_iata),
            destination_tz=self._tz(last.destination_iata), departs_at=first.departs_at, arrives_at=last.arrives_at, arrival_day_offset=day_offset(first.departs_at, last.arrives_at),
            duration_min=computed, stops=len(segs) - 1, layovers=layovers, segments=segs, cabin=first.cabin or cabin.value.replace("_", " ").title(),
            baggage="; ".join(dict.fromkeys(bag)) or None, price_per_person=price, currency=currency,
            price_note=("Round-trip fare per traveller as quoted by Google Flights" if round_trip else "One-way fare per traveller as quoted by Google Flights") if price else "Price unavailable",
            source=PROVIDER, data_kind=DataKind.LIVE, retrieved_at=retrieved, booking_url=url, departure_token=it.get("departure_token"),
            local_dep_label=fmt_local(first.departs_at), local_arr_label=fmt_local(last.arrives_at))

    # ------------------------------------------------------------------ hotels
    def search_hotels(self, query: str, check_in: dt.date, check_out: dt.date, adults: int, children: int, children_ages: list[int], currency: str, stars: list[int]) -> dict:
        params: dict[str, Any] = {"engine": "google_hotels", "q": query, "check_in_date": check_in.isoformat(), "check_out_date": check_out.isoformat(), "adults": adults,
                                  "children": children, "currency": currency, "hl": self.s.serpapi_language, "gl": self.s.serpapi_country}
        if children:
            params["children_ages"] = ",".join(str(a) for a in (children_ages + [8] * children)[:children])
        valid = sorted({s for s in stars if 2 <= s <= 5})
        if valid:
            params["hotel_class"] = ",".join(str(s) for s in valid)
        return self._call(params, self.s.ttl_hotels)

    def parse_hotels(self, data: dict, currency: str, check_in: dt.date, check_out: dt.date, rooms: int, search_url: Optional[str]) -> list[HotelOption]:
        nights = (check_out - check_in).days
        retrieved = (data.get("_meta") or {}).get("retrieved_at")
        out: list[HotelOption] = []
        for p in (data.get("properties") if isinstance(data, dict) and isinstance(data.get("properties"), list) else []):
            try:
                name = (p.get("name") or "").strip()
                kind = str(p.get("type") or "hotel").lower()
                skip = ("vacation rental", "apartment", "home", "villa rental", "holiday home")
                if not name or (kind and any(s in kind for s in skip) and not any(x in kind for x in ("hotel", "resort", "inn", "lodge"))):
                    continue
                rate = (p.get("rate_per_night") or {}).get("extracted_lowest")
                total = (p.get("total_rate") or {}).get("extracted_lowest")
                nightly = float(rate) if isinstance(rate, (int, float)) else (float(total) / nights if isinstance(total, (int, float)) and nights else None)
                total_all = float(total) if isinstance(total, (int, float)) else (nightly * nights if nightly is not None else None)
                if total_all is not None:
                    total_all *= rooms
                gps = p.get("gps_coordinates") or {}
                stars = p.get("extracted_hotel_class")
                if not isinstance(stars, int):
                    cls = str(p.get("hotel_class") or "")
                    stars = int(cls[0]) if cls[:1].isdigit() else None
                images = p.get("images") or []
                thumb = safe_url((images[0].get("thumbnail") if images and isinstance(images[0], dict) else None) or p.get("thumbnail"))
                amenities = [str(a) for a in (p.get("amenities") or [])][:14]
                canc = next((str(t) for t in (amenities + [str(p.get("essential_info") or "")]) if "cancel" in t.lower()), None)
                loc_bits = [str(n.get("name")) for n in (p.get("nearby_places") or [])[:2] if isinstance(n, dict) and n.get("name")]
                out.append(HotelOption(
                    id="htl-" + hashlib.sha1(f"{name}{gps}".encode()).hexdigest()[:10], name=name, location=("Near " + ", ".join(loc_bits)) if loc_bits else "",
                    lat=gps.get("latitude"), lon=gps.get("longitude"), star_rating=stars, guest_rating=p.get("overall_rating"), reviews=p.get("reviews"), price_per_night=nightly,
                    total_price=total_all, currency=currency, nights=nights, check_in=check_in, check_out=check_out, check_in_time=p.get("check_in_time"),
                    check_out_time=p.get("check_out_time"), amenities=amenities, cancellation=canc, thumbnail=thumb, source=PROVIDER, data_kind=DataKind.LIVE,
                    booking_url=safe_url(p.get("link")) or search_url,
                    price_note=("Lowest listed nightly rate for the searched occupancy; total = rate x rooms x nights (estimated). Confirm before booking." if nightly else "Price unavailable from provider")))
            except Exception as exc:
                log.warning("skipping malformed hotel item: %s", type(exc).__name__)
        for h in out:
            h.price_note = h.price_note + (f" Retrieved {retrieved}." if retrieved else "")
        return out
