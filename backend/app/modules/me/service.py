"""The caller's own projects (T-0014). Reads projects through ``projects.service`` only."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.core.auth.access import Grant
from app.modules.me.schemas import MyProject
from app.modules.projects import service as projects


def my_projects(session: Session, grants: Iterable[Grant]) -> list[MyProject]:
    """The live projects of ``grants`` with the caller's personas on each, by name then code.

    A deleted project, or one the session cannot see, is left out.
    """
    personas: dict[uuid.UUID, set[str]] = {}
    for grant in grants:
        personas.setdefault(grant.project_id, set()).add(grant.persona.value)
    found = projects.list_projects(session, set(personas))
    return [
        MyProject.model_validate(
            {"id": p.id, "code": p.code, "name": p.name, "personas": sorted(personas[p.id])}
        )
        for p in sorted(found, key=lambda p: (p.name.casefold(), p.code))
    ]
