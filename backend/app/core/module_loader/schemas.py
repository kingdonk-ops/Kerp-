"""API schemas of the module toggle endpoints (T-0011).

The endpoints are the ``projects`` module's (``/api/v1/projects/{project_id}/modules``, T-0084);
the shapes stay here, next to the service they describe.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ModuleState(BaseModel):
    """A loaded module and whether it is enabled for the project."""

    name: str
    version: str
    depends_on: list[str]
    toggleable: bool
    enabled: bool


class ModuleToggle(BaseModel):
    """Body of ``PUT /api/v1/projects/{project_id}/modules/{name}``."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
