"""Entra ID SSO endpoints (T-0054, diff §4.5), the public auth configuration and the Android
App Link verification file.

* ``GET /api/v1/auth/config``: what the sign-in screen offers (SSO preferred when enabled,
  password always available).
* ``GET /api/v1/auth/sso/entra/start``: state + nonce + PKCE, the pre-auth cookie, and a
  redirect to the tenant-pinned authority.
* ``POST /api/v1/auth/sso/entra/start`` (a top-level **form post**): the same, carrying an
  ``invite_token`` in the form body, so the token is never in a URL (T-0052, diff §4.1). Only
  its reference is kept with the ``state``; after the sign-in the post-authentication hook
  accepts the invite and the landing gets ``joined_project_id`` or ``invite_error``.
* ``GET /api/v1/auth/sso/entra/callback``: the one redirect URI of the app registration.
  Web: sets the refresh cookie (the same KERP session as a password sign-in) and redirects
  to the SPA's landing; no token in the URL. Native: a single-use exchange code through the
  verified App Link ``/app/sso-return``. Refusals redirect to the same place with
  ``error=<code>`` (a translated message key, ``entra.client_code``).
* ``POST /api/v1/auth/sso/entra/exchange``: the native app redeems the code (code and
  verifier checked **before** the device, T-0080) and gets the native body transport.
* ``GET /.well-known/assetlinks.json``: from configuration (package + signing certificate).
"""

from __future__ import annotations

import hmac
import logging
import re
import uuid
from typing import Annotated, Any, Final
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from app.core.auth import entra, sso
from app.core.auth.deps import get_auth_service
from app.core.auth.device_signal import DeviceRevokedError, device_revoked_response
from app.core.auth.models import User
from app.core.auth.post_auth import invite_reference
from app.core.auth.revocation import RevocationUnavailableError
from app.core.auth.router import (
    PREFIX,
    native_app_origin,
    own_origin,
    set_refresh_cookie,
    token_response,
)
from app.core.auth.runtime import AuthRuntime, EntraSso, get_auth_runtime
from app.core.auth.schemas import (
    INVITE_TOKEN_MAX_LENGTH,
    PKCE_CHALLENGE_PATTERN,
    AuthConfigResponse,
    SsoExchangeRequest,
    SsoOption,
    TokenResponse,
)
from app.core.auth.service import AuthService, refuse_revoked_device
from app.core.config import ENTRA_CALLBACK_PATH, Settings
from app.core.db import DbSession
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_tenancy

SSO_PREFIX: Final = f"{PREFIX}/sso/entra"
START_PATH: Final = f"{SSO_PREFIX}/start"
EXCHANGE_PATH: Final = f"{SSO_PREFIX}/exchange"
ASSETLINKS_PATH: Final = "/.well-known/assetlinks.json"
# The pre-auth cookie binds the flow to the browser that started it (login-CSRF, diff §4.5).
PREAUTH_COOKIE: Final = "kerp_sso_preauth"
PREAUTH_COOKIE_PATH: Final = f"{PREFIX}/sso"
# The verified Android App Link (native handoff) and the web SPA's landing (T-0055).
APP_RETURN_PATH: Final = "/app/sso-return"
WEB_RETURN_PATH: Final = "/login/sso-return"
SIGNED_IN: Final = "signed_in"
MAX_STATE_LENGTH: Final = 128
MAX_CODE_LENGTH: Final = 4096
FORM_CONTENT_TYPE: Final = "application/x-www-form-urlencoded"
MAX_START_FORM_BYTES: Final = 2048
WEB: Final = "web"
NATIVE: Final = "native"
# Exchange refusals: the reasons logged/audited (the client always gets the same 401).
EXCHANGE_CODE_UNKNOWN: Final = "exchange_code_unknown"
EXCHANGE_CODE_REUSED: Final = "exchange_code_reused"
EXCHANGE_VERIFIER_MISMATCH: Final = "exchange_verifier_mismatch"
EXCHANGE_DEVICE_MISMATCH: Final = "exchange_device_mismatch"
DEVICE_REVOKED_REASON: Final = "device_revoked"

_logger = logging.getLogger("kerp.auth.sso")

router = APIRouter(tags=["auth"])

