"""One-off provider probe. Never prints secrets."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import certifi
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
VERIFY = certifi.where()


def mask(url: str) -> str:
    return url.split("api_key=")[0] + ("api_key=***" if "api_key=" in url else "")


def main() -> None:
    key = os.getenv("SERPAPI_API_KEY") or ""
    av = os.getenv("AVIATIONSTACK_API_KEY") or ""
    print("serpapi", bool(key), "aviationstack", bool(av), "certifi", bool(VERIFY))
    dep = (dt.date.today() + dt.timedelta(days=14)).isoformat()
    try:
        r = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_flights",
                "departure_id": "MAA",
                "arrival_id": "GOI",
                "outbound_date": dep,
                "type": 2,
                "travel_class": 1,
                "adults": 1,
                "currency": "INR",
                "hl": "en",
                "gl": "in",
                "api_key": key,
            },
            timeout=40,
            verify=VERIFY,
        )
        j = r.json()
        print("serp_http", r.status_code, "error", str(j.get("error"))[:200] if j.get("error") else None)
        print("serp_status", (j.get("search_metadata") or {}).get("status"))
        print("best", len(j.get("best_flights") or []), "other", len(j.get("other_flights") or []))
        items = (j.get("best_flights") or [])[:1] + (j.get("other_flights") or [])[:1]
        if items:
            it = items[0]
            fl = (it.get("flights") or [{}])[0]
            print("price", it.get("price"), "dur", it.get("total_duration"))
            print("dep", fl.get("departure_airport"))
            print("arr", fl.get("arrival_airport"))
            print("airline", fl.get("airline"), "num", fl.get("flight_number"))
    except Exception as exc:
        print("serp_fail", type(exc).__name__, str(exc).split("api_key=")[0][:300])

    try:
        r = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_hotels",
                "q": "hotels in Goa, India",
                "check_in_date": dep,
                "check_out_date": (dt.date.today() + dt.timedelta(days=17)).isoformat(),
                "adults": 1,
                "currency": "INR",
                "hl": "en",
                "gl": "in",
                "api_key": key,
            },
            timeout=40,
            verify=VERIFY,
        )
        j = r.json()
        props = j.get("properties") or []
        print("hotels_http", r.status_code, "error", str(j.get("error"))[:200] if j.get("error") else None, "properties", len(props))
        if props:
            p = props[0]
            print("hotel0", p.get("name"), "type", p.get("type"), "rate", p.get("rate_per_night"))
    except Exception as exc:
        print("hotels_fail", type(exc).__name__, str(exc).split("api_key=")[0][:300])

    try:
        r = requests.get(
            "http://api.aviationstack.com/v1/flights",
            params={"access_key": av, "dep_iata": "MAA", "arr_iata": "GOI", "limit": 5},
            timeout=40,
        )
        j = r.json()
        err = j.get("error")
        print("avs_http", r.status_code, "error", (err or {}).get("code") if isinstance(err, dict) else err, "n", len(j.get("data") or []))
        if j.get("data"):
            f = j["data"][0]
            print("avs0", (f.get("airline") or {}).get("name"), (f.get("flight") or {}).get("iata"), (f.get("departure") or {}).get("scheduled"))
    except Exception as exc:
        print("avs_fail", type(exc).__name__, str(exc)[:200])

    q = '[out:json][timeout:25];(nwr["tourism"="hotel"](around:6000,15.4909,73.8278););out center tags 8;'
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data={"data": q}, timeout=40, verify=VERIFY)
        els = (r.json().get("elements") or [])
        print("overpass_http", r.status_code, "elements", len(els))
        for e in els[:3]:
            tags = e.get("tags") or {}
            print("osm", tags.get("name"), tags.get("stars"), tags.get("website"))
    except Exception as exc:
        print("overpass_fail", type(exc).__name__, str(exc)[:200])


if __name__ == "__main__":
    main()
