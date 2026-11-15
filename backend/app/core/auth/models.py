"""The ``auth_user`` (T-0008), ``auth_project_membership`` (T-0010) and
``oe_identity_device`` (T-0043) tables.

A user is a tenant-level row (``project_id IS NULL``) of one tenant, under the same
project-scoped RLS as every table: a session bound to the tenant (no project) reads and
writes it; another tenant's users do not exist for it. Project access (membership,
personas) is ``ProjectMembership`` below; the user's enrolled field devices ``Device``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, BaseRecordMixin
from app.core.tenancy.rls import RlsPolicy, rls_table_info

TABLE: Final = "auth_user"
ENTRA_OID_INDEX: Final = "uq_auth_user_tenant_entra_oid"
# Entra object / tenant ids are GUIDs (36 chars); room for other forms without a migration.
ENTRA_ID_LENGTH: Final = 64


def normalize_email(email: str) -> str:
    """Emails are matched case-insensitively: stored and looked up lower-cased and trimmed."""
    return email.strip().lower()


class User(Base, BaseRecordMixin):
    """A person who signs in to one tenant.

    ``password_hash`` is ``NULL`` for users without a local password (e.g. SSO-only users
    later, TD-043). ``password_changed_at`` invalidates every token issued before it.

    ``is_internal`` (T-0049, ADR 0009) is derived, never input: true when the account is
    linked to a member account of the company Entra ID tenant (``entra_oid``, set by the
    first SSO sign-in, T-0054) or its e-mail domain is on ``KERP_INTERNAL_EMAIL_DOMAINS``.
    It is recomputed at creation and at every sign-in (``service.refresh_internal_flag``)
    and grants nothing by itself. Other modules read it through
    ``service.is_internal_user``.
    """

    __tablename__ = TABLE
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_auth_user_tenant_email"),
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        # One KERP account per Entra object id in a tenant (diff §4.5); unlinked accounts
        # (NULL) do not collide. A unique index, so SQLite can add it without a table copy.
        Index(ENTRA_OID_INDEX, "tenant_id", "entra_oid", unique=True),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    entra_oid: Mapped[str | None] = mapped_column(String(ENTRA_ID_LENGTH), nullable=True)
    entra_tid: Mapped[str | None] = mapped_column(String(ENTRA_ID_LENGTH), nullable=True)


MEMBERSHIP_TABLE: Final = "auth_project_membership"
# PER-01 ... PER-16 (edge spec §5.2; PER-16 Project Viewer since migration 0012, T-0076).
# The capability matrix lives in ``access.py``.
PERSONA_CODES: Final = tuple(f"PER-{n:02d}" for n in range(1, 17))
SITE_INSPECTOR: Final = "PER-07"


class ProjectMembership(Base, BaseRecordMixin):
    """One persona of one user on one project (T-0010): the membership store.

    A tenant-level row (``project_id IS NULL``): the project it grants is
    ``member_project_id``. Under the membership-based policy (T-0014, TD-053) a row is
    visible to its own user (so the scope resolver reads the caller's memberships across
    projects), to sessions that see its project, and to ``system_admin`` sessions.
    A Site Inspector (PER-07) membership names its site (Asset-Site scoping). Revoking a
    membership soft-deletes it.
    """

    __tablename__ = MEMBERSHIP_TABLE
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "member_project_id",
            "persona",
            "site_node_id",
            name="uq_auth_project_membership_grant",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint(
            "persona IN (" + ", ".join(f"'{code}'" for code in PERSONA_CODES) + ")",
            name="persona",
        ),
        CheckConstraint(
            f"persona <> '{SITE_INSPECTOR}' OR site_node_id IS NOT NULL", name="inspector_site"
        ),
        # Membership-based policy (T-0014, TD-053): visible for the described project or the
        # caller's own rows (the scope resolver reads those, TD-152); migration 0015.
        rls_table_info(
            RlsPolicy("membership", owner_column="user_id", project_column="member_project_id")
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey(f"{TABLE}.id"), nullable=False, index=True
    )
    member_project_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    persona: Mapped[str] = mapped_column(String(6), nullable=False)
    site_node_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)


DEVICE_TABLE: Final = "oe_identity_device"
DEVICE_PLATFORMS: Final = ("android", "ios", "web")
DEVICE_ACTIVE: Final = "active"
DEVICE_REVOKED: Final = "revoked"
DEVICE_STATUSES: Final = (DEVICE_ACTIVE, DEVICE_REVOKED)
VERSION_LENGTH: Final = 64
WIPE_SECRET_HASH_LENGTH: Final = 64  # SHA-256, hex
PIN_HASH_LENGTH: Final = 255  # scrypt, as ``passwords.hash_password`` writes it


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class Device(Base, BaseRecordMixin):
    """An enrolled field device of one user (T-0043, diff §4.3): the device registry.

    ``device_id`` is generated by the client (a UUID, unique per tenant) and names the
    device in the API and in the audit ledger; ``id`` is the row's own key. A device
    belongs to the user who enrolled it. ``active`` until revoked; a revoked device stays
    revoked (the wiped client enrols again under a new ``device_id``), with who revoked it
    (``revoked_by``), when (``revoked_at``) and when the client confirmed its wipe
    (``wipe_confirmed_at``, with the wipe secret whose hash is ``wipe_secret_hash``; T-0080).
    The field PIN belongs to the device (T-0038, diff §4.2): ``pin_hash`` (scrypt) is set
    once at enrolment and changed only from a session of the device; ``pin_set_at`` says when.
    ``last_seen_at`` is the last authenticated request through the device,
    ``last_sync_at`` the last successful sync (set by the sync endpoints).

    A tenant-level row under the standard policy; the device service decides who sees or
    revokes which device (owner, ``system_admin``, PER-09 of a project the owner belongs to).
    """

    __tablename__ = DEVICE_TABLE
    __table_args__ = (
        UniqueConstraint("tenant_id", "device_id", name="uq_oe_identity_device_tenant_device"),
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint(_in("platform", DEVICE_PLATFORMS), name="platform"),
        CheckConstraint(_in("status", DEVICE_STATUSES), name="status"),
        CheckConstraint(
            f"(status = '{DEVICE_ACTIVE}' AND revoked_at IS NULL AND wipe_confirmed_at IS NULL)"
            f" OR (status = '{DEVICE_REVOKED}' AND revoked_at IS NOT NULL)",
            name="revocation",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey(f"{TABLE}.id"), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    os_version: Mapped[str] = mapped_column(String(VERSION_LENGTH), nullable=False, default="")
    app_version: Mapped[str] = mapped_column(String(VERSION_LENGTH), nullable=False, default="")
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=DEVICE_ACTIVE)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    wipe_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # SHA-256 (hex) of the per-device wipe secret issued once at enrolment (T-0080); never the
    # secret itself. ``None`` for a device enrolled before migration ``0018``: it cannot
    # confirm its wipe.
    wipe_secret_hash: Mapped[str | None] = mapped_column(
        String(WIPE_SECRET_HASH_LENGTH), nullable=True
    )
    # The device's field PIN (T-0038, diff §4.2 *Field PIN, hybrid*): scrypt hash, never the
    # PIN. ``None`` until the client sets one (enrolment): such a device's sessions are not
    # PIN-gated. The failure counter is per device in the revocation store (``lockout.py``).
    pin_hash: Mapped[str | None] = mapped_column(String(PIN_HASH_LENGTH), nullable=True)
    pin_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
