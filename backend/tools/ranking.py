"""Transparent ranking: every option gets a 0-100 score and human-readable reasons (documented in docs/DATA_SOURCES_AND_LIMITATIONS.md)."""
from __future__ import annotations

import math
from typing import Optional

from ..models.travel_models import FlightOption, HotelOption, TravelStyle
from .geo_tools import haversine_km

FLIGHT_WEIGHTS = {
    TravelStyle.BUDGET: dict(price=0.50, duration=0.15, stops=0.15, layover=0.05, time=0.15),
    TravelStyle.COMFORTABLE: dict(price=0.30, duration=0.25, stops=0.20, layover=0.10, time=0.15),
    TravelStyle.LUXURY: dict(price=0.10, duration=0.30, stops=0.25, layover=0.10, time=0.25),
}
HOTEL_WEIGHTS = {
    TravelStyle.BUDGET: dict(price=0.40, rating=0.25, reviews=0.10, stars=0.00, location=0.15, amenities=0.10),
    TravelStyle.COMFORTABLE: dict(price=0.25, rating=0.25, reviews=0.10, stars=0.15, location=0.15, amenities=0.10),
    TravelStyle.LUXURY: dict(price=0.10, rating=0.25, reviews=0.10, stars=0.30, location=0.15, amenities=0.10),
}


def _low_better(v: Optional[float], lo: float, hi: float) -> float:
    if v is None:
        return 0.5
    return 1.0 if hi <= lo else max(0.0, min(1.0, 1 - (v - lo) / (hi - lo)))


def _hour_penalty(hour: int) -> float:
    if 0 <= hour < 5:
        return 0.6
    if 5 <= hour < 7 or hour >= 23:
        return 0.25
    return 0.0


def _layover_score(f: FlightOption) -> float:
    if not f.layovers:
        return 1.0
    pen = 0.0
    for lv in f.layovers:
        m = lv.duration_min or 120
        pen += 0.35 if m < 50 else 0.4 if m > 360 else 0.2 if m > 240 else 0.0
        pen += 0.3 if lv.overnight else 0.0
    return max(0.0, 1 - pen / len(f.layovers))


def _time_score(f: FlightOption) -> float:
    pen = (_hour_penalty(f.departs_at.hour) if f.departs_at else 0) + (_hour_penalty(f.arrives_at.hour) if f.arrives_at else 0)
    return max(0.0, 1 - pen / 2)


def rank_flights(options: list[FlightOption], style: TravelStyle, flight_budget_total: Optional[float], travellers: int, top_n: int = 10) -> list[FlightOption]:
    """Value-based ranking (not 'cheapest N'): price, duration, stops, layover quality and departure/arrival comfort."""
    if not options:
        return []
    w = FLIGHT_WEIGHTS[style]
    prices = [o.price_per_person for o in options if o.price_per_person]
    durs = [o.duration_min for o in options if o.duration_min]
    pmin, pmax = (min(prices), max(prices)) if prices else (0, 0)
    dmin, dmax = (min(durs), max(durs)) if durs else (0, 0)
    for o in options:
        parts = dict(price=_low_better(o.price_per_person, pmin, pmax), duration=_low_better(o.duration_min, dmin, dmax), stops={0: 1.0, 1: 0.6, 2: 0.25}.get(o.stops, 0.0),
                     layover=_layover_score(o), time=_time_score(o))
        score = sum(w[k] * parts[k] for k in w)
        o.reasons = ["Non-stop" if o.stops == 0 else f"{o.stops} stop{'s' if o.stops > 1 else ''}"]
        if prices and o.price_per_person and o.price_per_person <= pmin * 1.02:
            o.reasons.append("Lowest fare in results")
        if durs and o.duration_min and o.duration_min <= dmin * 1.05:
            o.reasons.append("Among the quickest")
        if any((lv.duration_min or 0) > 360 for lv in o.layovers):
            o.reasons.append("Long layover")
        if any(lv.overnight for lv in o.layovers):
            o.reasons.append("Overnight layover")
        if o.departs_at and _hour_penalty(o.departs_at.hour) >= 0.6:
            o.reasons.append("Very early/late departure")
        if flight_budget_total and o.price_per_person and o.price_per_person * travellers > flight_budget_total:
            score -= 0.15
            o.reasons.append("Above your flight budget")
        o.score = round(max(0.0, score) * 100, 1)
        o.labels = []
    ranked = sorted(options, key=lambda o: -o.score)
    chosen = [ranked[0]]
    ranked[0].labels.append("Best overall")

    def pick(label: str, key) -> None:
        pool = [o for o in options if o not in chosen]
        if pool and len(chosen) < top_n:
            best = min(pool, key=key)
            best.labels.append(label)
            chosen.append(best)

    if prices:
        pick("Cheapest", lambda o: (o.price_per_person is None, o.price_per_person or 0, -o.score))
    if durs:
        pick("Fastest", lambda o: (o.duration_min is None, o.duration_min or 0, -o.score))
    pick("Fewest stops", lambda o: (o.stops, -o.score))
    for o in ranked:
        if len(chosen) < top_n and o not in chosen:
            chosen.append(o)
    return sorted(chosen, key=lambda o: -o.score)


