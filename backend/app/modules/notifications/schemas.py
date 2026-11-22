"""Request and response shapes of the notifications API (T-0019; broadcasts T-0228)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.core.mail.schemas import MailCountsRead

Priority = Literal["low", "normal", "high", "critical"]

# Keys the client looks up with ``t()``: ``notifications.kind.<kind>``, ``...label.<label>``.
MessageKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")]
Kind = Annotated[MessageKey, StringConstraints(max_length=64)]
Label = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
PinKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,119}$")]
ParamKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
ParamValue = Annotated[str, StringConstraints(max_length=200)] | int | float | bool | None
MAX_PARAMS = 20


class NotificationPost(BaseModel):
    """``POST /notifications/projects/{project_id}/notifications``.

    No pin over the API: a pin is cleared by the action of the module that set it, and an
    API caller has no such action.
    """

    model_config = ConfigDict(extra="forbid")

    recipient_id: uuid.UUID
    kind: Kind
    params: dict[ParamKey, ParamValue] = Field(default_factory=dict, max_length=MAX_PARAMS)
    label: Label | None = None
    priority: Priority = "normal"
    subject_type: Kind | None = None
    subject_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _subject_pair(self) -> Self:
        if (self.subject_type is None) != (self.subject_id is None):
            raise ValueError("subject_type and subject_id go together")
        return self


class NotificationCreate(NotificationPost):
    """What another module passes to ``service.notify``; it may pin.

    ``pin_key`` names the action that clears the pin, e.g. ``handover.ack:<handover id>``.
    """

    pinned: bool = False
    pin_key: PinKey | None = None

    @model_validator(mode="after")
    def _pin_pair(self) -> Self:
        if self.pinned != (self.pin_key is not None):
            raise ValueError("a pinned notification needs a pin_key, and only it")
        return self


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    recipient_id: uuid.UUID
    kind: str
    params: dict[str, Any]
    label: str | None
    priority: Priority
    pinned: bool
    read_at: datetime | None
    subject_type: str | None
    subject_id: uuid.UUID | None
    created_at: datetime


class DeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    channel: str
    ok: bool
    detail: str | None


class NotificationSent(BaseModel):
    """The stored notification and each channel's outcome."""

    notification: NotificationRead
    deliveries: list[DeliveryRead]


# --- broadcast e-mail (T-0228) ----------------------------------------------------------------

BroadcastScope = Literal["tenant", "projects"]
BroadcastCategory = Literal["internal", "external", "client"]
BroadcastStatus = Literal["draft", "queued", "sending", "sent", "failed"]
PersonaCode = Literal[
    "PER-01",
    "PER-02",
    "PER-03",
    "PER-04",
    "PER-05",
    "PER-06",
    "PER-07",
    "PER-08",
    "PER-09",
    "PER-10",
    "PER-11",
    "PER-12",
    "PER-13",
    "PER-14",
    "PER-15",
    "PER-16",
]
MAX_BROADCAST_PROJECTS = 100
MAX_SUBJECT = 200
MAX_BODY = 10_000
# Plain text: no control characters (the body may hold line breaks and tabs).
_SUBJECT_CONTROL = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")
_BODY_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u2028\u2029]")


class BroadcastAudienceWrite(BaseModel):
    """Who a broadcast goes to. ``tenant`` (System admin only): every active user;
    ``projects``: the users with a live membership on ``project_ids`` (a PER-09: only its own
    PER-09 projects). ``category`` and ``personas`` narrow it; both together must match."""

    model_config = ConfigDict(extra="forbid")

    scope: BroadcastScope
    project_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_BROADCAST_PROJECTS)
    category: BroadcastCategory | None = None
    personas: list[PersonaCode] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def _scope_projects(self) -> Self:
        if self.scope == "tenant" and self.project_ids:
            raise ValueError("a tenant-wide broadcast names no projects")
        if self.scope == "projects" and not self.project_ids:
            raise ValueError("name at least one project")
        if len(set(self.project_ids)) != len(self.project_ids):
            raise ValueError("project_ids must not repeat")
        if len(set(self.personas)) != len(self.personas):
            raise ValueError("personas must not repeat")
        return self


class BroadcastWrite(BroadcastAudienceWrite):
    """``POST /notifications/broadcasts``: the audience plus a plain-text subject and body.
    Line breaks in the body are kept (``\\r\\n`` becomes ``\\n``); every value is escaped when
    the e-mail is rendered."""

    subject: Annotated[str, StringConstraints(min_length=1, max_length=MAX_SUBJECT)]
    body: Annotated[str, StringConstraints(min_length=1, max_length=MAX_BODY)]

    @field_validator("subject")
    @classmethod
    def _plain_subject(cls, value: str) -> str:
        value = value.strip()
        if not value or _SUBJECT_CONTROL.search(value):
            raise ValueError("plain text on one line")
        return value

    @field_validator("body")
    @classmethod
    def _plain_body(cls, value: str) -> str:
        value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not value or _BODY_CONTROL.search(value):
            raise ValueError("plain text only")
        return value


class BroadcastPreviewRead(BaseModel):
    """How many users the broadcast would reach now: a count, never a list."""

    recipient_count: int


class BroadcastRead(BaseModel):
    """A broadcast with its counts. ``delivery``: the send log's counts for it (T-0226), on
    ``GET /broadcasts/{id}`` only."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender_id: uuid.UUID
    scope: BroadcastScope
    project_ids: list[uuid.UUID]
    category: BroadcastCategory | None
    personas: list[PersonaCode]
    subject: str
    body: str
    status: BroadcastStatus
    recipient_count: int
    queued_count: int
    skipped_count: int
    created_at: datetime
    finished_at: datetime | None
    delivery: MailCountsRead | None = None


class UnsubscribeWrite(BaseModel):
    """``POST /notifications/broadcasts/unsubscribe``: the opaque token from the e-mail's link.
    Any string is accepted (a malformed one gets the same answer as a valid one)."""

    token: str | None = None


class UnsubscribeRead(BaseModel):
    """The one answer of the one-click opt-out, whatever the token."""

    status: Literal["ok"] = "ok"
