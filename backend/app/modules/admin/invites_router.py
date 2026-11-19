"""Project invite endpoints, mounted with the admin API at ``/api/v1/admin`` (T-0052,
diff §4.1 Invites).

* ``POST/GET /projects/{p}/invites``, ``DELETE /projects/{p}/invites/{id}``,
  ``POST /projects/{p}/invites/{id}/resend``: a PER-09 of ``p`` or a ``system_admin`` (the same
  rule as the member endpoints: a non-member of ``p`` gets 404, a member without PER-09 403).
* ``POST /invites/preview``, ``POST /invites/accept``: any signed-in user, with the token of
  the invite link in the **body**. The invite is bound to the invited e-mail.
* ``POST /invites/setup-preview``, ``POST /invites/accept-with-password`` (T-0059): **not
  signed in**, with both tokens of the *e-mailed* link (``/invite#<invite_token>.<setup_token>``)
  in the body. An account without credentials sets its first password, joins and is signed
  in by one request (the normal token response of a sign-in). Neither endpoint reads a
  bearer token or the refresh cookie. The ``setup_token`` is in no response of this API.

The invite token never appears in a URL path or query of this API. It is returned once, by
create and re-send (``Cache-Control: no-store``), as the copyable link ``/invite#<token>``.

Every invite refusal has the same shape, ``{"detail": <message>, "code": <CODE>}`` (the
PER-09 + hold-authority confirmation keeps the T-0014 shape). Refusals of preview and accept
are normal responses (not exceptions), so the audit entry of the refusal commits with them; a
request that lost a race (``INVITE_CONFLICT``) rolls its unit of work back first.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth.access import (
    CallerGrants,
    CallerIsSystemAdmin,
    Capability,
    Grant,
    project_access,
)
from app.core.auth.bearer import AUTH_UNAVAILABLE
from app.core.auth.deps import CurrentPrincipal, get_auth_service
from app.core.auth.device_signal import request_device_id
from app.core.auth.password_policy import PasswordPolicyError
from app.core.auth.post_auth import PostAuthOutcome
from app.core.auth.revocation import RevocationUnavailableError
from app.core.auth.router import native_app_origin, token_response
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.auth.schemas import TokenResponse
from app.core.auth.service import AuthService
from app.core.db import DbSession, utcnow
from app.modules.admin import invites, service
from app.modules.admin.schemas import (
    GrantRead,
    InviteAccepted,
    InviteAcceptWithPassword,
    InviteCreate,
    InviteCreated,
    InvitePreview,
    InviteRead,
    InviteRefusal,
    InviteSent,
    InviteSetupIn,
    InviteSetupPreview,
    InviteTokenIn,
)

router = APIRouter(tags=["admin"])

INVITES: Final = "/projects/{project_id}/invites"
INVITE_PATH: Final = "/invite"  # the SPA route of the link; the token is its fragment
GONE_MESSAGE: Final = "This invite is no longer valid"
WRONG_USER_MESSAGE: Final = "This invite is for another account"
CONFLICT_MESSAGE: Final = "The invite changed in the meantime"
NOT_RESENDABLE_MESSAGE: Final = "Only a pending or expired invite can be re-sent or revoked"
TOO_MANY_MESSAGE: Final = "Too many invites sent to this address"
SIGN_IN_MESSAGE: Final = "This account already has a sign-in: sign in to join"

RuntimeDep = Annotated[AuthRuntime, Depends(get_auth_runtime)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def _require_project_admin(grants: list[Grant], is_admin: bool, project_id: uuid.UUID) -> None:
    """PER-09 of the project or ``system_admin``; a non-member gets 404, a member 403 (the
    rule of the member endpoints)."""
    if is_admin:
        return
    if not project_access(grants, project_id).has(Capability.ADMINISTER):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


def _refusal(status_code: int, code: str, message: str) -> JSONResponse:
    response = JSONResponse({"detail": message, "code": code}, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


def _not_resendable() -> JSONResponse:
    """Nothing was written: the invite is accepted or revoked."""
    return _refusal(
        status.HTTP_409_CONFLICT, invites.INVITE_NOT_RESENDABLE, NOT_RESENDABLE_MESSAGE
    )


def _lost_race(session: Session) -> JSONResponse:
    """A concurrent request changed the invite first: whatever this one wrote is undone."""
    session.rollback()
    return _refusal(status.HTTP_409_CONFLICT, invites.INVITE_CONFLICT, CONFLICT_MESSAGE)


def _rate_limited(exc: invites.InviteRateLimitedError) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        TOO_MANY_MESSAGE,
        headers={"Retry-After": str(exc.retry_after)},
    )


def _utc(moment: datetime) -> datetime:
    """SQLite hands ``TIMESTAMPTZ`` back naive; every value was stored in UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _read(entry: invites.InviteEntry) -> dict[str, Any]:
    invite = entry.invite
    return {
        "id": invite.id,
        "email": invite.invited_email,
        "grants": [GrantRead.model_validate(g) for g in invite.grants],
        "status": entry.status,
        "expires_at": _utc(invite.expires_at),
        "last_sent_at": _utc(invite.last_sent_at),
        "resend_available_at": entry.resend_available_at,
    }


