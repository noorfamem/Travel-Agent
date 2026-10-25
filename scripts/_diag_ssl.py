import datetime as dt
import os
from pathlib import Path

import truststore

truststore.inject_into_ssl()
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
key = os.getenv("SERPAPI_API_KEY")
dep = (dt.date.today() + dt.timedelta(days=14)).isoformat()
cout = (dt.date.today() + dt.timedelta(days=17)).isoformat()
try:
    r = requests.get(
        "https://serpapi.com/search.json",
        params={
            "engine": "google_flights",
            "departure_id": "MAA",
            "arrival_id": "GOI",
            "outbound_date": dep,
            "type": 2,
            "currency": "INR",
            "gl": "in",
            "hl": "en",
            "api_key": key,
        },
        timeout=45,
    )
    j = r.json()
    print("serp", r.status_code, "err", str(j.get("error"))[:180] if j.get("error") else None)
    print("best", len(j.get("best_flights") or []), "other", len(j.get("other_flights") or []))
    items = (j.get("best_flights") or []) or (j.get("other_flights") or [])
    if items:
        it = items[0]
        fl = (it.get("flights") or [{}])[0]
        print("price", it.get("price"), "dur", it.get("total_duration"))
        print("dep", fl.get("departure_airport"))
        print("arr", fl.get("arrival_airport"))
        print("airline", fl.get("airline"), "num", fl.get("flight_number"))
        print("item_keys", list(it.keys())[:25])
except Exception as e:
    print("serp_fail", type(e).__name__, str(e).split("api_key=")[0][:250])

try:
    r = requests.get(
        "https://serpapi.com/search.json",
        params={
            "engine": "google_hotels",
            "q": "hotels in Goa, India",
            "check_in_date": dep,
            "check_out_date": cout,
            "adults": 1,
            "currency": "INR",
            "gl": "in",
            "hl": "en",
            "api_key": key,
        },
        timeout=45,
    )
    j = r.json()
    props = j.get("properties") or []
    print("hotels", r.status_code, "err", str(j.get("error"))[:180] if j.get("error") else None, "n", len(props))
    if props:
        p = props[0]
        print("hotel0", p.get("name"), p.get("type"), p.get("rate_per_night"), p.get("overall_rating"), bool(p.get("link")))
except Exception as e:
    print("hotels_fail", type(e).__name__, str(e).split("api_key=")[0][:250])

try:
    r = requests.get(
        "https://overpass-api.de/api/interpreter",
        params={"data": '[out:json][timeout:20];(nwr["tourism"="hotel"](around:5000,15.4909,73.8278););out center tags 5;'},
        timeout=45,
    )
    print("osm", r.status_code, "n", len((r.json() or {}).get("elements") or []))
except Exception as e:
    print("osm_fail", type(e).__name__, str(e)[:200])
