"""Request and response shapes of the access-request API (T-0050, diff §4.1).

Request bodies forbid unknown fields (422): the requester, tenant, status and decider come
from the token and the server, never from the body. Personas and site scope on approval use
the admin module's ``GrantIn`` (PER-07 names its site, nobody else does).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.modules.access_requests.models import MESSAGE_MAX, REASON_MAX
from app.modules.admin.schemas import GrantRead, MemberAssign


def _utc(value: datetime) -> datetime:
    # SQLite hands TIMESTAMPTZ back naive; every value was stored in UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


UtcDatetime = Annotated[datetime, AfterValidator(_utc)]
Status = Literal["pending", "approved", "rejected", "withdrawn"]
# A lookup ticket is a compact JWT; anything longer is not one.
TICKET_MAX = 4096


class AccessRequestCreate(BaseModel):
    """``POST /access-requests``: ask for access to a project.

    ``lookup_ticket``: the ticket of a code lookup (``GET /lobby/projects/by-code/{code}``) for
    this project. A user's **first** request for a project needs it when the user is external,
    or internal and the project is not listed in the lobby; a new request after an earlier
    rejected or withdrawn one needs none.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    message: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=MESSAGE_MAX)] | None
    ) = None
    lookup_ticket: Annotated[str, StringConstraints(max_length=TICKET_MAX)] | None = None


class AccessRequestRead(BaseModel):
    """One of the caller's own requests (the lobby's "Active requests" panel).

    ``rejection_reason``: the decider's reason, on a ``rejected`` request only.
    ``request_again_at``: on a ``rejected`` request, when "Request again" is allowed (the
    re-request cooldown); ``null`` when a new request is allowed now, and on every other status.
    """

    id: uuid.UUID
    project_id: uuid.UUID
    project_code: str
    project_name: str
    status: Status
    message: str | None
    created_at: UtcDatetime
    decided_at: UtcDatetime | None
    withdrawn_at: UtcDatetime | None
    rejection_reason: str | None
    request_again_at: UtcDatetime | None


class Requester(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str


class PendingAccessRequest(BaseModel):
    """A pending request, as the project's PER-09 holders (and System admins) see it."""

    id: uuid.UUID
    project_id: uuid.UUID
    project_code: str
    project_name: str
    requester: Requester
    message: str | None
    created_at: UtcDatetime


class AccessRequestApprove(MemberAssign):
    """``POST /access-requests/{id}/approve``: the persona(s) and site scope to grant.

    They are added to any direct grants the requester already holds on the project. PER-09
    together with a hold-authority persona needs ``confirm_sod: true`` (else 409
    ``sod_confirmation_required``, TD-151).
    """


class AccessRequestReject(BaseModel):
    """``POST /access-requests/{id}/reject``: an optional reason, shown to the requester."""

    model_config = ConfigDict(extra="forbid")

    reason: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=REASON_MAX)] | None
    ) = None


class AccessRequestDecided(BaseModel):
    """The request after a decision."""

    id: uuid.UUID
    project_id: uuid.UUID
    requester_id: uuid.UUID
    status: Status
    decided_by: uuid.UUID | None
    decided_at: UtcDatetime | None
    rejection_reason: str | None
    granted: list[GrantRead] | None
    sod_warning: bool = Field(
        default=False,
        description="PER-09 together with a hold-authority persona (confirmed with confirm_sod)",
    )


class AccessRequestError(BaseModel):
    """A refusal with a machine-readable ``code`` (e.g. ``ACCESS_REQUEST_COOLDOWN``).
    ``retry_after``: when a new request becomes allowed (cooldown only)."""

    detail: str
    code: str
    retry_after: UtcDatetime | None = None
