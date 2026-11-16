"""Lobby API schemas (T-0040). A project is shown by id, code and name only (diff §4.1)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LobbyProject(BaseModel):
    """A project found in the lobby. ``member``: the caller already has effective access to
    it (direct or derived grants), so the UI opens it instead of offering a request."""

    id: uuid.UUID
    code: str
    name: str
    member: bool


class LobbyCodeMatch(LobbyProject):
    """The project with the code looked up, plus the **lookup ticket** a first access request
    for it needs when the caller is external or the project is not lobby-visible (T-0050)."""

    lookup_ticket: str = Field(description="Signed, bound to the caller and this project.")
    lookup_ticket_expires_at: datetime


class LobbyError(BaseModel):
    """A refusal with a machine-readable ``code`` (e.g. ``LOBBY_INTERNAL_ONLY``)."""

    detail: str
    code: str
