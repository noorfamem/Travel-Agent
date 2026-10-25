"""Input validation and sanitisation for user-supplied trip details."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Optional

from pydantic import ValidationError

from ..models.travel_models import Cabin, TravelStyle, TripRequest
from .errors import UserInputError

_CTRL = re.compile(r"[\x00-\x1f\x7f<>]")


def clean_text(value: Any, max_len: int = 120) -> str:
    return _CTRL.sub(" ", str(value or "")).strip()[:max_len]


def parse_number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = re.sub(r"[₹$€£,\s]|rs\.?|inr", "", str(value), flags=re.I)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(k|l|lakh|lakhs|lac)?", text, re.I)
    if not m:
        raise ValueError("not a number")
    n, unit = float(m.group(1)), (m.group(2) or "").lower()
    return n * (1000 if unit == "k" else 100000 if unit else 1)


def parse_date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("invalid date")


def build_trip_request(payload: dict, today: Optional[dt.date] = None) -> TripRequest:
    """Validate the form payload. Raises UserInputError(fields) with friendly per-field messages."""
    today = today or dt.date.today()
    errors: dict[str, str] = {}
    p = payload or {}
    origin, dest = clean_text(p.get("origin")), clean_text(p.get("destination"))
    if len(origin) < 2:
        errors["origin"] = "Please enter where you are travelling from."
    if len(dest) < 2:
        errors["destination"] = "Please enter your destination."
    dep = ret = None
    try:
        dep = parse_date(p.get("departure_date"))
    except ValueError:
        errors["departure_date"] = "Enter a valid departure date."
    try:
        ret = parse_date(p.get("return_date"))
    except ValueError:
        errors["return_date"] = "Enter a valid return date."
    if dep and dep < today:
        errors["departure_date"] = "Departure date cannot be in the past."
    if dep and ret and ret <= dep and "return_date" not in errors:
        errors["return_date"] = "Return date must be after the departure date."
    if dep and ret and (ret - dep).days > 60:
        errors["return_date"] = "Trips longer than 60 days are not supported."
    if dep and (dep - today).days > 330:
        errors["departure_date"] = "Flights can only be searched up to about 11 months ahead."

    def num(key: str, label: str) -> Optional[float]:
        try:
            n = parse_number(p.get(key))
        except ValueError:
            errors[key] = f"{label} must be a number."
            return None
        if n is not None and n <= 0:
            errors[key] = f"{label} must be greater than zero."
        if n is not None and n > 1e9:
            errors[key] = f"{label} looks too large."
        return n

    budget, fbud, hbud = num("budget_total", "Total budget"), num("flight_budget", "Flight budget"), num("hotel_budget", "Hotel budget")
    if budget and fbud and hbud and fbud + hbud > budget * 1.001:
        errors["budget_total"] = "Flight budget plus hotel budget is more than your total budget."

    def integer(key: str, default: int, lo: int, hi: int, label: str) -> int:
        try:
            raw = p.get(key)
            n = int(float(raw)) if raw not in (None, "") else default
        except (TypeError, ValueError):
            errors[key] = f"{label} must be a whole number."
            return default
        if not lo <= n <= hi:
            errors[key] = f"{label} must be between {lo} and {hi}."
        return n

    adults, children, rooms = integer("adults", 1, 1, 9, "Adults"), integer("children", 0, 0, 8, "Children"), integer("rooms", 1, 1, 6, "Rooms")
    if adults + children > 9:
        errors["adults"] = "At most 9 travellers per booking."
    if rooms > adults + children:
        errors["rooms"] = "Rooms cannot exceed the number of travellers."
    ages: list[int] = []
    for a in p.get("children_ages") or []:
        try:
            age = int(a)
            if not 0 <= age <= 17:
                raise ValueError
            ages.append(age)
        except (TypeError, ValueError):
            errors["children_ages"] = "Children's ages must be between 0 and 17."
    ages = ages[:children]

    def enum(key: str, cls, default):
        raw = str(p.get(key) or default.value).strip().lower().replace(" ", "_")
        try:
            return cls(raw)
        except ValueError:
            errors[key] = f"Choose one of: {', '.join(e.value for e in cls)}."
            return default

    cabin, style = enum("cabin", Cabin, Cabin.ECONOMY), enum("travel_style", TravelStyle, TravelStyle.COMFORTABLE)
    stars = sorted({int(s) for s in (p.get("hotel_stars") or []) if str(s).isdigit() and 2 <= int(s) <= 5})
    custom = clean_text(p.get("custom_interest"), 40).lower()
    interests = [clean_text(i, 40).lower() for i in (p.get("interests") or []) if clean_text(i, 40)][:10]
    if custom and custom not in interests:
        interests.append(custom)
    currency = clean_text(p.get("currency") or "INR", 3).upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        errors["currency"] = "Currency must be a 3-letter code such as INR."
    oa = clean_text(p.get("origin_airport"), 3).upper() or None
    if oa and not re.fullmatch(r"[A-Z]{3}", oa):
        errors["origin_airport"] = "Airport must be a 3-letter IATA code."
    if errors:
        raise UserInputError(errors)
    try:
        return TripRequest(origin_text=origin, destination_text=dest, origin_airport=oa, departure_date=dep, return_date=ret, adults=adults, children=children,
                           children_ages=ages, rooms=rooms, budget_total=budget, flight_budget=fbud, hotel_budget=hbud, currency=currency, cabin=cabin,
                           hotel_stars=stars, travel_style=style, interests=interests, custom_interest=custom, food_preference=clean_text(p.get("food_preference"), 60),
                           activity_preference=clean_text(p.get("activity_preference"), 60))
    except ValidationError as exc:
        raise UserInputError({".".join(str(x) for x in e["loc"]) or "request": "Invalid value." for e in exc.errors()})
