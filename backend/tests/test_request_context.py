"""Request id propagation, access log and unhandled-error handling (T-0021)."""

import logging
import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.request_context import (
    ACCESS_LOGGER_NAME,
    REQUEST_ID_HEADER,
    get_request_id,
    is_valid_request_id,
)
from app.main import create_app


def _app_with_probe_routes(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/_probe/request-id")
    def probe_request_id() -> dict[str, str | None]:
        return {"request_id": get_request_id()}

    @app.get("/_probe/boom")
    def probe_boom() -> None:
        raise RuntimeError("synthetic failure")

    @app.get("/_probe/stream-boom")
    def probe_stream_boom() -> StreamingResponse:
        def body() -> Iterator[bytes]:
            yield b"partial"
            raise RuntimeError("synthetic mid-stream failure")

        return StreamingResponse(body())

    @app.websocket("/_probe/ws")
    async def probe_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text(get_request_id() or "none")
        await websocket.close()

    return app


@pytest.fixture
def probe_client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(_app_with_probe_routes(settings)) as c:
        yield c


def _access_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == ACCESS_LOGGER_NAME]


# --- request id -------------------------------------------------------------


def test_header_name_is_canonical() -> None:
    assert REQUEST_ID_HEADER == "X-Request-ID"


def test_response_carries_generated_uuid4_request_id(client: TestClient) -> None:
    rid = client.get("/health").headers[REQUEST_ID_HEADER]
    assert uuid.UUID(rid).version == 4


def test_each_request_gets_a_distinct_id(client: TestClient) -> None:
    first = client.get("/health").headers[REQUEST_ID_HEADER]
    second = client.get("/health").headers[REQUEST_ID_HEADER]
    assert first != second


def test_sane_incoming_request_id_is_honoured(probe_client: TestClient) -> None:
    incoming = "synthetic-Req.42_abc"
    response = probe_client.get("/_probe/request-id", headers={REQUEST_ID_HEADER: incoming})
    assert response.headers[REQUEST_ID_HEADER] == incoming
    assert response.json() == {"request_id": incoming}


@pytest.mark.parametrize(
    "incoming",
    ["", "a" * 129, "has space", "semi;colon", "tab\there", "<script>", "café"],
)
def test_insane_incoming_request_id_is_replaced(probe_client: TestClient, incoming: str) -> None:
    raw = incoming.encode("utf-8")  # raw bytes on the wire
    response = probe_client.get("/_probe/request-id", headers={REQUEST_ID_HEADER: raw})
    rid = response.headers[REQUEST_ID_HEADER]
    assert rid != incoming
    assert uuid.UUID(rid).version == 4
    assert response.json() == {"request_id": rid}


def test_request_id_in_handler_matches_response(probe_client: TestClient) -> None:
    response = probe_client.get("/_probe/request-id")
    assert response.json() == {"request_id": response.headers[REQUEST_ID_HEADER]}


def test_request_id_is_unset_outside_a_request(probe_client: TestClient) -> None:
    probe_client.get("/_probe/request-id")
    assert get_request_id() is None


def test_request_id_on_404(client: TestClient) -> None:
    assert REQUEST_ID_HEADER in client.get("/does-not-exist").headers


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("abc", True),
        ("A.b_c-1", True),
        ("x" * 128, True),
        ("x" * 129, False),
        ("", False),
        ("a b", False),
        ("a/b", False),
        ("a\nb", False),
        ("abc\n", False),
    ],
)
def test_is_valid_request_id(value: str, valid: bool) -> None:
    assert is_valid_request_id(value) is valid


def test_websocket_scope_passes_through_untouched(probe_client: TestClient) -> None:
    with probe_client.websocket_connect("/_probe/ws") as conn:
        assert conn.receive_text() == "none"


# --- access log -------------------------------------------------------------


def test_one_access_log_line_per_request(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER_NAME)
    response = client.get("/health")

    (record,) = _access_records(caplog)
    assert record.levelno == logging.INFO
    assert record.__dict__["method"] == "GET"
    assert record.__dict__["path"] == "/health"
    assert record.__dict__["status"] == 200
    duration = record.__dict__["duration_ms"]
    assert isinstance(duration, float)
    assert duration >= 0
    assert record.__dict__["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_logs_never_contain_query_string_authorization_or_cookies(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client.get(
        "/health?token=synthetic-secret",
        headers={"Authorization": "Bearer synthetic-bearer", "Cookie": "s=synthetic-cookie"},
    )
    assert _access_records(caplog)
    # The "httpx" logger is the test client's own outbound log, not the server's.
    server_records = [r for r in caplog.records if not r.name.startswith("httpx")]
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in server_records)
    for secret in ("synthetic-secret", "synthetic-bearer", "synthetic-cookie", "token="):
        assert secret not in text


def test_access_log_records_404_status(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER_NAME)
    client.get("/does-not-exist")
    (record,) = _access_records(caplog)
    assert record.__dict__["status"] == 404


# --- unhandled errors -------------------------------------------------------


def test_unhandled_error_returns_generic_500_with_request_id_and_is_logged(
    probe_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    response = probe_client.get("/_probe/boom", headers={REQUEST_ID_HEADER: "synthetic-err-1"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    assert response.headers[REQUEST_ID_HEADER] == "synthetic-err-1"
    assert "synthetic failure" not in response.text

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert errors[0].__dict__["request_id"] == "synthetic-err-1"

    (access,) = _access_records(caplog)
    assert access.__dict__["status"] == 500


def test_unhandled_error_in_debug_mode_falls_through_to_debug_page() -> None:
    app = _app_with_probe_routes(Settings(environment="test", debug=True))
    with TestClient(app, raise_server_exceptions=False) as c:
        response = c.get("/_probe/boom")
    assert response.status_code == 500
    assert "synthetic failure" in response.text  # Starlette's traceback page


def test_error_after_response_started_is_reraised_and_logged(
    probe_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER_NAME)
    with pytest.raises(RuntimeError, match="mid-stream"):
        probe_client.get("/_probe/stream-boom")
    (access,) = _access_records(caplog)
    assert access.__dict__["status"] == 200
