"""Flight-offer provider interface. Agents depend on this, never on a concrete vendor.

Implemented:
  SerpApiFlightProvider  — SerpAPI (Google Flights). Primary. Needs SERPAPI_API_KEY.
  AmadeusFlightProvider  — Amadeus free tier. Fallback. Needs AMADEUS_CLIENT_ID + AMADEUS_CLIENT_SECRET.
  ChainedFlightProvider  — Tries providers in order; returns first non-empty result.
  SandboxFlightProvider  — Placeholder (always unconfigured).

Aviationstack is deliberately NOT a fare provider (supplies status/schedules only).
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from ..models.travel_models import Cabin, DataKind, FlightOption
from ..services.serpapi_service import SerpApiService
from ..utils.errors import ErrorKind, ProviderError
from ..utils.logging_config import get_logger

log = get_logger("flight_provider")


@dataclass
class ProviderFlights:
    options: list[FlightOption]
    provider: str
    data_kind: DataKind
    retrieved_at: Optional[str] = None
    notes: list[str] = field(default_factory=list)


class FlightProvider(Protocol):
    name: str
    @property
    def configured(self) -> bool: ...
    def search(self, dep_ids: list[str], arr_ids: list[str], outbound: dt.date, ret: Optional[dt.date], cabin: Cabin, currency: str) -> ProviderFlights: ...


# ──────────────────────────────────────────────────────────── SerpAPI ─────────

class SerpApiFlightProvider:
    name = "SerpAPI (Google Flights)"

    def __init__(self, svc: SerpApiService):
        self.svc = svc

    @property
    def configured(self) -> bool:
        return self.svc.configured

    def search(self, dep_ids, arr_ids, outbound, ret, cabin, currency) -> ProviderFlights:
        # Use the PRIMARY airport only — Google Flights rejects comma-joined multi-code queries.
        dep_id = dep_ids[0] if dep_ids else ""
        arr_id = arr_ids[0] if arr_ids else ""
        raw = self.svc.search_flights([dep_id], [arr_id], outbound, ret, cabin, currency)
        generic_url = self.svc.google_flights_url(dep_id, arr_id, outbound, ret)
        opts = self.svc.parse_flights(raw, "outbound", currency, cabin, bool(ret), generic_url)
        for o in opts:
            # Keep Google Flights as a working search page; airline sites are attached in FlightAgent.
            if o.booking_url == generic_url:
                o.booking_url = None

        # If primary airports found nothing, fan-out to alternates one by one
        if not opts:
            seen_ids: set[str] = set()
            for d in dep_ids[1:]:
                for a in arr_ids:
                    pair_key = f"{d}-{a}"
                    if pair_key in seen_ids:
                        continue
                    seen_ids.add(pair_key)
                    try:
                        raw2 = self.svc.search_flights([d], [a], outbound, ret, cabin, currency)
                        more = self.svc.parse_flights(raw2, "outbound", currency, cabin, bool(ret), generic_url)
                        for o in more:
                            if o.booking_url == generic_url:
                                o.booking_url = None
                        opts.extend(more)
                    except ProviderError:
                        pass
            # Also try primary dep with alternate arrivals
            for a in arr_ids[1:]:
                pair_key = f"{dep_id}-{a}"
                if pair_key in seen_ids:
                    continue
                seen_ids.add(pair_key)
                try:
                    raw2 = self.svc.search_flights([dep_id], [a], outbound, ret, cabin, currency)
                    more = self.svc.parse_flights(raw2, "outbound", currency, cabin, bool(ret), generic_url)
                    for o in more:
                        if o.booking_url == generic_url:
                            o.booking_url = None
                    opts.extend(more)
                except ProviderError:
                    pass

        return ProviderFlights(opts, self.name, DataKind.LIVE, (raw.get("_meta") or {}).get("retrieved_at"))


# ──────────────────────────────────────────────────────────── Amadeus ─────────

class AmadeusFlightProvider:
    """Amadeus free-tier flight offers — fallback when SerpAPI is unavailable."""
    name = "Amadeus (Flight Offers — test environment)"

    def __init__(self, svc: Any):  # AmadeusService; typed as Any to avoid circular
        self.svc = svc

    @property
    def configured(self) -> bool:
        return self.svc.configured

    def search(self, dep_ids: list[str], arr_ids: list[str], outbound: dt.date,
               ret: Optional[dt.date], cabin: Cabin, currency: str) -> ProviderFlights:
        dep_id = dep_ids[0] if dep_ids else ""
        arr_id = arr_ids[0] if arr_ids else ""
        retrieved = None
        opts: list[FlightOption] = []
        try:
            opts = self.svc.search_flights(dep_id, arr_id, outbound, ret, cabin, currency)
            if opts:
                retrieved = opts[0].retrieved_at
        except ProviderError:
            raise
        return ProviderFlights(opts, self.name, DataKind.LIVE, retrieved)


# ──────────────────────────────────────────────── Chained provider ────────────

class ChainedFlightProvider:
    """Try providers in order; return the first that yields results.

    This lets SerpAPI remain the primary source of truth while Amadeus
    acts as a transparent fallback — agents never need to know which fired.
    """

    def __init__(self, providers: list[Any]):
        self._providers = providers

    @property
    def name(self) -> str:
        names = [p.name for p in self._providers if p.configured]
        return " / ".join(names) if names else "No flight provider configured"

    @property
    def configured(self) -> bool:
        return any(p.configured for p in self._providers)

    def search(self, dep_ids: list[str], arr_ids: list[str], outbound: dt.date,
               ret: Optional[dt.date], cabin: Cabin, currency: str) -> ProviderFlights:
        last_err: Optional[ProviderError] = None
        for p in self._providers:
            if not p.configured:
                continue
            try:
                result = p.search(dep_ids, arr_ids, outbound, ret, cabin, currency)
                if result.options:
                    log.info("ChainedFlightProvider: %s returned %d offers", p.name, len(result.options))
                    return result
                log.info("ChainedFlightProvider: %s returned 0 offers, trying next", p.name)
            except ProviderError as e:
                log.warning("ChainedFlightProvider: %s failed (%s), trying next", p.name, e.kind.value)
                last_err = e
        if last_err:
            raise last_err
        # All configured providers returned 0 results — return empty
        configured = [p for p in self._providers if p.configured]
        prov_name = configured[0].name if configured else "unknown"
        return ProviderFlights([], prov_name, DataKind.LIVE)


# ──────────────────────────────────────────────────────────── Sandbox ─────────

class SandboxFlightProvider:
    """Placeholder until the exact sandbox provider, base URL and auth scheme are confirmed from its official docs."""
    name = "Sandbox flight provider (not yet identified)"

    @property
    def configured(self) -> bool:
        return False  # deliberately never True until a real, documented client exists

    @property
    def key_present(self) -> bool:
        return bool(os.getenv("FLIGHT_PROVIDER_API_KEY"))

    def search(self, *a: Any, **k: Any) -> ProviderFlights:
        raise ProviderError(self.name, ErrorKind.NOT_CONFIGURED, "No client implemented: the provider's official documentation must be confirmed first.")
