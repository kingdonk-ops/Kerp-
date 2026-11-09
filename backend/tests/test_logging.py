"""Structured JSON logging (T-0021)."""

import io
import json
import logging
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import JsonFormatter, configure_logging
from app.core.request_context import ACCESS_LOGGER_NAME, REQUEST_ID_HEADER, request_id_var
from app.main import create_app

_TOUCHED = ("", "uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Snapshot and restore the global logging state that ``configure_logging`` changes."""
    saved = {}
    for name in _TOUCHED:
        lg = logging.getLogger(name)
        saved[name] = (list(lg.handlers), lg.level, lg.propagate, lg.disabled)
    yield
    for name, (handlers, level, propagate, disabled) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate
        lg.disabled = disabled


def _record(msg: str = "hello %s", args: tuple[object, ...] = ("world",)) -> logging.LogRecord:
    return logging.LogRecord(
        name="kerp.synthetic",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def _format(record: logging.LogRecord) -> dict[str, object]:
    out: dict[str, object] = json.loads(JsonFormatter().format(record))
    return out


# --- formatter --------------------------------------------------------------


def test_formatter_emits_required_fields() -> None:
    out = _format(_record())
    assert out["message"] == "hello world"
    assert out["level"] == "WARNING"
    assert out["logger"] == "kerp.synthetic"
    assert out["request_id"] is None
    timestamp = str(out["timestamp"])
    assert timestamp.endswith("Z")
    assert "T" in timestamp


def test_formatter_picks_up_request_id_from_context() -> None:
    token = request_id_var.set("synthetic-rid")
    try:
        out = _format(_record())
    finally:
        request_id_var.reset(token)
    assert out["request_id"] == "synthetic-rid"


def test_formatter_prefers_request_id_set_on_record() -> None:
    record = _record()
    record.request_id = "explicit"
    assert _format(record)["request_id"] == "explicit"


def test_formatter_includes_extra_fields_and_stringifies_unknown_types() -> None:
    marker = object()
    record = _record()
    record.status = 201
    record.thing = marker
    out = _format(record)
    assert out["status"] == 201
    assert out["thing"] == str(marker)


def test_formatter_does_not_let_extras_clobber_core_fields() -> None:
    record = _record()
    record.__dict__["level"] = "FAKE"
    record.__dict__["timestamp"] = "FAKE"
    out = _format(record)
    assert out["level"] == "WARNING"
    assert out["timestamp"] != "FAKE"


def test_formatter_omits_stdlib_record_attributes() -> None:
    out = _format(_record())
    for internal in ("args", "msg", "pathname", "lineno", "levelno", "created", "taskName"):
        assert internal not in out


def test_formatter_renders_exception() -> None:
    record = _record()
    try:
        raise ValueError("synthetic-exc")
    except ValueError:
        record.exc_info = sys.exc_info()
    assert "ValueError: synthetic-exc" in str(_format(record)["exc_info"])


def test_formatter_renders_stack_info() -> None:
    record = _record()
    record.stack_info = "Stack (most recent call last):\n  synthetic"
    assert "synthetic" in str(_format(record)["stack_info"])


def test_formatter_output_is_single_line() -> None:
    line = JsonFormatter().format(_record(msg="multi\nline", args=()))
    assert "\n" not in line


# --- configure_logging ------------------------------------------------------


def _our_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if isinstance(h.formatter, JsonFormatter)]


def test_configure_logging_installs_single_json_handler() -> None:
    stream = io.StringIO()
    settings = Settings(environment="test", log_level="DEBUG")
    configure_logging(settings, stream=stream)
    configure_logging(settings, stream=stream)  # idempotent

    assert len(_our_handlers()) == 1
    assert logging.getLogger().level == logging.DEBUG

    logging.getLogger("kerp.synthetic").debug("ping")
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert lines[-1]["message"] == "ping"


def test_configure_logging_keeps_foreign_handlers() -> None:
    foreign = logging.NullHandler()
    logging.getLogger().addHandler(foreign)
    configure_logging(Settings(environment="test"), stream=io.StringIO())
    assert foreign in logging.getLogger().handlers


def test_configure_logging_routes_uvicorn_through_root_and_silences_its_access_log() -> None:
    logging.getLogger("uvicorn.error").addHandler(logging.StreamHandler(io.StringIO()))
    logging.getLogger("uvicorn.error").propagate = False
    configure_logging(Settings(environment="test"), stream=io.StringIO())

    error = logging.getLogger("uvicorn.error")
    assert error.handlers == []
    assert error.propagate is True
    # uvicorn's own access log prints the raw query string; ours replaces it.
    assert logging.getLogger("uvicorn.access").disabled is True


def test_create_app_configures_logging() -> None:
    create_app(Settings(environment="test", log_level="WARNING"))
    assert logging.getLogger().level == logging.WARNING
    assert len(_our_handlers()) == 1


def test_end_to_end_access_line_is_json_with_request_id() -> None:
    stream = io.StringIO()
    app = create_app(Settings(environment="test"))
    configure_logging(app.state.settings, stream=stream)
    with TestClient(app) as c:
        rid = c.get("/health", headers={REQUEST_ID_HEADER: "synthetic-e2e"}).headers[
            REQUEST_ID_HEADER
        ]
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    access = [entry for entry in lines if entry["logger"] == ACCESS_LOGGER_NAME]
    assert len(access) == 1
    assert access[0]["request_id"] == rid == "synthetic-e2e"
    assert access[0]["method"] == "GET"
    assert access[0]["path"] == "/health"
    assert access[0]["status"] == 200
    assert isinstance(access[0]["duration_ms"], float)
    assert access[0]["message"] == "GET /health 200"


# --- settings ---------------------------------------------------------------


@pytest.mark.parametrize("raw", ["info", "Info", "INFO"])
def test_log_level_is_case_insensitive(raw: str) -> None:
    assert Settings(log_level=raw).log_level == "INFO"  # type: ignore[arg-type]


def test_log_level_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERP_LOG_LEVEL", "debug")
    assert Settings().log_level == "DEBUG"


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(log_level="LOUD")  # type: ignore[arg-type]
