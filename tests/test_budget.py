import pytest

from backend.models.travel_models import DataKind
from backend.tools.budget import dedupe_flights, dedupe_hotels, hotel_stay_total, split_flights_by_budget, split_hotels_by_budget, trip_summary
from backend.tools.ranking import rank_flights
from backend.models.travel_models import TravelStyle
from .conftest import mk_flight, mk_hotel


def test_flight_budget_per_passenger_uses_actual_price():
    opts = [mk_flight("a", 4000, ("A1",)), mk_flight("b", 6000, ("B1",)), mk_flight("c", 5000, ("C1",))]
    within, over, _ = split_flights_by_budget(opts, 5000, "per_passenger", 2, "INR")
    assert {o.id for o in within} == {"a", "c"} and [o.id for o in over] == ["b"]


def test_flight_budget_total_multiplies_by_passengers():
    opts = [mk_flight("a", 4000, ("A1",)), mk_flight("b", 6000, ("B1",))]
    within, over, _ = split_flights_by_budget(opts, 9000, "total", 2, "INR")  # a: 8000 fits, b: 12000 doesn't
    assert [o.id for o in within] == ["a"] and [o.id for o in over] == ["b"]


def test_ranking_prefers_cheaper_when_otherwise_equal():
    cheap, dear = mk_flight("cheap", 3000, ("A1",)), mk_flight("dear", 9000, ("B1",))
    ranked = rank_flights([dear, cheap], TravelStyle.BUDGET, None, 1)
    assert ranked[0].id == "cheap"


def test_currency_mismatch_is_excluded_not_converted():
    within, over, excluded = split_flights_by_budget([mk_flight("usd", 50, currency="USD"), mk_flight("inr", 4000, ("X",))], 5000, "per_passenger", 1, "INR")
    assert [o.id for o in within] == ["inr"] and excluded and "currency" in excluded[0]["reason"]


def test_missing_price_is_excluded():
    f = mk_flight("np", 1000)
    f.price_per_person = None
    _, _, excluded = split_flights_by_budget([f], 5000, "per_passenger", 1, "INR")
    assert excluded and "no price" in excluded[0]["reason"]


def test_invalid_scope_rejected():
    with pytest.raises(ValueError):
        split_flights_by_budget([], 1, "weekly", 1, "INR")
    with pytest.raises(ValueError):
        split_hotels_by_budget([], 1, "weekly", 1, 1, "INR")


def test_duplicate_flights_keep_cheapest():
    out = dedupe_flights([mk_flight("a", 5000), mk_flight("b", 4500)])
    assert len(out) == 1 and out[0].id == "b"


def test_duplicate_hotels_keep_cheapest():
    out = dedupe_hotels([mk_hotel("a", "Taj  Resort", 5000), mk_hotel("b", "taj resort", 4000)])
    assert len(out) == 1 and out[0].id == "b"


def test_hotel_budget_units_per_night_vs_total_stay():
    h = mk_hotel("h", nightly=3000, nights=3)  # total 9000
    assert split_hotels_by_budget([h], 3000, "per_night", 3, 1, "INR")[0] == [h]
    assert split_hotels_by_budget([h], 8000, "total_stay", 3, 1, "INR")[1] == [h]
    assert split_hotels_by_budget([h], 9000, "total_stay", 3, 1, "INR")[0] == [h]


def test_hotel_total_calculated_only_from_price_data():
    h = mk_hotel("h", nightly=2000, total=None, nights=2, rooms=1)
    h.total_price = None
    assert hotel_stay_total(h, 2, 2) == 8000
    h.price_per_night = None
    assert hotel_stay_total(h, 2, 2) is None


def test_trip_summary_partial_and_estimates_never_exact():
    s = trip_summary(currency="INR", passengers=2, nights=3, rooms=1, flight=mk_flight("f", 5000), hotel=None, activity_total=1000, meal_total=500, overall_budget=20000)
    assert s["is_partial"] and not s["is_exact"] and s["total"] == 11500 and s["remaining"] == 8500


def test_trip_summary_over_budget_and_exact_only_with_provider_prices():
    s = trip_summary(currency="INR", passengers=1, nights=2, rooms=1, flight=mk_flight("f", 9000), hotel=mk_hotel("h", nightly=5000, nights=2), activity_total=None, meal_total=None, overall_budget=10000)
    assert s["over_budget"] and s["total"] == 19000
    assert s["is_partial"]  # itinerary costs missing -> flagged
