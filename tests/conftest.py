import datetime as dt
import os
import zoneinfo

import pytest

# Tests must never see real credentials or touch a real database file.
os.environ["TESTING"] = "1"
os.environ["DOMESTIC_ONLY"] = "true"
for k in ("GROQ_API_KEY", "TAVILY_API_KEY", "AVIATIONSTACK_API_KEY", "SERPAPI_API_KEY", "FLIGHT_API_KEY", "HOTEL_API_KEY", "OPENAI_API_KEY", "FLIGHT_PROVIDER_API_KEY"):
    os.environ.pop(k, None)
os.environ["DATABASE_URL"] = "sqlite:///./data/test_travelai.db"
os.environ["VERIFY_LINKS"] = "false"

from backend.agents.store import SelectionStore  # noqa: E402
from backend.models.travel_models import DataKind, FlightOption, HotelOption  # noqa: E402
from backend.providers.flight_provider import ProviderFlights  # noqa: E402
from backend.providers.hotel_provider import ProviderHotels  # noqa: E402
from backend.services.location_service import LocationService  # noqa: E402

IST = zoneinfo.ZoneInfo("Asia/Kolkata")


def future(days: int = 30) -> dt.date:
    return dt.date.today() + dt.timedelta(days=days)


def mk_flight(fid="f1", price=5000.0, numbers=("6E123",), stops=0, hour=9, currency="INR", airline="IndiGo", arrive_hour=None, day=None, kind=DataKind.LIVE) -> FlightOption:
    d = day or future()
    dep = dt.datetime(d.year, d.month, d.day, hour, 0, tzinfo=IST)
    arr = dep + dt.timedelta(hours=2) if arrive_hour is None else dt.datetime(d.year, d.month, d.day, arrive_hour, 0, tzinfo=IST)
    return FlightOption(id=fid, airlines=[airline], flight_numbers=list(numbers), origin_iata="MAA", destination_iata="GOI", destination_name="Goa", departs_at=dep, arrives_at=arr,
                        duration_min=int((arr - dep).total_seconds() // 60), stops=stops, price_per_person=price, currency=currency, price_note="Test fare", source="TestProvider", data_kind=kind,
                        local_dep_label="09:00", local_arr_label="11:00")


def mk_hotel(hid="h1", name="Hotel A", nightly=3000.0, total=None, nights=3, rooms=1, stars=4, currency="INR", cancellation=None, amenities=()) -> HotelOption:
    return HotelOption(id=hid, name=name, price_per_night=nightly, total_price=total if total is not None else (nightly * nights * rooms if nightly else None), currency=currency, nights=nights,
                       star_rating=stars, guest_rating=4.2, cancellation=cancellation, amenities=list(amenities), source="TestHotels", data_kind=DataKind.LIVE)


class FakeFlights:
    name = "FakeFlights"

    def __init__(self, options=None, error=None, configured=True, kind=DataKind.LIVE):
        self.options, self.error, self._c, self.kind, self.calls = options or [], error, configured, kind, 0

    @property
    def configured(self):
        return self._c

    def search(self, *a, **k):
        self.calls += 1
        if self.error:
            raise self.error
        return ProviderFlights(list(self.options), self.name, self.kind, "2026-10-19T10:00:00Z")


class FakeHotels:
    name = "FakeHotels"

    def __init__(self, options=None, error=None, configured=True, kind=DataKind.LIVE):
        self.options, self.error, self._c, self.kind = options or [], error, configured, kind

    @property
    def configured(self):
        return self._c

    def search(self, *a, **k):
        if self.error:
            raise self.error
        return ProviderHotels(list(self.options), self.name, self.kind, "2026-10-19T10:00:00Z")


@pytest.fixture(scope="session")
def locations():
    return LocationService()


@pytest.fixture
def store():
    return SelectionStore()
