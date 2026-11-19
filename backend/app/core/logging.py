"""Structured (one JSON object per line) logging on top of stdlib ``logging``.

Every line has ``timestamp`` (UTC, ISO 8601), ``level``, ``logger``, ``message`` and
``request_id`` (from the record, else the current request context, else ``null``).
Fields passed via ``extra=`` are added as top-level keys.

**Secrets are scrubbed** (T-0077, diff §4.2): the value of any field named like a credential
(``SENSITIVE_FIELDS``: ``token``, ``new_password``, ``password``, ...) is replaced by
``[redacted]`` wherever it sits in ``extra=`` (nested dicts and lists included), and
``token=...`` / ``"new_password": "..."`` pairs inside the message or a traceback are
redacted too. ``scrub_sentry_event`` applies the same rule to a Sentry event
(``before_send``), for the day an error tracker is wired in.
"""

import json
import logging
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import IO, Any, Final

from app.core.config import Settings
from app.core.request_context import request_id_var

_CORE_FIELDS = ("timestamp", "level", "logger", "message", "request_id")

# HTTP client libraries log every outbound request URL (query string included, which can
# carry tokens) at INFO; only their warnings and errors are let through (TD-024).
_QUIET_LOGGERS = ("httpx", "httpcore")

# Attributes every LogRecord has; anything else on a record came from ``extra=``.
_BLANK_RECORD = logging.LogRecord("", logging.INFO, "", 0, "", (), None)
_RECORD_ATTRS = frozenset(vars(_BLANK_RECORD)) | {"message", "asctime", "taskName"}


REDACTED: Final = "[redacted]"
# Field names whose value is a credential, compared lower-case with ``-`` as ``_``.
SENSITIVE_FIELDS: Final = frozenset(
    {
        "token",
        "new_password",
        "password",
        "current_password",
        "refresh_token",
        "access_token",
        "id_token",
        "reset_token",
        "invite_token",
        "setup_token",
        "wipe_secret",
        "client_secret",
        "pin",
        "authorization",
        "cookie",
        "set_cookie",
    }
)
_MAX_SCRUB_DEPTH: Final = 8
# ``token=abc``, ``token: abc``, ``"new_password": "abc"``, ``'token': 'abc'`` in free text.
# A quoted value runs to its closing quote; an unquoted one to the next separator.
_TEXT_SECRET: Final = re.compile(
    r"""(?P<key>(?<![A-Za-z0-9])(?:new_password|current_password|password|refresh_token|
        access_token|id_token|reset_token|invite_token|setup_token|wipe_secret|client_secret|
        token)["']?\s*[:=]\s*)
        (?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\s,;&)}\]]+)""",
    re.IGNORECASE | re.VERBOSE,
)


def is_sensitive_field(name: object) -> bool:
    return isinstance(name, str) and name.lower().replace("-", "_") in SENSITIVE_FIELDS


def scrub_text(text: str) -> str:
    """``text`` with the value of every ``token=...`` / ``"new_password": "..."`` pair
    replaced by ``[redacted]``."""
    return _TEXT_SECRET.sub(lambda match: f"{match.group('key')}{REDACTED}", text)


def scrub(value: object, *, _depth: int = 0) -> object:
    """A copy of ``value`` in which every credential-named field of a mapping (at any depth,
    through lists and tuples) is ``[redacted]``, and strings are passed through
    ``scrub_text``."""
    if _depth >= _MAX_SCRUB_DEPTH:
        return REDACTED if isinstance(value, Mapping | list | tuple) else value
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, Mapping):
        return {
            key: REDACTED if is_sensitive_field(key) else scrub(item, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [scrub(item, _depth=_depth + 1) for item in value]
    return value


def scrub_sentry_event(event: dict[str, Any], _hint: object = None) -> dict[str, Any]:
    """A Sentry ``before_send`` / ``before_breadcrumb`` hook: the event with every
    credential-named field (request body, extra, contexts, breadcrumb data, frame locals)
    redacted. KERP has no error tracker yet; whoever adds one must install this hook."""
    return {
        key: REDACTED if is_sensitive_field(key) else scrub(item) for key, item in event.items()
    }


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
            "message": scrub_text(record.getMessage()),
            "request_id": request_id,
        }
        for key, value in record.__dict__.items():
            if key not in _RECORD_ATTRS and key not in _CORE_FIELDS:
                payload[key] = REDACTED if is_sensitive_field(key) else scrub(value)
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
