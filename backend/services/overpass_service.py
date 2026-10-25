"""OpenStreetMap hotel listings via Overpass. Free, no API key. Directory data — not live room rates."""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any, Optional

from ..models.travel_models import DataKind, HotelOption
from ..tools.link_tools import is_ota_url, safe_url
from ..utils.errors import ErrorKind, ProviderError
from ..utils.http_client import HttpClient
from ..utils.logging_config import get_logger

log = get_logger("overpass")
PROVIDER = "OpenStreetMap (Overpass)"
ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)


def _num(v: Any) -> Optional[float]:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


class OverpassHotelService:
    def __init__(self, http: HttpClient):
        self.http = http

    @property
    def configured(self) -> bool:
        return True

    def search_hotels(self, lat: float, lon: float, check_in: dt.date, check_out: dt.date, rooms: int, currency: str, radius_m: int = 8000) -> list[HotelOption]:
        query = (
            f'[out:json][timeout:25];'
            f'(nwr["tourism"="hotel"](around:{radius_m},{lat:.5f},{lon:.5f});'
            f'nwr["tourism"="guest_house"](around:{radius_m},{lat:.5f},{lon:.5f});'
            f'nwr["tourism"="hostel"](around:{radius_m},{lat:.5f},{lon:.5f});'
            f'nwr["tourism"="motel"](around:{radius_m},{lat:.5f},{lon:.5f});'
            f'nwr["tourism"="chalet"](around:{radius_m},{lat:.5f},{lon:.5f});'
            f'nwr["tourism"="alpine_hut"](around:{radius_m},{lat:.5f},{lon:.5f}););'
            f'out center tags 40;'
        )
        data: dict[str, Any] | None = None
        last: Exception | None = None
        for url in ENDPOINTS:
            try:
                data = self.http.request_json(PROVIDER, "POST", url, data={"data": query}, retries=0)
                if isinstance(data, dict) and isinstance(data.get("elements"), list):
                    break
                last = ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, "unexpected payload")
                data = None
            except Exception as exc:
                last = exc
                data = None
        if data is None:
            if isinstance(last, ProviderError):
                raise last
            raise ProviderError(PROVIDER, ErrorKind.NETWORK, type(last).__name__ if last else "no endpoint")
        nights = max(1, (check_out - check_in).days)
        retrieved = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        out: list[HotelOption] = []
        seen: set[str] = set()
        for el in data.get("elements") or []:
            if not isinstance(el, dict):
                continue
            tags = el.get("tags") or {}
            if not isinstance(tags, dict):
                continue
            name = (tags.get("name") or tags.get("name:en") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            plat = el.get("lat") or (el.get("center") or {}).get("lat")
            plon = el.get("lon") or (el.get("center") or {}).get("lon")
            stars = tags.get("stars")
            star_n = int(stars[0]) if isinstance(stars, str) and stars[:1].isdigit() else (int(stars) if isinstance(stars, int) else None)
            amenities = []
            for tag, label in (
                ("internet_access", "Wi-Fi"),
                ("wifi", "Wi-Fi"),
                ("parking", "Parking"),
                ("swimming_pool", "Pool"),
                ("outdoor_seating", "Outdoor seating"),
                ("restaurant", "Restaurant"),
                ("air_conditioning", "Air conditioning"),
                ("wheelchair", "Wheelchair access"),
            ):
                val = str(tags.get(tag) or "").lower()
                if val in {"yes", "wlan", "public", "customers", "limited"}:
                    amenities.append(label)
            addr = ", ".join(x for x in (tags.get("addr:street"), tags.get("addr:city") or tags.get("addr:suburb")) if x)
            website = safe_url(tags.get("website") or tags.get("contact:website"))
            ota = is_ota_url(website)
            out.append(HotelOption(
                id="osm-" + hashlib.sha1(f"{name}{plat}{plon}".encode()).hexdigest()[:10],
                name=name,
                location=addr or "OpenStreetMap listing",
                lat=_num(plat),
                lon=_num(plon),
                star_rating=star_n if star_n and 1 <= star_n <= 5 else None,
                amenities=amenities[:14],
                nights=nights,
                check_in=check_in,
                check_out=check_out,
                currency=currency,
                source=PROVIDER,
                data_kind=DataKind.VERIFIED,
                booking_url=website if ota else None,
                official_website=None if ota else website,
                official_website_label=None if (ota or not website) else "Official Hotel Website",
                price_note="OpenStreetMap directory listing — live room rates are not included. Confirm price and availability on the hotel or a booking site.",
                retrieved_at=retrieved,
            ))
            if len(out) >= 12:
                break
        return out
