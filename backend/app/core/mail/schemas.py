"""Response bodies of the mail admin API: masked recipients only, never a link or a body."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

SendStatus = Literal["queued", "sent", "bounced", "complained", "failed", "suppressed"]


class MailSendRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    template_key: str
    recipient_masked: str
    recipient_hash: str
    status: SendStatus
    attempts: int
    provider_message_id: str | None
    error_code: str | None
    created_at: datetime
    sent_at: datetime | None
    status_at: datetime | None


class MailSuppressionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recipient_masked: str
    recipient_hash: str
    reason: Literal["bounce", "complaint"]
    detail: str | None
    created_at: datetime
