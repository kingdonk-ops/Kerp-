"""Request and response shapes of the notifications API (T-0019)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

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
