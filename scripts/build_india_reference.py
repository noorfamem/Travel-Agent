"""Rebuilds backend/data/airports.csv and cities.csv with India-wide coverage. Dev-time only (needs internet); the app itself never calls these sources.

Sources (public data):
  * OurAirports   https://github.com/davidmegginson/ourairports-data   (airports with IATA codes and scheduled service)
  * cities.json   https://github.com/lutangar/cities.json              (GeoNames-derived city list; admin1 is a numeric code)
Existing rows are kept untouched; only missing airports/cities are appended.
Usage:  python scripts/build_india_reference.py <ourairports_airports.csv> <cities.json>
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "backend" / "data"
ISO_STATE = {"AP": "Andhra Pradesh", "AR": "Arunachal Pradesh", "AS": "Assam", "BR": "Bihar", "CG": "Chhattisgarh", "GA": "Goa", "GJ": "Gujarat", "HR": "Haryana", "HP": "Himachal Pradesh",
             "JH": "Jharkhand", "KA": "Karnataka", "KL": "Kerala", "MP": "Madhya Pradesh", "MH": "Maharashtra", "MN": "Manipur", "ML": "Meghalaya", "MZ": "Mizoram", "NL": "Nagaland",
             "OD": "Odisha", "PB": "Punjab", "RJ": "Rajasthan", "SK": "Sikkim", "TN": "Tamil Nadu", "TG": "Telangana", "TS": "Telangana", "TR": "Tripura", "UP": "Uttar Pradesh", "UK": "Uttarakhand",
             "UT": "Uttarakhand", "WB": "West Bengal", "AN": "Andaman and Nicobar Islands", "CH": "Chandigarh", "DH": "Dadra and Nagar Haveli and Daman and Diu", "DD": "Dadra and Nagar Haveli and Daman and Diu",
             "DN": "Dadra and Nagar Haveli and Daman and Diu", "DL": "Delhi", "JK": "Jammu and Kashmir", "LA": "Ladakh", "LD": "Lakshadweep", "PY": "Puducherry"}
PRI = {"large_airport": 2, "medium_airport": 4, "small_airport": 6}
ALIASES = {"Thiruvananthapuram": "trivandrum", "Kochi": "cochin", "Kozhikode": "calicut", "Chennai": "madras", "Pune": "poona", "Vadodara": "baroda", "Varanasi": "benares|banaras",
           "Mysuru": "mysore", "Mangaluru": "mangalore", "Puducherry": "pondicherry", "Guwahati": "gauhati", "Bhubaneswar": "bhubaneshwar", "Visakhapatnam": "vizag|vishakhapatnam",
           "Prayagraj": "allahabad", "Kolkata": "calcutta", "Mumbai": "bombay", "Bengaluru": "bangalore|bengalore", "Gurugram": "gurgaon", "Shimla": "simla", "Udaipur": "udaipur rajasthan"}


def hav(a, b, c, d):
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def main(ap_csv: str, cities_json: str) -> None:
    a_path, c_path = DATA / "airports.csv", DATA / "cities.csv"
    ex_air = list(csv.DictReader(open(a_path, encoding="utf-8")))
    have = {r["iata"] for r in ex_air}
    added = 0
    for r in csv.DictReader(open(ap_csv, encoding="utf-8")):
        if r["iso_country"] != "IN" or not r["iata_code"] or r["iata_code"] in have or r["scheduled_service"] != "yes" or r["type"] not in PRI:
            continue
        st = ISO_STATE.get(r["iso_region"].split("-")[-1], "")
        ex_air.append({"iata": r["iata_code"], "name": r["name"], "city": r["municipality"] or r["name"], "state": st, "country": "IN", "lat": f"{float(r['latitude_deg']):.4f}",
                       "lon": f"{float(r['longitude_deg']):.4f}", "pri": str(PRI[r["type"]]), "tz": "Asia/Kolkata"})
        have.add(r["iata_code"]); added += 1
    # fill blank state for Indian airports from the nearest airport that has one
    known = [r for r in ex_air if r["country"] == "IN" and r["state"]]
    for r in ex_air:
        if r["country"] == "IN" and not r["state"] and known:
            near = min(known, key=lambda k: hav(float(r["lat"]), float(r["lon"]), float(k["lat"]), float(k["lon"])))
            r["state"] = near["state"]
    with open(a_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["iata", "name", "city", "state", "country", "lat", "lon", "pri", "tz"]); w.writeheader(); w.writerows(ex_air)
    # ---- cities
    ex_c = list(csv.DictReader(open(c_path, encoding="utf-8")))
    seen = {(norm(r["name"]), r["country"]) for r in ex_c}
    raw = [x for x in json.load(open(cities_json, encoding="utf-8")) if x["country"] == "IN"]
    anchors = [(float(r["lat"]), float(r["lon"]), r["state"]) for r in ex_c if r["country"] == "IN" and r["state"]] + [(float(r["lat"]), float(r["lon"]), r["state"]) for r in ex_air if r["country"] == "IN" and r["state"]]
    votes: dict[str, Counter] = {}
    for x in raw:  # state per GeoNames admin1 code = majority vote of the nearest anchor of every city in that code (consistent per code)
        lat, lon = float(x["lat"]), float(x["lng"])
        near = min(anchors, key=lambda a: hav(lat, lon, a[0], a[1]))
        votes.setdefault(x["admin1"], Counter())[near[2]] += 1
    code_state = {c: v.most_common(1)[0][0] for c, v in votes.items()}
    n_c = 0
    for x in raw:
        key = (norm(x["name"]), "IN")
        if key in seen or not x["name"].isascii():
            continue
        seen.add(key)
        ex_c.append({"name": x["name"], "country": "IN", "state": code_state.get(x["admin1"], ""), "lat": f"{float(x['lat']):.4f}", "lon": f"{float(x['lng']):.4f}", "pop_k": "0", "aliases": ""})
        n_c += 1
    for r in ex_c:
        extra = ALIASES.get(r["name"])
        if extra and r["country"] == "IN":
            cur = set(filter(None, r["aliases"].split("|")))
            r["aliases"] = "|".join(sorted(cur | set(extra.split("|"))))
    with open(c_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "country", "state", "lat", "lon", "pop_k", "aliases"]); w.writeheader(); w.writerows(ex_c)
    print(f"airports added: {added}; cities added: {n_c}; admin1 codes mapped: {len(code_state)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
