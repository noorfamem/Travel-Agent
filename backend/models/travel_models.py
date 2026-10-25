"""Pydantic domain models. Every agent exchanges these through the shared LangGraph state.

NOTE: no `from __future__ import annotations` here on purpose - Pydantic resolves the annotations at class creation.
"""
import datetime as dt
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class TripType(str, Enum):
    DOMESTIC = "DOMESTIC"
    INTERNATIONAL_OUTBOUND = "INTERNATIONAL_OUTBOUND"
    INTERNATIONAL_INBOUND = "INTERNATIONAL_INBOUND"
    INTERNATIONAL = "INTERNATIONAL"


class Cabin(str, Enum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class TravelStyle(str, Enum):
    BUDGET = "budget"
    COMFORTABLE = "comfortable"
    LUXURY = "luxury"


class DataKind(str, Enum):
    """Trust label shown next to every piece of information."""
    LIVE = "LIVE_API"
    WEB = "WEB_RESEARCH"
    AI = "AI_GENERATED"
    ESTIMATE = "ESTIMATE"
    VERIFIED = "VERIFIED_REFERENCE"
    USER = "USER_INPUT"
    SANDBOX = "SANDBOX"
    CACHED = "CACHED"
    UNAVAILABLE = "UNAVAILABLE"


INTEREST_OPTIONS = [("beaches", "Beaches"), ("adventure", "Adventure"), ("nature", "Nature"), ("history", "Historical places"), ("museums", "Museums"),
                    ("parks", "Parks"), ("shopping", "Shopping"), ("food", "Food"), ("family", "Family attractions"), ("nightlife", "Nightlife"),
                    ("photography", "Photography"), ("spiritual", "Spiritual / cultural places")]


# ------------------------------------------------------------------ locations
class Airport(BaseModel):
    iata: str
    name: str
    city: str = ""
    state: str = ""
    country_code: str = ""
    lat: float = 0.0
    lon: float = 0.0
    tz: str = "UTC"
    pri: int = 5
    distance_km: Optional[float] = None


class Location(BaseModel):
    query: str = ""
    kind: str = "unknown"  # airport | city | state | country | region | ambiguous | unknown
    name: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    country_code: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    airports: List[Airport] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    candidates: List[str] = Field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.kind in {"airport", "city", "state", "country"}

    @property
    def label(self) -> str:
        bits = [self.name]
        if self.state and self.state != self.name:
            bits.append(self.state)
        if self.country and self.country != self.name:
            bits.append(self.country)
        return ", ".join(b for b in bits if b)


# ------------------------------------------------------------------ request
class TripRequest(BaseModel):
    origin_text: str
    destination_text: str
    origin_airport: Optional[str] = None
    departure_date: dt.date
    return_date: dt.date
    adults: int = Field(1, ge=1, le=9)
    children: int = Field(0, ge=0, le=8)
    children_ages: List[int] = Field(default_factory=list)
    rooms: int = Field(1, ge=1, le=6)
    budget_total: Optional[float] = Field(None, gt=0)
    flight_budget: Optional[float] = Field(None, gt=0)
    hotel_budget: Optional[float] = Field(None, gt=0)
    currency: str = "INR"
    cabin: Cabin = Cabin.ECONOMY
    hotel_stars: List[int] = Field(default_factory=list)
    travel_style: TravelStyle = TravelStyle.COMFORTABLE
    interests: List[str] = Field(default_factory=list)
    custom_interest: str = ""
    food_preference: str = ""
    activity_preference: str = ""

    @field_validator("currency", mode="before")
    @classmethod
    def _cur(cls, v):
        return str(v or "INR").strip().upper()[:3]

    @property
    def travellers(self) -> int:
        return self.adults + self.children

    @property
    def trip_days(self) -> int:
        return (self.return_date - self.departure_date).days + 1


# ------------------------------------------------------------------ flights
class FlightSegment(BaseModel):
    airline: str = ""
    flight_number: str = ""
    airplane: str = ""
    origin_iata: str = ""
    origin_name: str = ""
    destination_iata: str = ""
    destination_name: str = ""
    departs_at: Optional[dt.datetime] = None  # timezone-aware, local to origin
    arrives_at: Optional[dt.datetime] = None  # timezone-aware, local to destination
    duration_min: Optional[int] = None
    cabin: str = ""
    overnight: bool = False


class Layover(BaseModel):
    iata: str = ""
    name: str = ""
    duration_min: Optional[int] = None
    overnight: bool = False


class FlightOption(BaseModel):
    link_kind: str = "none"  # offer (provider gave a link for this offer) | search (generic search page) | none
    search_url: Optional[str] = None
    id: str
    direction: str = "outbound"  # outbound | return
    airlines: List[str] = Field(default_factory=list)
    flight_numbers: List[str] = Field(default_factory=list)
    airline_logo: Optional[str] = None
    origin_iata: str = ""
    origin_name: str = ""
    destination_iata: str = ""
    destination_name: str = ""
    destination_city: str = ""
    origin_tz: str = ""
    destination_tz: str = ""
    departs_at: Optional[dt.datetime] = None
    arrives_at: Optional[dt.datetime] = None
    arrival_day_offset: int = 0
    duration_min: Optional[int] = None
    stops: int = 0
    layovers: List[Layover] = Field(default_factory=list)
    segments: List[FlightSegment] = Field(default_factory=list)
    cabin: str = ""
    baggage: Optional[str] = None
    price_per_person: Optional[float] = None
    total_price: Optional[float] = None
    currency: str = "INR"
    price_note: str = ""
    source: str = ""
    data_kind: DataKind = DataKind.LIVE
    retrieved_at: Optional[str] = None
    booking_url: Optional[str] = None
    official_website: Optional[str] = None
    official_website_label: Optional[str] = None
    airline_url: Optional[str] = None
    aggregator_url: Optional[str] = None
    open_search_url: Optional[str] = None
    open_search_label: str = "Open Flight Search"
    search_links: List[Dict[str, Any]] = Field(default_factory=list)
    departure_token: Optional[str] = None  # provider token used to fetch the return leg (internal)
    score: float = 0.0
    labels: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    local_dep_label: str = ""
    local_arr_label: str = ""


class FlightSearchOutcome(BaseModel):
    status: str  # ok | no_results | unavailable
    options: List[FlightOption] = Field(default_factory=list)
    message: str = ""
    searched_pairs: List[str] = Field(default_factory=list)
    provider: str = ""
    search_url: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)  # data-proof: provider, endpoint, params (no keys), counts, latency


