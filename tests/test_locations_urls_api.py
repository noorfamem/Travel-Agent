import pytest
from fastapi.testclient import TestClient

from backend.agents.flight_agent import FlightAgent
from backend.agents.hotel_agent import HotelAgent
from backend.agents.itinerary_agent import ItineraryAgent
from backend.agents.orchestrator import Orchestrator
from backend.agents.store import SelectionStore
from backend.main import app, orch, services
from backend.tools.link_tools import safe_url
from .conftest import FakeFlights, FakeHotels, future, mk_flight, mk_hotel
from .test_agents import FakeLLM, FakeTavily, good_plan

client = TestClient(app)


# ---------------------------------------------------------------- India locations
@pytest.mark.parametrize("text,iata", [("Chennai", "MAA"), ("Madras", "MAA"), ("Bangalore", "BLR"), ("Bengaluru", "BLR"), ("Vizag", "VTZ"), ("Trivandrum", "TRV"), ("Cochin", "COK"), ("Mysore", "MYQ"),
                                        ("Port Blair", "IXZ"), ("Hyderabad", "HYD"), ("Shirdi", "SAG"), ("Tirupati", "TIR"), ("Madurai", "IXM"), ("Kullu", "KUU"), ("MAA", "MAA"), ("Pondicherry", "PNY")])
def test_indian_cities_and_alternate_names_resolve(locations, text, iata):
    assert iata in [a.iata for a in locations.resolve(text).airports]


@pytest.mark.parametrize("text", ["Arni", "Vellore", "Ooty", "Munnar", "Darjeeling"])
def test_towns_without_airport_get_nearest_airports_with_distance(locations, text):
    r = locations.resolve(text)
    assert r.airports and all(a.distance_km and a.distance_km < 250 for a in r.airports)


def test_dataset_is_not_a_tiny_hardcoded_list(locations):
    assert sum(1 for a in locations.airports.values() if a.country_code == "IN") >= 110


def test_airports_are_in_india_timezone(locations):
    assert all(a.tz == "Asia/Kolkata" for a in locations.airports.values() if a.country_code == "IN")


def test_foreign_and_garbage_input_not_guessed(locations):
    assert locations.resolve("Paris").country_code != "IN"
    assert locations.resolve("Zzzzqqxx").airports == []
    assert locations.resolve("").airports == []


# ---------------------------------------------------------------- URL safety
@pytest.mark.parametrize("u", ["https://example.com/a?b=1", "http://example.in/x"])
def test_safe_urls_pass(u):
    assert safe_url(u) == u


@pytest.mark.parametrize("u", ["javascript:alert(1)", "file:///etc/passwd", "ftp://x.com/a", "data:text/html,<b>", "//evil.com", "https://", "not a url", "https://a b.com", "https://x.com/<script>", None, "", "https://localhost", "x" * 3000])
def test_unsafe_urls_rejected(u):
    assert safe_url(u) is None


# ---------------------------------------------------------------- API
def test_health_and_providers_do_not_leak_secrets():
    for path in ("/api/health", "/api/providers"):
        r = client.get(path)
        assert r.status_code == 200 and "gsk_" not in r.text and "tvly-" not in r.text and "api_key" not in r.text.lower().replace("key_present", "")


def test_provider_status_is_honest_about_unconfigured_state():
    p = client.get("/api/providers").json()["providers"]
    assert p["sandbox_flights"]["configured"] is False and p["flight_offers"]["configured"] is False


def test_providers_check_reports_not_configured_without_calling_network():
    j = client.post("/api/providers/check").json()
    assert j["groq"]["state"] == "not_configured" and j["tavily"]["state"] == "not_configured"


def test_unconfigured_flights_is_503_not_200():
    r = client.post("/api/flights/search", json={"origin": "Chennai", "destination": "Goa", "departure_date": future().isoformat()})
    assert r.status_code == 503 and r.json()["status"] == "unavailable" and r.json()["options"] == []


def test_unconfigured_hotels_is_503_with_search_link():
    # With ChainedHotelProvider, Overpass (OSM) is always configured but may timeout in tests.
    # Accept 503 (no provider configured) OR 502 (all providers timed out/failed),
    # both mean no live hotel data — the search_url fallback must always be present.
    r = client.post("/api/hotels/search", json={"destination": "Goa", "check_in": future().isoformat(), "check_out": future(33).isoformat()})
    assert r.status_code in (502, 503) and r.json()["search_url"].startswith("https://www.google.com/travel/hotels")


def test_unconfigured_itinerary_is_503():
    r = client.post("/api/itinerary/generate", json={"destination": "Goa"})
    assert r.status_code == 503 and r.json()["items"] == []


