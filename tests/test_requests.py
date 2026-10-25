import pytest
from pydantic import ValidationError

from backend.agents.schemas import FlightAgentRequest, HotelAgentRequest, ItineraryAgentRequest
from .conftest import future


def f(**kw):
    base = dict(origin="Chennai", destination="Goa", departure_date=future())
    return FlightAgentRequest(**{**base, **kw})


def test_flight_valid_defaults():
    r = f()
    assert r.currency == "INR" and r.passengers == 1 and r.budget_scope == "per_passenger"


@pytest.mark.parametrize("kw", [dict(departure_date=future(-1)), dict(return_date=future(1)), dict(adults=0), dict(adults=8, children=3), dict(budget=-5), dict(budget_scope="x"), dict(currency="RUPEES"), dict(origin="A")])
def test_flight_invalid(kw):
    with pytest.raises(ValidationError):
        f(**kw)


def h(**kw):
    base = dict(destination="Goa", check_in=future(), check_out=future(33))
    return HotelAgentRequest(**{**base, **kw})


def test_hotel_valid():
    assert h().rooms == 1


@pytest.mark.parametrize("kw", [dict(check_out=future(30)), dict(check_out=future(29)), dict(check_in=future(-2)), dict(rooms=3, adults=2), dict(budget_scope="nightly"), dict(stars=[7]), dict(check_out=future(90)), dict(check_in=None)])
def test_hotel_invalid(kw):
    with pytest.raises(ValidationError):
        h(**kw)


def test_hotel_checkin_optional_with_flight_id():
    assert HotelAgentRequest(destination="Goa", check_out=future(33), flight_id="x").check_in is None


@pytest.mark.parametrize("kw", [dict(start_time="25:00"), dict(start_time="8am"), dict(pace="warp"), dict(window_hours=2)])
def test_itinerary_invalid(kw):
    with pytest.raises(ValidationError):
        ItineraryAgentRequest(destination="Goa", **kw)
