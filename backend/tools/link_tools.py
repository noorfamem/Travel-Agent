"""URL hygiene. We never invent URLs; we only accept well-formed http(s) links that came from a provider or were built from real data."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

_BAD = re.compile(r"[\s<>\"']")


def safe_url(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    if len(u) > 2000 or _BAD.search(u):
        return None
    parts = urlsplit(u)
    if parts.scheme not in {"http", "https"} or not parts.netloc or "." not in parts.netloc:
        return None
    return u


def domain(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def is_google(url: str) -> bool:
    d = domain(url)
    return d.endswith("google.com") or d.endswith("google.co.in") or d.endswith("goo.gl")


# Booking aggregators / OTAs — useful as search pages, not as a hotel's official site.
_OTA_HOSTS = (
    "booking.com", "makemytrip.com", "agoda.com", "tripadvisor.com", "tripadvisor.in",
    "expedia.com", "expedia.co.in", "hotels.com", "goibibo.com", "yatra.com",
    "cleartrip.com", "kayak.com", "kayak.co.in", "trivago.com", "trivago.in",
    "google.com", "google.co.in", "skyscanner.com", "skyscanner.co.in",
    "ixigo.com", "easemytrip.com", "priceline.com", "hotwire.com", "orbitz.com",
    "travelocity.com", "tripadvisor.in",
)


def is_ota_url(url: str | None) -> bool:
    if not url:
        return False
    host = domain(url)
    return any(host == h or host.endswith("." + h) for h in _OTA_HOSTS)