def rank_hotels(options: list[HotelOption], style: TravelStyle, hotel_budget_total: Optional[float], nights: int, rooms: int, preferred_stars: list[int],
                centre: Optional[tuple[float, float]], top_n: int = 5) -> list[HotelOption]:
    if not options:
        return []
    w = HOTEL_WEIGHTS[style]
    prices = [o.price_per_night for o in options if o.price_per_night]
    pmin, pmax = (min(prices), max(prices)) if prices else (0, 0)
    target = hotel_budget_total / max(nights, 1) / max(rooms, 1) if hotel_budget_total else None
    for o in options:
        price_s = _low_better(o.price_per_night, pmin, pmax)
        if target and o.price_per_night:
            price_s = 1.0 if o.price_per_night <= target else max(0.0, 1 - (o.price_per_night - target) / target)
        rating_s = 0.4 if o.guest_rating is None else max(0.0, min(1.0, (o.guest_rating - 3.0) / 2.0))
        reviews_s = 0.2 if not o.reviews else min(1.0, math.log10(o.reviews + 1) / 4)
        if o.star_rating is None:
            stars_s = 0.4
        elif preferred_stars:
            stars_s = 1.0 if o.star_rating in preferred_stars else max(0.0, 1 - 0.4 * min(abs(o.star_rating - s) for s in preferred_stars))
        else:
            stars_s = o.star_rating / 5
        loc_s, dist = 0.5, None
        if centre and o.lat is not None and o.lon is not None:
            dist = haversine_km(centre[0], centre[1], o.lat, o.lon)
            loc_s = max(0.0, 1 - min(dist, 15) / 15)
        score = (w["price"] * price_s + w["rating"] * rating_s + w["reviews"] * reviews_s + w["stars"] * stars_s + w["location"] * loc_s + w["amenities"] * min(1.0, len(o.amenities) / 12))
        o.reasons = []
        if o.guest_rating and o.guest_rating >= 4.3:
            o.reasons.append(f"Guest rating {o.guest_rating:.1f}/5")
        if dist is not None:
            o.reasons.append(f"{dist:.1f} km from city centre")
        if target and o.price_per_night and o.price_per_night > target:
            o.reasons.append("Above your suggested hotel budget per night")
            score -= 0.1
        if o.cancellation:
            o.reasons.append("Cancellation info available")
        o.score = round(max(0.0, score) * 100, 1)
        o.labels = []
    ranked = sorted(options, key=lambda o: -o.score)
    chosen = [ranked[0]]
    ranked[0].labels.append("Best overall")

    def pick(label: str, key) -> None:
        pool = [o for o in options if o not in chosen]
        if pool and len(chosen) < top_n:
            best = min(pool, key=key)
            best.labels.append(label)
            chosen.append(best)

    if prices:
        pick("Lowest price", lambda o: (o.price_per_night is None, o.price_per_night or 0))
    pick("Top rated", lambda o: (-(o.guest_rating or 0), -(o.reviews or 0)))
    for o in ranked:
        if len(chosen) < top_n and o not in chosen:
            chosen.append(o)
    return sorted(chosen, key=lambda o: -o.score)
