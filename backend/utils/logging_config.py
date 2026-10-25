"""Logging with secret redaction."""
from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler

_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|access[_-]?key|token|secret|key|password)=([^&\s\"']+)"),
    re.compile(r"(?i)(bearer)\s+([A-Za-z0-9._\-]{8,})"),
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{10,}|gsk_[A-Za-z0-9]{10,})\b"),
    re.compile(r"(postgres(?:ql)?://[^:\s]+:)([^@\s]+)(@)"),
]


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str] | None = None):
        super().__init__()
        self._secrets = [s for s in (secrets or []) if s]

    def redact(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, "***")
        text = _PATTERNS[0].sub(lambda m: f"{m.group(1)}=***", text)
        text = _PATTERNS[1].sub(lambda m: f"{m.group(1)} ***", text)
        text = _PATTERNS[2].sub("***", text)
        return _PATTERNS[3].sub(lambda m: f"{m.group(1)}***{m.group(3)}", text)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = ()
        return True


def setup_logging(level: str = "INFO", log_dir: str = "logs", secrets: list[str] | None = None) -> logging.Logger:
    root = logging.getLogger("travelai")
    if getattr(root, "_configured", False):
        return root
    root.setLevel(level)
    root.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%H:%M:%S")
    flt = RedactingFilter(secrets)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        os.makedirs(log_dir, exist_ok=True)
        handlers.append(RotatingFileHandler(os.path.join(log_dir, "travelai.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(flt)
        root.addHandler(h)
    root._configured = True  # type: ignore[attr-defined]
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"travelai.{name}")
