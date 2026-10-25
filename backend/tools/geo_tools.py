"""Geographic helpers (pure functions)."""
from __future__ import annotations

import math
from urllib.parse import quote_plus

EARTH_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


def map_link(lat: float | None = None, lon: float | None = None, name: str = "", city: str = "") -> str | None:
    """Google Maps link built from real coordinates when known, otherwise a name search (never invented coordinates)."""
    if lat is not None and lon is not None:
        return f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}"
    q = " ".join(x for x in (name, city) if x).strip()
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(q)}" if q else None


def estimate_travel_minutes(distance_km: float) -> int:
    """Rough urban door-to-door time: ~22 km/h average incl. waiting; minimum 8 minutes."""
    return max(8, int(round(distance_km / 22 * 60)))


def transport_mode(distance_km: float) -> str:
    if distance_km <= 1.2:
        return "Walk"
    if distance_km <= 6:
        return "Taxi / ride-hail or local transit"
    return "Taxi / metro / local train (check local options)"
