"""The hook between core mail and the job queue (diff §4.6: core never imports a module).

``send_mail`` stages a ``QueuedMail`` on the caller's session; once the caller's transaction
commits, the staged mails go to the installed enqueuer. The ``jobs`` module installs it at
start-up (``app.modules.jobs.mail.install``: SAQ interactive queue); core only knows this
callable. No enqueuer installed (no Redis): the send-log rows stay ``queued`` and a warning
is logged; nothing is ever sent from the request.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.core.mail.templates import ContextValue


@dataclass(frozen=True, slots=True)
class QueuedMail:
    """One committed send waiting for the worker.

    ``context`` may hold a secret (a link token): it travels only through the queue's
    short-lived payload store, never through the send log, the SAQ job kwargs or a log line.
    """

    send_id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID | None
    template_key: str
    to: str
    locale: str | None
    context: Mapping[str, ContextValue]
    request_id: str | None

    def __repr__(self) -> str:  # never the address or the context
        return f"QueuedMail(send_id={self.send_id}, template_key={self.template_key!r})"

    def to_payload(self) -> dict[str, Any]:
        return {
            "send_id": str(self.send_id),
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id) if self.project_id else None,
            "template_key": self.template_key,
            "to": self.to,
            "locale": self.locale,
            "context": dict(self.context),
            "request_id": self.request_id,
        }

    @classmethod
    def from_payload(cls, data: Mapping[str, Any]) -> QueuedMail:
        project = data.get("project_id")
        context = data["context"]
        if not isinstance(context, dict):
            raise ValueError("mail payload context must be an object")
        return cls(
            send_id=uuid.UUID(str(data["send_id"])),
            tenant_id=uuid.UUID(str(data["tenant_id"])),
            project_id=uuid.UUID(str(project)) if project else None,
            template_key=str(data["template_key"]),
            to=str(data["to"]),
            locale=str(data["locale"]) if data.get("locale") else None,
            context=context,
            request_id=str(data["request_id"]) if data.get("request_id") else None,
        )


MailEnqueuer = Callable[[Sequence[QueuedMail]], None]

_enqueuer: MailEnqueuer | None = None


def set_mail_enqueuer(enqueuer: MailEnqueuer | None) -> None:
    """Install (or remove) the process's post-commit mail enqueuer (jobs module, start-up)."""
    global _enqueuer
    _enqueuer = enqueuer


def mail_enqueuer() -> MailEnqueuer | None:
    return _enqueuer
