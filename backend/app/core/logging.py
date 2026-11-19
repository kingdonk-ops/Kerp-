"""Structured (one JSON object per line) logging on top of stdlib ``logging``.

Every line has ``timestamp`` (UTC, ISO 8601), ``level``, ``logger``, ``message`` and
``request_id`` (from the record, else the current request context, else ``null``).
Fields passed via ``extra=`` are added as top-level keys.

**Secret fields are scrubbed** (T-0052, diff §4.1 Invites): a field named in
``SCRUBBED_FIELDS`` (``invite_token``, ``setup_token``) never reaches a log line. ``scrub``
redacts it in an ``extra=`` value, however deeply nested, and ``scrub_text`` redacts
``invite_token=...`` / ``"invite_token": "..."`` inside a message or a traceback. The request
log never contains bodies or query strings in the first place (``request_context``); this is
the second line of defence. An error reporter (Sentry) must pass its events through ``scrub``
in ``before_send`` when one is added.
"""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import IO, Any

from app.core.config import Settings
from app.core.request_context import request_id_var

_CORE_FIELDS = ("timestamp", "level", "logger", "message", "request_id")

# HTTP client libraries log every outbound request URL (query string included, which can
# carry tokens) at INFO; only their warnings and errors are let through (TD-024).
_QUIET_LOGGERS = ("httpx", "httpcore")

# Attributes every LogRecord has; anything else on a record came from ``extra=``.
_BLANK_RECORD = logging.LogRecord("", logging.INFO, "", 0, "", (), None)
_RECORD_ATTRS = frozenset(vars(_BLANK_RECORD)) | {"message", "asctime", "taskName"}


# Body fields that carry a secret a log line (or an error report) must never show.
SCRUBBED_FIELDS = frozenset({"invite_token", "setup_token"})
REDACTED = "[redacted]"
_SCRUB_TEXT = re.compile(
    r"""(?P<name>\b(?:"""
    + "|".join(sorted(SCRUBBED_FIELDS))
    + r""")\b\\?["']?\s*[=:]\s*\\?["']?)[^\s"'\\&,;)}\]]+"""
)


def scrub_text(text: str) -> str:
    """``text`` with the value of every ``invite_token=...`` / ``"invite_token": "..."``
    (any ``SCRUBBED_FIELDS`` name) replaced by ``[redacted]``."""
    return _SCRUB_TEXT.sub(lambda match: match.group("name") + REDACTED, text)


def scrub(value: object) -> object:
    """``value`` with every ``SCRUBBED_FIELDS`` key redacted, at any depth, and every string
    passed through ``scrub_text``. Mappings and sequences are copied, never changed."""
    if isinstance(value, dict):
        return {
            key: REDACTED if key in SCRUBBED_FIELDS else scrub(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


class JsonFormatter(logging.Formatter):
    """Render a log record as a single-line JSON object (secret fields scrubbed)."""

    def format(self, record: logging.LogRecord) -> str:
        request_id = record.__dict__.get("request_id", request_id_var.get())
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": scrub_text(record.getMessage()),
            "request_id": request_id,
        }
        for key, value in record.__dict__.items():
            if key not in _RECORD_ATTRS and key not in _CORE_FIELDS:
                payload[key] = REDACTED if key in SCRUBBED_FIELDS else scrub(value)
        if record.exc_info:
            payload["exc_info"] = scrub_text(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack_info"] = scrub_text(self.formatStack(record.stack_info))
        # ASCII-only (non-ASCII escaped) so a non-UTF-8 console can never fail to write a line.
        return json.dumps(payload, default=str, ensure_ascii=True)


class _KerpJsonHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Marker type so ``configure_logging`` can replace its own handler idempotently."""


def configure_logging(settings: Settings, *, stream: IO[str] | None = None) -> None:
    """Send all logs to one JSON handler on the root logger (default: stdout).

    Idempotent; handlers installed by others (e.g. pytest's caplog) are left alone.
    uvicorn's loggers are routed through the root logger, and uvicorn's own access log
    is disabled because it prints raw query strings; ``kerp.access`` replaces it.
    ``httpx``/``httpcore`` are capped at WARNING so outbound URLs are never logged (TD-024).
    """
    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h, _KerpJsonHandler)]:
        root.removeHandler(handler)
    handler = _KerpJsonHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    for name in ("uvicorn", "uvicorn.error"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers.clear()
    uvicorn_access.propagate = False
    uvicorn_access.disabled = True

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
