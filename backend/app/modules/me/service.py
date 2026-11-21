"""The caller's own projects (T-0014). Reads projects through ``projects.service`` only."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.core.auth.access import Grant
from app.modules.me.schemas import MyProject
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectAccessKind


def my_projects(
    session: Session, grants: Iterable[Grant], *, system_admin: bool = False
) -> list[MyProject]:
    """The live projects of ``grants`` with the caller's personas on each, by name then code;
    for a ``system_admin`` every live project of the tenant (ADR 0016, T-0275), those without
    a membership with ``access: system_admin`` and no personas.

    A deleted project, or one the session cannot see, is left out.
    """
    personas: dict[uuid.UUID, set[str]] = {}
    for grant in grants:
        personas.setdefault(grant.project_id, set()).add(grant.persona.value)
    if system_admin:
        found = projects.tenant_projects(session)
    else:
        found = projects.list_projects(session, set(personas))
    return [
        MyProject.model_validate(
            {
                "id": p.id,
                "code": p.code,
                "name": p.name,
                "personas": sorted(personas.get(p.id, ())),
                "access": (
                    ProjectAccessKind.MEMBER if p.id in personas else ProjectAccessKind.SYSTEM_ADMIN
                ),
            }
        )
        for p in sorted(found, key=lambda p: (p.name.casefold(), p.code))
    ]
