"""Structured logging with secret redaction.

All log output goes through structlog. Standard-library loggers (uvicorn, sqlalchemy, alembic)
are routed through the same processor chain so every line has the same shape.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from typing import Any

import structlog
from structlog.types import EventDict, Processor

REDACTED = "***REDACTED***"

_SENSITIVE_KEY = re.compile(
    r"(authorization|api[_-]?key|secret|password|passwd|token|cookie|credential|private[_-]?key)",
    re.IGNORECASE,
)
# Credentials embedded in URLs, e.g. postgresql://user:pass@host/db
_URL_CREDENTIALS = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^:/@\s]*):[^@/\s]+@"
)
_BEARER = re.compile(r"(?i)\b(bearer|negotiate)\s+[A-Za-z0-9._~+/=-]+")
# OpenLLM-Eval API tokens, wherever they appear.
_API_TOKEN = re.compile(r"\boel_[A-Za-z0-9_-]{16,}")


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY.search(key))


def redact_text(text: str) -> str:
    text = _URL_CREDENTIALS.sub(lambda m: f"{m['scheme']}{m['user']}:{REDACTED}@", text)
    text = _BEARER.sub(lambda m: f"{m[1]} {REDACTED}", text)
    return _API_TOKEN.sub(f"oel_{REDACTED}", text)


def redact_value(value: Any) -> Any:
    """Recursively redact sensitive keys in mappings and credentials in strings."""
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and is_sensitive_key(k) else redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(redact_value(v) for v in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_processor(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    return redact_value(event_dict)  # type: ignore[no-any-return]


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_processor,
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    # Access logs are replaced by the request-logging middleware.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # httpx logs every outgoing request at INFO; adapters emit their own structured line.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)
