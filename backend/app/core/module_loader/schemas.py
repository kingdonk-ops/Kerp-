"""API schemas of the module toggle endpoints (T-0011)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ModuleState(BaseModel):
    """A loaded module and whether it is enabled for the caller's project."""

    name: str
    version: str
    depends_on: list[str]
    toggleable: bool
    enabled: bool


class ModuleToggle(BaseModel):
    """Body of ``PUT /api/v1/modules/{name}``."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
