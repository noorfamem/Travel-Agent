"""One HTTP gateway for every provider: timeouts, retries, error classification, safe logging."""
from __future__ import annotations

import time
from typing import Any, Callable
from urllib.parse import urlsplit

from .errors import ErrorKind, ProviderError
from .logging_config import get_logger
from .ssl_setup import install_system_certs

# Install a working CA bundle the moment this module is imported so every
# subsequent requests call already has a valid TLS store (critical on Windows).
install_system_certs()

log = get_logger("http")
Transport = Callable[..., Any]


def _default_transport(method: str, url: str, **kwargs: Any) -> Any:
    import requests  # lazy so tests can run without network libs
    return requests.request(method, url, **kwargs)


class HttpClient:
    """`transport` is injectable so tests never touch the network."""

    def __init__(self, timeout: int = 25, retries: int = 2, transport: Transport | None = None, backoff: float = 0.6):
        self.timeout, self.retries, self.backoff = timeout, retries, backoff
        self._transport = transport or _default_transport

    def request_json(self, provider: str, method: str, url: str, *, retries: int | None = None, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self.timeout)
        attempts = (self.retries if retries is None else retries) + 1
        shown = f"{urlsplit(url).netloc}{urlsplit(url).path}"
        last: ProviderError | None = None
        for attempt in range(attempts):
            try:
                resp = self._transport(method, url, **kwargs)
            except Exception as exc:  # requests.Timeout / ConnectionError / anything transport-level
                name = type(exc).__name__.lower()
                last = ProviderError(provider, ErrorKind.TIMEOUT if "timeout" in name else ErrorKind.NETWORK, type(exc).__name__)
            else:
                code = getattr(resp, "status_code", 0)
                if code in (401, 403):
                    raise ProviderError(provider, ErrorKind.AUTH, f"HTTP {code}")
                if code == 429:
                    last = ProviderError(provider, ErrorKind.RATE_LIMITED, "HTTP 429")
                elif code >= 500:
                    last = ProviderError(provider, ErrorKind.SERVER, f"HTTP {code}")
                elif code >= 400:
                    detail = ""
                    try:
                        body = resp.json()
                        detail = str(body.get("error") or body.get("message") or "")[:160] if isinstance(body, dict) else ""
                    except Exception:
                        pass
                    raise ProviderError(provider, ErrorKind.BAD_REQUEST, f"HTTP {code} {detail}".strip())
                else:
                    try:
                        return resp.json()
                    except Exception:
                        raise ProviderError(provider, ErrorKind.INVALID_RESPONSE, "response was not JSON")
            if attempt < attempts - 1 and last and last.retryable:
                log.warning("retrying %s (%s) attempt %d", shown, last.kind.value, attempt + 1)
                time.sleep(self.backoff * (2 ** attempt))
            else:
                break
        assert last is not None
        log.warning("call failed %s: %s", shown, last.kind.value)
        raise last
