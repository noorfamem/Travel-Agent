import json

import pytest

from backend.agents.flight_agent import FlightAgent
from backend.agents.hotel_agent import HotelAgent
from backend.agents.itinerary_agent import ItineraryAgent, LLMItem, check_schedule, earliest_start
from backend.agents.orchestrator import Orchestrator
from backend.agents.schemas import FlightAgentRequest, HotelAgentRequest, ItineraryAgentRequest, TripSummaryRequest
from backend.models.travel_models import DataKind
from backend.services.tavily_service import WebResult, WebSearch
from backend.utils.errors import ErrorKind, LLMOutputError, ProviderError
from .conftest import FakeFlights, FakeHotels, future, mk_flight, mk_hotel


def freq(**kw):
    return FlightAgentRequest(**{**dict(origin="Chennai", destination="Goa", departure_date=future()), **kw})


def hreq(**kw):
    return HotelAgentRequest(**{**dict(destination="Goa", check_in=future(), check_out=future(33)), **kw})


def agent(locations, store, **kw):
    return FlightAgent(locations, FakeFlights(**kw), store)


# ---------------------------------------------------------------- flights
def test_flights_top5_sorted_within_budget_and_overbudget_separate(locations, store):
    opts = [mk_flight(f"f{i}", 3000 + i * 500, (f"X{i}",), hour=6 + i) for i in range(8)]
    out = agent(locations, store, options=opts).search(freq(budget=5000))
    assert out["status"] == "ok" and len(out["options"]) == 5
    assert all(o["price_per_person"] <= 5000 for o in out["options"])
    assert out["over_budget"] and all("OVER BUDGET" in o["labels"] and o["price_per_person"] > 5000 for o in out["over_budget"])


def test_flights_fewer_than_five_returns_fewer(locations, store):
    out = agent(locations, store, options=[mk_flight("a", 4000)]).search(freq())
    assert len(out["options"]) == 1


def test_flights_nothing_in_budget_shows_nearest_alternatives(locations, store):
    out = agent(locations, store, options=[mk_flight("a", 9000, ("A",)), mk_flight("b", 8000, ("B",))]).search(freq(budget=1000))
    assert out["options"] == [] and len(out["over_budget"]) == 2 and "over-budget" in out["message"]
    assert out["over_budget"][0]["price_per_person"] == 8000


def test_flights_missing_booking_link_is_labelled_search_link_not_offer(locations, store):
    out = agent(locations, store, options=[mk_flight("a", 4000)]).search(freq())
    o = out["options"][0]
    assert o["booking_url"] is None and o["link_kind"] == "search" and o["search_url"].startswith("https://www.google.com/travel/flights")
    assert o["open_search_url"] and o["open_search_url"].startswith("https://")
    assert o["search_links"] and all(x["url"].startswith("http") for x in o["search_links"])
    assert "NOT an offer" in out["search_url_label"]


def test_flights_provider_link_kept_only_if_safe(locations, store):
    good, bad = mk_flight("g", 4000, ("G",)), mk_flight("b", 4100, ("B",))
    good.booking_url, bad.booking_url = "https://airline.example/offer/1", "javascript:alert(1)"
    out = agent(locations, store, options=[good, bad]).search(freq())
    by = {o["id"]: o for o in out["options"]}
    assert by["g"]["link_kind"] == "offer" and by["b"]["booking_url"] is None and by["b"]["link_kind"] == "search"


def test_flights_nonstop_filter_and_duplicates(locations, store):
    opts = [mk_flight("a", 4000, ("A",), stops=1), mk_flight("b", 4500, ("B",)), mk_flight("b2", 4400, ("B",))]
    out = agent(locations, store, options=opts).search(freq(nonstop_only=True))
    assert [o["id"] for o in out["options"]] == ["b2"]


def test_flights_currency_mismatch_excluded(locations, store):
    out = agent(locations, store, options=[mk_flight("u", 50, ("U",), currency="USD"), mk_flight("i", 4000, ("I",))]).search(freq())
    assert [o["id"] for o in out["options"]] == ["i"] and out["excluded"]


