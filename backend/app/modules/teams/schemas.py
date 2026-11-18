"""Request and response shapes of the teams API (T-0013).

Request bodies forbid unknown fields (422), so no tenant id can be supplied: the tenant comes
from the token. A link persona is one of PER-01..PER-14 except PER-09, plus PER-16 (TD-251);
PER-09 and PER-15 are individual assignments only and give 422 here.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.auth.access import PERSONAS, Persona, Scope
from app.core.auth.team_grants import LINK_PERSONAS
from app.modules.teams.models import DESCRIPTION_MAX, NAME_MAX

TeamName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX)
]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=DESCRIPTION_MAX)]


class TeamCreate(BaseModel):
    """``POST /teams``."""

    model_config = ConfigDict(extra="forbid")

    name: TeamName
    description: Description = ""


class TeamUpdate(BaseModel):
    """``PATCH /teams/{id}``: rename and/or describe."""

    model_config = ConfigDict(extra="forbid")

    name: TeamName | None = None
    description: Description | None = None

    @model_validator(mode="after")
    def _given(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("give at least one field to change")
        for name in sorted(self.model_fields_set):
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class MemberPut(BaseModel):
    """Optional body of ``PUT /teams/{id}/members/{user}``."""

    model_config = ConfigDict(extra="forbid")

    confirm_sod: bool = Field(
        default=False,
        description="Confirm that the user, an individual PER-09 of a linked project, gets a"
        " hold-authority persona there through this team (else 409 sod_warning)",
    )


class LinkPut(BaseModel):
    """``PUT /teams/{id}/projects/{project}``: the persona every member gets on the project."""

    model_config = ConfigDict(extra="forbid")

    persona: Persona = Field(
        description="PER-01..PER-14 except PER-09, or PER-16; PER-09 and PER-15 are"
        " individual assignments only (422)"
    )
    site_node_id: uuid.UUID | None = Field(
        default=None, description="The site of a PER-07 (Site Inspector) link; only then"
    )
    confirm_sod: bool = Field(
        default=False,
        description="Confirm a hold-authority persona for members who hold individual PER-09"
        " on the project (else 409 sod_warning)",
    )

    @model_validator(mode="after")
    def _link_role(self) -> Self:
        if self.persona not in LINK_PERSONAS:
            raise ValueError(
                f"{self.persona.value} is an individual assignment only, not a team link role"
            )
        site_scoped = PERSONAS[self.persona].scope is Scope.SITE
        if site_scoped and self.site_node_id is None:
            raise ValueError(f"{self.persona.value} needs a site_node_id")
        if not site_scoped and self.site_node_id is not None:
            raise ValueError(f"{self.persona.value} is not site-scoped: omit site_node_id")
        return self


class TeamRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    member_count: int = Field(description="Live members whose account is active")
    project_count: int = Field(description="Projects the team is linked to")


class TeamMemberRead(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    is_active: bool = Field(
        description="A deactivated member stays in the team; their access returns on reactivation"
    )


class TeamLinkRead(BaseModel):
    id: uuid.UUID = Field(description="The link id (source_id of the derived memberships)")
    project_id: uuid.UUID
    persona: Persona
    site_node_id: uuid.UUID | None


class TeamDetail(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    members: list[TeamMemberRead]
    links: list[TeamLinkRead]


class ProjectTeamRead(BaseModel):
    """A team linked to a project, with its role there."""

    team_id: uuid.UUID
    name: str
    link_id: uuid.UUID
    persona: Persona
    site_node_id: uuid.UUID | None
    member_count: int = Field(description="Live members whose account is active")


class SodWarning(BaseModel):
    """The users who would combine individual PER-09 with a hold-authority persona, and where."""

    user_ids: list[uuid.UUID]
    project_ids: list[uuid.UUID]
