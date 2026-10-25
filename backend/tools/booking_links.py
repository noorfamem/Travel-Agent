"""Working booking/search URLs built from real IATA codes, dates and hotel names.

These are public search pages (airline sites, Google Flights/Hotels, Kayak, Skyscanner,
MakeMyTrip, Booking.com). They are not confirmed inventory; they must open in a browser.
Provider-specific deep links are included only for documented URL formats.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional
from urllib.parse import quote, quote_plus, urlencode

from .link_tools import safe_url

# Official airline sites keyed by IATA code and common name.
_AIRLINE_SITES: dict[str, str] = {
    "6e": "https://www.goindigo.in/",
    "indigo": "https://www.goindigo.in/",
    "go indigo": "https://www.goindigo.in/",
    "ai": "https://www.airindia.com/",
    "air india": "https://www.airindia.com/",
    "ix": "https://www.airindiaexpress.com/",
    "air india express": "https://www.airindiaexpress.com/",
    "sg": "https://www.spicejet.com/",
    "spicejet": "https://www.spicejet.com/",
    "spice jet": "https://www.spicejet.com/",
    "qp": "https://www.akasaair.com/",
    "akasa": "https://www.akasaair.com/",
    "akasa air": "https://www.akasaair.com/",
    "9i": "https://www.allianceair.in/",
    "alliance air": "https://www.allianceair.in/",
    "uk": "https://www.airindia.com/",
    "vistara": "https://www.airindia.com/",
    "g8": "https://www.flygofirst.com/",
    "go first": "https://www.flygofirst.com/",
    "i5": "https://www.airasia.co.in/",
    "airasia": "https://www.airasia.co.in/",
    "air asia": "https://www.airasia.co.in/",
    "s5": "https://www.starair.in/",
    "star air": "https://www.starair.in/",
    "6e express": "https://www.goindigo.in/",
}

_CODE_RE = re.compile(r"^([A-Z0-9]{2})\d")
_CABIN_GOOGLE = {"economy": "e", "premium_economy": "p", "premium economy": "p", "business": "b", "first": "f"}
_CABIN_MMT = {"economy": "E", "premium_economy": "W", "premium economy": "W", "business": "B", "first": "F"}
_CABIN_KAYAK = {"economy": None, "premium_economy": "premium", "premium economy": "premium", "business": "business", "first": "first"}
_CABIN_SKY = {"economy": "economy", "premium_economy": "premiumeconomy", "premium economy": "premiumeconomy", "business": "business", "first": "first"}
_CABIN_CLEAR = {"economy": "Economy", "premium_economy": "Premium Economy", "premium economy": "Premium Economy", "business": "Business", "first": "First"}


def _airline_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _cabin_key(cabin: str | None) -> str:
    return (cabin or "economy").strip().lower().replace("-", "_")


def _iata(code: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())[:3]


def _adults(n: int | None) -> int:
    try:
        return max(1, min(9, int(n or 1)))
    except (TypeError, ValueError):
        return 1


def _children(n: int | None) -> int:
    try:
        return max(0, min(8, int(n or 0)))
    except (TypeError, ValueError):
        return 0


def airline_site(airline: str | None, flight_number: str | None = None) -> Optional[str]:
    if airline:
        url = _AIRLINE_SITES.get(_airline_key(airline))
        if url:
            return url
    if flight_number:
        m = _CODE_RE.match(str(flight_number).upper().replace(" ", ""))
        if m:
            return _AIRLINE_SITES.get(m.group(1).lower())
    return None


def google_flights_open_url(
    origin: str,
    dest: str,
    outbound: dt.date,
    ret: Optional[dt.date] = None,
    adults: int = 1,
    cabin: str = "economy",
) -> str:
    """Google Flights search page.

    Google Flights does not honour a bare `q=DEL to BOM DATE` query. The documented
    `#flt=ORIGIN.DEST.YYYY-MM-DD` fragment (with optional return segment) is used
    together with a natural-language `q` so the page still opens a real search.
    """
    o, d = _iata(origin) or (origin or "").strip(), _iata(dest) or (dest or "").strip()
    adults_n = _adults(adults)
    cab = _CABIN_GOOGLE.get(_cabin_key(cabin), "e")
    if ret:
        flt = f"{o}.{d}.{outbound.isoformat()}*{d}.{o}.{ret.isoformat()}"
        q = f"Round trip flights from {o} to {d} on {outbound.isoformat()} through {ret.isoformat()}"
    else:
        flt = f"{o}.{d}.{outbound.isoformat()}"
        q = f"One way flights from {o} to {d} on {outbound.isoformat()}"
    fragment = f"flt={flt};c:INR;e:{adults_n};sd:1;t:{cab}"
    return "https://www.google.com/travel/flights?hl=en-IN&gl=IN&curr=INR&q=" + quote(q) + "#" + fragment


def kayak_flights_url(
    origin: str,
    dest: str,
    outbound: dt.date,
    ret: Optional[dt.date] = None,
    adults: int = 1,
    cabin: str = "economy",
) -> Optional[str]:
    o, d = _iata(origin), _iata(dest)
    if len(o) != 3 or len(d) != 3:
        return None
    parts = [f"{o}-{d}", outbound.isoformat()]
    if ret:
        parts.append(ret.isoformat())
    adults_n = _adults(adults)
    if adults_n > 1:
        parts.append(f"{adults_n}adults")
    kayak_cabin = _CABIN_KAYAK.get(_cabin_key(cabin))
    if kayak_cabin:
        parts.append(kayak_cabin)
    return "https://www.kayak.co.in/flights/" + "/".join(parts)


def skyscanner_flights_url(
    origin: str,
    dest: str,
    outbound: dt.date,
    ret: Optional[dt.date] = None,
    adults: int = 1,
    children: int = 0,
    cabin: str = "economy",
) -> Optional[str]:
    o, d = _iata(origin).lower(), _iata(dest).lower()
    if len(o) != 3 or len(d) != 3:
        return None
    od = outbound.strftime("%y%m%d")
    if ret:
        path = f"{o}/{d}/{od}/{ret.strftime('%y%m%d')}"
        rtn = "1"
    else:
        path = f"{o}/{d}/{od}"
        rtn = "0"
    cabin_q = _CABIN_SKY.get(_cabin_key(cabin), "economy")
    q = urlencode({
        "adultsv2": str(_adults(adults)),
        "cabinclass": cabin_q,
        "childrenv2": str(_children(children)),
        "inboundaltsenabled": "false",
        "outboundaltsenabled": "false",
        "preferdirects": "false",
        "ref": "home",
        "rtn": rtn,
    })
    return f"https://www.skyscanner.co.in/transport/flights/{path}/?{q}"


def makemytrip_flights_url(
    origin_iata: str,
    dest_iata: str,
    outbound: dt.date,
    ret: Optional[dt.date] = None,
    adults: int = 1,
    children: int = 0,
    cabin: str = "economy",
    domestic: bool = True,
) -> Optional[str]:
    o, d = _iata(origin_iata), _iata(dest_iata)
    if len(o) != 3 or len(d) != 3:
        return None
    dep = outbound.strftime("%d/%m/%Y")
    adults_n, child_n = _adults(adults), _children(children)
    if ret:
        itin = f"{o}-{d}-{dep}_{d}-{o}-{ret.strftime('%d/%m/%Y')}"
        trip = "R"
    else:
        itin = f"{o}-{d}-{dep}"
        trip = "O"
    cabin_code = _CABIN_MMT.get(_cabin_key(cabin), "E")
    # Slashes in the itinerary date are required by MakeMyTrip; do not percent-encode them.
    return (
        "https://www.makemytrip.com/flight/search?itinerary="
        + quote(itin, safe="-_/")
        + f"&tripType={trip}&paxType=A-{adults_n}_C-{child_n}_I-0"
        + f"&intl={'false' if domestic else 'true'}&cabinClass={cabin_code}&ccde=IN&lang=eng"
    )


def cleartrip_flights_url(
    origin: str,
    dest: str,
    outbound: dt.date,
    ret: Optional[dt.date] = None,
    adults: int = 1,
    children: int = 0,
    cabin: str = "economy",
) -> Optional[str]:
    o, d = _iata(origin), _iata(dest)
    if len(o) != 3 or len(d) != 3:
        return None
    params = {
        "from": o,
        "to": d,
        "depart_date": outbound.strftime("%d/%m/%Y"),
        "adults": str(_adults(adults)),
        "childs": str(_children(children)),
        "infants": "0",
        "class": _CABIN_CLEAR.get(_cabin_key(cabin), "Economy"),
        "intl": "n",
        "sd": "1",
    }
    if ret:
        params["return_date"] = ret.strftime("%d/%m/%Y")
    return "https://www.cleartrip.com/flights/results?" + urlencode(params)


def google_hotels_open_url(place: str, check_in: dt.date, check_out: dt.date, adults: int = 1) -> str:
    q = (place or "India").strip() or "India"
    return (
        "https://www.google.com/travel/hotels/"
        + quote(q)
        + "?hl=en-IN&gl=IN&curr=INR&q="
        + quote(q)
        + f"&checkin={check_in.isoformat()}&checkout={check_out.isoformat()}&adults={_adults(adults)}"
    )


def booking_hotels_url(place: str, check_in: dt.date, check_out: dt.date, adults: int = 1, rooms: int = 1, children: int = 0) -> str:
    params = {
        "ss": place or "India",
        "checkin": check_in.isoformat(),
        "checkout": check_out.isoformat(),
        "group_adults": str(_adults(adults)),
        "group_children": str(_children(children)),
        "no_rooms": str(max(1, min(6, int(rooms or 1)))),
        "dest_type": "city",
        "lang": "en-gb",
        "selected_currency": "INR",
    }
    return "https://www.booking.com/searchresults.html?" + urlencode(params)


def google_hotel_name_url(name: str, place: str, check_in: dt.date, check_out: dt.date) -> str:
    q = f"{name} hotel {place} India".strip()
    return (
        "https://www.google.com/travel/hotels/"
        + quote(q)
        + "?hl=en-IN&gl=IN&curr=INR&q="
        + quote(q)
        + f"&checkin={check_in.isoformat()}&checkout={check_out.isoformat()}"
    )


def booking_hotel_name_url(name: str, place: str, check_in: dt.date, check_out: dt.date, adults: int = 1, rooms: int = 1) -> str:
    return booking_hotels_url(f"{name} {place}".strip(), check_in, check_out, adults, rooms)


def maps_place_url(name: str, place: str) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(f"{name} {place} India")


def flight_open_links(
    airlines: list[str],
    flight_numbers: list[str],
    origin_iata: str,
    dest_iata: str,
    origin_label: str,
    dest_label: str,
    outbound: dt.date,
    ret: Optional[dt.date],
    adults: int = 1,
    children: int = 0,
    cabin: str = "economy",
    domestic: bool = True,
) -> dict[str, Optional[str] | list]:
    site = None
    for a in airlines or []:
        site = airline_site(a, (flight_numbers or [None])[0])
        if site:
            break
    if not site and flight_numbers:
        site = airline_site(None, flight_numbers[0])
    o = origin_iata or origin_label
    d = dest_iata or dest_label
    google = google_flights_open_url(o, d, outbound, ret, adults, cabin)
    kayak = kayak_flights_url(origin_iata, dest_iata, outbound, ret, adults, cabin)
    sky = skyscanner_flights_url(origin_iata, dest_iata, outbound, ret, adults, children, cabin)
    mmt = makemytrip_flights_url(origin_iata, dest_iata, outbound, ret, adults, children, cabin, domestic)
    clear = cleartrip_flights_url(origin_iata, dest_iata, outbound, ret, adults, children, cabin)
    open_search = kayak or sky or mmt or google
    labelled = []
    for label, url, note in (
        ("Open Flight Search", kayak, "Kayak search with origin, destination, dates and passengers"),
        ("Google Flights", google, "Google Flights search page — not a confirmed fare"),
        ("MakeMyTrip", mmt, "MakeMyTrip search page — complete booking there"),
        ("Skyscanner", sky, "Skyscanner search page — complete booking there"),
        ("Cleartrip", clear, "Cleartrip search page — complete booking there"),
        ("Airline website", site, "Official airline homepage; complete the search on the airline site"),
    ):
        cleaned = safe_url(url)
        if cleaned:
            labelled.append({"label": label, "url": cleaned, "note": note, "kind": "external_search_page_not_confirmed_offer"})
    return {
        "airline_url": safe_url(site),
        "google_url": safe_url(google),
        "mmt_url": safe_url(mmt),
        "kayak_url": safe_url(kayak),
        "skyscanner_url": safe_url(sky),
        "cleartrip_url": safe_url(clear),
        "open_search_url": safe_url(open_search),
        "links": labelled,
    }