def test_flights_empty_provider_response(locations, store):
    out = agent(locations, store, options=[]).search(freq())
    assert out["status"] == "no_results" and out["options"] == [] and out["search_url"]


@pytest.mark.parametrize("kind,expected", [(ErrorKind.NOT_CONFIGURED, "unavailable"), (ErrorKind.AUTH, "unavailable"), (ErrorKind.RATE_LIMITED, "unavailable"), (ErrorKind.TIMEOUT, "error"), (ErrorKind.NETWORK, "error"), (ErrorKind.INVALID_RESPONSE, "error")])
def test_flights_provider_failures_are_never_ok(locations, store, kind, expected):
    out = agent(locations, store, error=ProviderError("P", kind, "boom")).search(freq())
    assert out["status"] == expected and out["data_kind"] == "UNAVAILABLE" and out["options"] == [] and out["search_url"]


def test_flights_missing_credentials(locations, store):
    out = agent(locations, store, configured=False).search(freq())
    assert out["status"] == "unavailable" and out["options"] == [] and "not configured" in out["message"]


def test_flights_sandbox_label_preserved(locations, store):
    out = agent(locations, store, options=[mk_flight("s", 4000, kind=DataKind.SANDBOX)], kind=DataKind.SANDBOX).search(freq())
    assert out["data_kind"] == "SANDBOX" and out["options"][0]["data_kind"] == "SANDBOX"


def test_flights_ambiguous_or_foreign_or_unknown_location(locations, store):
    a = agent(locations, store, options=[mk_flight()])
    assert a.search(freq(destination="Paris"))["status"] == "invalid_location"
    assert a.search(freq(destination="Zzzzqqxx"))["status"] == "invalid_location"
    assert a.search(freq(origin="Chennai", destination="MAA"))["status"] == "invalid_location"  # same airport


def test_flights_results_registered_for_selection(locations, store):
    agent(locations, store, options=[mk_flight("sel1", 4000)]).search(freq())
    assert store.get("sel1", "flight") is not None and store.get("sel1", "hotel") is None


def test_flights_preferred_airline_first(locations, store):
    opts = [mk_flight("a", 3000, ("A",), airline="IndiGo"), mk_flight("b", 3500, ("B",), airline="Air India")]
    out = agent(locations, store, options=opts).search(freq(preferred_airlines=["Air India"]))
    assert out["options"][0]["id"] == "b"


def test_nearby_airport_distance_reported_for_city_without_airport(locations, store):
    out = agent(locations, store, options=[mk_flight()]).search(freq(origin="Arni"))
    assert any(a["distance_km"] for a in out["searched_airports"]["origin"])


# ---------------------------------------------------------------- hotels
def hagent(locations, store, **kw):
    return HotelAgent(locations, FakeHotels(**kw), store)


def test_hotels_top5_budget_per_night(locations, store):
    hs = [mk_hotel(f"h{i}", f"Hotel {i}", 2000 + i * 500) for i in range(8)]
    out = hagent(locations, store, options=hs).search(hreq(budget=4000))
    assert out["status"] == "ok" and len(out["options"]) <= 5 and all(o["price_per_night"] <= 4000 for o in out["options"]) and out["over_budget"]


def test_hotels_no_provider_never_fabricates(locations, store):
    out = hagent(locations, store, configured=False).search(hreq())
    assert out["status"] == "unavailable" and out["options"] == [] and "invented" in out["message"] and "NOT an offer" in out["search_url_label"]


@pytest.mark.parametrize("kind", [ErrorKind.AUTH, ErrorKind.TIMEOUT, ErrorKind.RATE_LIMITED, ErrorKind.INVALID_RESPONSE])
def test_hotels_provider_failures(locations, store, kind):
    out = hagent(locations, store, error=ProviderError("P", kind, "x")).search(hreq())
    assert out["status"] in ("unavailable", "error") and out["options"] == []


