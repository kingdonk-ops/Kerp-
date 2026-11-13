"""Per-request context: request id, access log and last-resort error handling.

Pure ASGI middleware (not ``BaseHTTPMiddleware``) so streaming responses, background
tasks and context variables behave normally.
"""

import asyncio
import logging
import re
import time
import uuid
from contextvars import ContextVar

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
ACCESS_LOGGER_NAME = "kerp.access"
# Logged status when the client went away before the request finished (nginx convention).
CLIENT_CLOSED_REQUEST = 499

_REQUEST_ID_HEADER_KEY = REQUEST_ID_HEADER.lower().encode("latin-1")
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")

request_id_var: ContextVar[str | None] = ContextVar("kerp_request_id", default=None)

access_logger = logging.getLogger(ACCESS_LOGGER_NAME)
error_logger = logging.getLogger("kerp.error")


def get_request_id() -> str | None:
    """Id of the request currently being handled, or ``None`` outside a request."""
    return request_id_var.get()


def is_valid_request_id(value: str) -> bool:
    """Accept caller-supplied ids only if short and made of ``[A-Za-z0-9._-]``."""
    return _REQUEST_ID_RE.fullmatch(value) is not None


def _incoming_request_id(scope: Scope) -> str | None:
    for key, raw in scope.get("headers", ()):
        if key == _REQUEST_ID_HEADER_KEY:
            value = bytes(raw).decode("latin-1")
            return value if is_valid_request_id(value) else None
    return None


class RequestContextMiddleware:
    """Assign a request id, echo it as ``X-Request-ID``, and write one access-log line.

    Only the method, the path (never the query string) and the status are logged;
    request headers (``Authorization``, cookies) are never read or logged.

    An exception raised before the response has started is logged with its traceback
    and answered with a generic JSON 500 that still carries the request id. In debug
    mode it is re-raised instead so Starlette's traceback page is shown.

    A cancelled request (``asyncio.CancelledError``: the client disconnected) is logged
    with status 499 and ``disconnected=true``, never as a 500, and the cancellation propagates.
    """

    def __init__(self, app: ASGIApp, *, debug: bool = False) -> None:
        self.app = app
        self.debug = debug

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or str(uuid.uuid4())
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500
        response_started = False
        disconnected = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != _REQUEST_ID_HEADER_KEY
                ]
                headers.append((_REQUEST_ID_HEADER_KEY, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except asyncio.CancelledError:
            # The client disconnected (or the server is shutting down): not a server error.
            disconnected = True
            raise
        except Exception:
            error_logger.exception("unhandled exception", extra={"request_id": request_id})
            if response_started or self.debug:
                raise
            response = JSONResponse({"detail": "Internal Server Error"}, status_code=500)
            await response(scope, receive, send_with_request_id)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            method = scope.get("method", "")
            path = scope.get("path", "")
            extra: dict[str, object] = {}
            if disconnected:
                status = CLIENT_CLOSED_REQUEST
                extra["disconnected"] = True
            access_logger.info(
                "%s %s %s",
                method,
                path,
                status,
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status": status,
                    "duration_ms": duration_ms,
                    **extra,
                },
            )
            request_id_var.reset(token)