# ------------------------------------------------------------------ hotels
class HotelOption(BaseModel):
    retrieved_at: Optional[str] = None
    link_kind: str = "none"  # offer (provider gave a link for this offer) | search (generic search page) | none
    search_url: Optional[str] = None
    id: str
    name: str
    location: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    star_rating: Optional[int] = None
    guest_rating: Optional[float] = None
    reviews: Optional[int] = None
    room_type: Optional[str] = None
    price_per_night: Optional[float] = None
    total_price: Optional[float] = None
    currency: str = "INR"
    nights: int = 0
    check_in: Optional[dt.date] = None
    check_out: Optional[dt.date] = None
    check_in_time: Optional[str] = None
    check_out_time: Optional[str] = None
    amenities: List[str] = Field(default_factory=list)
    cancellation: Optional[str] = None
    thumbnail: Optional[str] = None
    source: str = ""
    data_kind: DataKind = DataKind.LIVE
    booking_url: Optional[str] = None
    official_website: Optional[str] = None
    official_website_label: Optional[str] = None
    booking_search_url: Optional[str] = None
    price_note: str = ""
    score: float = 0.0
    labels: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)


class HotelSearchOutcome(BaseModel):
    status: str
    options: List[HotelOption] = Field(default_factory=list)
    message: str = ""
    provider: str = ""
    search_url: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    allocation: Dict[str, Any] = Field(default_factory=dict)  # budget split after the selected flight