def test_hotels_filters_stars_amenities_cancellation(locations, store):
    hs = [mk_hotel("a", "A", 3000, stars=5, amenities=["Free Wi-Fi", "Pool"], cancellation="Free cancellation"), mk_hotel("b", "B", 2000, stars=3, amenities=["Free Wi-Fi"]), mk_hotel("c", "C", 2500, stars=5, amenities=["Pool"])]
    out = hagent(locations, store, options=hs).search(hreq(stars=[5], amenities=["wi-fi"], free_cancellation=True))
    assert [o["id"] for o in out["options"]] == ["a"]


def test_hotels_empty_and_currency_mismatch(locations, store):
    assert hagent(locations, store, options=[]).search(hreq())["status"] == "no_results"
    out = hagent(locations, store, options=[mk_hotel("u", "U", 50, currency="USD")]).search(hreq())
    assert out["options"] == [] and out["excluded"]


def test_hotels_link_none_when_provider_gave_none(locations, store):
    out = hagent(locations, store, options=[mk_hotel("a", "A", 3000)]).search(hreq())
    assert out["options"][0]["link_kind"] == "search" and out["options"][0]["search_url"]
    assert out["options"][0]["booking_search_url"] and out["options"][0]["booking_search_url"].startswith("https://www.booking.com/")


def test_hotels_foreign_destination_rejected(locations, store):
    assert hagent(locations, store, options=[mk_hotel()]).search(hreq(destination="Paris"))["status"] == "invalid_location"


# ---------------------------------------------------------------- itinerary
class FakeLLM:
    provider_name = "fake:llm"

    def __init__(self, plan=None, error=None, configured=True, bad=False):
        self.plan, self.error, self.configured, self.bad = plan, error, configured, bad

    def complete_model(self, model_cls, system, user, repairs=1, max_tokens=0):
        if self.error:
            raise self.error
        self.last_user = user
        return model_cls.model_validate(self.plan)


class FakeTavily:
    def __init__(self, error=None, configured=True):
        self.error, self.configured = error, configured

    def search(self, q, max_results=5):
        if self.error:
            raise self.error
        return WebSearch(q, "", [WebResult("Official Goa Tourism", "https://goa-tourism.example/forts", "Fort Aguada opens 9:30am", 0.9)])


def good_plan(start="08:00"):
    def it(s, e, act, cat="attraction", cost=0, src=None):
        return {"start": s, "end": e, "activity": act, "category": cat, "location": "Goa", "details": "d", "reason": "r", "cost_per_person": cost, "transport_note": "taxi ~20 min", "source_idx": src}
    return {"items": [it("08:00", "09:00", "Breakfast", "breakfast", 200), it("09:30", "12:00", "Fort Aguada", "attraction", 50, 0), it("12:30", "13:30", "Lunch", "lunch", 300), it("14:00", "17:00", "Beach", "attraction", 0), it("19:00", "20:00", "Dinner", "dinner", 400, 7)]}


def iagent(store, plan=None, **kw):
    llm_kw = {k: kw.pop(k) for k in ("error", "configured") if k in kw}
    tav = FakeTavily(**{k: v for k, v in kw.items() if k in ("tav_error",)} and {"error": kw["tav_error"]}) if "tav_error" in kw else FakeTavily()
    return ItineraryAgent(FakeLLM(plan or good_plan(), **llm_kw), tav, store)


def test_itinerary_ok_structured_with_estimates_and_sources(store):
    out = iagent(store).plan(ItineraryAgentRequest(destination="Goa", adults=2, daily_activity_budget=3000))
    assert out["status"] == "ok" and out["data_kind"] == "AI_GENERATED" and len(out["items"]) == 5
    fort = out["items"][1]
    assert fort["source_url"] == "https://goa-tourism.example/forts" and fort["cost_kind"] == "ESTIMATE" and fort["hours_verified"] is False and fort["cost_total"] == 100
    assert out["items"][4]["source_url"] is None  # out-of-range source_idx cannot invent a URL
    assert out["costs"]["meals_total"] == 1800 and out["costs"]["activities_total"] == 100 and out["itinerary_id"]


