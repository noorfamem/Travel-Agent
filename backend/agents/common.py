from __future__ import annotations

from typing import Any, Optional

from ..config.settings import get_settings
from ..models.travel_models import Airport
from ..services.location_service import LocationService
from ..tools.link_tools import safe_url
from ..utils.errors import ErrorKind, ProviderError

# HTTP status each agent status maps to (HTTP 200 is only used when the agent really produced a result)
HTTP_FOR_STATUS = {"ok": 200, "no_results": 200, "needs_clarification": 200, "invalid_location": 422, "unavailable": 503, "error": 502, "no_time": 200}


def status_for_error(e: ProviderError) -> str:
    return "unavailable" if e.kind in (ErrorKind.NOT_CONFIGURED, ErrorKind.AUTH, ErrorKind.RATE_LIMITED) else "error"


def india_airports(locations: LocationService, text: str, label: str) -> tuple[list[Airport], Optional[dict[str, Any]]]:
    """Resolve free text to up to 3 airports, or an explanation dict (never guesses)."""
    loc = locations.resolve(text)
    if loc.kind == "ambiguous":
        return [], {"status": "needs_clarification", "message": f"'{text}' could mean several places. Please choose one.", "candidates": loc.candidates[:8], "field": label}
    s = get_settings()
    if s.domestic_only:
        ap = [a for a in loc.airports if a.iata and (not a.country_code or a.country_code == "IN")]
        if loc.country_code and loc.country_code != "IN":
            return [], {"status": "invalid_location", "message": f"'{text}' is outside India; this app searches Indian domestic routes.", "field": label}
    else:
        ap = [a for a in loc.airports if a.iata]
    if not ap:
        hint = f" Did you mean: {', '.join(loc.candidates[:4])}?" if loc.candidates else ""
        return [], {"status": "invalid_location", "message": f"No airport found for '{text}'. Enter a nearby city or a 3-letter airport code (e.g. MAA).{hint}", "field": label, "candidates": loc.candidates[:4]}
    return ap[:3], None


def airport_info(aps) -> list[dict[str, Any]]:
    return [{"iata": a.iata, "name": a.name, "distance_km": a.distance_km} for a in aps]


def clean_link(url: Optional[str]) -> Optional[str]:
    return safe_url(url)
