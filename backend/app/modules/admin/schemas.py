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
from typing import Annotated, Any, Final, Literal, Self

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


class UserUpdateRefusal(BaseModel):
    """A refused ``PATCH /admin/users/{id}`` that carries a ``code`` (T-0085): the caller
    tried to deactivate their own account."""

    detail: str
    code: Literal["CANNOT_DEACTIVATE_SELF"]


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
    confirm_sod: bool = Field(
        default=False,
        description="Confirms system_admin for a user who holds a hold-authority persona"
        " (PER-02, PER-06, PER-07, PER-11) on a live project (else 409 sod_warning; ADR 0016)",
    )


class AdminGrantRead(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    grant: AdminGrantKind


class AdminGranted(AdminGrantRead):
    """``POST /admin/grants``: the grant, and whether it combined ``system_admin`` with a
    hold-authority persona (confirmed with ``confirm_sod``, audited ``sod_warning: true``)."""

    sod_warning: bool = Field(
        description="system_admin given to a holder of a hold-authority persona (ADR 0016)"
    )


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


# --- System console: audit explorer (T-0222, diff §4.7) --------------------------------------


class AuditEntryRead(BaseModel):
    """One ledger entry, as written (``actor_id`` is the entry's ``created_by``)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    chain_key: str
    seq: int
    created_at: datetime
    actor_id: uuid.UUID | None = Field(validation_alias="created_by")
    device_id: uuid.UUID | None
    source: str
    payload: dict[str, Any]
    prev_hash: str
    content_hash: str
    hash_version: int


class AuditPageRead(BaseModel):
    """``GET /admin/system/audit``: a page, newest first; pass ``next_cursor`` as ``cursor``."""

    items: list[AuditEntryRead]
    next_cursor: str | None


class ChainVerifyIn(BaseModel):
    """``POST /admin/system/audit/verify``: the chain key and its scope (a project, or
    ``null`` for the tenant level); the same key in two scopes is two chains."""

    model_config = ConfigDict(extra="forbid")

    chain_key: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    project_id: uuid.UUID | None = None


class ChainVerifyRead(BaseModel):
    """The verification report: ``first_broken_seq`` is the first entry that fails a ledger
    rule (after a removed entry: the one behind the gap); ``problems`` holds at most 100."""

    chain_key: str
    project_id: uuid.UUID | None
    ok: bool
    length: int
    head_hash: str
    first_broken_seq: int | None
    problem_count: int
    problems: list[str]


# --- System console: queue monitor (T-0218, diff §4.7) ---------------------------------------
# Numbers, names and sanitised text only: never a job's payload, result or SAQ kwargs.


class QueueRead(BaseModel):
    """One SAQ queue: SAQ's counts (deployment-wide) and the failed job rows."""

    name: Literal["interactive", "batch"]
    queued: int
    active: int
    scheduled: int
    failed: int
    oldest_queued_age_s: float | None


class CronJobRead(BaseModel):
    """A cron job of the batch worker (there is no cron queue)."""

    name: str
    schedule: str
    last_run_at: datetime | None
    last_finished_at: datetime | None
    last_outcome: Literal["succeeded", "failed"] | None
    next_run_at: datetime | None


class QueueOverviewRead(BaseModel):
    """``GET /admin/system/queues``."""

    queues: list[QueueRead]
    cron_jobs: list[CronJobRead]


class FailedJobRead(BaseModel):
    """``GET /admin/system/queues/failed``: one failed job, message at most 500 characters."""

    id: uuid.UUID
    kind: str
    queue: Literal["interactive", "batch"]
    project_id: uuid.UUID | None
    project_code: str | None
    attempts: int
    error_class: str
    message: str = Field(max_length=500)
    failed_at: datetime | None


class JobActionRead(BaseModel):
    """The job after a retry or an abort."""

    id: uuid.UUID
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]


# --- System console: deployment stats and health (T-0219, diff §4.7) ---------------------------


class ProjectCounts(BaseModel):
    active: int
    soft_deleted: int


class UserCounts(BaseModel):
    active: int
    deactivated: int


class DeviceCounts(BaseModel):
    active: int
    revoked: int


class StorageClassTotals(BaseModel):
    storage_class: Literal["STANDARD", "GLACIER_IR", "DEEP_ARCHIVE", "other"]
    objects: int
    bytes: int


class ObjectStoreTotals(BaseModel):
    """From the last ``admin.object_store_totals`` batch run (``as_of``: when it finished;
    ``null`` and no classes before the first run). ``refreshing``: a run is queued or running."""

    as_of: datetime | None
    refreshing: bool
    storage_classes: list[StorageClassTotals]


class SystemStatsRead(BaseModel):
    """``GET /admin/system/stats``: counts and byte totals only, never ids, names or rows.

    ``modules``: per module the counts its registered stats provider returns."""

    projects: ProjectCounts
    users: UserCounts
    devices: DeviceCounts
    modules: dict[str, dict[str, int]]
    object_store: ObjectStoreTotals


class ObjectStoreRefreshRead(BaseModel):
    """``POST /admin/system/stats/object-store/refresh`` (202) or the run in progress (409)."""

    job_id: uuid.UUID
    status: Literal["queued", "running"]


class HealthProbeRead(BaseModel):
    """One probe: status and latency only, never a host name, connection string or credential."""

    name: Literal["postgresql", "redis", "object_store", "malware_scanner"]
    status: Literal["ok", "degraded", "down"]
    latency_ms: float | None


class SystemHealthRead(BaseModel):
    """``GET /admin/system/health``: ``status`` is the worst probe's."""

    status: Literal["ok", "degraded", "down"]
    checked_at: datetime
    probes: list[HealthProbeRead]
