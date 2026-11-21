"""Response shapes of the ``me`` API (T-0014)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.auth.access import Persona
from app.modules.projects.schemas import ProjectAccessKind


class MyProject(BaseModel):
    """One project of the caller, for the project picker (T-0042)."""

    id: uuid.UUID
    code: str
    name: str
    personas: list[Persona] = Field(
        description="The caller's own personas on the project (empty for system_admin access)"
    )
    access: ProjectAccessKind = Field(
        description="member: through a membership; system_admin: only through the caller's "
        "system_admin grant (ADR 0016)"
    )
