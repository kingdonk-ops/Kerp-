"""Security response headers (pure ASGI middleware).

Headers a route has already set are kept (never overwritten or duplicated).
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

HSTS_VALUE = "max-age=31536000; includeSubDomains"

# The interactive API docs pages (Swagger UI / ReDoc) are the only HTML the API serves.
# They load their assets from FastAPI's default CDNs and use inline bootstrap scripts.
DOCS_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
    "connect-src 'self'; "
    "worker-src blob:; "
    "frame-ancestors 'none'"
)


def _header_list(values: dict[str, str]) -> list[tuple[bytes, bytes]]:
    return [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in values.items()]


class SecurityHeadersMiddleware:
    """Add CSP, HSTS (optional), ``nosniff``, ``DENY`` framing and ``no-referrer``."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        content_security_policy: str,
        hsts: bool,
        docs_paths: frozenset[str] = frozenset(),
    ) -> None:
        self.app = app
        self.docs_paths = docs_paths
        common = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        }
        if hsts:
            common["Strict-Transport-Security"] = HSTS_VALUE
        self._api_headers = _header_list(
            {**common, "Content-Security-Policy": content_security_policy}
        )
        self._docs_headers = _header_list(
            {**common, "Content-Security-Policy": DOCS_CONTENT_SECURITY_POLICY}
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        extra = self._docs_headers if scope.get("path") in self.docs_paths else self._api_headers

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                headers.extend((k, v) for k, v in extra if k not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