def _sent(issued: invites.IssuedInvite, runtime: AuthRuntime, response: Response) -> dict[str, Any]:
    """The invite with its token and copyable link (fragment). Shown once: never cached."""
    response.headers["Cache-Control"] = "no-store"
    origin = runtime.settings.web_origin or ""
    return {
        **_read(issued.entry),
        "invite_token": issued.token,
        "invite_link": f"{origin}{INVITE_PATH}#{issued.token}",
    }


_ADMIN_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_403_FORBIDDEN: {"description": "A member of the project without PER-09"},
    status.HTTP_404_NOT_FOUND: {"description": "Not a member of the project, or no such invite"},
}
_SEND_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **_ADMIN_RESPONSES,
    status.HTTP_429_TOO_MANY_REQUESTS: {
        "description": (
            "The send cap of this project + e-mail is reached (1 per 60 s, 5 per 24 h, "
            "creations and re-sends together): retry after `Retry-After` seconds. Nothing "
            "was created or revoked"
        )
    },
}
_INVITEE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_403_FORBIDDEN: {
        "model": InviteRefusal,
        "description": "`INVITE_WRONG_USER`: the session's e-mail is not the invited one",
    },
    status.HTTP_410_GONE: {
        "model": InviteRefusal,
        "description": "`INVITE_GONE`: expired, revoked, used or unknown (one neutral message)",
    },
}


# --- the project administrators' side -------------------------------------------------------


@router.post(
    INVITES,
    response_model=InviteCreated,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_SEND_RESPONSES,
        status.HTTP_409_CONFLICT: {
            "description": (
                "`detail.code: sod_confirmation_required` (send `confirm_sod`), or "
                "`code: INVITE_CONFLICT` (a concurrent request changed the invite: retry)"
            )
        },
    },
)
def create_invite(
    project_id: uuid.UUID,
    body: InviteCreate,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
    runtime: RuntimeDep,
    response: Response,
) -> InviteCreated | JSONResponse:
    """Invite an e-mail address to the project. The account is created without credentials if
    none exists; an earlier open invite of the same address is revoked; no membership exists
    until the invite is accepted. The response carries the copyable link once."""
    _require_project_admin(grants, is_admin, project_id)
    wanted = [Grant(project_id, g.persona, g.site_node_id) for g in body.grants]
    settings = runtime.settings
    try:
        issued = invites.create_invite(
            session,
            project_id,
            body.email,
            wanted,
            confirm_sod=body.confirm_sod,
            actor_id=principal.user_id,
            ttl=timedelta(hours=settings.invite_ttl_hours),
            now=utcnow(),
            internal_domains=settings.internal_email_domains,
        )
    except invites.InviteRateLimitedError as exc:
        raise _rate_limited(exc) from exc
    except service.SodConfirmationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "sod_confirmation_required", "sod_warning": True, "message": str(exc)},
        ) from exc
    except invites.InviteConflictError:
        return _lost_race(session)
    body_out = {**_sent(issued, runtime, response), "sod_warning": issued.sod_warning}
    return InviteCreated.model_validate(body_out)