RuntimeDep = Annotated[AuthRuntime, Depends(get_auth_runtime)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def _unavailable() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication unavailable")


def _enabled(runtime: AuthRuntime) -> tuple[EntraSso, uuid.UUID]:
    """The SSO runtime and the deployment's tenant; ``404`` when SSO is not offered."""
    tenant = runtime.settings.default_tenant_id
    if runtime.entra is None or tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return runtime.entra, tenant


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _redirect_uri(runtime: AuthRuntime, request: Request) -> str:
    """The registered redirect URI; outside production it may follow the request."""
    configured = runtime.settings.entra_redirect_uri
    return configured or f"{own_origin(request)}{ENTRA_CALLBACK_PATH}"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(_settings)]


def sso_offered(settings: Settings) -> bool:
    """SSO is offered with the Entra tenant + client id and the deployment's tenant."""
    return settings.entra_enabled and settings.default_tenant_id is not None


@router.get(f"{PREFIX}/config", response_model=AuthConfigResponse)
def auth_config(settings: SettingsDep, response: Response) -> AuthConfigResponse:
    """The sign-in methods: Entra ID SSO as the preferred one when the deployment offers it,
    password always (SSO is promoted, never enforced, diff §4.5). Needs no key or Redis."""
    response.headers["Cache-Control"] = "no-cache"
    offered = sso_offered(settings)
    return AuthConfigResponse(sso=SsoOption(start_url=START_PATH) if offered else None)


@router.get(
    START_PATH,
    status_code=status.HTTP_302_FOUND,
    response_class=RedirectResponse,
    responses={404: {"description": "SSO is not offered by this deployment"}},
)
def sso_start(
    runtime: RuntimeDep,
    request: Request,
    device_id: Annotated[
        uuid.UUID | None, Query(description="The enrolling field client's device id")
    ] = None,
    app_challenge: Annotated[
        str | None,
        Query(
            pattern=PKCE_CHALLENGE_PATTERN,
            description=(
                "Native app only: S256 hash of its own verifier (with `device_id`); the "
                "callback then hands off through the App Link with a single-use code"
            ),
        ),
    ] = None,
) -> RedirectResponse:
    """Start an Entra ID sign-in: a redirect to the company tenant's authority.

    An invite is never carried here (a query string is a server-visible URL): a sign-in that
    accepts an invite starts with the form post of the same path."""
    return _start(runtime, request, None, device_id, app_challenge, status.HTTP_302_FOUND)


_START_FORM: Final[dict[str, Any]] = {
    "requestBody": {
        "required": False,
        "content": {
            FORM_CONTENT_TYPE: {
                "schema": {
                    "type": "object",
                    "properties": {
                        "invite_token": {"type": "string", "maxLength": INVITE_TOKEN_MAX_LENGTH},
                        "device_id": {"type": "string", "format": "uuid"},
                        "app_challenge": {"type": "string", "pattern": PKCE_CHALLENGE_PATTERN},
                    },
                }
            }
        },
    }
}


def _form_field(fields: dict[str, list[str]], name: str) -> str | None:
    values = fields.get(name, [])
    if len(values) > 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{name} given twice")
    return values[0] if values else None


@router.post(
    START_PATH,
    status_code=status.HTTP_303_SEE_OTHER,
    response_class=RedirectResponse,
    responses={404: {"description": "SSO is not offered by this deployment"}},
    openapi_extra=_START_FORM,
)
async def sso_start_form(runtime: RuntimeDep, request: Request) -> RedirectResponse:
    """Start an Entra ID sign-in that accepts a project invite afterwards (T-0052).

    A top-level HTML form post (`application/x-www-form-urlencoded`: `invite_token`,
    optional `device_id` and `app_challenge` as on the `GET`), so the invite token travels in
    the body, never in a URL. Only its reference is stored with the `state`."""
    _enabled(runtime)
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != FORM_CONTENT_TYPE:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Unsupported Media Type")
    raw = await request.body()
    if len(raw) > MAX_START_FORM_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Content Too Large")
    try:
        fields = parse_qs(raw.decode("ascii"), keep_blank_values=False, strict_parsing=bool(raw))
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Malformed form") from None
    invite_token = _form_field(fields, "invite_token")
    if invite_token is not None and len(invite_token) > INVITE_TOKEN_MAX_LENGTH:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invite_token too long")
    app_challenge = _form_field(fields, "app_challenge")
    if app_challenge is not None and re.fullmatch(PKCE_CHALLENGE_PATTERN, app_challenge) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "app_challenge malformed")
    try:
        device = _form_field(fields, "device_id")
        device_id = uuid.UUID(device) if device is not None else None
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "device_id malformed") from None
    # The store is a blocking client: off the event loop, like every sync endpoint.
    return await run_in_threadpool(
        _start,
        runtime,
        request,
        invite_reference(invite_token),
        device_id,
        app_challenge,
        status.HTTP_303_SEE_OTHER,
    )