def test_itinerary_llm_failure_returns_no_fake_plan(store):
    out = iagent(store, error=ProviderError("LLM", ErrorKind.NETWORK, "x")).plan(ItineraryAgentRequest(destination="Goa"))
    assert out["status"] == "error" and out["items"] == [] and out["data_kind"] == "UNAVAILABLE"
    out = iagent(store, error=LLMOutputError("bad json")).plan(ItineraryAgentRequest(destination="Goa"))
    assert out["status"] == "error" and out["items"] == []


def test_itinerary_without_groq_key(store):
    out = iagent(store, configured=False).plan(ItineraryAgentRequest(destination="Goa"))
    assert out["status"] == "unavailable" and out["items"] == []


def test_itinerary_tavily_failure_is_partial_and_flagged(store):
    a = ItineraryAgent(FakeLLM(good_plan()), FakeTavily(error=ProviderError("Tavily", ErrorKind.TIMEOUT, "t")), store)
    out = a.plan(ItineraryAgentRequest(destination="Goa"))
    assert out["status"] == "partial" and out["research_status"] == "timeout" and out["sources"] == [] and any("NOT backed by sources" in n for n in out["notes"])


def test_itinerary_infeasible_schedule_rejected(store):
    bad = good_plan()
    bad["items"][1]["start"] = "08:30"  # overlaps breakfast
    out = iagent(store, plan=bad).plan(ItineraryAgentRequest(destination="Goa"))
    assert out["status"] == "error" and "overlap" in out["message"]


def test_check_schedule_rules():
    mk = lambda s, e, c="attraction": LLMItem(start=s, end=e, activity="Stop", category=c)  # noqa: E731
    assert check_schedule([mk("08:00", "09:00", "breakfast"), mk("09:00", "13:00", "lunch")], 480, 1200) == []
    assert any("outside" in p for p in check_schedule([mk("07:00", "09:00")], 480, 1200))
    assert any("end must be after" in p for p in check_schedule([mk("10:00", "10:00")], 480, 1200))
    assert any("lunch" in p for p in check_schedule([mk("08:00", "09:00")], 480, 1200))


def test_arrival_time_delays_start(store):
    f = mk_flight("late", 4000, arrive_hour=14)
    start, note = earliest_start(480, f, f.arrives_at.date())
    assert start == 16 * 60 + 30 and "assumption" in note
    assert earliest_start(480, f, f.arrives_at.date().replace(year=f.arrives_at.year + 1))[0] == 480  # other day: unchanged
    assert earliest_start(480, None, f.arrives_at.date()) == (480, None)


def test_itinerary_uses_selected_flight_and_hotel(store):
    f, h = mk_flight("fx", 4000, arrive_hour=10), mk_hotel("hx", "Sea View")
    store.put("flight", f, key="fx"), store.put("hotel", h, key="hx")
    plan = {"items": [{"start": "13:00", "end": "14:00", "activity": "Lunch", "category": "lunch"}, {"start": "14:30", "end": "17:00", "activity": "Beach", "category": "attraction"}, {"start": "18:00", "end": "19:30", "activity": "Dinner", "category": "dinner"}]}
    a = iagent(store, plan=plan)
    out = a.plan(ItineraryAgentRequest(destination="Goa", flight_id="fx", hotel_id="hx"))
    assert out["status"] == "ok" and out["uses"] == {"flight": "fx", "hotel": "hx"} and out["window"]["start"] == "12:30"
    sent = json.loads(a.llm.last_user)
    assert sent["hotel"]["name"] == "Sea View" and sent["arrival_airport"] == "GOI"