@pytest.mark.parametrize("path,body", [("/api/flights/search", {"origin": "Chennai", "destination": "Goa", "departure_date": "2001-01-01"}),
                                       ("/api/hotels/search", {"destination": "Goa", "check_in": "2099-01-13", "check_out": "2099-01-10"}),
                                       ("/api/flights/search", {"origin": "Chennai", "destination": "Goa", "departure_date": "garbage"}),
                                       ("/api/itinerary/generate", {"destination": "Goa", "start_time": "99:99"})])
def test_bad_input_is_422(path, body):
    assert client.post(path, json=body).status_code == 422


def test_foreign_destination_is_422():
    r = client.post("/api/flights/search", json={"origin": "Chennai", "destination": "Paris", "departure_date": future().isoformat()})
    assert r.status_code == 422


def test_provider_timeout_is_502_not_200(monkeypatch):
    from backend.utils.errors import ErrorKind, ProviderError
    monkeypatch.setattr(orch.flight_agent, "provider", FakeFlights(error=ProviderError("P", ErrorKind.TIMEOUT, "t")))
    r = client.post("/api/flights/search", json={"origin": "Chennai", "destination": "Goa", "departure_date": future().isoformat()})
    assert r.status_code == 502 and r.json()["status"] == "error" and r.json()["data_kind"] == "UNAVAILABLE"


def test_malformed_provider_object_is_502_not_500(monkeypatch):
    class Boom(FakeFlights):
        def search(self, *a, **k):
            return {"not": "a ProviderFlights"}
    monkeypatch.setattr(orch.flight_agent, "provider", Boom())
    r = client.post("/api/flights/search", json={"origin": "Chennai", "destination": "Goa", "departure_date": future().isoformat()})
    assert r.status_code == 502 and r.json()["status"] == "error"


def test_full_workflow_smoke_flight_hotel_itinerary_summary(monkeypatch):
    """Whole flow through real HTTP routes with mocked providers (NOT a live-provider test)."""
    st = SelectionStore()
    monkeypatch.setattr(services.orchestrator, "store", st)
    fa = FlightAgent(services.locations, FakeFlights(options=[mk_flight("W1", 4200, ("AI501",), hour=2, arrive_hour=5), mk_flight("W2", 9900, ("AI502",))]), st)
    ha = HotelAgent(services.locations, FakeHotels(options=[mk_hotel("WH1", "Beach Stay", 3500, nights=3)]), st)
    ia = ItineraryAgent(FakeLLM(good_plan()), FakeTavily(), st)
    new = Orchestrator(fa, ha, ia, st)
    for attr in ("flight_agent", "hotel_agent", "itinerary_agent", "store"):
        monkeypatch.setattr(orch, attr, getattr(new, attr))
    d = future()
    r = client.post("/api/flights/search", json={"origin": "Chennai", "destination": "Goa", "departure_date": d.isoformat(), "budget": 6000, "adults": 2})
    j = r.json()
    assert r.status_code == 200 and j["status"] == "ok" and [o["id"] for o in j["options"]] == ["W1"] and j["over_budget"][0]["id"] == "W2"
    r = client.post("/api/hotels/search", json={"destination": "Goa", "check_out": future(33).isoformat(), "flight_id": "W1", "budget": 4000, "adults": 2})
    h = r.json()
    assert r.status_code == 200 and h["check_in"] == d.isoformat() and h["options"][0]["id"] == "WH1"
    r = client.post("/api/itinerary/generate", json={"destination": "Goa", "flight_id": "W1", "hotel_id": "WH1", "adults": 2})
    it = r.json()
    assert r.status_code == 200 and it["status"] == "ok" and it["uses"] == {"flight": "W1", "hotel": "WH1"}
    r = client.post("/api/trip/summary", json={"flight_id": "W1", "hotel_id": "WH1", "itinerary_id": it["itinerary_id"], "adults": 2, "overall_budget": 40000})
    s = r.json()
    assert r.status_code == 200 and s["total"] > 0 and s["is_exact"] is False and s["remaining"] == 40000 - s["total"]
    forged = client.post("/api/itinerary/generate", json={"destination": "Goa", "flight_id": "FORGED"})
    assert forged.status_code == 502


def test_frontend_is_served_and_has_no_secrets():
    r = client.get("/ui/")
    assert r.status_code == 200 and "TravelAI" in r.text and "gsk_" not in r.text and "tvly-" not in r.text
    assert "innerHTML" not in r.text  # provider/LLM text must only be inserted as text


def test_root_redirects_to_ui_and_ui_is_not_cached():
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/ui/"
    assert client.get("/ui/").headers["cache-control"] == "no-store"
    assert client.get("/api/health").json()["version"] == "3.0.0"
