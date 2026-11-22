"""Response bodies of the mail admin API: masked recipients only, never a link or a body."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

SendStatus = Literal["queued", "sent", "delivered", "bounced", "complained", "failed", "suppressed"]


class MailSendRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    template_key: str
    recipient_masked: str
    recipient_hash: str
    status: SendStatus
    recipient_user_id: uuid.UUID | None
    project_id: uuid.UUID | None
    broadcast_id: uuid.UUID | None
    attempts: int
    provider_message_id: str | None
    error_code: str | None
    created_at: datetime
    sent_at: datetime | None
    status_at: datetime | None


class MailCountsRead(BaseModel):
    """Delivery-ledger counts (T-0226). ``sent``: accepted by SES (now sent, delivered,
    bounced or complained); ``delivered``: SES reported the delivery (now delivered or
    complained); the others: sends in that status. ``delivery_rate`` = delivered / sent
    (0..1, 4 decimals), ``null`` when nothing was sent."""

    model_config = ConfigDict(from_attributes=True)

    total: int
    queued: int
    sent: int
    delivered: int
    bounced: int
    complained: int
    failed: int
    suppressed: int
    delivery_rate: float | None


class MailTemplateCountsRead(MailCountsRead):
    template_key: str


class MailStatsRead(BaseModel):
    """Counts of the sends created in ``[from, to)`` (of ``template`` when given), in total and
    per template key. Counts only, never a row."""

    created_from: datetime | None
    created_to: datetime | None
    template_key: str | None
    totals: MailCountsRead
    by_template: list[MailTemplateCountsRead]


class MailSuppressionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recipient_masked: str
    recipient_hash: str
    reason: Literal["bounce", "complaint"]
    detail: str | None
    created_at: datetime
