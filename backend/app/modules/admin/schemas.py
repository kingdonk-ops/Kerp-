"""Request and response shapes of the admin API (T-0014).

Every request body forbids unknown fields (422), so no tenant id, internal-staff flag
(``is_internal``) or Entra link (``entra_oid``, ``entra_tid``) can be supplied: the tenant
comes from the token, the flag and the link are derived (ADR 0009, TD-265). Personas come
from ``access.Persona`` (PER-01..PER-16, the same list as the membership CHECK, TD-256);
tenant-level grants are not personas and cannot be assigned here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.auth.access import CREATE_PROJECTS, PERSONAS, SYSTEM_ADMIN, Persona, Scope

MAX_GRANTS: Final = len(Persona)

Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$"
    ),
]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class UserCreate(BaseModel):
    """``POST /admin/users``: an account without credentials (invite / reset set them)."""

    model_config = ConfigDict(extra="forbid")

    email: Email
    display_name: DisplayName = ""


class UserUpdate(BaseModel):
    """``PATCH /admin/users/{id}``: rename, deactivate (``is_active: false``) or reactivate."""

    model_config = ConfigDict(extra="forbid")

    display_name: DisplayName | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _given(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("give at least one field to change")
        for name in sorted(self.model_fields_set):
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    is_active: bool
    is_internal: bool = Field(
        description="Internal staff: read-only, derived at sign-in and account creation"
    )


class UserLookupRequest(BaseModel):
    """``POST /admin/users/lookup``: an exact e-mail, in the body so it stays out of URLs."""

    model_config = ConfigDict(extra="forbid")

    email: Email


class UserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str


class GrantIn(BaseModel):
    """One persona on the project; a Site Inspector (PER-07) names its site, nobody else."""

    model_config = ConfigDict(extra="forbid")

    persona: Persona
    site_node_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _site(self) -> Self:
        site_scoped = PERSONAS[self.persona].scope is Scope.SITE
        if site_scoped and self.site_node_id is None:
            raise ValueError(f"{self.persona.value} needs a site_node_id")
        if not site_scoped and self.site_node_id is not None:
            raise ValueError(f"{self.persona.value} is not site-scoped: omit site_node_id")
        return self


class MemberAssign(BaseModel):
    """``PUT /admin/projects/{p}/members/{user}``: the user's complete set of personas on the
    project. ``confirm_sod`` confirms a PER-09 + hold-authority combination (else 409)."""

    model_config = ConfigDict(extra="forbid")

    grants: list[GrantIn] = Field(min_length=1, max_length=MAX_GRANTS)
    confirm_sod: bool = False

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        keys = [(g.persona, g.site_node_id) for g in self.grants]
        if len(set(keys)) != len(keys):
            raise ValueError("grants must not repeat")
        return self


class GrantSource(StrEnum):
    """Where a member's grant comes from (T-0073, ADR 0008)."""

    DIRECT = "direct"  # an individual assignment: this API changes it
    TEAM = "team"  # derived from a team link (``source_id``): read-only here


class GrantRead(BaseModel):
    persona: Persona
    site_node_id: uuid.UUID | None


class MemberGrantRead(GrantRead):
    """One live grant of a project member, with its source (T-0073, ADR 0008)."""

    source: GrantSource = Field(
        description="direct: individual assignment; team: derived from a team link, read-only"
    )
    source_id: uuid.UUID | None = Field(description="The team link id of a team-derived grant")
    team_name: str | None = Field(
        description="The team's name of a team-derived grant (teams service, T-0013)"
    )


