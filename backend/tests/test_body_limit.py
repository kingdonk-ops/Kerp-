"""App-wide request body limit: ``KERP_MAX_BODY_BYTES``, 413 above it (T-0060).

Checked twice: a declared ``Content-Length`` above the limit is refused before the app runs,
and a body without one (chunked) is counted as it is read and refused once it passes the
limit. File bytes never pass through the API (presigned ``PUT`` to object storage), so the
limit does not cap uploads; ``tests/test_uploads.py`` pins that.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.types import Message, Receive, Scope, Send

from app.core.body_limit import DEFAULT_MAX_BODY_BYTES, BodyLimitMiddleware
from app.core.config import Settings
from app.main import create_app

LIMIT = 2048
TOO_LARGE = {"detail": "Request body too large"}


class Echo(BaseModel):
    text: str


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(Settings(environment="test", max_body_bytes=LIMIT))

    @app.post("/_probe/echo")
    def echo(body: Echo) -> dict[str, int]:
        return {"length": len(body.text)}

    @app.post("/_probe/raw")
    async def raw(request: Request) -> dict[str, int]:
        return {"length": len(await request.body())}

    with TestClient(app) as test_client:
        yield test_client


def _json_of_size(size: int) -> bytes:
    body = b'{"text": "' + b"x" * (size - len(b'{"text": ""}')) + b'"}'
    assert len(body) == size
    return body


def test_the_default_is_one_mebibyte_and_the_env_var_overrides_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KERP_MAX_BODY_BYTES", raising=False)
    assert Settings().max_body_bytes == DEFAULT_MAX_BODY_BYTES == 1024 * 1024
    monkeypatch.setenv("KERP_MAX_BODY_BYTES", "4096")
    assert Settings().max_body_bytes == 4096


@pytest.mark.parametrize("value", [0, 1023])
def test_a_limit_too_small_for_any_api_call_is_rejected(value: int) -> None:
    with pytest.raises(ValueError, match="greater than or equal to 1024"):
        Settings(max_body_bytes=value)


def test_a_body_at_the_limit_is_accepted(client: TestClient) -> None:
    headers = {"Content-Type": "application/json"}
    response = client.post("/_probe/echo", content=_json_of_size(LIMIT), headers=headers)
    assert response.status_code == 200
    assert response.json() == {"length": LIMIT - len('{"text": ""}')}


@pytest.mark.parametrize("path", ["/_probe/echo", "/_probe/raw", "/does-not-exist"])
def test_a_declared_length_above_the_limit_is_413(client: TestClient, path: str) -> None:
    headers = {"Content-Type": "application/json"}
    response = client.post(path, content=_json_of_size(LIMIT + 1), headers=headers)
    assert response.status_code == 413
    assert response.json() == TOO_LARGE
    # The 413 still goes through the request-context and security-header middleware.
    assert response.headers["x-request-id"]
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/_probe/echo", "/_probe/raw"])
def test_a_chunked_body_above_the_limit_is_413(client: TestClient, path: str) -> None:
    def chunks() -> Iterator[bytes]:  # no Content-Length: the body is streamed
        body = _json_of_size(LIMIT * 4)
        for start in range(0, len(body), 512):
            yield body[start : start + 512]

    headers = {"Content-Type": "application/json"}
    response = client.post(path, content=chunks(), headers=headers)
    assert response.status_code == 413
    assert response.json() == TOO_LARGE


def test_a_chunked_body_within_the_limit_is_accepted(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"text": '
        yield b'"abc"}'

    headers = {"Content-Type": "application/json"}
    response = client.post("/_probe/echo", content=chunks(), headers=headers)
    assert response.json() == {"length": 3}


def test_a_bodyless_request_is_unaffected(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


# --- the middleware on its own (ASGI edge cases) ---------------------------------------------


def _call(
    middleware: BodyLimitMiddleware, scope: dict[str, Any], messages: list[Message]
) -> list[Message]:
    sent: list[Message] = []

    async def receive() -> Message:
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    return sent


def _http(headers: list[tuple[bytes, bytes]] | None = None) -> dict[str, Any]:
    return {"type": "http", "method": "POST", "path": "/", "headers": headers or []}


async def _read_all(scope: Scope, receive: Receive, send: Send) -> None:
    more = True
    while more:
        message = await receive()
        more = message.get("more_body", False)
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def test_non_http_scopes_pass_through() -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    middleware = BodyLimitMiddleware(app, max_bytes=1)
    _call(middleware, {"type": "lifespan"}, [])
    assert seen == ["lifespan"]


def test_a_malformed_content_length_is_counted_as_read() -> None:
    middleware = BodyLimitMiddleware(_read_all, max_bytes=4)
    scope = _http([(b"content-length", b"not-a-number")])
    sent = _call(middleware, scope, [{"type": "http.request", "body": b"12345"}])
    assert sent[0]["status"] == 413


def test_an_overflow_after_the_response_started_is_not_masked() -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await receive()  # a streaming endpoint that reads after it started answering

    middleware = BodyLimitMiddleware(app, max_bytes=4)
    with pytest.raises(Exception, match="too large"):
        _call(middleware, _http(), [{"type": "http.request", "body": b"12345"}])
