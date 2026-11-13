"""Notifications API, mounted at ``/api/v1/notifications`` (T-0019).

The project is the one in the path. A non-member, another tenant's project or a deleted
project is 404; the request session is then bound to that project (until TD-122 binds
requests generally), so every read and write is under the project's RLS scope. A caller
only ever sees their own notifications: somebody else's is 404.

* ``POST /projects/{p}/notifications``            send (``admin``, PER-09); no pin
* ``GET  /projects/{p}/notifications``            the caller's own, pinned/priority/newest
* ``POST /projects/{p}/notifications/{id}/read``  the recipient marks it read
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.core.auth.access import CallerGrants, Capability, Grant, project_access, resolve_scope
from app.core.auth.deps import CurrentPrincipal, Principal
from app.core.config import Settings
from app.core.db import DbSession
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_tenancy, session_tenancy
from app.modules.notifications import service
from app.modules.notifications.schemas import (
    DeliveryRead,
    NotificationCreate,
    NotificationPost,
    NotificationRead,
    NotificationSent,
)
from app.modules.projects import service as projects

router = APIRouter(tags=["notifications"])

NOTIFICATIONS = "/projects/{project_id}/notifications"


def _bind_project(
    principal: Principal,
    grants: list[Grant],
    session: DbSession,
    project_id: uuid.UUID,
    capability: Capability,
) -> None:
    if not project_access(grants, project_id).has(capability):  # non-member: 404
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    base = session_tenancy(session) or TenancyContext(
        tenant_id=principal.tenant_id, user_id=principal.user_id
    )
    # A session holding objects cannot be rebound (``bind_tenancy``).
    session.expunge_all()
    bind_tenancy(session, resolve_scope(grants, base, project_id))
    projects.get_project(session, project_id)  # a deleted project: 404


@router.post(NOTIFICATIONS, response_model=NotificationSent, status_code=status.HTTP_201_CREATED)
def send_notification(
    project_id: uuid.UUID,
    body: NotificationPost,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
    request: Request,
) -> NotificationSent:
    _bind_project(principal, grants, session, project_id, Capability.ADMINISTER)
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
    _bind_project(principal, grants, session, project_id, Capability.VIEW)
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
    _bind_project(principal, grants, session, project_id, Capability.VIEW)
    return NotificationRead.model_validate(
        service.mark_read(session, notification_id, principal.user_id)
    )
