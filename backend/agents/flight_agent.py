"""AGENT 1 - Flight Search Agent. Prices, flight numbers and links come ONLY from the provider response; the LLM is never involved."""
from __future__ import annotations

from typing import Any

from ..models.travel_models import DataKind, FlightOption
from ..providers.flight_provider import FlightProvider, ProviderFlights
from ..services.location_service import LocationService
from ..services.tavily_service import TavilyService
from ..tools.booking_links import flight_open_links, google_flights_open_url, kayak_flights_url
from ..tools.budget import dedupe_flights, flight_total_cost, split_flights_by_budget
from ..tools.ranking import rank_flights
from ..utils.errors import ProviderError
from ..utils.logging_config import get_logger
from .common import airport_info, clean_link, india_airports, status_for_error
from .schemas import FlightAgentRequest
from .store import SelectionStore

log = get_logger("flight_agent")


class FlightAgent:
    def __init__(self, locations: LocationService, provider: FlightProvider, store: SelectionStore, tavily: TavilyService | None = None):
        self.locations, self.provider, self.store, self.tavily = locations, provider, store, tavily

    def search(self, req: FlightAgentRequest) -> dict[str, Any]:
        base: dict[str, Any] = {"agent": "flight", "provider": self.provider.name, "currency": req.currency, "passengers": req.passengers,
                                "budget": {"amount": req.budget, "scope": req.budget_scope}, "options": [], "over_budget": [], "excluded": []}
        dep, err = india_airports(self.locations, req.origin, "origin")
        if err:
            return {**base, **err, "data_kind": DataKind.UNAVAILABLE.value}
        arr, err = india_airports(self.locations, req.destination, "destination")
        if err:
            return {**base, **err, "data_kind": DataKind.UNAVAILABLE.value}
        dep_ids, arr_ids = [a.iata for a in dep], [a.iata for a in arr]
        if set(dep_ids) & set(arr_ids):
            return {**base, "status": "invalid_location", "message": "Origin and destination resolve to the same airport.", "data_kind": DataKind.UNAVAILABLE.value}
        dest_city = (arr[0].city or arr[0].name or req.destination).strip()
        origin_in = not dep[0].country_code or dep[0].country_code == "IN"
        dest_in = not arr[0].country_code or arr[0].country_code == "IN"
        domestic = origin_in and dest_in
        search_url = google_flights_open_url(
            dep_ids[0], arr_ids[0], req.departure_date, req.return_date, req.adults, req.cabin.value
        )
        open_search = kayak_flights_url(dep_ids[0], arr_ids[0], req.departure_date, req.return_date, req.adults, req.cabin.value) or search_url
        base.update(
            search_url=search_url,
            open_search_url=open_search,
            search_url_label="Route search on Google Flights (a search page, NOT an offer or booking link)",
            searched_airports={"origin": airport_info(dep), "destination": airport_info(arr)},
            destination_city=dest_city,
            domestic=domestic,
        )
        if not self.provider.configured:
            links, research_note = self._research_flight_links(req)
            return {**base, "status": "unavailable", "data_kind": DataKind.UNAVAILABLE.value,
                    "message": f"{self.provider.name} is not configured, so no verified flight offers or fares can be shown. " + research_note,
                    "flight_search_links": links, "flight_search_links_label": "Web-researched flight search pages (not confirmed flight offers)"}
        try:
            got = self.provider.search(dep_ids, arr_ids, req.departure_date, req.return_date, req.cabin, req.currency)
        except ProviderError as e:
            log.warning("flight provider failed: %s", e.kind.value)
            links, research_note = self._research_flight_links(req)
            return {**base, "status": status_for_error(e), "data_kind": DataKind.UNAVAILABLE.value, "message": e.user_message() + " " + research_note,
                    "error_kind": e.kind.value, "flight_search_links": links,
                    "flight_search_links_label": "Web-researched flight search pages (not confirmed flight offers)"}
        except Exception as exc:
            log.exception("unexpected %s provider failure: %s", "flight", type(exc).__name__)
            return {**base, "status": "error", "data_kind": DataKind.UNAVAILABLE.value, "message": "The provider returned data in an unexpected format.", "error_kind": "invalid_response"}
        if not isinstance(got, ProviderFlights):
            return {**base, "status": "error", "data_kind": DataKind.UNAVAILABLE.value, "message": "The provider returned data in an unexpected format.", "error_kind": "invalid_response"}
        base.update(provider=got.provider, data_kind=got.data_kind.value, retrieved_at=got.retrieved_at)
        opts = dedupe_flights(got.options)
        # Anti-hallucination / error guard: filter out extreme pricing anomalies (>5x median) when multiple priced offers exist
        priced = [o for o in opts if o.price_per_person is not None]
        if len(priced) >= 3:
            prices = sorted(o.price_per_person for o in priced)
            median = prices[len(prices) // 2]
            opts = [o for o in opts if o.price_per_person is None or o.price_per_person <= median * 5]
        if req.nonstop_only:
            opts = [o for o in opts if o.stops == 0]
        if not opts:
            links, research_note = self._research_flight_links(req)
            return {**base, "status": "no_results", "message": "The flight provider returned no matching offers for this route and date. " + research_note,
                    "flight_search_links": links,
                    "flight_search_links_label": "Web-researched flight search pages (not confirmed flight offers)"}
        total_budget = (req.budget * req.passengers if req.budget_scope == "per_passenger" else req.budget) if req.budget else None
        within, over, excluded = split_flights_by_budget(opts, req.budget, req.budget_scope, req.passengers, req.currency)
        ranked = rank_flights(within, req.style, total_budget, req.passengers, top_n=len(within) or 1) if within else []
        pref = {a.lower() for a in req.preferred_airlines}
        if pref:
            ranked.sort(key=lambda o: 0 if any(a.lower() in pref for a in o.airlines) else 1)
        top = ranked[:5]
        near = sorted(over, key=lambda o: o.price_per_person if o.price_per_person is not None else float("inf"))[:3]
        for o in top + near:
            self._finish(o, got.retrieved_at, search_url, req, dest_city, domestic)
        for o in near:
            o.labels = [*o.labels, "OVER BUDGET"]
        for o in top + near:
            self.store.put("flight", o, key=o.id)
        msg = ""
        if not top and near:
            msg = "No offer fits your budget. The nearest over-budget alternatives are listed separately."
        status = "ok" if top else "no_results"
        if not top and not near:
            msg = "No priced offers were usable (see 'excluded')."
        return {**base, "status": status, "message": msg or "Prices and availability can change; verify with the airline/agency before buying.",
                "options": [o.model_dump(mode="json") for o in top], "over_budget": [o.model_dump(mode="json") for o in near], "excluded": excluded}

    def _research_flight_links(self, req: FlightAgentRequest) -> tuple[list[dict[str, str]], str]:
        """Return real Tavily-sourced pages as links, never as fabricated flight offers."""
        if not self.tavily or not self.tavily.configured:
            return [], "Tavily is not configured; use the Google Flights route-search link."
        query = (f"flight tickets flights from {req.origin} to {req.destination} "
                 f"departure {req.departure_date.isoformat()} "
                 + (f"return {req.return_date.isoformat()} " if req.return_date else "one way ")
                 + "airline official booking flight search")
        try:
            result = self.tavily.search(query, max_results=5)
            links: list[dict[str, str]] = []
            seen: set[str] = set()
            for item in result.results:
                url = clean_link(item.url)
                if not url or url in seen:
                    continue
                seen.add(url)
                links.append({"title": item.title or "Flight search result", "url": url,
                              "snippet": item.content[:500], "source": "Tavily web research",
                              "kind": "external_search_page_not_confirmed_offer"})
                if len(links) == 5:
                    break
            note = ("Tavily found external pages to check. These links are not proof of a specific flight, fare, or availability."
                    if links else "Tavily did not return usable flight-search pages; use the Google Flights route-search link.")
            return links, note
        except Exception as exc:
            log.warning("Tavily flight-link research failed: %s", type(exc).__name__)
            return [], "Tavily flight-link research failed; use the Google Flights route-search link."

    @staticmethod
    def _finish(o: FlightOption, retrieved_at, search_url: str, req: FlightAgentRequest, dest_city: str = "", domestic: bool = True) -> None:
        dep = o.departs_at.date() if o.departs_at else req.departure_date
        links = flight_open_links(
            o.airlines, o.flight_numbers, o.origin_iata, o.destination_iata,
            req.origin, req.destination, dep, req.return_date,
            req.adults, req.children, req.cabin.value, domestic,
        )
        o.airline_url = links["airline_url"]
        o.aggregator_url = links["mmt_url"]
        o.search_url = links["google_url"] or search_url
        o.open_search_url = links["open_search_url"] or o.search_url
        o.open_search_label = "Open Flight Search"
        o.search_links = list(links["links"] or [])
        o.destination_city = dest_city or o.destination_city or o.destination_name
        o.booking_url = clean_link(o.booking_url)
        # Generic Google/Kayak search pages are not offer-specific booking links.
        if o.booking_url and o.booking_url in {o.search_url, o.open_search_url, search_url}:
            o.booking_url = None
        o.link_kind = "offer" if o.booking_url else "search"
        o.retrieved_at = o.retrieved_at or retrieved_at
        total = flight_total_cost(o, req.passengers)
        if total is not None and o.price_per_person is not None:
            o.price_note = (
                f"{o.price_note}. Shown per passenger; all {req.passengers} passenger(s) = "
                f"{total:,.0f} {o.currency} (per-person x passengers, no extra taxes added). "
                "Prices are indicative until confirmed on the airline or search site."
            )
