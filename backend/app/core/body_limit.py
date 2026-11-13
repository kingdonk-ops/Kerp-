"""App-wide request body limit (pure ASGI middleware, T-0060).

No endpoint accepts an unbounded body. A request whose ``Content-Length`` is above
``KERP_MAX_BODY_BYTES`` is answered ``413`` before the app runs; a body without one
(chunked) is counted as the app reads it and refused as soon as it passes the limit.

File bytes never pass through the API: uploads go straight to object storage with a
presigned ``PUT`` (``app/modules/uploads``), so this limit does not cap file sizes.
"""

from typing import Final

from fastapi import HTTPException, status
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_MAX_BODY_BYTES: Final = 1024 * 1024
MIN_MAX_BODY_BYTES: Final = 1024
DETAIL: Final = "Request body too large"


class RequestBodyTooLarge(HTTPException):
    """Raised from ``receive`` once the body passes the limit.

    An ``HTTPException`` so FastAPI's body parsing re-raises it as-is (any other exception
    becomes a 400) and the app's exception middleware renders it as a ``413``.
    """

    def __init__(self) -> None:
        super().__init__(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=DETAIL)


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None  # the server rejects it; otherwise the counter still applies
    return None


class BodyLimitMiddleware:
    """Refuse request bodies larger than ``max_bytes`` with ``413``."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and declared > self.max_bytes:
            await self._refuse(scope, receive, send)
            return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestBodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except RequestBodyTooLarge:
            if started:  # too late for a 413; let the server close the connection
                raise
            await self._refuse(scope, receive, send)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"detail": DETAIL}, status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        await response(scope, receive, send)
