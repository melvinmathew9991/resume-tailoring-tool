"""Structured logging with PII redaction.

Two things matter here beyond "logs exist":

1. **Correlation.** Every log line emitted while handling a request carries the
   same ``request_id``, so a compile failure can be traced back to the exact
   call that caused it.
2. **Redaction.** This application handles a real person's email, phone number
   and address. Those must never land in a log file, which is the kind of thing
   that is trivial to add on day one and impossible to retrofit after the logs
   have already been written.
"""

from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

import structlog

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+\d{1,3}[\s-]?)?(?:\d[\s-]?){9,14}\d(?!\w)")
_REDACTED = "[redacted]"

_SENSITIVE_KEYS = frozenset(
    {"email", "phone", "location", "name", "api_key", "authorization", "x-api-key"}
)


def _redact_text(value: str) -> str:
    value = _EMAIL_RE.sub(_REDACTED, value)
    return _PHONE_RE.sub(_REDACTED, value)


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {
            key: (_REDACTED if str(key).lower() in _SENSITIVE_KEYS else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_redact(item) for item in value)
    return value


def redact_pii(
    _logger: object, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """structlog processor: strip emails/phones and blank sensitive keys."""
    return {
        key: (_REDACTED if str(key).lower() in _SENSITIVE_KEYS else _redact(value))
        for key, value in event_dict.items()
    }


def add_request_id(
    _logger: object, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    event_dict["request_id"] = request_id_var.get()
    return event_dict


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    """Idempotent logging setup. Safe to call from tests and from lifespan."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_request_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_pii,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
