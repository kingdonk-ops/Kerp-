"""Me API, mounted at ``/api/v1/me`` (T-0014).

* ``GET /projects``: the caller's live projects (id, code, name, personas, ``access``) for the
  project picker (T-0042). Built from the caller's memberships; for a ``system_admin`` every
  live project of its tenant, those without a membership marked ``access: system_admin``
  (ADR 0016, T-0275). The flag is the one the session was bound with, never the request.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.auth.access import CallerGrants, CallerIsSystemAdmin
from app.core.db import DbSession
from app.modules.me import service
from app.modules.me.schemas import MyProject

router = APIRouter(tags=["me"])


@router.get("/projects", response_model=list[MyProject])
def my_projects(
    grants: CallerGrants, is_admin: CallerIsSystemAdmin, session: DbSession
) -> list[MyProject]:
    return service.my_projects(session, grants, system_admin=is_admin)
