"""Response bodies of the mail admin API: masked recipients only, never a link or a body."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


# --- automated template overrides (T-0227) ----------------------------------------------------

Audience = Literal["internal_staff", "client", "external_party", "deployment_wide"]


class MailTemplateTextRead(BaseModel):
    """A template's rewordable texts in one locale, unformatted (``{name}`` placeholders).
    ``action_label`` is ``null`` for a template without a link."""

    model_config = ConfigDict(from_attributes=True)

    subject: str
    intro: str
    outro: str
    action_label: str | None


class MailTemplateTextWrite(BaseModel):
    """Plain text only, with the template's declared placeholders (``{user_name}``); no HTML,
    no URL, never the link secret. ``action_label`` only for a template with a link. Checked
    by the server (422 with one entry per refused field)."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(max_length=200)
    intro: str = Field(max_length=2000)
    outro: str = Field(max_length=2000)
    action_label: str | None = Field(default=None, max_length=80)


class MailTemplateOverrideRead(MailTemplateTextRead):
    """One revision of an override. ``version`` numbers the revisions; ``is_current``: the
    one sends use now (none current: the shipped text)."""

    id: uuid.UUID
    template_key: str
    locale: str
    version: int
    is_current: bool
    created_at: datetime
    created_by: uuid.UUID | None


class MailTemplateLocaleRead(BaseModel):
    locale: str
    shipped: MailTemplateTextRead
    override: MailTemplateOverrideRead | None


class MailTemplateRead(BaseModel):
    """A registered template: its audience group, whether it is required (no empty subject
    or text block), whether it has an action link, the placeholders its texts may use, and
    per locale the shipped text and the current override."""

    key: str
    audience: Audience
    required: bool
    has_action: bool
    placeholders: list[str]
    locales: list[MailTemplateLocaleRead]


class MailPreviewRead(BaseModel):
    """The rendered e-mail with synthetic sample values. ``html`` is a complete document."""

    subject: str
    text: str
    html: str


class MailTestSendRead(BaseModel):
    send_id: uuid.UUID