def test_itinerary_rejects_unknown_selection_ids(store):
    assert iagent(store).plan(ItineraryAgentRequest(destination="Goa", flight_id="forged"))["status"] == "error"
    assert iagent(store).plan(ItineraryAgentRequest(destination="Goa", hotel_id="forged"))["status"] == "error"


def test_itinerary_too_late_arrival_returns_no_time(store):
    f = mk_flight("night", 4000, hour=17, arrive_hour=19)
    store.put("flight", f, key="night")
    out = iagent(store).plan(ItineraryAgentRequest(destination="Goa", flight_id="night"))
    assert out["status"] == "no_time"


# ---------------------------------------------------------------- orchestration
def make_orch(locations, store, flights, hotels, llm=None):
    fa, ha = FlightAgent(locations, FakeFlights(options=flights), store), HotelAgent(locations, FakeHotels(options=hotels), store)
    return Orchestrator(fa, ha, ItineraryAgent(llm or FakeLLM(good_plan()), FakeTavily(), store), store)


def test_flight_to_hotel_to_itinerary_to_summary(locations, store):
    o = make_orch(locations, store, [mk_flight("F1", 4000, ("6E1",), hour=2, arrive_hour=5)], [mk_hotel("H1", "Sea View", 3000, nights=3)])
    fl = o.flights(freq())
    fid = fl["options"][0]["id"]
    hotels = o.hotels(HotelAgentRequest(destination="Goa", check_out=future(33), flight_id=fid))  # check-in derived from the flight's real arrival date
    assert hotels["check_in"] == future().isoformat() and hotels["status"] == "ok"
    hid = hotels["options"][0]["id"]
    plan = o.itinerary(ItineraryAgentRequest(destination="Goa", adults=2, flight_id=fid, hotel_id=hid))
    assert plan["status"] == "ok" and plan["uses"] == {"flight": "F1", "hotel": "H1"}
    s = o.summary(TripSummaryRequest(flight_id=fid, hotel_id=hid, itinerary_id=plan["itinerary_id"], adults=2, overall_budget=30000))
    assert s["status"] == "ok" and not s["is_exact"]
    labels = {l["label"]: l for l in s["lines"]}
    assert labels["Flight"]["amount"] == 8000 and labels["Hotel"]["amount"] == 9000 and labels["Flight"]["kind"] == "LIVE_API"
    assert s["total"] == 8000 + 9000 + plan["costs"]["activities_total"] + plan["costs"]["meals_total"]


def test_hotels_with_unknown_flight_id_fails_cleanly(locations, store):
    o = make_orch(locations, store, [], [])
    out = o.hotels(HotelAgentRequest(destination="Goa", check_out=future(33), flight_id="nope"))
    assert out["status"] == "error"


def test_summary_unknown_ids(locations, store):
    assert make_orch(locations, store, [], []).summary(TripSummaryRequest(flight_id="zzz"))["status"] == "error"


def test_agents_run_independently(locations, store):
    o = make_orch(locations, store, [], [])
    assert o.itinerary(ItineraryAgentRequest(destination="Goa"))["status"] == "ok"  # no flight/hotel needed


def test_missing_fare_provider_returns_tavily_links_not_fake_flights(locations, store):
    from types import SimpleNamespace
    class TavilyStub:
        configured = True
        def search(self, query, max_results=5):
            assert 'Chennai' in query and 'Goa' in query
            assert max_results == 5
            return SimpleNamespace(results=[
                SimpleNamespace(title=f'Flight search {i}', url=f'https://example{i}.com/flights', content='Search flights and fares')
                for i in range(1, 7)
            ])
    agent = FlightAgent(locations, FakeFlights(configured=False), store, TavilyStub())
    out = agent.search(freq())
    assert out['status'] == 'unavailable'
    assert out['options'] == []
    assert len(out['flight_search_links']) == 5
    assert out['flight_search_links'][0]['url'] == 'https://example1.com/flights'
    assert all(x['kind'] == 'external_search_page_not_confirmed_offer' for x in out['flight_search_links'])
