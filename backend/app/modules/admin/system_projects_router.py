"""System console: per-project legal hold, mounted with the admin API at
``/api/v1/admin/system/projects/{project_id}/legal-hold`` (T-0223; diff §4.7 Legal hold).

``PUT`` with ``{on, reason}``: ``system_admin`` only; every other caller, a PER-09 of the
project included, gets 403 (an admin surface, not a project record). A reason of 1-500
characters is required both ways (422 otherwise). An unknown, deleted or other tenant's
project is 404. The projects service does the write and appends ``legal_hold_set`` /
``legal_hold_cleared`` to the tenant-level console chain ``admin-system:<tenant>`` in the
same transaction; a System admin needs no membership on the project (ADR 0016).
"""

from __future__ import annotations

import uuid
from typing import Final

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth.access import CallerIsSystemAdmin
from app.core.auth.deps import CurrentPrincipal
from app.core.db import DbSession
from app.modules.projects import service as projects
from app.modules.projects.schemas import LegalHoldChange, LegalHoldRead

LEGAL_HOLD: Final = "/system/projects/{project_id}/legal-hold"

router = APIRouter(tags=["admin"])


def _require_system_admin(is_admin: CallerIsSystemAdmin) -> None:
    if not is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


@router.put(
    LEGAL_HOLD, response_model=LegalHoldRead, dependencies=[Depends(_require_system_admin)]
)
def set_legal_hold(
    project_id: uuid.UUID, body: LegalHoldChange, principal: CurrentPrincipal, session: DbSession
) -> LegalHoldRead:
    """Put the project on legal hold or take it off; its hold as it now stands."""
    project = projects.set_legal_hold(
        session, project_id, body.on, body.reason, actor_id=principal.user_id
    )
    return LegalHoldRead.model_validate(project)