class MemberRead(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    grants: list[MemberGrantRead]


class MemberAssigned(MemberRead):
    sod_warning: bool = Field(
        description="PER-09 together with a hold-authority persona (confirmed with confirm_sod)"
    )


# --- tenant-level admin grants (T-0039) -----------------------------------------------------


class AdminGrantKind(StrEnum):
    """The two tenant-level grants (``access.ADMIN_GRANTS``); neither is a persona."""

    SYSTEM_ADMIN = SYSTEM_ADMIN
    CREATE_PROJECTS = CREATE_PROJECTS


class AdminGrantIn(BaseModel):
    """``POST /admin/grants``: give ``grant`` to the user (``system_admin`` callers only)."""

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    grant: AdminGrantKind


class AdminGrantRead(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    grant: AdminGrantKind


# --- project invites (T-0052) ---------------------------------------------------------------

INVITE_TOKEN_MAX: Final = 512


class InviteCreate(BaseModel):
    """``POST /admin/projects/{p}/invites``: invite an e-mail address with persona(s) and site
    scope. ``confirm_sod`` confirms a PER-09 + hold-authority combination (else 409)."""

    model_config = ConfigDict(extra="forbid")

    email: Email
    grants: list[GrantIn] = Field(min_length=1, max_length=MAX_GRANTS)
    confirm_sod: bool = False

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        keys = [(g.persona, g.site_node_id) for g in self.grants]
        if len(set(keys)) != len(keys):
            raise ValueError("grants must not repeat")
        return self


class InviteRead(BaseModel):
    """An open invite of a project, as its administrators see it. Never the token."""

    id: uuid.UUID
    email: str
    grants: list[GrantRead]
    status: Literal["pending", "expired"] = Field(
        description="expired: past `expires_at` and never accepted (it can be re-sent)"
    )
    expires_at: datetime
    last_sent_at: datetime
    resend_available_at: datetime | None = Field(
        description=(
            "When the send cap of this project + e-mail allows the next re-send (1 per 60 s "
            "and 5 per 24 h, counting creations and re-sends); null: allowed now"
        )
    )


class InviteSent(InviteRead):
    """An invite right after a send (creation or re-send), with the copyable link. Shown
    once: the server keeps only a hash of the token. The link carries the `invite_token`
    only, so it can never set a password (diff §4.1)."""

    invite_token: str = Field(description="The single-use token of the link; shown once")
    invite_link: str = Field(description="`<web origin>/invite#<invite_token>` (URL fragment)")


class InviteCreated(InviteSent):
    sod_warning: bool = Field(
        description="PER-09 together with a hold-authority persona (confirmed with confirm_sod)"
    )


class InviteTokenIn(BaseModel):
    """The invite token of the link's fragment: always in the body, never in a URL."""

    model_config = ConfigDict(extra="forbid")

    invite_token: str = Field(min_length=16, max_length=INVITE_TOKEN_MAX, repr=False)


class InviteSetupIn(BaseModel):
    """Both tokens of the **e-mailed** invite link (`/invite#<invite_token>.<setup_token>`):
    always in the body, never in a URL. Loose bounds on purpose: any wrong token gets the
    neutral 410."""

    model_config = ConfigDict(extra="forbid")

    invite_token: str = Field(min_length=1, max_length=INVITE_TOKEN_MAX, repr=False)
    setup_token: str = Field(min_length=1, max_length=INVITE_TOKEN_MAX, repr=False)


class InviteAcceptWithPassword(InviteSetupIn):
    """Set the first password, accept the invite and sign in (one request)."""

    # Bounded so a huge body cannot make scrypt hash megabytes; the password policy
    # (minimum length, at most 256 characters, blocklist) answers with a ``code``.
    new_password: str = Field(min_length=1, max_length=1024, repr=False)


class InviteSetupPreview(BaseModel):
    """What the e-mailed link shows before sign-in. `can_set_password: false`: the account
    already has credentials, so sign in (the invite is accepted right after); the project is
    not named then."""

    can_set_password: bool
    project_code: str | None = None
    project_name: str | None = None


class InvitePreview(BaseModel):
    """What the "Join project" confirmation shows the invitee."""

    project_id: uuid.UUID
    project_code: str
    project_name: str
    inviter_name: str


class InviteAccepted(BaseModel):
    joined_project_id: uuid.UUID


class InviteRefusal(BaseModel):
    """A refused invite: ``code`` says what the client shows (the message is neutral)."""

    detail: str
    code: Literal[
        "INVITE_WRONG_USER",
        "INVITE_GONE",
        "INVITE_CONFLICT",
        "INVITE_NOT_RESENDABLE",
        "INVITE_SIGN_IN_REQUIRED",
    ]
