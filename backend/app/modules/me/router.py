"""Me API, mounted at ``/api/v1/me`` (T-0014).

* ``GET /projects``: the caller's live projects (id, code, name, personas) for the project
  picker (T-0042). Built from the caller's memberships only; a ``system_admin`` grant adds
  no project here.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.auth.access import CallerGrants
from app.core.db import DbSession
from app.modules.me import service
from app.modules.me.schemas import MyProject

router = APIRouter(tags=["me"])


@router.get("/projects", response_model=list[MyProject])
def my_projects(grants: CallerGrants, session: DbSession) -> list[MyProject]:
    return service.my_projects(session, grants)
