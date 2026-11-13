"""Application factory.

Run with: ``uvicorn app.main:create_app --factory``.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.core.auth.router import router as auth_router
from app.core.auth.runtime import check_startup_config, close_auth_runtime
from app.core.body_limit import BodyLimitMiddleware
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.logging import configure_logging
from app.core.module_loader.loader import DEFAULT_PACKAGE, install_modules
from app.core.request_context import RequestContextMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.core.tenancy.scope import OutOfScopeError
from app.modules.jobs.broker import LoopDispatcher, SaqBroker
from app.modules.jobs.service import set_dispatcher


async def _out_of_scope_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def create_app(
    settings: Settings | None = None, *, modules_package: str = DEFAULT_PACKAGE
) -> FastAPI:
    """Build a configured FastAPI app. Uses environment settings unless ``settings`` is given.

    ``modules_package`` is where the module loader finds domain modules (tests swap it).
    """
    if settings is None:
        settings = get_settings()

    configure_logging(settings)

    database = Database(settings.database_url) if settings.database_url else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        check_startup_config(settings)
        # Jobs (T-0015): rows committed by requests are enqueued on SAQ after the commit.
        broker = SaqBroker(settings.redis_url) if settings.redis_url else None
        dispatcher = LoopDispatcher(broker, asyncio.get_running_loop()) if broker else None
        if dispatcher is not None:
            set_dispatcher(dispatcher)
        yield
        if broker is not None and dispatcher is not None:
            set_dispatcher(None)
            await dispatcher.drain()
            await broker.close()
        close_auth_runtime(_app)
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
    # middleware so they are also applied to the generic 500 it produces. The body limit is
    # innermost, so its 413 carries a request id and the security headers (T-0060).
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(RequestContextMiddleware, debug=settings.debug)
    docs_paths = {app.docs_url, app.redoc_url, app.swagger_ui_oauth2_redirect_url}
    app.add_middleware(
        SecurityHeadersMiddleware,
        content_security_policy=settings.content_security_policy,
        hsts=settings.hsts_active,
        docs_paths=frozenset(p for p in docs_paths if p),
    )

    # Another tenant's or project's record is "not found", never "forbidden" (CLAUDE.md §6).
    app.add_exception_handler(OutOfScopeError, _out_of_scope_handler)

    app.include_router(health_router)
    app.include_router(auth_router)  # core auth + JWKS (T-0008)
    # Domain modules (app/modules/<name>/) and the per-project toggle API (T-0011).
    install_modules(app, modules_package)

    return app
