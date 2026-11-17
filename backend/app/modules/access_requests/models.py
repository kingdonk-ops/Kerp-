"""Access requests ``oe_access_requests_request`` (T-0050, diff §4.1 access-request lifecycle).

One row per request a user files for one project: ``pending`` until a PER-09 of the project
(or a System admin) approves or rejects it, or the requester withdraws it. Decided and
withdrawn rows are kept as history; at most one ``pending`` row per user and project (partial
unique index). The row is tenant-level (``project_id`` NULL, like a membership): the requester
is not a member of ``requested_project_id`` and files it from a tenant-level session. It has
its own RLS policy (migration ``0023``, edge spec §6 amendments): visible to the requester, to
the PER-09 holders of the requested project (``app.admin_project_ids``) and to System admins;
only the requester inserts; nobody deletes.

``requested_project_id`` references the projects module by id only (no FK across modules).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, BaseRecordMixin, JSONType
from app.core.tenancy.rls import RlsPolicy, rls_table_info

TABLE: Final = "oe_access_requests_request"
PENDING_INDEX: Final = "uq_oe_access_requests_request_pending"
PENDING: Final = "pending"
APPROVED: Final = "approved"
REJECTED: Final = "rejected"
WITHDRAWN: Final = "withdrawn"
STATUSES: Final = (PENDING, APPROVED, REJECTED, WITHDRAWN)
MESSAGE_MAX: Final = 1000
REASON_MAX: Final = 500
_PENDING_ONLY: Final = "status = 'pending'"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class AccessRequest(Base, BaseRecordMixin):
    """A user's request for access to one project, and its outcome.

    ``granted``: on approval, the persona(s) and site scope the decider chose
    (``[{"persona": "PER-03", "site_node_id": null}, ...]``); the membership itself lives in
    the membership store. ``decided_by`` / ``decided_at``: who approved or rejected it and
    when; ``rejection_reason``: the decider's optional reason, shown to the requester.
    ``withdrawn_at``: when the requester withdrew it.
    """

    __tablename__ = TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint(_in("status", STATUSES), name="status"),
        CheckConstraint(
            f"message IS NULL OR length(message) <= {MESSAGE_MAX}", name="message_length"
        ),
        CheckConstraint(
            f"rejection_reason IS NULL OR (status = 'rejected'"
            f" AND length(rejection_reason) <= {REASON_MAX})",
            name="rejection_reason",
        ),
        CheckConstraint(
            "(status IN ('approved', 'rejected')) = (decided_at IS NOT NULL)", name="decided"
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'rejected') OR decided_by IS NOT NULL", name="decider"
        ),
        CheckConstraint(
            "decided_by IS NULL OR decided_by <> requester_id", name="never_self_decided"
        ),
        CheckConstraint("(status = 'withdrawn') = (withdrawn_at IS NOT NULL)", name="withdrawn"),
        # At most one pending request per user and project; history rows are not limited.
        Index(
            PENDING_INDEX,
            "tenant_id",
            "requester_id",
            "requested_project_id",
            unique=True,
            postgresql_where=text(_PENDING_ONLY),
            sqlite_where=text(_PENDING_ONLY),
        ),
        rls_table_info(
            RlsPolicy(
                "access_request",
                owner_column="requester_id",
                project_column="requested_project_id",
            )
        ),
    )

    requester_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("auth_user.id"), nullable=False, index=True
    )
    requested_project_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PENDING)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType(), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def closed_at(self) -> datetime | None:
        """When the request stopped being pending (decided or withdrawn)."""
        return self.decided_at or self.withdrawn_at
