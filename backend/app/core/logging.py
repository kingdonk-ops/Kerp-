"""Structured (one JSON object per line) logging on top of stdlib ``logging``.

Every line has ``timestamp`` (UTC, ISO 8601), ``level``, ``logger``, ``message`` and
``request_id`` (from the record, else the current request context, else ``null``).
Fields passed via ``extra=`` are added as top-level keys.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import IO, Any

from app.core.config import Settings
from app.core.request_context import request_id_var

_CORE_FIELDS = ("timestamp", "level", "logger", "message", "request_id")

# Attributes every LogRecord has; anything else on a record came from ``extra=``.
_BLANK_RECORD = logging.LogRecord("", logging.INFO, "", 0, "", (), None)
_RECORD_ATTRS = frozenset(vars(_BLANK_RECORD)) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Render a log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        request_id = record.__dict__.get("request_id", request_id_var.get())
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id,
        }
        for key, value in record.__dict__.items():
            if key not in _RECORD_ATTRS and key not in _CORE_FIELDS:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class _KerpJsonHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Marker type so ``configure_logging`` can replace its own handler idempotently."""


def configure_logging(settings: Settings, *, stream: IO[str] | None = None) -> None:
    """Send all logs to one JSON handler on the root logger (default: stdout).

    Idempotent; handlers installed by others (e.g. pytest's caplog) are left alone.
    uvicorn's loggers are routed through the root logger, and uvicorn's own access log
    is disabled because it prints raw query strings; ``kerp.access`` replaces it.
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
