"""AGENT 3 - Personalised 12-hour Day-Plan Agent.

Facts (attractions, hours, prices) may only come from Tavily research; Groq structures them into a schedule.
Every cost is labelled ESTIMATE and opening hours/travel times are flagged unverified (no routing/hours provider is configured).
If Groq fails, no fake itinerary is returned. If Tavily fails, the plan is returned but marked as NOT web-researched.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ..models.travel_models import DataKind
from ..services.llm_service import LLMService
from ..services.tavily_service import TavilyService, WebSearch
from ..utils.errors import LLMOutputError, ProviderError
from ..utils.logging_config import get_logger
from .common import status_for_error
from .schemas import ItineraryAgentRequest
from .store import SelectionStore

log = get_logger("itinerary_agent")

LANDING_TO_READY_MIN = 150  # ASSUMPTION (not provider data): bag claim + transfer + freshen up after a flight lands
MEAL_CATS = {"breakfast", "lunch", "dinner", "snack"}


class LLMItem(BaseModel):
    start: str
    end: str
    activity: str = Field(min_length=2, max_length=160)
    category: Literal["breakfast", "lunch", "dinner", "snack", "attraction", "transport", "rest", "shopping", "other"] = "other"
    location: str = Field(default="", max_length=200)
    details: str = Field(default="", max_length=600)
    reason: str = Field(default="", max_length=300)
    cost_per_person: float = Field(default=0, ge=0, le=1e6)
    transport_note: str = Field(default="", max_length=200)
    source_idx: Optional[int] = None


class LLMPlan(BaseModel):
    items: list[LLMItem] = Field(min_length=3, max_length=24)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _fmt(mins: int) -> str:
    return f"{mins // 60:02d}:{mins % 60:02d}"


def check_schedule(items: list[LLMItem], start: int, end: int) -> list[str]:
    """Returns a list of problems (empty = feasible). Pure function, unit-tested."""
    problems, prev_end = [], start
    for i, it in enumerate(items):
        try:
            s, e = _minutes(it.start), _minutes(it.end)
        except Exception:
            problems.append(f"item {i + 1}: time is not HH:MM")
            continue
        if e <= s:
            problems.append(f"item {i + 1}: end must be after start")
        if s < start or e > end:
            problems.append(f"item {i + 1}: {it.start}-{it.end} is outside the {_fmt(start)}-{_fmt(end)} window")
        if s < prev_end:
            problems.append(f"item {i + 1}: overlaps the previous item")
        prev_end = max(prev_end, e)
    cats = {it.category for it in items}
    if end - start >= 600 and not ({"lunch"} & cats):
        problems.append("no lunch break scheduled")
    return problems


def earliest_start(window_start: int, flight, plan_date: dt.date) -> tuple[int, Optional[str]]:
    """If the selected flight lands on the plan date, nothing is scheduled before landing + LANDING_TO_READY_MIN."""
    if flight is None or flight.arrives_at is None or flight.arrives_at.date() != plan_date:
        return window_start, None
    ready = flight.arrives_at.hour * 60 + flight.arrives_at.minute + LANDING_TO_READY_MIN
    ready = ((ready + 14) // 15) * 15
    if ready > window_start:
        return ready, f"Your flight lands at {flight.arrives_at.strftime('%H:%M')} local time; the plan starts at {_fmt(ready)} (assumes {LANDING_TO_READY_MIN} min from landing to being ready - an assumption, not provider data)."
    return window_start, None


def _merge_research(*parts: Optional[WebSearch]) -> Optional[WebSearch]:
    rows, seen = [], set()
    query, answer = "", ""
    for p in parts:
        if not p:
            continue
        query = query or p.query
        answer = answer or p.answer
        for r in p.results:
            if r.url in seen:
                continue
            seen.add(r.url)
            rows.append(r)
    if not rows and not answer:
        return None
    return WebSearch(query=query, answer=answer, results=rows)


def _place_from_result(title: str, content: str, dest: str) -> str:
    t = (title or "").split("|")[0].split(" - ")[0].strip()
    t = re.sub(r"(?i)^\d+\s*(best|top)?\s*", "", t)
    t = re.sub(r"(?i)\b(things to do|tourist places|attractions?|complete guide|travel guide|official website)\b.*", "", t)
    t = re.sub(r"(?i)\bin\s+" + re.escape(dest) + r"\b.*", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -:|")
    if len(t) >= 4:
        return t[:80]
    first = (content or "").split(".")[0].strip()
    return (first[:80] if len(first) >= 8 else f"Explore {dest}")


def plan_from_research(research: Optional[WebSearch], dest: str, start: int, end: int, hotel_name: Optional[str],
                       dietary: str, interests: list[str]) -> LLMPlan:
    """Hourly 12-hour skeleton from Tavily results (9-10, 10-11, …). No LLM required."""
    places: list[tuple[str, Optional[int], str]] = []
    seen: set[str] = set()
    for i, r in enumerate(research.results if research else []):
        name = _place_from_result(r.title, r.content, dest)
        key = name.lower()
        if key in seen or "tripadvisor" in key:
            continue
        seen.add(key)
        snippet = (r.content or "")[:280]
        places.append((name, i, snippet))
        if len(places) >= 8:
            break
    if not places:
        places = [
            (f"Main market and local food in {dest}", None, "Local streets and markets."),
            (f"Landmark walk in {dest}", None, "City highlights."),
            (f"Park or viewpoint in {dest}", None, "Outdoor time."),
            (f"Museum or heritage stop in {dest}", None, "Culture."),
            (f"Evening waterfront or cafe in {dest}", None, "Wind-down."),
        ]
    items: list[LLMItem] = []
    t = start
    lunch_at, dinner_at = 12 * 60 + 30, 19 * 60
    lunch_done = dinner_done = False
    pi = 0
    food = dietary or "local"
    if t + 45 <= end:
        items.append(LLMItem(start=_fmt(t), end=_fmt(t + 45), activity=f"Breakfast ({food})" + (f" near {hotel_name}" if hotel_name else ""),
                             category="breakfast", location=hotel_name or dest, details="Start the day with a local breakfast.",
                             reason="Energy before sightseeing", cost_per_person=250, transport_note="Walk or short cab"))
        t += 60
    while t + 50 <= end:
        if not lunch_done and t >= lunch_at - 15:
            items.append(LLMItem(start=_fmt(t), end=_fmt(min(t + 60, end)), activity=f"Lunch — {food} restaurant in {dest}",
                                 category="lunch", location=dest, details="Sit-down meal; hours unverified.",
                                 reason="Midday break", cost_per_person=400, transport_note="Auto/cab ~15 min"))
            lunch_done, t = True, t + 75
            continue
        if not dinner_done and t >= dinner_at - 15:
            items.append(LLMItem(start=_fmt(t), end=_fmt(min(t + 60, end)), activity=f"Dinner in {dest}",
                                 category="dinner", location=dest, details="Evening meal. Confirm opening hours locally.",
                                 reason="End-of-day meal", cost_per_person=500, transport_note="Cab back to stay"))
            dinner_done, t = True, t + 70
            continue
        name, idx, snippet = places[pi % len(places)]
        slot = 60 if req_pace_slot(end - start) else 75
        if t + slot > end:
            slot = end - t
        if slot < 40:
            break
        cat = "attraction"
        blob = f"{name} {' '.join(interests)}".lower()
        if any(w in blob for w in ("trek", "raft", "dive", "adventure", "safari", "paraglide")):
            cat = "attraction"
        elif any(w in blob for w in ("shop", "market", "bazaar")):
            cat = "shopping"
        items.append(LLMItem(start=_fmt(t), end=_fmt(t + slot), activity=name, category=cat, location=dest,
                             details=snippet or f"Visit {name}. Opening hours unverified.",
                             reason="From live Tavily research", cost_per_person=150, transport_note="Taxi or local bus ~20 min",
                             source_idx=idx))
        pi += 1
        t += slot + 15
        if len(items) >= 14:
            break
    if len(items) < 3:
        items.append(LLMItem(start=_fmt(start), end=_fmt(min(start + 180, end)), activity=f"Guided orientation in {dest}",
                             category="other", location=dest, details="Fallback block when research was thin.", cost_per_person=0))
    return LLMPlan(items=items[:24])


def req_pace_slot(window_min: int) -> bool:
    return window_min >= 600


class ItineraryAgent:
    def __init__(self, llm: LLMService, tavily: TavilyService, store: SelectionStore):
        self.llm, self.tavily, self.store = llm, tavily, store

    def _research(self, req: ItineraryAgentRequest) -> tuple[Optional[WebSearch], str, list[str]]:
        notes: list[str] = []
        if not self.tavily.configured:
            return None, "not_configured", ["Tavily is not configured — plan is not web-researched."]
        interests = " ".join(req.interests) or "sightseeing adventure food"
        queries = [
            f"best tourist places attractions in {req.destination} India opening hours ticket prices {interests}",
            f"adventure activities things to do in {req.destination} India",
            f"best restaurants local food in {req.destination} India {req.dietary}",
        ]
        parts: list[WebSearch] = []
        last_kind = "ok"
        for q in queries:
            try:
                parts.append(self.tavily.search(q, max_results=6))
            except ProviderError as e:
                last_kind = e.kind.value
                notes.append(f"Web research issue ({e.user_message()}).")
        merged = _merge_research(*parts)
        if merged and merged.results:
            return merged, "ok", notes
        return merged, last_kind, notes + ["Web research returned no usable sources; itinerary is NOT backed by sources."]

    def plan(self, req: ItineraryAgentRequest) -> dict[str, Any]:
        base: dict[str, Any] = {"agent": "itinerary", "destination": req.destination, "currency": req.currency, "items": [], "sources": []}
        if not self.llm.configured:
            return {**base, "status": "unavailable", "data_kind": DataKind.UNAVAILABLE.value, "items": [], "message": f"{self.llm.provider_name} is not configured, so no itinerary can be generated."}
        flight = self.store.get(req.flight_id, "flight") if req.flight_id else None
        hotel = self.store.get(req.hotel_id, "hotel") if req.hotel_id else None
        if req.flight_id and flight is None:
            return {**base, "status": "error", "data_kind": DataKind.UNAVAILABLE.value, "message": "The selected flight is unknown or expired. Search flights again."}
        if req.hotel_id and hotel is None:
            return {**base, "status": "error", "data_kind": DataKind.UNAVAILABLE.value, "message": "The selected hotel is unknown or expired. Search hotels again."}
        plan_date = req.plan_date or (flight.arrives_at.date() if flight and flight.arrives_at else None)
        w_start = _minutes(req.start_time)
        w_end = w_start + req.window_hours * 60
        notes: list[str] = ["Travel times and opening hours are NOT verified by a routing/hours provider; treat them as estimates.", "Weather was not checked."]
        eff_start = w_start
        if plan_date:
            eff_start, note = earliest_start(w_start, flight, plan_date)
            if note:
                notes.append(note)
        if w_end - eff_start < 180:
            return {**base, "status": "no_time", "data_kind": DataKind.UNAVAILABLE.value, "message": "Too little of the day remains after your flight lands for a useful plan. Choose another date or an earlier flight."}
        research, research_status, extra_notes = self._research(req)
        notes.extend(extra_notes)
        sources = [{"idx": i, "title": r.title, "url": r.url} for i, r in enumerate(research.results if research else [])]
        plan: Optional[LLMPlan] = None
        problems: list[str] = []
        used_llm = False
        if self.llm.configured:
            user = json.dumps({
                "destination": req.destination, "window": f"{_fmt(eff_start)}-{_fmt(w_end)}", "pace": req.pace, "interests": req.interests, "mobility_needs": req.mobility_needs,
                "dietary": req.dietary, "adults": req.adults, "currency": req.currency, "daily_budget_per_person": req.daily_activity_budget,
                "arrival": flight.arrives_at.isoformat() if flight and flight.arrives_at else None, "arrival_airport": flight.destination_iata if flight else None,
                "hotel": {"name": hotel.name, "location": hotel.location} if hotel else None,
                "research": [{"idx": s["idx"], "title": s["title"], "text": research.results[s["idx"]].content[:700]} for s in sources] if research else [],
            }, ensure_ascii=False)
            system = ("You are a careful travel planner for India. Reply with ONE JSON object {\"items\":[{start,end,activity,category,location,details,reason,cost_per_person,transport_note,source_idx}]}. "
                      "start/end are 24-hour HH:MM inside the given window, strictly increasing, no overlaps. Hour-by-hour (e.g. 09:00-10:00, 10:00-11:00) is preferred. "
                      "category is one of breakfast,lunch,dinner,snack,attraction,transport,rest,shopping,other. "
                      "Include breakfast/lunch/dinner as fits the window. Use ONLY attractions supported by the research text when research is provided; "
                      "set source_idx to that research idx, else null. cost_per_person is an estimate in the given currency (0 if free). Never state that prices or hours are verified. No markdown.")
            for attempt in range(2):
                try:
                    plan = self.llm.complete_model(LLMPlan, system, user + (f"\nFix these problems from your previous answer: {problems}" if problems else ""), repairs=1, max_tokens=3500)
                    used_llm = True
                except ProviderError as e:
                    log.warning("LLM provider failed: %s", e.kind.value)
                    return {**base, "status": status_for_error(e), "data_kind": DataKind.UNAVAILABLE.value, "items": [], "message": e.user_message(), "error_kind": e.kind.value}
                except LLMOutputError as e:
                    log.warning("LLM output error: %s", str(e))
                    return {**base, "status": "error", "data_kind": DataKind.UNAVAILABLE.value, "items": [], "message": f"LLM output could not be validated: {str(e)}"}
                problems = check_schedule(plan.items, eff_start, w_end)
                if not problems:
                    break
            if plan and problems:
                return {**base, "status": "error", "data_kind": DataKind.UNAVAILABLE.value, "items": [], "message": f"LLM generated an infeasible schedule with overlaps or invalid timings: {'; '.join(problems)}"}
        if plan is None:
            plan = plan_from_research(research, req.destination, eff_start, w_end, hotel.name if hotel else None, req.dietary, req.interests)
            used_llm = False
        if research_status != "ok" and not (research and research.results) and not used_llm and not self.tavily.configured:
            return {**base, "status": "unavailable", "data_kind": DataKind.UNAVAILABLE.value, "message": "No itinerary source is available."}
        items, act_total, meal_total = [], 0.0, 0.0
        for it in plan.items:
            src = sources[it.source_idx] if it.source_idx is not None and 0 <= it.source_idx < len(sources) else None
            cost = round(it.cost_per_person * req.adults, 2)
            if it.category in MEAL_CATS:
                meal_total += cost
            else:
                act_total += cost
            items.append({**it.model_dump(exclude={"source_idx"}), "duration_min": _minutes(it.end) - _minutes(it.start), "cost_total": cost, "cost_kind": "ESTIMATE",
                          "hours_verified": False, "source_url": src["url"] if src else None, "source_title": src["title"] if src else None})
        total = act_total + meal_total
        budget_total = req.daily_activity_budget * req.adults if req.daily_activity_budget else None
        if budget_total is not None and total > budget_total * 1.1:
            notes.append(f"Estimated day cost {total:,.0f} {req.currency} is above your daily budget of {budget_total:,.0f}.")
        data_kind = DataKind.AI if used_llm else DataKind.WEB
        out = {**base, "status": "ok" if research_status == "ok" else "partial", "data_kind": data_kind.value, "research_status": research_status,
               "provider": (self.llm.provider_name if used_llm else "Tavily hour-by-hour"),
               "plan_date": plan_date.isoformat() if plan_date else None, "window": {"start": _fmt(eff_start), "end": _fmt(w_end)},
               "items": items, "sources": [{"title": s["title"], "url": s["url"]} for s in sources], "notes": notes,
               "uses": {"flight": flight.id if flight else None, "hotel": hotel.id if hotel else None},
               "costs": {"activities_total": round(act_total, 2), "meals_total": round(meal_total, 2), "total": round(total, 2), "kind": "ESTIMATE", "adults": req.adults},
               "message": ("AI-generated plan. Costs are estimates, not quotes; opening hours and travel times are unverified."
                           if used_llm else "Hour-by-hour plan from Tavily live web research. Costs are estimates; opening hours and travel times are unverified.")}
        out["itinerary_id"] = self.store.put("itinerary", out)
        return out
