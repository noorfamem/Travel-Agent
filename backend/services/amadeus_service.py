"""Amadeus for Developers — free-tier flight offers search.

Free tier: https://developers.amadeus.com/self-service/category/flights/api-doc/flight-offers-search
  - 500 API calls/month on the free test environment (test.api.amadeus.com).
  - Production requires approval but test data covers most major Indian routes.
  - Requires OAuth2 client_credentials grant; token expires in 30 min.

This service is used as a FALLBACK when SerpAPI is unavailable or returns zero results.
It never provides hotel prices (hotel search requires a separate paid endpoint).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import time
from typing import Any, Optional

from ..config.settings import Settings
from ..models.travel_models import Cabin, DataKind, FlightOption, FlightSegment, Layover
from ..tools.time_tools import day_offset, fmt_local, localize, minutes_between
from ..utils.errors import ErrorKind, ProviderError
from ..utils.http_client import HttpClient
from ..utils.logging_config import get_logger
from .location_service import LocationService

log = get_logger("amadeus")

PROVIDER = "Amadeus (Flight Offers)"
BASE_URL = "https://test.api.amadeus.com/v2"
TOKEN_URL = "https://test.api.amadeus.com/v1/security/oauth2/token"

# Map Amadeus cabin codes to our Cabin enum
_CABIN_PARAM = {
    Cabin.ECONOMY: "ECONOMY",
    Cabin.PREMIUM_ECONOMY: "PREMIUM_ECONOMY",
    Cabin.BUSINESS: "BUSINESS",
    Cabin.FIRST: "FIRST",
}

# Amadeus uses IATA airline codes — we show them as-is
_AIRLINE_NAMES: dict[str, str] = {
    "6E": "IndiGo", "SG": "SpiceJet", "AI": "Air India", "IX": "Air India Express",
    "UK": "Vistara", "G8": "Go First", "QP": "Akasa Air", "S5": "Star Air",
    "YW": "Air Nostrum", "9W": "Jet Airways",
}


class AmadeusService:
    """Amadeus Flight Offers Search — real fares for Indian domestic routes."""

    def __init__(self, settings: Settings, http: HttpClient, locations: LocationService):
        self.s, self.http, self.locations = settings, http, locations
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.s.amadeus_client_id and self.s.amadeus_client_secret)

    # ──────────────────────────────────────────────── auth ─────────────────────

    def _get_token(self) -> str:
        """Return a valid OAuth2 bearer token, refreshing if needed."""
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        import requests  # already imported by http_client; safe to use directly here
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.s.amadeus_client_id,
                "client_secret": self.s.amadeus_client_secret,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            raise ProviderError(PROVIDER, ErrorKind.AUTH, f"Token request failed: HTTP {resp.status_code}")
        data = resp.json()
        self._token = data.get("access_token", "")
        expires_in = int(data.get("expires_in", 1799))
        self._token_expiry = time.time() + expires_in
        if not self._token:
            raise ProviderError(PROVIDER, ErrorKind.AUTH, "No access_token in response")
        return self._token

    # ──────────────────────────────────────────── search ────────────────────────

    def search_flights(
        self,
        origin_iata: str,
        dest_iata: str,
        outbound: dt.date,
        ret: Optional[dt.date],
        cabin: Cabin,
        currency: str,
        adults: int = 1,
    ) -> list[FlightOption]:
        """Return up to 10 flight offers for the given route."""
        if not self.configured:
            raise ProviderError(PROVIDER, ErrorKind.NOT_CONFIGURED, "AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET missing")

        token = self._get_token()
        params: dict[str, Any] = {
            "originLocationCode": origin_iata.upper(),
            "destinationLocationCode": dest_iata.upper(),
            "departureDate": outbound.isoformat(),
            "adults": adults,
            "travelClass": _CABIN_PARAM.get(cabin, "ECONOMY"),
            "currencyCode": currency,
            "max": 10,
        }
        if ret:
            params["returnDate"] = ret.isoformat()

        try:
            import requests
            resp = requests.get(
                f"{BASE_URL}/shopping/flight-offers",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=20,
            )
        except Exception as exc:
            raise ProviderError(PROVIDER, ErrorKind.NETWORK, type(exc).__name__)

        if resp.status_code == 401:
            self._token = None  # force refresh next call
            raise ProviderError(PROVIDER, ErrorKind.AUTH, "Amadeus 401 — token expired")
        if resp.status_code == 429:
            raise ProviderError(PROVIDER, ErrorKind.RATE_LIMITED, "Amadeus monthly quota exceeded")
        if resp.status_code >= 500:
            raise ProviderError(PROVIDER, ErrorKind.SERVER, f"Amadeus HTTP {resp.status_code}")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("errors", [{}])[0].get("detail", "")[:200]
            except Exception:
                detail = ""
            raise ProviderError(PROVIDER, ErrorKind.BAD_REQUEST, f"HTTP {resp.status_code} {detail}")

        try:
            data = resp.json()
        except Exception:
            raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, "not JSON")

        return self._parse(data, currency, cabin, bool(ret))

    # ─────────────────────────────────────────── parsing ────────────────────────

    def _parse(self, data: dict, currency: str, cabin: Cabin, round_trip: bool) -> list[FlightOption]:
        out: list[FlightOption] = []
        offers = data.get("data") or []
        dictionaries = data.get("dictionaries") or {}
        carriers: dict[str, str] = dictionaries.get("carriers") or {}
        aircraft_dict: dict[str, str] = dictionaries.get("aircraft") or {}

        retrieved = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

        for offer in offers:
            try:
                opt = self._parse_offer(offer, currency, cabin, round_trip, carriers, aircraft_dict, retrieved)
                if opt:
                    out.append(opt)
            except Exception as exc:
                log.warning("skipping malformed Amadeus offer: %s", exc)
        return out

    def _parse_offer(
        self, offer: dict, currency: str, cabin: Cabin, round_trip: bool,
        carriers: dict, aircraft_dict: dict, retrieved: str,
    ) -> Optional[FlightOption]:
        # Price
        price_block = offer.get("price") or {}
        total_str = price_block.get("grandTotal") or price_block.get("total") or ""
        try:
            price = float(total_str) / (int(offer.get("travelerPricings", [{}])[0].get("travelerId", "1") or 1) or 1)
            # Amadeus grandTotal is already per-traveler when adults=1
            price = float(total_str)
        except Exception:
            price = None

        itineraries = offer.get("itineraries") or []
        if not itineraries:
            return None

        # Use first itinerary (outbound)
        itin = itineraries[0]
        segments_raw = itin.get("segments") or []
        if not segments_raw:
            return None

        segs: list[FlightSegment] = []
        layovers: list[Layover] = []

        for i, s in enumerate(segments_raw):
            dep_info = s.get("departure") or {}
            arr_info = s.get("arrival") or {}
            dep_iata = dep_info.get("iataCode", "")
            arr_iata = arr_info.get("iataCode", "")
            dep_ap = self.locations.airport(dep_iata)
            arr_ap = self.locations.airport(arr_iata)
            dep_tz = dep_ap.tz if dep_ap else "Asia/Kolkata"
            arr_tz = arr_ap.tz if arr_ap else "Asia/Kolkata"

            dep_at = localize(dep_info.get("at", "").replace("T", " ").replace("Z", "")[:16], dep_tz)
            arr_at = localize(arr_info.get("at", "").replace("T", " ").replace("Z", "")[:16], arr_tz)

            carrier_code = s.get("carrierCode", "")
            airline_name = carriers.get(carrier_code) or _AIRLINE_NAMES.get(carrier_code) or carrier_code
            flight_num = f"{carrier_code}{s.get('number', '')}"
            aircraft_code = (s.get("aircraft") or {}).get("code", "")
            aircraft_name = aircraft_dict.get(aircraft_code, aircraft_code)

            dur_min = minutes_between(dep_at, arr_at) if dep_at and arr_at else None

            segs.append(FlightSegment(
                airline=airline_name, flight_number=flight_num, airplane=aircraft_name,
                origin_iata=dep_iata, origin_name=dep_ap.name if dep_ap else dep_iata,
                destination_iata=arr_iata, destination_name=arr_ap.name if arr_ap else arr_iata,
                departs_at=dep_at, arrives_at=arr_at,
                duration_min=dur_min, cabin=_CABIN_PARAM.get(cabin, "ECONOMY"),
            ))

            # Layover between segments
            if i < len(segments_raw) - 1:
                next_dep = (segments_raw[i + 1].get("departure") or {}).get("at", "")
                if arr_at and next_dep:
                    next_dep_dt = localize(next_dep.replace("T", " ").replace("Z", "")[:16], arr_tz)
                    lay_min = minutes_between(arr_at, next_dep_dt) if next_dep_dt else None
                    layovers.append(Layover(iata=arr_iata, name=arr_ap.name if arr_ap else arr_iata, duration_min=lay_min))

        if not segs or segs[0].departs_at is None or segs[-1].arrives_at is None:
            return None

        first, last = segs[0], segs[-1]
        total_dur = minutes_between(first.departs_at, last.arrives_at)
        if total_dur <= 0:
            return None

        airlines = list(dict.fromkeys(s.airline for s in segs if s.airline))
        numbers = [s.flight_number for s in segs if s.flight_number]
        fid = "amd-" + hashlib.sha1(f"{numbers}{first.departs_at.isoformat()}{price}".encode()).hexdigest()[:10]

        price_note = (
            "Round-trip fare per traveller as quoted by Amadeus (test environment)"
            if round_trip else
            "One-way fare per traveller as quoted by Amadeus (test environment)"
        ) if price else "Price unavailable"

        return FlightOption(
            id=fid, direction="outbound", airlines=airlines, flight_numbers=numbers,
            origin_iata=first.origin_iata, origin_name=first.origin_name,
            destination_iata=last.destination_iata, destination_name=last.destination_name,
            origin_tz=self.locations.airport(first.origin_iata).tz if self.locations.airport(first.origin_iata) else "Asia/Kolkata",
            destination_tz=self.locations.airport(last.destination_iata).tz if self.locations.airport(last.destination_iata) else "Asia/Kolkata",
            departs_at=first.departs_at, arrives_at=last.arrives_at,
            arrival_day_offset=day_offset(first.departs_at, last.arrives_at),
            duration_min=total_dur, stops=len(segs) - 1, layovers=layovers, segments=segs,
            cabin=_CABIN_PARAM.get(cabin, "ECONOMY"),
            price_per_person=price, currency=currency,
            price_note=price_note,
            source=PROVIDER, data_kind=DataKind.LIVE, retrieved_at=retrieved,
            local_dep_label=fmt_local(first.departs_at), local_arr_label=fmt_local(last.arrives_at),
        )