def _start(
    runtime: AuthRuntime,
    request: Request,
    invite_ref: str | None,
    device_id: uuid.UUID | None,
    app_challenge: str | None,
    redirect_status: int,
) -> RedirectResponse:
    entra_sso, _tenant = _enabled(runtime)
    if app_challenge is not None and device_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "app_challenge needs device_id")
    config = entra_sso.provider.config
    state, nonce, verifier, cookie = (sso.new_secret() for _ in range(4))
    redirect_uri = _redirect_uri(runtime, request)
    entry = sso.SsoState(
        nonce=nonce,
        verifier=verifier,
        cookie_hash=sso.digest(cookie),
        redirect_uri=redirect_uri,
        invite_ref=invite_ref,
        device_id=str(device_id) if device_id is not None else None,
        app_challenge=app_challenge,
    )
    try:
        sso.put_state(runtime.store, state, entry)
    except RevocationUnavailableError as exc:
        raise _unavailable() from exc
    query = urlencode(
        {
            "client_id": config.client_id,
            "response_type": "code",
            "response_mode": "query",
            "redirect_uri": redirect_uri,
            "scope": entra.SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": entra.pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(f"{config.authorize_url}?{query}", redirect_status)
    _no_store(response)
    response.set_cookie(
        PREAUTH_COOKIE,
        cookie,
        max_age=int(sso.STATE_TTL.total_seconds()),
        path=PREAUTH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


def _landing(runtime: AuthRuntime, deployment: str, entry: sso.SsoState | None) -> str:
    """The App Link of the native app, else the web SPA's landing page."""
    if entry is not None and entry.native:
        return f"{deployment}{APP_RETURN_PATH}"
    return f"{runtime.settings.web_origin or deployment}{WEB_RETURN_PATH}"


def _redirect(url: str, params: dict[str, str]) -> RedirectResponse:
    response = RedirectResponse(f"{url}?{urlencode(params)}", status.HTTP_303_SEE_OTHER)
    _no_store(response)
    response.delete_cookie(
        PREAUTH_COOKIE, path=PREAUTH_COOKIE_PATH, secure=True, httponly=True, samesite="lax"
    )
    return response


@router.get(
    ENTRA_CALLBACK_PATH,
    status_code=status.HTTP_303_SEE_OTHER,
    response_class=RedirectResponse,
    responses={404: {"description": "SSO is not offered by this deployment"}},
)
def sso_callback(
    session: DbSession,
    runtime: RuntimeDep,
    service: AuthServiceDep,
    request: Request,
    state: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    kerp_sso_preauth: Annotated[str | None, Cookie()] = None,
) -> RedirectResponse:
    """Entra's redirect back: verify everything, then sign in (web) or hand off (native).

    Always a redirect that clears the pre-auth cookie; a refusal carries ``error=<code>``."""
    entra_sso, tenant = _enabled(runtime)
    bind_tenancy(session, TenancyContext(tenant_id=tenant))
    fallback = _origin_of(_redirect_uri(runtime, request))
    entry: sso.SsoState | None = None
    flow = WEB
    try:
        if state and len(state) <= MAX_STATE_LENGTH:
            entry = sso.take_state(runtime.store, state)
        if entry is None:
            raise entra.SsoRefusedError(entra.STATE_INVALID)
        flow = NATIVE if entry.native else WEB
        presented = sso.digest(kerp_sso_preauth) if kerp_sso_preauth else ""
        if not hmac.compare_digest(presented.encode(), entry.cookie_hash.encode()):
            raise entra.SsoRefusedError(entra.PREAUTH_COOKIE_MISMATCH)
        if error is not None or not code or len(code) > MAX_CODE_LENGTH:
            raise entra.SsoRefusedError(entra.PROVIDER_ERROR)
        return _complete(session, runtime, service, entra_sso, tenant, entry, code)
    except entra.SsoRefusedError as refusal:
        return _refuse(session, runtime, refusal, entry, flow, fallback)
    except RevocationUnavailableError as exc:
        raise _unavailable() from exc


def _complete(
    session: DbSession,
    runtime: AuthRuntime,
    service: AuthService,
    entra_sso: EntraSso,
    tenant: uuid.UUID,
    entry: sso.SsoState,
    code: str,
) -> RedirectResponse:
    provider = entra_sso.provider
    try:
        id_token = entra_sso.transport.redeem_code(
            provider.config, code=code, verifier=entry.verifier, redirect_uri=entry.redirect_uri
        )
    except entra.EntraTransportError:
        # A wrong PKCE verifier, a spent or foreign code: Entra refuses the redemption.
        raise entra.SsoRefusedError(entra.TOKEN_EXCHANGE_FAILED) from None
    identity = provider.verify(id_token, nonce=entry.nonce)
    match = provider.match(session, tenant, identity)
    user = match.user
    flow = NATIVE if entry.native else WEB
    if match.linked:
        sso.audit_event(session, "sso_linked", flow=flow, user_id=user.id)
    deployment = _origin_of(entry.redirect_uri)
    if entry.native:
        # Tokens never travel in a redirect: a 60-second single-use code through the App
        # Link. The device is checked at the exchange, after the code and the verifier.
        if entry.app_challenge is None or entry.device_id is None:  # /start requires both
            raise entra.SsoRefusedError(entra.STATE_INVALID)
        handoff = sso.put_exchange_code(
            runtime.store,
            sso.ExchangeGrant(
                user_id=str(user.id),
                tenant_id=str(tenant),
                device_id=entry.device_id,
                app_challenge=entry.app_challenge,
                invite_ref=entry.invite_ref,
            ),
        )
        sso.audit_event(session, "sso_verified", flow=flow, user_id=user.id)
        return _redirect(_landing(runtime, deployment, entry), {"code": handoff})
    try:
        # Credentials first (T-0080): the device is looked at only now.
        refuse_revoked_device(session, entry.device_uuid)
    except DeviceRevokedError:
        raise entra.SsoRefusedError(DEVICE_REVOKED_REASON, user_id=user.id) from None
    pair = service.start_session(
        session, user, device_id=entry.device_uuid, invite_ref=entry.invite_ref
    )
    sso.audit_event(session, "sso_signed_in", flow=flow, user_id=user.id)
    # The invite's outcome (T-0052): a project id or an error code, never the token.
    params = {"status": SIGNED_IN}
    if pair.invite.joined_project_id is not None:
        params["joined_project_id"] = str(pair.invite.joined_project_id)
    if pair.invite.invite_error is not None:
        params["invite_error"] = pair.invite.invite_error
    response = _redirect(_landing(runtime, deployment, entry), params)
    set_refresh_cookie(response, pair)
    return response


def _refuse(
    session: DbSession,
    runtime: AuthRuntime,
    refusal: entra.SsoRefusedError,
    entry: sso.SsoState | None,
    flow: str,
    fallback: str,
) -> RedirectResponse:
    reason = refusal.reason
    entra.log_refusal(reason, flow=flow)
    if reason == entra.ACCT_CLAIM_MISSING:
        sso.record_acct_missing(runtime.store)
    if reason in entra.IDENTIFIED_REASONS or reason == DEVICE_REVOKED_REASON:
        sso.audit_event(session, "sso_refused", flow=flow, reason=reason, user_id=refusal.user_id)
    code = "DEVICE_REVOKED" if reason == DEVICE_REVOKED_REASON else entra.client_code(reason)
    deployment = _origin_of(entry.redirect_uri) if entry is not None else fallback
    return _redirect(_landing(runtime, deployment, entry), {"error": code})


_EXCHANGE_REFUSED: Final[dict[str, Any]] = {"detail": "Not authenticated"}
_EXCHANGE_REFUSED_HEADERS: Final = {"WWW-Authenticate": 'Bearer error="invalid_token"'}


def _exchange_refused(session: DbSession, reason: str, user_id: str | None = None) -> JSONResponse:
    """The one answer to a bad, expired, reused or foreign code or verifier, whatever the
    device (no device-status oracle). A normal response, so the audit entry commits."""
    entra.log_refusal(reason, flow=NATIVE)
    if user_id is not None:
        sso.audit_event(
            session, "sso_refused", flow=NATIVE, reason=reason, user_id=uuid.UUID(user_id)
        )
    response = JSONResponse(
        _EXCHANGE_REFUSED,
        status_code=status.HTTP_401_UNAUTHORIZED,
        headers=_EXCHANGE_REFUSED_HEADERS,
    )
    _no_store(response)
    return response


@router.post(
    EXCHANGE_PATH,
    response_model=TokenResponse,
    response_model_exclude_none=True,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": (
                "Bad, expired, reused or foreign code or verifier (the same answer for any "
                "device); with valid credentials `code: DEVICE_REVOKED` for a revoked device"
            )
        },
        status.HTTP_403_FORBIDDEN: {"description": "`Origin` is not an allow-listed native app"},
        status.HTTP_404_NOT_FOUND: {"description": "SSO is not offered by this deployment"},
    },
)
def sso_exchange(
    body: SsoExchangeRequest,
    session: DbSession,
    runtime: RuntimeDep,
    service: AuthServiceDep,
    request: Request,
    response: Response,
) -> TokenResponse | JSONResponse:
    """The native app redeems its App Link code with its verifier and device id: a new KERP
    session in the native body transport (the refresh token in the body)."""
    _entra_sso, tenant = _enabled(runtime)
    if not native_app_origin(request, runtime.settings):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-origin request refused")
    bind_tenancy(session, TenancyContext(tenant_id=tenant))
    try:
        grant = sso.take_exchange_code(runtime.store, body.code)
        if grant is None:
            reused_by = sso.spent_code_user(runtime.store, body.code)
            if reused_by is not None:
                return _exchange_refused(session, EXCHANGE_CODE_REUSED, reused_by)
            return _exchange_refused(session, EXCHANGE_CODE_UNKNOWN)
        challenge = entra.pkce_challenge(body.verifier)
        if not hmac.compare_digest(challenge.encode(), grant.app_challenge.encode()):
            return _exchange_refused(session, EXCHANGE_VERIFIER_MISMATCH, grant.user_id)
        if grant.device_id != str(body.device_id) or grant.tenant_id != str(tenant):
            return _exchange_refused(session, EXCHANGE_DEVICE_MISMATCH, grant.user_id)
        user_id = uuid.UUID(grant.user_id)
        bind_tenancy(session, TenancyContext(tenant_id=tenant, user_id=user_id))
        user = session.scalars(
            select(User).where(User.id == user_id, User.tenant_id == tenant)
        ).one_or_none()
        if user is None or not user.is_active:
            return _exchange_refused(session, entra.ACCOUNT_DEACTIVATED, grant.user_id)
        try:
            # Only now, with the code and the verifier proven, is the device looked at.
            refuse_revoked_device(session, body.device_id)
        except DeviceRevokedError:
            entra.log_refusal(DEVICE_REVOKED_REASON, flow=NATIVE)
            sso.audit_event(
                session, "sso_refused", flow=NATIVE, reason=DEVICE_REVOKED_REASON, user_id=user.id
            )
            return device_revoked_response()
        pair = service.start_session(
            session, user, device_id=body.device_id, invite_ref=grant.invite_ref
        )
    except RevocationUnavailableError as exc:
        raise _unavailable() from exc
    sso.audit_event(session, "sso_signed_in", flow=NATIVE, user_id=user.id)
    return token_response(pair, response, native=True)


@router.get(
    ASSETLINKS_PATH,
    responses={404: {"description": "No Android app is configured"}},
)
def assetlinks(settings: SettingsDep) -> JSONResponse:
    """Digital Asset Links for the verified Android App Link ``/app/sso-return`` (from
    ``KERP_ANDROID_APP_PACKAGE`` + ``KERP_ANDROID_APP_CERT_FINGERPRINTS``)."""
    if not settings.android_app_package or not settings.android_app_cert_fingerprints:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    statements = [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": settings.android_app_package,
                "sha256_cert_fingerprints": list(settings.android_app_cert_fingerprints),
            },
        }
    ]
    return JSONResponse(statements, headers={"Cache-Control": "public, max-age=300"})
