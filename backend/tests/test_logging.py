"""Structured JSON logging (T-0021)."""

import io
import json
import logging
import sys
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import (
    REDACTED,
    JsonFormatter,
    configure_logging,
    is_sensitive_field,
    scrub,
    scrub_sentry_event,
    scrub_text,
)
from app.core.request_context import ACCESS_LOGGER_NAME, REQUEST_ID_HEADER, request_id_var
from app.main import create_app

_TOUCHED = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore")


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


def test_formatter_output_is_ascii_only() -> None:
    """Non-ASCII text is escaped, so a non-UTF-8 console cannot fail on it (T-0030)."""
    line = JsonFormatter().format(_record(msg="Prüfung %s", args=("溶接 ✓",)))
    assert line.isascii()
    assert json.loads(line)["message"] == "Prüfung 溶接 ✓"


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


def test_configure_logging_survives_a_non_utf8_console() -> None:
    """A cp1252/ASCII console must not raise on non-ASCII log text (T-0030)."""
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="ascii", errors="strict", write_through=True)
    configure_logging(Settings(environment="test"), stream=console)
    errors: list[logging.LogRecord] = []
    handler = _our_handlers()[0]
    handler.handleError = errors.append  # type: ignore[method-assign]

    logging.getLogger("kerp.synthetic").warning("Schweißnaht %s", "検査")

    assert errors == []
    assert json.loads(raw.getvalue().decode("ascii"))["message"] == "Schweißnaht 検査"


@pytest.mark.parametrize("name", ["httpx", "httpcore"])
def test_configure_logging_quiets_http_client_loggers(name: str) -> None:
    configure_logging(Settings(environment="test", log_level="DEBUG"), stream=io.StringIO())
    assert logging.getLogger(name).level == logging.WARNING


def test_outbound_request_url_with_token_is_not_logged() -> None:
    """httpx logs every request URL at INFO; query strings can carry tokens (TD-024)."""
    stream = io.StringIO()
    configure_logging(Settings(environment="test", log_level="DEBUG"), stream=stream)
    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    with httpx.Client(transport=transport) as http:
        http.get("https://partner.invalid/api?access_token=synthetic-secret-token")
    logging.getLogger("kerp.synthetic").info("marker")

    output = stream.getvalue()
    assert "marker" in output  # the handler is live at INFO/DEBUG
    assert "synthetic-secret-token" not in output
    assert "partner.invalid" not in output


def test_http_client_warnings_still_reach_the_log() -> None:
    stream = io.StringIO()
    configure_logging(Settings(environment="test"), stream=stream)
    logging.getLogger("httpx").warning("synthetic httpx warning")
    assert "synthetic httpx warning" in stream.getvalue()


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


# --- secrets are scrubbed (T-0077, diff §4.2) -------------------------------------------------


def test_credential_fields_in_extra_are_redacted_at_any_depth() -> None:
    record = _record()
    record.__dict__.update(
        token="tok-SYNTHETIC-1",
        new_password="synthetic pass 1",
        body={"Token": "tok-SYNTHETIC-2", "nested": [{"new-password": "synthetic pass 2"}]},
        user_id="u-1",
    )
    line = JsonFormatter().format(record)
    out = json.loads(line)
    assert out["token"] == out["new_password"] == REDACTED
    assert out["body"] == {"Token": REDACTED, "nested": [{"new-password": REDACTED}]}
    assert out["user_id"] == "u-1"
    for secret in ("tok-SYNTHETIC-1", "tok-SYNTHETIC-2", "synthetic pass 1", "synthetic pass 2"):
        assert secret not in line


def test_token_and_password_pairs_in_messages_and_tracebacks_are_redacted() -> None:
    message = 'confirm failed: body={"token": "tok-SYNTHETIC-3", "new_password": "pass \\" 3"}'
    out = _format(_record(message + " token=%s", ("tok-SYNTHETIC-4",)))
    assert "tok-SYNTHETIC" not in out["message"]
    assert "pass " not in out["message"]
    assert out["message"].count(REDACTED) == 3
    try:
        raise ValueError("bad request new_password='synthetic pass 5' rejected")
    except ValueError:
        record = _record()
        record.exc_info = sys.exc_info()
    assert "synthetic pass 5" not in _format(record)["exc_info"]
    record = _record()
    record.stack_info = "frame: token=tok-SYNTHETIC-6"
    assert "tok-SYNTHETIC-6" not in str(_format(record)["stack_info"])


def test_bearer_tokens_and_password_hashes_in_free_text_are_redacted() -> None:
    header = "upstream said: Authorization: Bearer eyJhbGciOiJFZERTQSJ9.c3ludGhldGlj.sig-SYNTHETIC"
    assert scrub_text(header) == f"upstream said: Authorization: Bearer {REDACTED}"
    dump = "[parameters: {'password_hash': 'scrypt$synthetic$hash', 'id': 7}]"
    assert scrub_text(dump) == f"[parameters: {{'password_hash': {REDACTED}, 'id': 7}}]"
    assert scrub_text("password_hash=scrypt$synthetic done") == f"password_hash={REDACTED} done"


def test_ordinary_text_about_tokens_and_passwords_is_left_alone() -> None:
    for text in (
        "3 tokens issued",
        "the password was changed",
        "retoken=5",
        "broken=1",
        "a bearer token is expected",
        'WWW-Authenticate: Bearer error="invalid_token"',
    ):
        assert scrub_text(text) == text
    assert scrub({"count": 3, "items": ("a", 1)}) == {"count": 3, "items": ["a", 1]}
    assert is_sensitive_field("Authorization")
    assert not is_sensitive_field(7)


def test_scrubbing_stops_at_a_fixed_depth() -> None:
    deep: dict[str, object] = {"leaf": "kept"}
    for _ in range(12):
        deep = {"inner": deep}
    assert "kept" not in json.dumps(scrub(deep))


def test_the_sentry_hook_scrubs_request_data_extra_and_breadcrumbs() -> None:
    event = {
        "request": {
            "url": "https://kerp.example.test/api/v1/auth/password-reset/confirm",
            "data": {"token": "tok-SYNTHETIC-7", "new_password": "synthetic pass 7"},
            "headers": {"Authorization": "Bearer synthetic", "Accept": "application/json"},
        },
        "extra": {"reset_token": "tok-SYNTHETIC-8"},
        "breadcrumbs": {
            "values": [{"message": "POST token=tok-SYNTHETIC-9", "data": {"pin": "1"}}]
        },
        "exception": {"values": [{"stacktrace": {"frames": [{"vars": {"new_password": "x y"}}]}}]},
    }
    scrubbed = scrub_sentry_event(event, {})
    text = json.dumps(scrubbed)
    assert "SYNTHETIC" not in text
    assert "synthetic" not in text
    assert "x y" not in text
    assert scrubbed["request"]["headers"]["Accept"] == "application/json"
    assert scrubbed["request"]["url"].endswith("/confirm")
    assert event["request"]["data"]["token"] == "tok-SYNTHETIC-7"  # the input is not mutated
