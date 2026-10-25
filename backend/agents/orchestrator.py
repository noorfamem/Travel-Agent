"""Wires the three agents together. Data moves between them as server-side IDs so provenance is preserved."""
from __future__ import annotations

from typing import Any

from ..tools.budget import allocate_budget, trip_summary
from .flight_agent import FlightAgent
from .hotel_agent import HotelAgent
from .itinerary_agent import ItineraryAgent
from .schemas import FlightAgentRequest, HotelAgentRequest, ItineraryAgentRequest, OverallTripRequest, TripSummaryRequest
from .store import SelectionStore


class Orchestrator:
    def __init__(self, flight: FlightAgent, hotel: HotelAgent, itinerary: ItineraryAgent, store: SelectionStore):
        self.flight_agent, self.hotel_agent, self.itinerary_agent, self.store = flight, hotel, itinerary, store

    def flights(self, req: FlightAgentRequest) -> dict[str, Any]:
        if req.overall_budget and req.budget is None:
            alloc = allocate_budget(req.overall_budget, passengers=req.passengers)
            req = req.model_copy(update={"budget": alloc["flight_budget_per_passenger"], "budget_scope": "per_passenger"})
        return self.flight_agent.search(req)

    def hotels(self, req: HotelAgentRequest) -> dict[str, Any]:
        """If a flight was selected, its real arrival date becomes the default check-in (Agent 1 -> Agent 2)."""
        if req.flight_id:
            flight = self.store.get(req.flight_id, "flight")
            if flight is None:
                return {"agent": "hotel", "status": "error", "options": [], "message": "The selected flight is unknown or expired. Search flights again."}
            if req.check_in is None:
                if not flight.arrives_at:
                    return {"agent": "hotel", "status": "error", "options": [], "message": "The selected flight has no arrival time; enter check-in manually."}
                req = req.model_copy(update={"check_in": flight.arrives_at.date()})
                if req.check_out <= req.check_in:
                    return {"agent": "hotel", "status": "error", "options": [], "message": "Check-out must be after the flight's arrival date."}
        if req.overall_budget and req.budget is None:
            nights = max(1, (req.check_out - req.check_in).days) if req.check_in else 3
            alloc = allocate_budget(req.overall_budget, nights=nights, passengers=req.adults + req.children, rooms=req.rooms)
            req = req.model_copy(update={"budget": alloc["hotel_budget_per_night"], "budget_scope": "per_night"})
        return self.hotel_agent.search(req)

    def itinerary(self, req: ItineraryAgentRequest) -> dict[str, Any]:
        return self.itinerary_agent.plan(req)

    def summary(self, req: TripSummaryRequest) -> dict[str, Any]:
        flight = self.store.get(req.flight_id, "flight")
        hotel = self.store.get(req.hotel_id, "hotel")
        plan = self.store.get(req.itinerary_id, "itinerary")
        missing = [n for n, i, o in (("flight", req.flight_id, flight), ("hotel", req.hotel_id, hotel), ("itinerary", req.itinerary_id, plan)) if i and o is None]
        if missing:
            return {"status": "error", "message": f"Unknown or expired selection: {', '.join(missing)}. Search again."}
        nights = (hotel.nights if hotel else 0)
        costs = (plan or {}).get("costs") or {}
        s = trip_summary(currency=req.currency, passengers=req.adults + req.children, nights=nights, rooms=req.rooms, flight=flight, hotel=hotel,
                         activity_total=costs.get("activities_total"), meal_total=costs.get("meals_total"), overall_budget=req.overall_budget)
        return {"status": "ok", **s}
