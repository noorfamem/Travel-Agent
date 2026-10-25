"""Guarantee a working TLS CA bundle for requests on every OS (especially Windows).

Priority:
1. truststore  — uses the OS certificate store (best on Windows 11 / macOS).
2. certifi      — ships its own CA bundle; always works, slightly older root set.

Both methods are safe to mix: whichever installs first wins.
The function is idempotent and called at import time from HttpClient.__init__.
"""
from __future__ import annotations

import os

_installed = False


def install_system_certs() -> None:
    """Inject a working CA bundle into ssl / requests. Called once at startup."""
    global _installed
    if _installed:
        return

    # ── Attempt 1: truststore (OS native store) ──────────────────────────────
    try:
        import truststore
        truststore.inject_into_ssl()
        _installed = True
        return
    except Exception:
        pass  # not installed or not supported on this OS build

    # ── Attempt 2: certifi (bundled CA file) ─────────────────────────────────
    try:
        import certifi
        ca_bundle = certifi.where()
        # Tell both requests and the stdlib ssl module where the certs are.
        os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_bundle)
        os.environ.setdefault("SSL_CERT_FILE", ca_bundle)
        _installed = True
        return
    except Exception:
        pass

    # If neither works we leave _installed = False so the next call retries.
    # requests will fall back to its own (possibly broken) bundle — still better
    # than crashing at import time.
