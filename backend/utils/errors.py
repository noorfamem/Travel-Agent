"""Error types shared across services and agents."""
from __future__ import annotations

import uuid
from enum import Enum


class ErrorKind(str, Enum):
    NOT_CONFIGURED = "not_configured"
    TIMEOUT = "timeout"
    NETWORK = "network"
    RATE_LIMITED = "rate_limited"
    AUTH = "auth_failed"
    BAD_REQUEST = "bad_request"
    SERVER = "provider_error"
    INVALID_RESPONSE = "invalid_response"
    NO_RESULTS = "no_results"


class ProviderError(Exception):
    """An external provider could not deliver. Never swallowed silently."""

    def __init__(self, provider: str, kind: ErrorKind, message: str):
        super().__init__(f"{provider}: {kind.value}: {message}")
        self.provider, self.kind, self.message = provider, kind, message

    def user_message(self) -> str:
        friendly = {
            ErrorKind.NOT_CONFIGURED: "is not configured (API key missing)", ErrorKind.TIMEOUT: "took too long to respond",
            ErrorKind.NETWORK: "could not be reached (network problem)", ErrorKind.RATE_LIMITED: "rate limit or quota reached",
            ErrorKind.AUTH: "rejected the API key", ErrorKind.BAD_REQUEST: "could not process this request",
            ErrorKind.SERVER: "had a server problem", ErrorKind.INVALID_RESPONSE: "returned an unexpected response", ErrorKind.NO_RESULTS: "returned no results",
        }[self.kind]
        return f"{self.provider} {friendly}."

    @property
    def retryable(self) -> bool:
        return self.kind in {ErrorKind.TIMEOUT, ErrorKind.NETWORK, ErrorKind.RATE_LIMITED, ErrorKind.SERVER}


class LLMOutputError(Exception):
    """The language model did not return valid structured output."""


class UserInputError(ValueError):
    """Invalid user input; `fields` maps field name -> message."""

    def __init__(self, fields: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in fields.items()))
        self.fields = fields


def new_error_id() -> str:
    return uuid.uuid4().hex[:10]
