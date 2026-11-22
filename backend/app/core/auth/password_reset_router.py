"""Self-service password reset endpoints (T-0077, diff §4.2). See ``password_reset.py``.

* ``POST /api/v1/auth/password-reset/request`` ``{email}``: always the same ``202`` (empty
  body, same headers) for an unknown, a deactivated and an active account; the link is
  issued and its e-mail queued after the response. ``429`` + ``Retry-After`` when the client
  address or the mailbox is throttled (decided before any account lookup).
* ``POST /api/v1/auth/password-reset/confirm`` ``{token, new_password}``: ``200`` once the
  password is set (no tokens: the user signs in with it); ``410`` for a dead link; ``422``
  with a policy ``code`` for a refused password (the link stays usable); ``429`` when
  throttled.

Neither endpoint reads a bearer token or a cookie, and neither sets one.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.core.auth.lockout import LockedOutError
from app.core.auth.password_reset import PasswordResetService
from app.core.auth.router import PREFIX, TOO_MANY_ATTEMPTS
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.auth.schemas import (
    PasswordResetConfirm,
    PasswordResetDone,
    PasswordResetRefusal,
    PasswordResetRequest,
)
from app.core.db import Database, DbSession

RESET_LINK_INVALID: Final = "RESET_LINK_INVALID"
RESET_LINK_INVALID_DETAIL: Final = "This reset link is invalid or has expired"

router = APIRouter(tags=["auth"])


def get_password_reset_service(
    runtime: Annotated[AuthRuntime, Depends(get_auth_runtime)],
) -> PasswordResetService:
    return PasswordResetService(runtime)


ResetServiceDep = Annotated[PasswordResetService, Depends(get_password_reset_service)]

_LOCKED_RESPONSE: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_429_TOO_MANY_REQUESTS: {
        "description": "Throttled; retry after `Retry-After` seconds"
    }
}


def _locked(exc: LockedOutError) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        TOO_MANY_ATTEMPTS,
        headers={"Retry-After": str(exc.retry_after)},
    )


def _client(request: Request) -> str | None:
    # Behind a proxy, uvicorn sets this from X-Forwarded-For only for trusted peers.
    return request.client.host if request.client else None


@router.post(
    f"{PREFIX}/password-reset/request",
    status_code=status.HTTP_202_ACCEPTED,
    responses=_LOCKED_RESPONSE,
)
def request_password_reset(
    body: PasswordResetRequest,
    request: Request,
    tasks: BackgroundTasks,
    service: ResetServiceDep,
) -> None:
    """Ask for a password-reset link by e-mail (diff §4.2).

    Always `202` with the same body and headers, whether the address has an active account,
    a deactivated one or none: the answer reveals no accounts. An active account (with or
    without a password) is sent a single-use link `/reset-password#<token>` that expires
    after `KERP_PASSWORD_RESET_TTL_MINUTES`; a new request invalidates the earlier link.
    The request only counts the attempt; everything else runs after the response.
    """
    database: Database | None = getattr(request.app.state, "db", None)
    if database is None:
        raise RuntimeError("database not configured (set KERP_DATABASE_URL)")
    try:
        job = service.request(email=body.email, client=_client(request))
    except LockedOutError as exc:
        raise _locked(exc) from exc
    if job is not None:
        tasks.add_task(service.issue, database, job)


@router.post(
    f"{PREFIX}/password-reset/confirm",
    response_model=PasswordResetDone,
    responses={
        status.HTTP_410_GONE: {
            "model": PasswordResetRefusal,
            "description": (
                "The link is unknown, used, expired or superseded by a newer one "
                "(`code: RESET_LINK_INVALID`): request a new link"
            ),
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": (
                "The password policy refused the new password: `{detail, code, min_length, "
                "max_length}` with `code` `PASSWORD_TOO_SHORT`, `PASSWORD_TOO_LONG` or "
                "`PASSWORD_TOO_COMMON` (the link is still usable). Or a malformed body"
            ),
        },
        **_LOCKED_RESPONSE,
    },
)
def confirm_password_reset(
    body: PasswordResetConfirm,
    session: DbSession,
    request: Request,
    response: Response,
    service: ResetServiceDep,
) -> PasswordResetDone | JSONResponse:
    """Set a new password with the token of a reset link (diff §4.2).

    On success every session and refresh token of the user is revoked on all devices
    (device enrolment and PIN stay), outstanding magic links and reset links die, and a
    "your password was changed" notice is e-mailed. Nobody is signed in: sign in with the
    new password.
    """
    no_store = {"Cache-Control": "no-store"}
    try:
        result = service.confirm(
            session, token=body.token, new_password=body.new_password, client=_client(request)
        )
    except LockedOutError as exc:
        raise _locked(exc) from exc
    # Refusals are ordinary responses, not exceptions: the request's unit of work commits,
    # and with it their audit entries.
    if result.status == "refused" and result.error is not None:
        refusal = PasswordResetRefusal(
            detail=str(result.error),
            code=result.error.code,
            min_length=result.error.min_length,
            max_length=result.error.max_length,
        )
        return JSONResponse(
            refusal.model_dump(),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers=no_store,
        )
    if result.status != "changed":
        gone = PasswordResetRefusal(detail=RESET_LINK_INVALID_DETAIL, code=RESET_LINK_INVALID)
        return JSONResponse(
            gone.model_dump(exclude_none=True), status_code=status.HTTP_410_GONE, headers=no_store
        )
    response.headers.update(no_store)
    return PasswordResetDone()