# ------------------------------------------------------------------ places
class Place(BaseModel):
    id: str
    name: str
    category: str = "attraction"
    description: str = ""
    address: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    image_url: Optional[str] = None
    image_verified: bool = False
    image_credit: str = ""
    wiki_url: Optional[str] = None
    official_url: Optional[str] = None
    map_url: Optional[str] = None
    opening_hours: Optional[str] = None
    open_minute: Optional[int] = None  # minutes after midnight; ONLY when a source stated it
    close_minute: Optional[int] = None
    closed_weekdays: List[int] = Field(default_factory=list)  # 0 = Monday
    hours_verified: bool = False
    hours_source: str = ""
    ticket_info: Optional[str] = None
    ticket_amount_inr: Optional[float] = None
    visit_minutes: int = 90
    best_time: str = ""
    free_entry: Optional[bool] = None
    distance_from_hotel_km: Optional[float] = None
    travel_minutes: Optional[int] = None
    transport: str = ""
    data_sources: List[DataKind] = Field(default_factory=list)
    source_urls: List[str] = Field(default_factory=list)
    interest_tags: List[str] = Field(default_factory=list)
    why: str = ""
    popularity: float = 0.0
    notes: List[str] = Field(default_factory=list)


# ------------------------------------------------------------------ itinerary
class ItinerarySlot(BaseModel):
    period: str
    kind: str = "activity"  # activity | meal | transfer | checkin | checkout | arrival | departure | rest
    start: str = ""
    next_day: bool = False
    end: str = ""
    title: str = ""
    place_id: Optional[str] = None
    transport: str = ""
    transport_minutes: Optional[int] = None
    cost: Optional[float] = None  # INR; None = unknown
    cost_kind: DataKind = DataKind.ESTIMATE
    cost_note: str = ""
    link: Optional[str] = None
    map_link: Optional[str] = None
    image_url: Optional[str] = None
    why: str = ""
    notes: str = ""


class ItineraryDay(BaseModel):
    day_number: int
    date: dt.date
    title: str = ""
    slots: List[ItinerarySlot] = Field(default_factory=list)
    food_estimate: float = 0.0
    transport_estimate: float = 0.0
    activities_estimate: float = 0.0
    daily_total: float = 0.0
    notes: List[str] = Field(default_factory=list)


# ------------------------------------------------------------------ budget, integration, recommendation
class BudgetLine(BaseModel):
    key: str
    label: str
    amount: Optional[float] = None
    kind: DataKind = DataKind.ESTIMATE
    note: str = ""


class BudgetReport(BaseModel):
    currency: str = "INR"
    lines: List[BudgetLine] = Field(default_factory=list)
    total: float = 0.0
    budget: Optional[float] = None
    remaining: Optional[float] = None
    over_budget: bool = False
    per_person: Optional[float] = None
    warnings: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)


class IntegrationCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""
    fixed: bool = False


class IntegrationReport(BaseModel):
    checks: List[IntegrationCheck] = Field(default_factory=list)
    all_passed: bool = False
    fixes_applied: List[str] = Field(default_factory=list)


class AgentTrace(BaseModel):
    agent: str
    status: str = "ok"  # ok | degraded | failed | skipped
    duration_ms: int = 0
    input_summary: str = ""
    output_summary: str = ""
    fallback: str = ""
    errors: List[str] = Field(default_factory=list)


class Clarification(BaseModel):
    field: str
    question: str
    suggestions: List[str] = Field(default_factory=list)


# ------------------------------------------------------------------ LLM structured outputs
class LLMPlaceCandidate(BaseModel):
    name: str
    category: str = "attraction"
    why: str = ""
    interest_tags: List[str] = Field(default_factory=list)


class LLMPlaceCandidates(BaseModel):
    places: List[LLMPlaceCandidate] = Field(default_factory=list)


class LLMPlaceDetail(BaseModel):
    name: str
    opening_hours: Optional[str] = None
    hours_quote: Optional[str] = None  # verbatim source text; used to verify the hours
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    closed_days: List[str] = Field(default_factory=list)
    ticket_info: Optional[str] = None
    free_entry: Optional[bool] = None
    visit_minutes: Optional[int] = Field(None, ge=15, le=600)
    best_time: Optional[str] = None


class LLMPlaceDetails(BaseModel):
    details: List[LLMPlaceDetail] = Field(default_factory=list)


class LLMNarrative(BaseModel):
    summary: str = ""


def to_jsonable(obj: Any) -> Any:
    """Recursively convert models/enums/dates to JSON-safe primitives."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    return obj
