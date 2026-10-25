"""Dependency container: one place builds every service, so agents never create API clients themselves."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..config.settings import Settings, get_settings
from ..database.db import Database
from ..database.repository import TripRepository
from ..utils.cache import TTLCache
from ..utils.http_client import HttpClient
from ..utils.logging_config import get_logger
from .aviationstack_service import AviationStackService
from .fx_service import FxService
from .link_checker import LinkChecker
from .llm_service import LLMService
from .location_service import LocationService
from .serpapi_service import SerpApiService
from .tavily_service import TavilyService
from .wikipedia_service import WikipediaService
from .amadeus_service import AmadeusService
from ..agents.flight_agent import FlightAgent
from ..agents.hotel_agent import HotelAgent
from ..agents.itinerary_agent import ItineraryAgent
from ..agents.orchestrator import Orchestrator
from ..agents.store import SelectionStore
from ..providers.flight_provider import AmadeusFlightProvider, ChainedFlightProvider, SandboxFlightProvider, SerpApiFlightProvider
from ..providers.hotel_provider import ChainedHotelProvider, OverpassHotelProvider, SerpApiHotelProvider, TavilyHotelProvider
from .overpass_service import OverpassHotelService

log = get_logger("container")


@dataclass
class Services:
    settings: Settings
    http: HttpClient
    cache: TTLCache
    locations: LocationService
    serpapi: SerpApiService
    tavily: TavilyService
    wiki: WikipediaService
    fx: FxService
    llm: LLMService
    aviationstack: AviationStackService
    amadeus: AmadeusService
    links: LinkChecker
    db: Database
    repo: TripRepository
    orchestrator: Orchestrator
    sandbox_flights: SandboxFlightProvider


def build_services(settings: Optional[Settings] = None, transport: Optional[Callable[..., Any]] = None, link_transport: Optional[Callable[..., Any]] = None,
                   locations: Optional[LocationService] = None, db: Optional[Database] = None) -> Services:
    s = settings or get_settings()
    http = HttpClient(timeout=s.http_timeout, retries=s.http_retries, transport=transport, backoff=0.0 if transport else 0.6)
    cache = TTLCache()
    loc = locations or LocationService()
    if db is None:
        db = Database(s.database_url)
        if db.engine == "sqlite":
            log.warning("DATABASE_URL is not a PostgreSQL URL - using a local SQLite file for DEVELOPMENT only. Set DATABASE_URL for PostgreSQL.")
        db.init_schema()
    serp = SerpApiService(s, http, cache, loc)
    llm = LLMService(s, http)
    tav = TavilyService(s, http, cache)
    ama = AmadeusService(s, http, loc)
    store = SelectionStore()
    # SerpAPI is the primary (and sole active) flight fare provider.
    # Amadeus is instantiated but not chained by default — add it to the list
    # below only after configuring AMADEUS_CLIENT_ID + AMADEUS_CLIENT_SECRET.
    flight_provider = SerpApiFlightProvider(serp)
    # Hotel provider chain: SerpAPI (live rates) → Overpass/OSM (directory) → Tavily (web research).
    # All three are tried in order; results are merged so every destination returns hotels even
    # when the SerpAPI key is exhausted or the destination is unknown to Google Hotels.
    overpass_svc = OverpassHotelService(http)
    hotel_provider = ChainedHotelProvider([
        SerpApiHotelProvider(serp),
        OverpassHotelProvider(overpass_svc, loc),
        TavilyHotelProvider(tav),
    ])
    orch = Orchestrator(FlightAgent(loc, flight_provider, store, tav), HotelAgent(loc, hotel_provider, store, tav), ItineraryAgent(llm, tav, store), store)
    return Services(settings=s, http=http, cache=cache, locations=loc, serpapi=serp, tavily=tav,
                    wiki=WikipediaService(s, http, cache), fx=FxService(s, http, cache), llm=llm, aviationstack=AviationStackService(s, http, loc),
                    amadeus=ama, links=LinkChecker(enabled=s.verify_links, transport=link_transport), db=db, repo=TripRepository(db), orchestrator=orch, sandbox_flights=SandboxFlightProvider())
