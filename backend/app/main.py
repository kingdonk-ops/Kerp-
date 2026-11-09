"""Application factory.

Run with: ``uvicorn app.main:create_app --factory``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.logging import configure_logging
from app.core.request_context import RequestContextMiddleware
from app.core.security_headers import SecurityHeadersMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI app. Uses environment settings unless ``settings`` is given."""
    if settings is None:
        settings = get_settings()

    configure_logging(settings)

    database = Database(settings.database_url) if settings.database_url else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if database is not None:
            database.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = database

    # Middleware added last runs outermost: security headers wrap the request-context
    # middleware so they are also applied to the generic 500 it produces.
    app.add_middleware(RequestContextMiddleware, debug=settings.debug)
    docs_paths = {app.docs_url, app.redoc_url, app.swagger_ui_oauth2_redirect_url}
    app.add_middleware(
        SecurityHeadersMiddleware,
        content_security_policy=settings.content_security_policy,
        hsts=settings.hsts_active,
        docs_paths=frozenset(p for p in docs_paths if p),
    )

    app.include_router(health_router)
    # Domain modules (app/modules/<name>/) are mounted by the module loader (later task).

    return app
