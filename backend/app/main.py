"""Application factory.

Run with: ``uvicorn app.main:create_app --factory``.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.core.auth.device_api import router as device_router
from app.core.auth.device_signal import (
    DEVICE_HEADER,
    DeviceRevokedError,
    InvalidDeviceHeaderError,
    device_revoked_handler,
    invalid_device_header_handler,
)
from app.core.auth.field import PinGateError, pin_gate_handler
from app.core.auth.password_reset_router import router as password_reset_router
from app.core.auth.router import router as auth_router
from app.core.auth.runtime import check_startup_config, close_auth_runtime
from app.core.auth.sso_router import router as sso_router
from app.core.auth.team_grants import check_team_grants_at_startup
from app.core.body_limit import BodyLimitMiddleware
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.logging import configure_logging
from app.core.mail.router import router as mail_router
from app.core.mail.transport import check_mail_config
from app.core.module_loader.loader import DEFAULT_PACKAGE, install_modules, registry_of
from app.core.request_context import RequestContextMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.core.tenancy.provision import ensure_default_tenant
from app.core.tenancy.scope import OutOfScopeError
from app.modules.jobs import mail as jobs_mail
from app.modules.jobs.broker import LoopDispatcher, SaqBroker
from app.modules.jobs.service import set_dispatcher, set_queue_monitor


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
        check_mail_config(settings)  # production refuses to start without SES (T-0058)
        # Team-derived grants without the teams module: refuse to start (T-0073, ADR 0008).
        check_team_grants_at_startup(database, registry_of(_app).names())
        # The deployment's tenant has its directory row before any tenant-scoped insert
        # (T-0079, ADR 0011): every tenant_id is a foreign key to it. Idempotent.
        ensure_default_tenant(database, settings)
        # Jobs (T-0015): rows committed by requests are enqueued on SAQ after the commit.
        # Mail (T-0058): the jobs module installs core mail's post-commit enqueuer.
        broker = SaqBroker(settings.redis_url) if settings.redis_url else None
        loop = asyncio.get_running_loop()
        dispatcher = LoopDispatcher(broker, loop) if broker else None
        mail_dispatcher = jobs_mail.install(broker.queues["interactive"], loop) if broker else None
        if dispatcher is not None:
            set_dispatcher(dispatcher)
        # The System console's queue monitor reads SAQ through the same broker (T-0218).
        set_queue_monitor(broker)
        yield
        set_queue_monitor(None)
        if broker is not None and dispatcher is not None and mail_dispatcher is not None:
            set_dispatcher(None)
            jobs_mail.uninstall()
            await dispatcher.drain()
            await mail_dispatcher.drain()
            await broker.close()
        close_auth_runtime(_app)
        if database is not None:
            database.dispose()

    # API docs and schema are not served in production (TD-022); KERP_API_DOCS_ENABLED overrides.
    # ``app.openapi()`` (used by ``npm run gen:api``) works either way.
    docs = settings.api_docs_active
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        debug=settings.debug,
        lifespan=lifespan,
        openapi_url="/openapi.json" if docs else None,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        swagger_ui_oauth2_redirect_url="/docs/oauth2-redirect" if docs else None,
    )
    app.state.settings = settings
    app.state.db = database

    # Middleware added last runs outermost: security headers wrap the request-context
    # middleware so they are also applied to the generic 500 it produces. The body limit is
    # innermost, so its 413 carries a request id and the security headers (T-0060).
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(RequestContextMiddleware, debug=settings.debug)
    if settings.native_app_origins:
        # The native field app (T-0043, TD-203) calls the API cross-origin: exactly the
        # allow-listed origins, never with credentials (its refresh token travels in the
        # body, so no cookie is needed). The web SPA is served from KERP_WEB_ORIGIN and is
        # never on this list (settings refuse it).
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.native_app_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", DEVICE_HEADER],
            expose_headers=["Retry-After", "WWW-Authenticate"],
            max_age=600,
        )
    docs_paths = {app.docs_url, app.redoc_url, app.swagger_ui_oauth2_redirect_url}
    app.add_middleware(
        SecurityHeadersMiddleware,
        content_security_policy=settings.content_security_policy,
        hsts=settings.hsts_active,
        docs_paths=frozenset(p for p in docs_paths if p),
    )

    # Another tenant's or project's record is "not found", never "forbidden" (CLAUDE.md §6).
    app.add_exception_handler(OutOfScopeError, _out_of_scope_handler)
    # A revoked field device: 401 with code DEVICE_REVOKED, the client wipes (T-0043).
    app.add_exception_handler(DeviceRevokedError, device_revoked_handler)
    app.add_exception_handler(PinGateError, pin_gate_handler)
    app.add_exception_handler(InvalidDeviceHeaderError, invalid_device_header_handler)

    app.include_router(health_router)
    app.include_router(auth_router)  # core auth + JWKS (T-0008)
    app.include_router(password_reset_router)  # self-service password reset (T-0077)
    app.include_router(sso_router)  # Entra ID SSO, auth config, assetlinks (T-0054)
    app.include_router(device_router)  # device registry (T-0043)
    app.include_router(mail_router)  # outbound e-mail admin + SES bounce endpoint (T-0058)
    # Domain modules (app/modules/<name>/) and the per-project toggle API (T-0011).
    install_modules(app, modules_package)

    return app
