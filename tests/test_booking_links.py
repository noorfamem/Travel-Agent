import datetime as dt

import pytest

from backend.tools.booking_links import (
    booking_hotels_url,
    cleartrip_flights_url,
    flight_open_links,
    google_flights_open_url,
    google_hotels_open_url,
    kayak_flights_url,
    makemytrip_flights_url,
    skyscanner_flights_url,
)
from backend.tools.link_tools import is_ota_url, safe_url


def test_google_flights_url_is_https_and_encodes_route_and_dates():
    d = dt.date(2026, 11, 15)
    r = dt.date(2026, 11, 22)
    one = google_flights_open_url("MAA", "GOI", d, adults=2, cabin="economy")
    rnd = google_flights_open_url("MAA", "GOI", d, r, adults=2, cabin="business")
    assert one.startswith("https://www.google.com/travel/flights")
    assert "q=" in one and "MAA" in one and "GOI" in one and "2026-11-15" in one
    assert " " not in one and safe_url(one)
    assert "2026-11-22" in rnd and "#flt=" in rnd
    assert safe_url(rnd)


def test_kayak_skyscanner_mmt_cleartrip_are_formed_and_safe():
    d, r = dt.date(2026, 11, 15), dt.date(2026, 11, 22)
    kayak = kayak_flights_url("MAA", "GOI", d, r, adults=2, cabin="economy")
    sky = skyscanner_flights_url("MAA", "GOI", d, r, adults=2, children=1, cabin="economy")
    mmt = makemytrip_flights_url("MAA", "GOI", d, r, adults=2, children=1, cabin="economy")
    ct = cleartrip_flights_url("MAA", "GOI", d, None, adults=1)
    assert kayak == "https://www.kayak.co.in/flights/MAA-GOI/2026-11-15/2026-11-22/2adults"
    assert sky.startswith("https://www.skyscanner.co.in/transport/flights/maa/goi/261115/261122/")
    assert "itinerary=MAA-GOI-15/11/2026_GOI-MAA-22/11/2026" in mmt
    assert "from=MAA" in ct and "to=GOI" in ct
    for u in (kayak, sky, mmt, ct):
        assert safe_url(u)


def test_flight_open_links_every_displayed_link_is_non_empty_https():
    links = flight_open_links(["IndiGo"], ["6E123"], "DEL", "BOM", "Delhi", "Mumbai", dt.date(2026, 12, 1), None, 1, 0, "economy")
    assert links["open_search_url"] and links["google_url"] and links["airline_url"]
    assert links["airline_url"].startswith("https://www.goindigo.in")
    for item in links["links"]:
        assert safe_url(item["url"])
        assert item["url"].startswith("https://")
        assert item["kind"] == "external_search_page_not_confirmed_offer"


def test_hotel_search_urls_encode_place_and_dates():
    cin, cout = dt.date(2026, 11, 15), dt.date(2026, 11, 18)
    g = google_hotels_open_url("Munnar, India", cin, cout, adults=2)
    b = booking_hotels_url("Munnar", cin, cout, adults=2, rooms=1)
    assert g.startswith("https://www.google.com/travel/hotels/")
    assert "checkin=2026-11-15" in g and "checkout=2026-11-18" in g
    assert b.startswith("https://www.booking.com/searchresults.html")
    assert "ss=Munnar" in b and safe_url(g) and safe_url(b)


@pytest.mark.parametrize("u", ["javascript:alert(1)", "file:///etc/passwd", "", None])
def test_unsafe_flight_like_urls_rejected(u):
    assert safe_url(u) is None


def test_ota_detection():
    assert is_ota_url("https://www.booking.com/hotel/in/foo.html")
    assert not is_ota_url("https://www.tajhotels.com/en-in/taj/taj-goa/")
