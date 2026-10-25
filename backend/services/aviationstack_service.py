"""AviationStack schedules (optional, paid 'flightsFuture' endpoint). Schedule-only: it NEVER provides fares."""
from __future__ import annotations

import datetime as dt
import hashlib

from ..config.settings import Settings
from ..models.travel_models import DataKind, FlightOption, FlightSegment
from ..tools.geo_tools import haversine_km
from ..tools.time_tools import day_offset, fmt_local, localize, minutes_between
from ..utils.errors import ErrorKind, ProviderError
from ..utils.http_client import HttpClient
from .location_service import LocationService

PROVIDER = "AviationStack"


class AviationStackService:
    def __init__(self, settings: Settings, http: HttpClient, locations: LocationService):
        self.s, self.http, self.locations = settings, http, locations

    @property
    def configured(self) -> bool:
        return bool(self.s.aviationstack_api_key and self.s.aviationstack_future)

    def future_schedule(self, dep_iata: str, arr_iata: str, date: dt.date) -> list[FlightOption]:
        if not self.configured:
            raise ProviderError(PROVIDER, ErrorKind.NOT_CONFIGURED, "AVIATIONSTACK_API_KEY / AVIATIONSTACK_FUTURE_ENABLED missing")
        data = self.http.request_json(PROVIDER, "GET", f"{self.s.aviationstack_base_url}/flightsFuture",
                                      params={"access_key": self.s.aviationstack_api_key, "iataCode": dep_iata, "type": "departure", "date": date.isoformat()})
        if isinstance(data, dict) and data.get("error"):
            code = str((data["error"] or {}).get("code", ""))
            raise ProviderError(PROVIDER, ErrorKind.RATE_LIMITED if "limit" in code else ErrorKind.AUTH if "key" in code or "access" in code else ErrorKind.BAD_REQUEST, code or "error")
        dep_ap, arr_ap = self.locations.airport(dep_iata), self.locations.airport(arr_iata)
        if not dep_ap or not arr_ap:
            return []
        est = haversine_km(dep_ap.lat, dep_ap.lon, arr_ap.lat, arr_ap.lon) / 800 * 60 + 40
        out: list[FlightOption] = []
        for f in (data or {}).get("data", []) or []:
            if (f.get("arrival") or {}).get("iataCode", "").upper() != arr_iata.upper() or f.get("codeshared"):
                continue
            dep = localize(f"{date.isoformat()} {(f.get('departure') or {}).get('scheduledTime', '')}", dep_ap.tz)
            arr_time = (f.get("arrival") or {}).get("scheduledTime", "")
            if not dep or not arr_time:
                continue
            best = None
            for add in range(0, 3):  # AviationStack gives the arrival wall-clock time only: infer the day from physical plausibility
                arr = localize(f"{(date + dt.timedelta(days=add)).isoformat()} {arr_time}", arr_ap.tz)
                if arr and 25 <= minutes_between(dep, arr) <= est * 2.2 + 120 and (best is None or abs(minutes_between(dep, arr) - est) < abs(minutes_between(dep, best) - est)):
                    best = arr
            if not best:
                continue
            airline, num = (f.get("airline") or {}).get("name", ""), (f.get("flight") or {}).get("iataNumber", "")
            seg = FlightSegment(airline=airline, flight_number=num, origin_iata=dep_iata, origin_name=dep_ap.name, destination_iata=arr_iata, destination_name=arr_ap.name,
                                departs_at=dep, arrives_at=best, duration_min=minutes_between(dep, best))
            out.append(FlightOption(id="avs-" + hashlib.sha1(f"{num}{dep.isoformat()}".encode()).hexdigest()[:10], airlines=[airline] if airline else [], flight_numbers=[num] if num else [],
                                    origin_iata=dep_iata, origin_name=dep_ap.name, destination_iata=arr_iata, destination_name=arr_ap.name, origin_tz=dep_ap.tz, destination_tz=arr_ap.tz,
                                    departs_at=dep, arrives_at=best, arrival_day_offset=day_offset(dep, best), duration_min=seg.duration_min, stops=0, segments=[seg],
                                    price_note="Fare unavailable - AviationStack provides schedules only", source=PROVIDER, data_kind=DataKind.LIVE,
                                    local_dep_label=fmt_local(dep), local_arr_label=fmt_local(best)))
        return out
