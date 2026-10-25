"""Persistence for the planning workflow. Agents READ their inputs from here (e.g. the Hotel Agent loads the SELECTED FLIGHT from the
database), which is how state is shared between agents."""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from ..models.travel_models import AgentTrace, BudgetReport, FlightOption, HotelOption, ItineraryDay, Place, TripRequest, to_jsonable
from .db import Database


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (dt.datetime, dt.date)) else v


class TripRepository:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ trips
    def create_trip(self, trip_id: str, client: str, req: TripRequest, origin, dest, trip_type) -> None:
        self.db.execute("INSERT INTO travel_sessions(id, client_label) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING", (trip_id, client[:60]))
        self.db.execute(
            "INSERT INTO trips(id, session_id, origin_text, destination_text, origin_label, destination_label, origin_iata, destination_iata, trip_type, departure_date, return_date,"
            " adults, children, rooms, budget_total, currency, cabin, travel_style, status, request) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, request = EXCLUDED.request, updated_at = CURRENT_TIMESTAMP",
            (trip_id, trip_id, req.origin_text, req.destination_text, origin.label, dest.label, origin.airports[0].iata if origin.airports else "",
             dest.airports[0].iata if dest.airports else "", getattr(trip_type, "value", str(trip_type or "")), req.departure_date.isoformat(), req.return_date.isoformat(),
             req.adults, req.children, req.rooms, req.budget_total, req.currency, req.cabin.value, req.travel_style.value, "created", self.db.js(to_jsonable(req))))
        self.save_preferences(trip_id, req)

    def set_status(self, trip_id: str, status: str) -> None:
        self.db.execute("UPDATE trips SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s", (status, trip_id))

    def save_preferences(self, trip_id: str, req: TripRequest) -> None:
        self.db.execute(
            "INSERT INTO travel_preferences(trip_id, interests, custom_interest, food_preference, activity_preference, hotel_stars) VALUES (%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (trip_id) DO UPDATE SET interests = EXCLUDED.interests, custom_interest = EXCLUDED.custom_interest, food_preference = EXCLUDED.food_preference,"
            " activity_preference = EXCLUDED.activity_preference, hotel_stars = EXCLUDED.hotel_stars, updated_at = CURRENT_TIMESTAMP",
            (trip_id, self.db.js(req.interests), req.custom_interest, req.food_preference, req.activity_preference, self.db.js(req.hotel_stars)))

    def get_preferences(self, trip_id: str) -> Optional[dict]:
        r = self.db.fetchone("SELECT interests, custom_interest, food_preference, activity_preference FROM travel_preferences WHERE trip_id = %s", (trip_id,))
        if r:
            r["interests"] = self.db.load_json(r["interests"])
        return r

    def get_trip(self, trip_id: str) -> Optional[dict]:
        return self.db.fetchone("SELECT * FROM trips WHERE id = %s", (trip_id,))

    def list_trips(self, limit: int = 30) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT t.id, t.origin_label, t.destination_label, t.departure_date, t.return_date, t.adults, t.children, t.budget_total, t.currency, t.status, t.created_at,"
            " (f.trip_id IS NOT NULL) AS has_plan FROM trips t LEFT JOIN final_recommendations f ON f.trip_id = t.id ORDER BY t.created_at DESC LIMIT %s", (limit,))
        return [{k: (bool(v) if k == "has_plan" else _iso(v)) for k, v in r.items()} for r in rows]

    # ------------------------------------------------------------------ searches (data-origin proof)
    def save_search_run(self, trip_id: str, kind: str, provider: str, status: str, message: str, meta: dict, options: list) -> None:
        self.db.execute("INSERT INTO search_runs(trip_id, kind, provider, status, message, meta, options) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (trip_id, kind, provider or "none", status, message[:500], self.db.js(to_jsonable(meta)), self.db.js(to_jsonable(options))))

    def latest_search_run(self, trip_id: str, kind: str) -> Optional[dict]:
        r = self.db.fetchone("SELECT * FROM search_runs WHERE trip_id = %s AND kind = %s ORDER BY id DESC LIMIT 1", (trip_id, kind))
        if r:
            r["meta"], r["options"], r["created_at"] = self.db.load_json(r["meta"]), self.db.load_json(r["options"]), _iso(r["created_at"])
        return r

    # ------------------------------------------------------------------ selected flight / hotel
    def save_selected_flight(self, trip_id: str, f: FlightOption, ret: Optional[FlightOption] = None) -> None:
        self.db.execute(
            "INSERT INTO selected_flights(trip_id, flight_id, origin, destination, travel_date, airline, flight_number, departure_at, arrival_at, duration_min, stops,"
            " price_per_person, total_price, currency, booking_url, source, flight, return_flight) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (trip_id) DO UPDATE SET flight_id = EXCLUDED.flight_id, origin = EXCLUDED.origin, destination = EXCLUDED.destination, travel_date = EXCLUDED.travel_date,"
            " airline = EXCLUDED.airline, flight_number = EXCLUDED.flight_number, departure_at = EXCLUDED.departure_at, arrival_at = EXCLUDED.arrival_at,"
            " duration_min = EXCLUDED.duration_min, stops = EXCLUDED.stops, price_per_person = EXCLUDED.price_per_person, total_price = EXCLUDED.total_price,"
            " currency = EXCLUDED.currency, booking_url = EXCLUDED.booking_url, source = EXCLUDED.source, flight = EXCLUDED.flight, return_flight = EXCLUDED.return_flight,"
            " selected_at = CURRENT_TIMESTAMP",
            (trip_id, f.id, f.origin_iata, f.destination_iata, f.departs_at.date().isoformat(), " + ".join(f.airlines), " + ".join(f.flight_numbers), f.departs_at.isoformat(),
             f.arrives_at.isoformat(), f.duration_min, f.stops, f.price_per_person, f.total_price, f.currency, f.booking_url, f.source, self.db.js(to_jsonable(f)),
             self.db.js(to_jsonable(ret)) if ret else None))
        self.set_status(trip_id, "flight_selected")

    def get_selected_flight(self, trip_id: str) -> Optional[tuple[FlightOption, Optional[FlightOption], Optional[float]]]:
        r = self.db.fetchone("SELECT flight, return_flight, price_per_person FROM selected_flights WHERE trip_id = %s", (trip_id,))
        if not r:
            return None
        ret = self.db.load_json(r["return_flight"]) if r["return_flight"] else None
        return (FlightOption.model_validate(self.db.load_json(r["flight"])), FlightOption.model_validate(ret) if ret else None, r["price_per_person"])

    def update_return_flight(self, trip_id: str, ret: Optional[FlightOption], fare: Optional[float]) -> None:
        self.db.execute("UPDATE selected_flights SET return_flight = %s, price_per_person = COALESCE(%s, price_per_person) WHERE trip_id = %s",
                        (self.db.js(to_jsonable(ret)) if ret else None, fare, trip_id))

    def save_selected_hotel(self, trip_id: str, hotel: Optional[HotelOption], destination: str) -> None:
        h = hotel
        self.db.execute(
            "INSERT INTO selected_hotels(trip_id, skipped, hotel_id, name, destination, check_in, check_out, nights, price_per_night, total_price, currency, rating, booking_url, source, hotel)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (trip_id) DO UPDATE SET skipped = EXCLUDED.skipped, hotel_id = EXCLUDED.hotel_id, name = EXCLUDED.name,"
            " destination = EXCLUDED.destination, check_in = EXCLUDED.check_in, check_out = EXCLUDED.check_out, nights = EXCLUDED.nights, price_per_night = EXCLUDED.price_per_night,"
            " total_price = EXCLUDED.total_price, currency = EXCLUDED.currency, rating = EXCLUDED.rating, booking_url = EXCLUDED.booking_url, source = EXCLUDED.source,"
            " hotel = EXCLUDED.hotel, selected_at = CURRENT_TIMESTAMP",
            (trip_id, h is None, h.id if h else None, h.name if h else None, destination, _iso(h.check_in) if h else None, _iso(h.check_out) if h else None, h.nights if h else None,
             h.price_per_night if h else None, h.total_price if h else None, h.currency if h else None, h.guest_rating if h else None, h.booking_url if h else None,
             h.source if h else None, self.db.js(to_jsonable(h)) if h else None))
        self.set_status(trip_id, "hotel_selected")

    def get_selected_hotel(self, trip_id: str) -> tuple[bool, Optional[HotelOption]]:
        r = self.db.fetchone("SELECT skipped, hotel FROM selected_hotels WHERE trip_id = %s", (trip_id,))
        if not r:
            return False, None
        return bool(r["skipped"]), (HotelOption.model_validate(self.db.load_json(r["hotel"])) if r["hotel"] else None)

    # ------------------------------------------------------------------ places / itinerary / budget / final
    def save_places(self, trip_id: str, places: list[Place]) -> None:
        self.db.execute("DELETE FROM trip_places WHERE trip_id = %s", (trip_id,))
        for p in places:
            self.db.execute("INSERT INTO trip_places(trip_id, place_id, name, category, place) VALUES (%s,%s,%s,%s,%s)", (trip_id, p.id, p.name, p.category, self.db.js(to_jsonable(p))))

    def get_places(self, trip_id: str) -> list[Place]:
        return [Place.model_validate(self.db.load_json(r["place"])) for r in self.db.fetchall("SELECT place FROM trip_places WHERE trip_id = %s", (trip_id,))]

    def save_itinerary(self, trip_id: str, days: list[ItineraryDay]) -> None:
        self.db.execute("DELETE FROM itinerary_days WHERE trip_id = %s", (trip_id,))
        for d in days:
            self.db.execute(
                "INSERT INTO itinerary_days(trip_id, day_number, day_date, title, food_estimate, transport_estimate, activities_estimate, daily_total, day) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (trip_id, d.day_number, d.date.isoformat(), d.title, d.food_estimate, d.transport_estimate, d.activities_estimate, d.daily_total, self.db.js(to_jsonable(d))))

    def get_itinerary(self, trip_id: str) -> list[ItineraryDay]:
        return [ItineraryDay.model_validate(self.db.load_json(r["day"])) for r in self.db.fetchall("SELECT day FROM itinerary_days WHERE trip_id = %s ORDER BY day_number", (trip_id,))]

    def save_budget(self, trip_id: str, b: BudgetReport) -> None:
        self.db.execute(
            "INSERT INTO budgets(trip_id, currency, total, budget, remaining, over_budget, report) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (trip_id) DO UPDATE SET currency = EXCLUDED.currency,"
            " total = EXCLUDED.total, budget = EXCLUDED.budget, remaining = EXCLUDED.remaining, over_budget = EXCLUDED.over_budget, report = EXCLUDED.report, created_at = CURRENT_TIMESTAMP",
            (trip_id, b.currency, b.total, b.budget, b.remaining, b.over_budget, self.db.js(to_jsonable(b))))

    def save_final(self, trip_id: str, plan: dict, summary: str) -> None:
        self.db.execute(
            "INSERT INTO final_recommendations(trip_id, summary, plan) VALUES (%s,%s,%s) ON CONFLICT (trip_id) DO UPDATE SET summary = EXCLUDED.summary, plan = EXCLUDED.plan, created_at = CURRENT_TIMESTAMP",
            (trip_id, summary[:2000], self.db.js(plan)))
        self.set_status(trip_id, "completed")

    def get_final(self, trip_id: str) -> Optional[dict]:
        r = self.db.fetchone("SELECT plan FROM final_recommendations WHERE trip_id = %s", (trip_id,))
        return self.db.load_json(r["plan"]) if r else None

    # ------------------------------------------------------------------ observability
    def log_agent_run(self, trip_id: str, t: AgentTrace) -> None:
        self.db.execute("INSERT INTO agent_runs(trip_id, agent, status, duration_ms, input_summary, output_summary, errors) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (trip_id, t.agent, t.status, t.duration_ms, t.input_summary[:300], t.output_summary[:300], self.db.js(t.errors)))

    def agent_runs(self, trip_id: str) -> list[dict]:
        rows = self.db.fetchall("SELECT agent, status, duration_ms, input_summary, output_summary, errors, created_at FROM agent_runs WHERE trip_id = %s ORDER BY id", (trip_id,))
        return [{**r, "errors": self.db.load_json(r["errors"]), "created_at": _iso(r["created_at"])} for r in rows]
