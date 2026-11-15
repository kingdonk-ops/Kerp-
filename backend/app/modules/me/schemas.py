"""Response shapes of the ``me`` API (T-0014)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.core.auth.access import Persona


class MyProject(BaseModel):
    """One project of the caller, for the project picker (T-0042)."""

    id: uuid.UUID
    code: str
    name: str
    personas: list[Persona]
