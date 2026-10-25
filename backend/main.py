"""TravelAI Pro API.

Every response carries an explicit `status` and `data_kind`. HTTP 200 is returned only when an agent actually produced a result;
provider failures use 502/503 so a failure can never be mistaken for success.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agents.common import HTTP_FOR_STATUS, status_for_error
from .agents.schemas import FlightAgentRequest, HotelAgentRequest, ItineraryAgentRequest, TripSummaryRequest
from .config.settings import get_settings
from .services.container import build_services
from .services.llm_service import extract_json
from .utils.errors import ProviderError

settings = get_settings()
services = build_services(settings)
orch = services.orchestrator
app = FastAPI(title="TravelAI Pro", version="3.0.0", description="Three cooperating agents: flights, hotels, 12-hour day plan. Provider-labelled results only.")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def no_cache_for_ui(request, call_next):
    """Stops the browser from showing a stale copy of an older UI."""
    resp = await call_next(request)
    if request.url.path.startswith("/ui"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui/")


def respond(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(body, status_code=HTTP_FOR_STATUS.get(body.get("status", "error"), 502))


class ResearchRequest(BaseModel):
    destination: str = Field(min_length=2, max_length=100)
    interests: list[str] = Field(default_factory=list, max_length=12)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "TravelAI Pro", "version": "3.0.0", "providers": settings.provider_status(),
            "notice": "'configured' means a key is present, NOT that a live request succeeded. Use POST /api/providers/check to test."}


@app.get("/api/providers")
def providers():
    flights_ok, hotels_ok = services.orchestrator.flight_agent.provider.configured, services.orchestrator.hotel_agent.provider.configured
    return {"providers": {
        "groq": {"configured": services.llm.configured, "role": "Itinerary structuring and preference interpretation. Never a source of prices, flight numbers or links."},
        "tavily": {"configured": services.tavily.configured, "role": "Web research for attractions and sources (search snippets are not verified prices)."},
        "aviationstack": {"configured": bool(settings.aviationstack_api_key), "role": "Flight status/schedules only - NOT fares."},
        "flight_offers": {"configured": flights_ok, "name": services.orchestrator.flight_agent.provider.name, "role": "Flight fares (primary: SerpAPI; fallback: Amadeus free tier)."},
        "hotel_offers": {"configured": hotels_ok, "name": services.orchestrator.hotel_agent.provider.name, "role": "Hotel rates (the only source of prices)."},
        "amadeus": {"configured": services.amadeus.configured, "role": "Amadeus free tier — fallback flight fares when SerpAPI is unavailable."},
        "sandbox_flights": {"configured": False, "key_present": services.sandbox_flights.key_present, "role": "Sandbox provider not yet identified; no client exists, so this key is currently unused."}},
        "labels": {"LIVE_API": "real request to a provider just now", "SANDBOX": "test data, not real offers", "CACHED": "earlier provider response", "ESTIMATE": "AI/approximate, not a quote", "UNAVAILABLE": "provider missing or failed"},
        "booking": "This app never books or takes payment. Links go to provider or search pages."}


@app.post("/api/providers/check")
def providers_check():
    """Makes ONE tiny real request per configured provider. This is the only endpoint that proves a key works."""
    out: dict[str, Any] = {}
    for name, fn in (("tavily", lambda: services.tavily.search("Taj Mahal Agra", 1)), ("groq", lambda: services.llm.complete("Reply with the word OK.", "ping", max_tokens=5))):
        svc_ok = services.tavily.configured if name == "tavily" else services.llm.configured
        if not svc_ok:
            out[name] = {"ok": False, "state": "not_configured"}
            continue
        try:
            fn()
            out[name] = {"ok": True, "state": "reachable_and_authenticated"}
        except ProviderError as e:
            out[name] = {"ok": False, "state": e.kind.value, "message": e.user_message()}
    out["flight_offers"] = {"ok": None, "state": "not_probed" if services.orchestrator.flight_agent.provider.configured else "not_configured", "note": "Probed by a real flight search to avoid wasting quota."}
    return out


@app.get("/api/budget/allocate")
def budget_allocate_endpoint(overall: float, nights: int = 3, passengers: int = 1, rooms: int = 1):
    from .tools.budget import allocate_budget
    if overall <= 0:
        raise HTTPException(422, "overall budget must be positive")
    return allocate_budget(overall, nights, passengers, rooms)


@app.post("/api/flights/search")
def search_flights(req: FlightAgentRequest):
    return respond(orch.flights(req))


@app.post("/api/hotels/search")
def search_hotels(req: HotelAgentRequest):
    return respond(orch.hotels(req))


@app.post("/api/itinerary/generate")
def generate_itinerary(req: ItineraryAgentRequest):
    return respond(orch.itinerary(req))


@app.post("/api/trip/summary")
def trip_summary(req: TripSummaryRequest):
    return respond(orch.summary(req))


@app.get("/api/locations/resolve")
def resolve_location(q: str):
    loc = services.locations.resolve(q[:100])
    return {"query": q, "kind": loc.kind, "name": loc.name, "country_code": loc.country_code, "airports": [{"iata": a.iata, "name": a.name, "city": a.city, "tz": a.tz} for a in loc.airports[:5]], "candidates": loc.candidates[:8], "notes": loc.notes}


@app.post("/api/research")
def research(req: ResearchRequest):
    query = f"official tourism attractions activities ticket prices opening hours in {req.destination} India {' '.join(req.interests)}"
    if not services.tavily.configured:
        return respond({"status": "unavailable", "provider": "Tavily", "results": [], "message": "Add TAVILY_API_KEY to .env."})
    try:
        found = services.tavily.search(query, max_results=8)
    except ProviderError as e:
        return respond({"status": status_for_error(e), "provider": "Tavily", "results": [], "message": e.user_message()})
    return respond({"status": "ok" if found.results else "no_results", "provider": "Tavily", "data_kind": "WEB_RESEARCH", "answer": found.answer,
                    "results": [{"title": r.title, "url": r.url, "content": r.content[:600]} for r in found.results], "note": "Web snippets are not verified prices or hours."})


@app.post("/api/assistant")
def assistant(body: dict[str, Any]):
    """Natural-language entry point. The LLM only extracts fields; the matching agent then does the real work."""
    instruction = str(body.get("instruction", "")).strip()[:1000]
    if not instruction:
        raise HTTPException(422, "instruction is required")
    if not services.llm.configured:
        return respond({"status": "unavailable", "message": "Add GROQ_API_KEY to .env."})
    system = ("Extract a travel request. Return JSON only with keys intent (flight_search|hotel_search|itinerary|clarify), origin, destination, departure_date (YYYY-MM-DD or empty), "
              "return_date (YYYY-MM-DD or empty), adults (int), budget (number or null), interests (array), clarification (string). Never invent dates; if essential fields are missing use clarify.")
    try:
        parsed = json.loads(extract_json(services.llm.complete(system, instruction, json_mode=True, max_tokens=500)))
    except ProviderError as e:
        return respond({"status": status_for_error(e), "message": e.user_message()})
    except Exception:
        return respond({"status": "error", "message": "The AI reply could not be understood. Use the forms instead."})
    try:
        intent, dest, orig = parsed.get("intent"), str(parsed.get("destination") or ""), str(parsed.get("origin") or "")
        dep, ret = str(parsed.get("departure_date") or ""), str(parsed.get("return_date") or "")
        adults = int(parsed.get("adults") or 1)
        if intent == "flight_search" and orig and dest and dep:
            r = FlightAgentRequest(origin=orig, destination=dest, departure_date=dt.date.fromisoformat(dep), return_date=dt.date.fromisoformat(ret) if ret else None, adults=adults, budget=parsed.get("budget"))
            return {"status": "ok", "action": "flight_search", "parsed_instruction": parsed, "result": orch.flights(r)}
        if intent == "hotel_search" and dest and dep and ret:
            r = HotelAgentRequest(destination=dest, check_in=dt.date.fromisoformat(dep), check_out=dt.date.fromisoformat(ret), adults=adults, budget=parsed.get("budget"))
            return {"status": "ok", "action": "hotel_search", "parsed_instruction": parsed, "result": orch.hotels(r)}
        if intent == "itinerary" and dest:
            return {"status": "ok", "action": "itinerary", "parsed_instruction": parsed, "result": orch.itinerary(ItineraryAgentRequest(destination=dest, interests=parsed.get("interests") or [], adults=adults))}
    except Exception as e:  # validation errors on AI-extracted fields
        return respond({"status": "error", "message": f"Could not use the extracted details ({type(e).__name__}). Please use the forms.", "parsed_instruction": parsed})
    return {"status": "clarify", "parsed_instruction": parsed, "message": parsed.get("clarification") or "Please give origin, destination and dates."}


_ui = Path(__file__).resolve().parents[1] / "frontend"
if (_ui / "index.html").exists():
    app.mount("/ui", StaticFiles(directory=str(_ui), html=True), name="ui")