@router.get(INVITES, response_model=list[InviteRead], responses=_ADMIN_RESPONSES)
def list_invites(
    project_id: uuid.UUID, grants: CallerGrants, is_admin: CallerIsSystemAdmin, session: DbSession
) -> list[InviteRead]:
    """The project's open invites: pending, and expired never accepted. Accepted and revoked
    invites are not listed."""
    _require_project_admin(grants, is_admin, project_id)
    entries = invites.list_invites(session, project_id, now=utcnow())
    return [InviteRead.model_validate(_read(entry)) for entry in entries]


@router.delete(
    f"{INVITES}/{{invite_id}}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **_ADMIN_RESPONSES,
        status.HTTP_409_CONFLICT: {
            "model": InviteRefusal,
            "description": "`INVITE_NOT_RESENDABLE` (accepted or revoked) or `INVITE_CONFLICT`",
        },
    },
)
def revoke_invite(
    project_id: uuid.UUID,
    invite_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> Response:
    """Revoke an open invite: its link stops working at once."""
    _require_project_admin(grants, is_admin, project_id)
    try:
        invites.revoke_invite(session, project_id, invite_id, actor_id=principal.user_id)
    except invites.InviteNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found") from exc
    except invites.InviteNotResendableError:
        return _not_resendable()
    except invites.InviteConflictError:
        return _lost_race(session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    f"{INVITES}/{{invite_id}}/resend",
    response_model=InviteSent,
    responses={
        **_SEND_RESPONSES,
        status.HTTP_409_CONFLICT: {
            "model": InviteRefusal,
            "description": "`INVITE_NOT_RESENDABLE` (accepted or revoked) or `INVITE_CONFLICT`",
        },
    },
)
def resend_invite(
    project_id: uuid.UUID,
    invite_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
    runtime: RuntimeDep,
    response: Response,
) -> InviteSent | JSONResponse:
    """Re-send a pending or expired (never accepted) invite: a fresh token and expiry, the old
    link is gone at once. The response carries the new copyable link once."""
    _require_project_admin(grants, is_admin, project_id)
    try:
        issued = invites.resend_invite(
            session,
            project_id,
            invite_id,
            actor_id=principal.user_id,
            ttl=timedelta(hours=runtime.settings.invite_ttl_hours),
            now=utcnow(),
        )
    except invites.InviteNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found") from exc
    except invites.InviteNotResendableError:
        return _not_resendable()
    except invites.InviteRateLimitedError as exc:
        raise _rate_limited(exc) from exc
    except invites.InviteConflictError:
        return _lost_race(session)
    return InviteSent.model_validate(_sent(issued, runtime, response))


# --- the invitee's side ---------------------------------------------------------------------


@router.post("/invites/preview", response_model=InvitePreview, responses=_INVITEE_RESPONSES)
def preview_invite(
    body: InviteTokenIn, principal: CurrentPrincipal, session: DbSession
) -> InvitePreview | JSONResponse:
    """What the "Join project" confirmation shows: project code and name, the inviter's name.
    Only for the invited account; nothing is granted."""
    try:
        preview = invites.preview_invite(session, body.invite_token, principal.user, now=utcnow())
    except invites.InviteWrongUserError:
        return _refusal(status.HTTP_403_FORBIDDEN, invites.INVITE_WRONG_USER, WRONG_USER_MESSAGE)
    except invites.InviteGoneError:
        return _refusal(status.HTTP_410_GONE, invites.INVITE_GONE, GONE_MESSAGE)
    return InvitePreview.model_validate(preview, from_attributes=True)


@router.post(
    "/invites/accept",
    response_model=InviteAccepted,
    responses={
        **_INVITEE_RESPONSES,
        status.HTTP_409_CONFLICT: {
            "model": InviteRefusal,
            "description": "`INVITE_CONFLICT`: a parallel request accepted it first",
        },
    },
)
def accept_invite(
    body: InviteTokenIn, principal: CurrentPrincipal, session: DbSession
) -> InviteAccepted | JSONResponse:
    """Join the project: the membership and the invite's `accepted` mark in one transaction.
    Single use, also under concurrency."""
    try:
        joined = invites.accept_invite(session, body.invite_token, principal.user, now=utcnow())
    except invites.InviteWrongUserError:
        return _refusal(status.HTTP_403_FORBIDDEN, invites.INVITE_WRONG_USER, WRONG_USER_MESSAGE)
    except invites.InviteGoneError:
        return _refusal(status.HTTP_410_GONE, invites.INVITE_GONE, GONE_MESSAGE)
    except invites.InviteConflictError:
        return _refusal(status.HTTP_409_CONFLICT, invites.INVITE_CONFLICT, CONFLICT_MESSAGE)
    return InviteAccepted(joined_project_id=joined)


# --- the e-mailed link, not signed in (T-0059) ----------------------------------------------

_GONE_RESPONSE: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_410_GONE: _INVITEE_RESPONSES[status.HTTP_410_GONE]
}


