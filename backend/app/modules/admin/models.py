"""Tenant-level admin grants ``oe_admin_grant`` (T-0014, diff §4.1).

One row per user and grant: ``system_admin`` (runs the deployment) or ``create_projects``
(Project Admin). A grant is neither a persona nor a project membership and confers no
capability (no ``A``/``S``, TD-151). The row is tenant-level (no project) and has its own RLS
policy (migration ``0015``): a user reads their own rows (``app.current_user_id``); an
``app.is_system_admin`` session reads and writes all; nobody else writes. Revoking a grant
soft-deletes it; at most one live row per user and grant.

Project invites ``oe_admin_project_invite`` (T-0052, diff §4.1 Invites): ``ProjectInvite``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.auth.access import ADMIN_GRANTS
from app.core.db import GUID, Base, BaseRecordMixin, JSONType
from app.core.tenancy.rls import RlsPolicy, rls_table_info

TABLE: Final = "oe_admin_grant"
LIVE_INDEX: Final = "uq_oe_admin_grant_live"
_LIVE: Final = "deleted_at IS NULL"


class AdminGrant(Base, BaseRecordMixin):
    __tablename__ = TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint("kind IN (" + ", ".join(f"'{g}'" for g in ADMIN_GRANTS) + ")", name="kind"),
        Index(
            LIVE_INDEX,
            "tenant_id",
            "user_id",
            "kind",
            unique=True,
            postgresql_where=text(_LIVE),
            sqlite_where=text(_LIVE),
        ),
        rls_table_info(RlsPolicy("admin_grant", owner_column="user_id")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("auth_user.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)


# --- project invites (T-0052, diff §4.1 Invites) --------------------------------------------

INVITE_TABLE: Final = "oe_admin_project_invite"
INVITE_PENDING_INDEX: Final = "uq_oe_admin_project_invite_pending"
INVITE_TOKEN_INDEX: Final = "uq_oe_admin_project_invite_token_hash"
INVITE_PENDING: Final = "pending"
INVITE_ACCEPTED: Final = "accepted"
INVITE_REVOKED: Final = "revoked"
INVITE_EXPIRED: Final = "expired"
INVITE_STATUSES: Final = (INVITE_PENDING, INVITE_ACCEPTED, INVITE_REVOKED, INVITE_EXPIRED)
_INVITE_PENDING_ONLY: Final = "status = 'pending'"


class ProjectInvite(Base, BaseRecordMixin):
    """An invitation of one e-mail address to one project.

    The row is tenant-level (``project_id`` NULL, like a membership: the invitee is not a
    member yet) and names its project in ``invited_project_id`` (no FK across modules). Its
    own RLS policy (``project_invite``, migration ``0030``) shows it only to the PER-09
    holders of that project and to System admins; the invitee never reads it and accepts by
    token through the narrow system path of ``invites.py``.

    * ``invited_email``: normalised (``auth.models.normalize_email``); the invite is bound to it.
    * ``grants``: the persona(s) and site scope the membership gets on acceptance
      (``[{"persona": "PER-14", "site_node_id": null}, ...]``).
    * ``token_hash``: SHA-256 (hex) of the random ``invite_token``. The token itself is never
      stored or logged; a re-send replaces the hash, which ends the old link.
    * ``setup_token_hash`` (T-0059): SHA-256 (hex) of the ``setup_token`` that only the invite
      e-mail carries; with the ``invite_token`` it lets a brand-new invitee set a first
      password. Never stored in clear; replaced by a re-send, cleared (``NULL``) once the
      invite is accepted or revoked. Same expiry as the invite.
    * ``expires_at``: creation or latest re-send + ``KERP_INVITE_TTL_HOURS``.
    * ``last_sent_at`` / ``sent_at``: the latest send, and every send of this row (creation
      and re-sends, ISO 8601 UTC) inside the rate-limit window. The send cap is counted over
      every row of the same project and e-mail, so revoking and re-creating never resets it.
    * ``status``: ``pending | accepted | revoked | expired``. ``expired`` is stored lazily (a
      pending row past ``expires_at`` already counts as expired); accepted and revoked rows
      are history. At most one ``pending`` row per project and e-mail (partial unique index).
    """

    __tablename__ = INVITE_TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in INVITE_STATUSES) + ")", name="status"
        ),
        CheckConstraint("length(token_hash) = 64", name="token_hash_sha256"),
        CheckConstraint(
            "(status = 'accepted') = (accepted_at IS NOT NULL)"
            " AND (status = 'accepted') = (accepted_by IS NOT NULL)",
            name="accepted",
        ),
        # At most one pending invite per project and e-mail; history rows are not limited.
        Index(
            INVITE_PENDING_INDEX,
            "tenant_id",
            "invited_project_id",
            "invited_email",
            unique=True,
            postgresql_where=text(_INVITE_PENDING_ONLY),
            sqlite_where=text(_INVITE_PENDING_ONLY),
        ),
        Index(INVITE_TOKEN_INDEX, "token_hash", unique=True),
        rls_table_info(RlsPolicy("project_invite", project_column="invited_project_id")),
    )

    invited_project_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    invited_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    grants: Mapped[list[dict[str, Any]]] = mapped_column(JSONType(), nullable=False)
    inviter_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("auth_user.id"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    setup_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[list[str]] = mapped_column(JSONType(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=INVITE_PENDING)
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
