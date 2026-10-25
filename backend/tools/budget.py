"""Currency-aware budget filtering, de-duplication and totals. Pure functions: no I/O, fully unit-tested.

Rules: only real provider numbers are used; nothing is added (no invented taxes/fees);
offers in a different currency than the request are EXCLUDED, never silently converted.
"""
from __future__ import annotations

from typing import Optional

from ..models.travel_models import FlightOption, HotelOption

FLIGHT_SCOPES = ("per_passenger", "total")
HOTEL_SCOPES = ("per_night", "total_stay")


def flight_total_cost(o: FlightOption, passengers: int) -> Optional[float]:
    """Total for all passengers. Providers here quote per person, so total = per_person x passengers."""
    if o.price_per_person is not None:
        return round(o.price_per_person * passengers, 2)
    return o.total_price


def dedupe_flights(options: list[FlightOption]) -> list[FlightOption]:
    seen: dict[tuple, FlightOption] = {}
    for o in options:
        key = (tuple(o.flight_numbers), o.departs_at.isoformat() if o.departs_at else None)
        cur = seen.get(key)
        if cur is None or (o.price_per_person is not None and (cur.price_per_person is None or o.price_per_person < cur.price_per_person)):
            seen[key] = o
    return list(seen.values())


def dedupe_hotels(options: list[HotelOption]) -> list[HotelOption]:
    seen: dict[str, HotelOption] = {}
    for h in options:
        key = " ".join(h.name.lower().split())
        cur = seen.get(key)
        if cur is None or (h.price_per_night is not None and (cur.price_per_night is None or h.price_per_night < cur.price_per_night)):
            seen[key] = h
    return list(seen.values())


def split_flights_by_budget(options: list[FlightOption], budget: Optional[float], scope: str, passengers: int, currency: str):
    """Returns (within_budget, over_budget, excluded). `excluded` = wrong currency or no price (reasons given)."""
    if scope not in FLIGHT_SCOPES:
        raise ValueError(f"flight budget scope must be one of {FLIGHT_SCOPES}")
    within, over, excluded = [], [], []
    for o in options:
        if o.currency.upper() != currency.upper():
            excluded.append({"id": o.id, "reason": f"currency {o.currency} differs from requested {currency}; not converted"})
            continue
        if o.price_per_person is None and o.total_price is None:
            excluded.append({"id": o.id, "reason": "provider returned no price"})
            continue
        compare = (o.price_per_person if o.price_per_person is not None else (o.total_price or 0) / passengers) if scope == "per_passenger" else flight_total_cost(o, passengers)
        if budget is None or compare <= budget:
            within.append(o)
        else:
            over.append(o)
    return within, over, excluded


def hotel_stay_total(h: HotelOption, nights: int, rooms: int) -> Optional[float]:
    if h.total_price is not None:
        return h.total_price
    if h.price_per_night is not None and nights > 0:
        return round(h.price_per_night * nights * rooms, 2)
    return None


def split_hotels_by_budget(options: list[HotelOption], budget: Optional[float], scope: str, nights: int, rooms: int, currency: str):
    if scope not in HOTEL_SCOPES:
        raise ValueError(f"hotel budget scope must be one of {HOTEL_SCOPES}")
    within, over, excluded = [], [], []
    for h in options:
        if h.currency.upper() != currency.upper():
            excluded.append({"id": h.id, "reason": f"currency {h.currency} differs from requested {currency}; not converted"})
            continue
        total = hotel_stay_total(h, nights, rooms)
        if total is None:
            excluded.append({"id": h.id, "reason": "provider returned no price"})
            continue
        compare = total if scope == "total_stay" else total / max(nights, 1)
        if budget is None or compare <= budget:
            within.append(h)
        else:
            over.append(h)
    return within, over, excluded


def trip_summary(*, currency: str, passengers: int, nights: int, rooms: int, flight: Optional[FlightOption], hotel: Optional[HotelOption],
                 activity_total: Optional[float], meal_total: Optional[float], overall_budget: Optional[float]) -> dict:
    """Combined budget. `exact` is True only when every included part is provider-priced (no estimates)."""
    lines, partial_reasons = [], []
    total = 0.0

    def add(label: str, amount: Optional[float], kind: str, note: str):
        nonlocal total
        lines.append({"label": label, "amount": amount, "kind": kind, "note": note})
        if amount is not None:
            total += amount

    if flight is not None and flight.currency.upper() == currency.upper() and flight_total_cost(flight, passengers) is not None:
        add("Flight", flight_total_cost(flight, passengers), flight.data_kind.value, f"{flight.price_note or 'provider price'}; x{passengers} passenger(s)")
    elif flight is not None:
        add("Flight", None, "UNAVAILABLE", "price missing or in another currency"); partial_reasons.append("flight price unavailable")
    else:
        partial_reasons.append("no flight selected")
    if hotel is not None and hotel.currency.upper() == currency.upper() and hotel_stay_total(hotel, nights, rooms) is not None:
        t = hotel_stay_total(hotel, nights, rooms)
        add("Hotel", t, hotel.data_kind.value, f"{nights} night(s), {rooms} room(s); taxes/fees only if included by provider")
    elif hotel is not None:
        add("Hotel", None, "UNAVAILABLE", "price missing or in another currency"); partial_reasons.append("hotel price unavailable")
    else:
        partial_reasons.append("no hotel selected")
    if activity_total is not None:
        add("Activities & local transport", activity_total, "ESTIMATE", "AI estimate, not a provider quote")
    else:
        partial_reasons.append("no itinerary costs")
    if meal_total is not None:
        add("Meals", meal_total, "ESTIMATE", "AI estimate, not a provider quote")
    exact = bool(lines) and all(l["kind"] not in ("ESTIMATE", "AI_GENERATED", "UNAVAILABLE") for l in lines) and not partial_reasons
    return {"currency": currency, "lines": lines, "total": round(total, 2), "is_partial": bool(partial_reasons), "partial_reasons": partial_reasons,
            "is_exact": exact, "overall_budget": overall_budget,
            "remaining": round(overall_budget - total, 2) if overall_budget is not None else None,
            "over_budget": bool(overall_budget is not None and total > overall_budget)}


def allocate_budget(overall_budget: float, nights: int = 3, passengers: int = 1, rooms: int = 1) -> dict:
    """Intelligently partitions overall budget into category allowances and recommended caps."""
    p = max(passengers, 1)
    n = max(nights, 1)
    r = max(rooms, 1)
    flight_total = overall_budget * 0.40
    hotel_total = overall_budget * 0.35
    food_transport_total = overall_budget * 0.15
    activities_total = overall_budget * 0.10
    
    return {
        "overall_budget": overall_budget,
        "flight_budget_total": round(flight_total, 2),
        "flight_budget_per_passenger": round(flight_total / p, 2),
        "flight_pct": 40,
        "hotel_budget_total": round(hotel_total, 2),
        "hotel_budget_per_night": round(hotel_total / n / r, 2),
        "hotel_pct": 35,
        "food_transport_budget": round(food_transport_total, 2),
        "food_transport_pct": 15,
        "activities_budget": round(activities_total, 2),
        "activities_pct": 10,
        "contingency_pct": 0,
        "passengers": p,
        "nights": n,
        "rooms": r,
    }

