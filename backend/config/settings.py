"""Application settings loaded from environment variables (never hard-code secrets)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

if os.getenv("TESTING") != "1" and "PYTEST_CURRENT_TEST" not in os.environ:
    try:  # python-dotenv is optional at import time
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except Exception:  # pragma: no cover
        pass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration."""

    app_name: str = "TravelAI Pro"
    env: str = "production"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    log_dir: str = "logs"
    secret_key: str = field(default="", repr=False)
    database_url: str = field(default="", repr=False)

    # Providers (all optional - a missing key degrades to a clear 'unavailable' message, never to fake data)
    serpapi_api_key: str = field(default="", repr=False)  # FLIGHT_API_KEY / HOTEL_API_KEY (SerpAPI: Google Flights + Google Hotels)
    tavily_api_key: str = field(default="", repr=False)
    aviationstack_api_key: str = field(default="", repr=False)
    aviationstack_base_url: str = "http://api.aviationstack.com/v1"
    aviationstack_future: bool = False
    openai_api_key: str = field(default="", repr=False)
    groq_api_key: str = field(default="", repr=False)
    llm_provider: str = ""  # auto: groq unless only an OpenAI key is present
    llm_model: str = ""
    llm_base_url: str = ""
    amadeus_client_id: str = field(default="", repr=False)      # free tier: developers.amadeus.com
    amadeus_client_secret: str = field(default="", repr=False)

    # Behaviour
    default_currency: str = "INR"
    domestic_only: bool = True
    serpapi_country: str = "in"
    serpapi_language: str = "en"
    http_timeout: int = 25
    http_retries: int = 2
    verify_links: bool = True
    enable_osm_hotels: bool = True
    place_detail_searches: int = 4
    max_places: int = 14
    session_ttl_minutes: int = 180
    sessions_per_hour: int = 20
    max_concurrent_stages: int = 4

    # Cache lifetimes (seconds). Prices are short-lived on purpose.
    ttl_flights: int = 600
    ttl_hotels: int = 1800
    ttl_research: int = 43200
    ttl_wiki: int = 604800
    ttl_fx: int = 43200

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_key)

    @property
    def resolved_llm_provider(self) -> str:
        if self.llm_provider in {"openai", "groq"}:
            return self.llm_provider
        return "openai" if (self.openai_api_key and not self.groq_api_key) else "groq"

    @property
    def llm_key(self) -> str:
        return self.openai_api_key if self.resolved_llm_provider == "openai" else self.groq_api_key

    @property
    def resolved_llm_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        return "gpt-4o-mini" if self.resolved_llm_provider == "openai" else "qwen/qwen3.8-27b"

    @property
    def resolved_llm_base_url(self) -> str:
        if self.llm_base_url:
            return self.llm_base_url.rstrip("/")
        return "https://api.openai.com/v1" if self.resolved_llm_provider == "openai" else "https://api.groq.com/openai/v1"

    def secret_values(self) -> list[str]:
        vals = [self.serpapi_api_key, self.tavily_api_key, self.aviationstack_api_key,
                self.openai_api_key, self.groq_api_key, self.secret_key, self.database_url,
                self.amadeus_client_id, self.amadeus_client_secret]
        return [v for v in vals if v and len(v) >= 6]

    def provider_status(self) -> dict:
        """Safe-to-expose booleans (no keys)."""
        return {
            "flights_hotels": bool(self.serpapi_api_key),
            "web_research": bool(self.tavily_api_key),
            "llm": self.llm_enabled,
            "llm_provider": self.resolved_llm_provider if self.llm_enabled else None,
            "flight_schedules": bool(self.aviationstack_api_key and self.aviationstack_future),
            "amadeus": bool(self.amadeus_client_id and self.amadeus_client_secret),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        env=_env("ENVIRONMENT", "production"), host=_env("HOST", "127.0.0.1"), port=_int("PORT", 8000), log_level=_env("LOG_LEVEL", "INFO").upper(),
        log_dir=_env("LOG_DIR", "logs"), secret_key=_env("SECRET_KEY") or secrets.token_urlsafe(32), database_url=_env("DATABASE_URL"),
        serpapi_api_key=_env("SERPAPI_API_KEY") or _env("FLIGHT_API_KEY") or _env("HOTEL_API_KEY"),
        tavily_api_key=_env("TAVILY_API_KEY"), aviationstack_api_key=_env("AVIATIONSTACK_API_KEY"),
        aviationstack_base_url=_env("AVIATIONSTACK_BASE_URL", "http://api.aviationstack.com/v1").rstrip("/"),
        aviationstack_future=_bool("AVIATIONSTACK_FUTURE_ENABLED", False), openai_api_key=_env("OPENAI_API_KEY"), groq_api_key=_env("GROQ_API_KEY"),
        llm_provider=_env("LLM_PROVIDER").lower(), llm_model=_env("LLM_MODEL"), llm_base_url=_env("LLM_BASE_URL"),
        default_currency=_env("DEFAULT_CURRENCY", "INR").upper(), domestic_only=_bool("DOMESTIC_ONLY", False), serpapi_country=_env("SERPAPI_COUNTRY", "in").lower(),
        http_timeout=_int("HTTP_TIMEOUT", 25), http_retries=_int("HTTP_RETRIES", 2), verify_links=_bool("VERIFY_LINKS", True),
        enable_osm_hotels=_bool("ENABLE_OSM_HOTELS", True),
        place_detail_searches=_int("PLACE_DETAIL_SEARCHES", 4), max_places=_int("MAX_PLACES", 14), session_ttl_minutes=_int("SESSION_TTL_MINUTES", 180),
        sessions_per_hour=_int("SESSIONS_PER_HOUR", 20),
        amadeus_client_id=_env("AMADEUS_CLIENT_ID"), amadeus_client_secret=_env("AMADEUS_CLIENT_SECRET"),
    )
