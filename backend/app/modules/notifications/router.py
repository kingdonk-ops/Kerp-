"""Notifications API, mounted at ``/api/v1/notifications`` (T-0019).

The project is the one in the path. A non-member, another tenant's project or a deleted
project is 404; the request session is then bound to that project (until TD-122 binds
requests generally), so every read and write is under the project's RLS scope. A caller
only ever sees their own notifications: somebody else's is 404.

* ``POST /projects/{p}/notifications``            send (``admin``, PER-09); no pin
* ``GET  /projects/{p}/notifications``            the caller's own, pinned/priority/newest
* ``POST /projects/{p}/notifications/{id}/read``  the recipient marks it read

Broadcast e-mail (T-0228, diff §4.7): a System admin (tenant-wide or any project) or a PER-09
(its own PER-09 projects only; any other project is 404) e-mails an audience resolved on the
server.

* ``POST /broadcasts/preview``      the recipient count only
* ``POST /broadcasts``              queue it (202); 429 past the rate limit; 403 while
  impersonating
* ``GET  /broadcasts``              the broadcasts the caller may read, newest first
* ``GET  /broadcasts/{id}``         one, with its delivery counts from the send log
* ``POST /broadcasts/unsubscribe`` (``{token}`` body, the SPA page) and
  ``POST /broadcasts/unsubscribe/one-click?token=`` (RFC 8058, mail clients; not in the
  OpenAPI schema): the opt-out. No JWT, the same ``200`` for every token
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from app.core.auth.access import CallerGrants, CallerIsSystemAdmin, Capability, Grant, Persona
from app.core.auth.deps import CurrentPrincipal
from app.core.config import Settings
from app.core.db import Database, DbSession
from app.core.mail.schemas import MailCountsRead
from app.core.tenancy.context import TenancyContext
from app.modules.notifications import broadcasts, service
from app.modules.notifications.models import Broadcast
from app.modules.notifications.schemas import (
    BroadcastAudienceWrite,
    BroadcastPreviewRead,
    BroadcastRead,
    BroadcastWrite,
    DeliveryRead,
    NotificationCreate,
    NotificationPost,
    NotificationRead,
    NotificationSent,
    UnsubscribeRead,
    UnsubscribeWrite,
)
from app.modules.projects import service as projects

router = APIRouter(tags=["notifications"])

BROADCASTS = "/broadcasts"

NOTIFICATIONS = "/projects/{project_id}/notifications"


@router.post(NOTIFICATIONS, response_model=NotificationSent, status_code=status.HTTP_201_CREATED)
def send_notification(
    project_id: uuid.UUID,
    body: NotificationPost,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
    request: Request,
) -> NotificationSent:
    projects.bind_project(session, principal, grants, project_id, require=Capability.ADMINISTER)
    settings: Settings = request.app.state.settings
    try:
        delivery = service.notify(
            session,
            NotificationCreate.model_validate(body.model_dump()),
            actor_id=principal.user_id,
            channels=settings.notification_channels,
        )
    except service.RecipientNotMemberError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return NotificationSent(
        notification=NotificationRead.model_validate(delivery.notification),
        deliveries=[DeliveryRead.model_validate(r) for r in delivery.results],
    )


@router.get(NOTIFICATIONS, response_model=list[NotificationRead])
def list_notifications(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=service.MAX_LIMIT)] = service.DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[NotificationRead]:
    projects.bind_project(session, principal, grants, project_id, require=Capability.VIEW)
    notes = service.list_notifications(
        session, principal.user_id, unread_only=unread_only, limit=limit, offset=offset
    )
    return [NotificationRead.model_validate(n) for n in notes]


@router.post(NOTIFICATIONS + "/{notification_id}/read", response_model=NotificationRead)
def mark_read(
    project_id: uuid.UUID,
    notification_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> NotificationRead:
    projects.bind_project(session, principal, grants, project_id, require=Capability.VIEW)
    return NotificationRead.model_validate(
        service.mark_read(session, notification_id, principal.user_id)
    )


# --- broadcast e-mail (T-0228) ----------------------------------------------------------------


def _sender(principal: CurrentPrincipal, is_admin: bool, grants: list[Grant]) -> broadcasts.Sender:
    return broadcasts.Sender.of(principal.user_id, system_admin=is_admin, grants=grants)


def _audience(body: BroadcastAudienceWrite) -> broadcasts.Audience:
    return broadcasts.Audience(
        scope=body.scope,
        project_ids=frozenset(body.project_ids),
        category=body.category,
        personas=frozenset(Persona(p) for p in body.personas),
    )


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


def _read(broadcast: Broadcast, delivery: MailCountsRead | None = None) -> BroadcastRead:
    audience = broadcast.audience or {}
    return BroadcastRead.model_validate(
        {
            "id": broadcast.id,
            "sender_id": broadcast.sender_id,
            "scope": broadcast.scope,
            "project_ids": broadcast.project_ids,
            "category": audience.get("category"),
            "personas": audience.get("personas", []),
            "subject": broadcast.subject,
            "body": broadcast.body,
            "status": broadcast.status,
            "recipient_count": broadcast.recipient_count,
            "queued_count": broadcast.queued_count,
            "skipped_count": broadcast.skipped_count,
            "created_at": broadcast.created_at,
            "finished_at": broadcast.finished_at,
            "delivery": delivery,
        }
    )


@router.post(f"{BROADCASTS}/preview", response_model=BroadcastPreviewRead)
def preview_broadcast(
    body: BroadcastAudienceWrite,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    grants: CallerGrants,
    session: DbSession,
) -> BroadcastPreviewRead:
    """How many users the audience reaches now (active, not opted out, not suppressed): a
    count, never a list. `403` for a caller who is neither System admin nor PER-09, or a PER-09
    asking for the whole tenant; `404` for a project that is not one of a PER-09's own."""
    try:
        count = broadcasts.preview(session, _sender(principal, is_admin, grants), _audience(body))
    except broadcasts.BroadcastForbiddenError:
        raise _forbidden() from None
    return BroadcastPreviewRead(recipient_count=count)