def _gone() -> JSONResponse:
    return _refusal(status.HTTP_410_GONE, invites.INVITE_GONE, GONE_MESSAGE)


@router.post(
    "/invites/setup-preview",
    response_model=InviteSetupPreview,
    response_model_exclude_none=True,
    responses=_GONE_RESPONSE,
)
def setup_preview(
    body: InviteSetupIn, session: DbSession, runtime: RuntimeDep, response: Response
) -> InviteSetupPreview | JSONResponse:
    """What the e-mailed invite link shows before sign-in (no session needed; both tokens of
    `/invite#<invite_token>.<setup_token>` in the body): the project's code and name and
    `can_set_password: true` for an account without credentials. For an account that already
    has credentials only `can_set_password: false`: sign in, the invite is accepted right
    after. A copied link has no `setup_token` and cannot use this endpoint."""
    response.headers["Cache-Control"] = "no-store"
    tenant = runtime.settings.default_tenant_id
    if tenant is None:
        return _gone()
    try:
        preview = invites.setup_preview(
            session, tenant, body.invite_token, body.setup_token, now=utcnow()
        )
    except invites.InviteGoneError:
        return _gone()
    return InviteSetupPreview.model_validate(preview, from_attributes=True)


@router.post(
    "/invites/accept-with-password",
    response_model=TokenResponse,
    response_model_exclude_none=True,
    responses={
        **_GONE_RESPONSE,
        status.HTTP_409_CONFLICT: {
            "model": InviteRefusal,
            "description": (
                "`INVITE_SIGN_IN_REQUIRED`: the account already has credentials. Sign in "
                "instead (with the `invite_token`); nothing was changed"
            ),
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": (
                "The password policy refused the new password: `{detail, code, min_length, "
                "max_length}` with `code` `PASSWORD_TOO_SHORT`, `PASSWORD_TOO_LONG` or "
                "`PASSWORD_TOO_COMMON` (the invite stays pending, the link still works). "
                "Or a malformed body"
            ),
        },
    },
)
def accept_with_password(
    body: InviteAcceptWithPassword,
    session: DbSession,
    service: AuthServiceDep,
    runtime: RuntimeDep,
    request: Request,
    response: Response,
) -> TokenResponse | JSONResponse:
    """A brand-new invitee sets their first password from the e-mailed invite link, joins
    the project and is signed in: one request, one transaction (diff §4.1). Only for an
    active account without credentials whose e-mail is the invited one. The answer is the
    normal token response of a sign-in (refresh cookie, or the body for a native app) with
    `joined_project_id`. Single use, also under concurrency."""
    tenant = runtime.settings.default_tenant_id
    if tenant is None:
        return _gone()
    try:
        pair, joined = invites.accept_with_password(
            session,
            service,
            tenant,
            body.invite_token,
            body.setup_token,
            body.new_password,
            device_id=request_device_id(request),
            now=utcnow(),
        )
    except invites.InviteGoneError:
        return _gone()
    except invites.InviteSignInRequiredError:
        return _refusal(status.HTTP_409_CONFLICT, invites.INVITE_SIGN_IN_REQUIRED, SIGN_IN_MESSAGE)
    except PasswordPolicyError as exc:
        refused = {
            "detail": str(exc),
            "code": exc.code,
            "min_length": exc.min_length,
            "max_length": exc.max_length,
        }
        return JSONResponse(
            refused,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers={"Cache-Control": "no-store"},
        )
    except RevocationUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, AUTH_UNAVAILABLE) from exc
    signed_in = replace(pair, invite=PostAuthOutcome(joined_project_id=joined))
    return token_response(signed_in, response, native=native_app_origin(request, runtime.settings))