@router.post(
    BROADCASTS,
    response_model=BroadcastRead,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        403: {"description": "Not allowed to broadcast (to this scope), or impersonating"},
        404: {"description": "A project that is not the caller's to target"},
        422: {"description": "Invalid body, or the audience has no recipients"},
        429: {"description": "Rate limit: 5 broadcasts per sender per 24 hours"},
    },
)
def create_broadcast(
    body: BroadcastWrite,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    grants: CallerGrants,
    session: DbSession,
) -> BroadcastRead | JSONResponse:
    """Queue a broadcast: the audience is resolved now and a `batch` job e-mails each recipient
    (`notifications.broadcast` with the broadcast id in the send log). Audited. Refused while
    impersonating (`403 IMPERSONATION_ACTION_FORBIDDEN`): a broadcast is sent by the real
    person only."""
    if principal.is_impersonation:
        return JSONResponse(
            {"detail": "Not allowed while impersonating", "code": "IMPERSONATION_ACTION_FORBIDDEN"},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    try:
        broadcast = broadcasts.create(
            session,
            _sender(principal, is_admin, grants),
            _audience(body),
            subject=body.subject,
            body=body.body,
        )
    except broadcasts.BroadcastForbiddenError:
        raise _forbidden() from None
    except broadcasts.NoRecipientsError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The audience has no recipients"
        ) from None
    except broadcasts.BroadcastRateLimitedError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many broadcasts; try again later",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    return _read(broadcast)


@router.get(BROADCASTS, response_model=list[BroadcastRead])
def list_broadcasts(
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    grants: CallerGrants,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=broadcasts.MAX_LIST)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BroadcastRead]:
    """The broadcasts the caller may read, newest first: a System admin every one of the
    tenant, a PER-09 those to its own PER-09 projects only. `403` for anyone else."""
    try:
        rows = broadcasts.list_broadcasts(
            session, _sender(principal, is_admin, grants), limit=limit, offset=offset
        )
    except broadcasts.BroadcastForbiddenError:
        raise _forbidden() from None
    return [_read(row) for row in rows]


@router.get(BROADCASTS + "/{broadcast_id}", response_model=BroadcastRead)
def get_broadcast(
    broadcast_id: uuid.UUID,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    grants: CallerGrants,
    session: DbSession,
) -> BroadcastRead:
    """One broadcast with its delivery counts from the send log (T-0226). `404` when the
    caller may not read it."""
    broadcast = broadcasts.get_broadcast(
        session, _sender(principal, is_admin, grants), broadcast_id
    )
    counts = broadcasts.delivery_counts(session, broadcast.id)
    delivery = MailCountsRead.model_validate(
        {
            "total": counts.total,
            "queued": counts.queued,
            "sent": counts.sent,
            "delivered": counts.delivered,
            "bounced": counts.bounced,
            "complained": counts.complained,
            "failed": counts.failed,
            "suppressed": counts.suppressed,
            "delivery_rate": counts.delivery_rate,
        }
    )
    return _read(broadcast, delivery)


def _unsubscribe(request: Request, token: str | None) -> UnsubscribeRead | JSONResponse:
    settings: Settings = request.app.state.settings
    db: Database | None = request.app.state.db
    if db is None or settings.default_tenant_id is None:
        return JSONResponse({"detail": "Unavailable"}, status_code=503)
    # The deployment's hidden tenant (as the SNS endpoint): nothing in the request names one.
    with db.unit_of_work(TenancyContext(tenant_id=settings.default_tenant_id)) as session:
        broadcasts.unsubscribe(session, token)
    return UnsubscribeRead()


@router.post(BROADCASTS + "/unsubscribe", response_model=UnsubscribeRead)
def unsubscribe(
    request: Request, body: UnsubscribeWrite | None = None
) -> UnsubscribeRead | JSONResponse:
    """Opt out of broadcast e-mails with the token from the e-mail's link (the SPA page
    `/unsubscribe#<token>` posts it here, in the body: never in a URL). No sign-in. Every
    token, valid, used, unknown or malformed, gets the same `200 {"status": "ok"}`; only a
    valid unused one records the opt-out. Transactional e-mail is never affected."""
    return _unsubscribe(request, body.token if body is not None else None)


@router.post(
    BROADCASTS + "/unsubscribe/one-click",  # = broadcasts.ONE_CLICK_PATH under the prefix
    response_model=UnsubscribeRead,
    include_in_schema=False,
)
def unsubscribe_one_click(request: Request) -> UnsubscribeRead | JSONResponse:
    """RFC 8058 one-click opt-out: the mail client posts `List-Unsubscribe=One-Click` to the
    `List-Unsubscribe` URI, so the token can only be in that URI's query (`?token=`, never
    logged; the body is ignored). For mail providers, not API clients: not in the OpenAPI
    schema (as the SNS endpoint). The same answer as `/unsubscribe` for every token."""
    return _unsubscribe(request, request.query_params.get("token"))
