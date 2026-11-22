"""Audit ledger: ``impersonated_by_user_id`` and hash v3 (T-0279, ADR 0015 §6).

Core-table change (ADR 0015, following ADR 0010): additive on ``audit_ledger``.

* ``impersonated_by_user_id`` (UUID, nullable, no foreign key: the ledger outlives users and
  must never block or cascade): the ``system_admin`` who acted as ``created_by`` through an
  impersonation token. Every existing row gets ``NULL``.
* CHECK ``hash_version IN (1, 2)`` becomes ``hash_version IN (1, 2, 3)``; new CHECK
  ``hash_version >= 3 OR impersonated_by_user_id IS NULL`` (a v1/v2 row cannot claim an
  impersonator its hash would not cover). Like the other ledger CHECKs they hold even for a
  superuser with the append-only triggers disabled.

Existing rows keep their ``hash_version`` (1 or 2) and keep verifying. On PostgreSQL
``ADD COLUMN`` without a default rewrites nothing and fires no row trigger, so the append-only
guard of ``0002`` (REVOKE + triggers) is untouched. Runtime role (ADR 0019): table-level
``SELECT, INSERT`` from ``0039`` cover the new column; nothing is granted or revoked. No
policy changes. SQLite cannot change a CHECK in place, so batch mode copies the table from the
frozen definition below (offline ``--sql`` works; SQLite has no guard to keep).

Downgrade (development databases only, ADR 0015 / ADR 0010): drops the column and restores
``hash_version IN (1, 2)``. It fails while any v3 entry exists: a downgrade can never strip
the admin from an impersonated entry silently. Production rolls back the code, not the schema.

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEDGER = "audit_ledger"
COLUMN = "impersonated_by_user_id"
VERSION_CHECK = "hash_version"
VERSIONS_BEFORE = "hash_version IN (1, 2)"
VERSIONS_AFTER = "hash_version IN (1, 2, 3)"
IMPERSONATOR_CHECK = ("pre_v3_no_impersonator", f"hash_version >= 3 OR {COLUMN} IS NULL")
NIL_UUID = "00000000-0000-0000-0000-000000000000"  # scope_id of tenant-level chains


def _name(check: str) -> str:
    return f"ck_{LEDGER}_{check}"


def _frozen_ledger(*, with_impersonator: bool) -> sa.Table:
    """``audit_ledger`` as of ``0042`` (``with_impersonator=False``) or this revision, for the
    SQLite batch copy: ``0002`` + ``0016`` (device, hash version) + ``0026`` (tenant FK)."""
    if with_impersonator:
        extra: list[sa.SchemaItem] = [
            sa.Column(COLUMN, GUID(), nullable=True),
            sa.CheckConstraint(VERSIONS_AFTER, name=_name(VERSION_CHECK)),
            sa.CheckConstraint(IMPERSONATOR_CHECK[1], name=_name(IMPERSONATOR_CHECK[0])),
        ]
    else:
        extra = [sa.CheckConstraint(VERSIONS_BEFORE, name=_name(VERSION_CHECK))]
    return sa.Table(
        LEDGER,
        sa.MetaData(),
        sa.Column("scope_id", GUID(), nullable=False),
        sa.Column("chain_key", sa.String(length=200), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", JSONType(), nullable=False),
        sa.Column("id", GUID(), nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.Column("project_id", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("custom_fields", JSONType(), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("device_id", GUID(), nullable=True),
        sa.Column("hash_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.CheckConstraint("seq >= 1", name=_name("seq_positive")),
        sa.CheckConstraint(
            f"scope_id = COALESCE(project_id, '{NIL_UUID}')", name=_name("scope_id")
        ),
        sa.CheckConstraint("deleted_at IS NULL", name=_name("not_deleted")),
        sa.CheckConstraint("revision = 1", name=_name("first_revision")),
        sa.CheckConstraint("custom_fields = '{}'", name=_name("no_custom_fields")),
        sa.CheckConstraint("hash_version >= 2 OR device_id IS NULL", name=_name("v1_no_device")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{LEDGER}_tenant_id_tenants"
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{LEDGER}"),
        sa.UniqueConstraint(
            "tenant_id", "scope_id", "chain_key", "seq", name=f"uq_{LEDGER}_chain_seq"
        ),
        sa.Index(f"ix_{LEDGER}_tenant_id", "tenant_id"),
        sa.Index(f"ix_{LEDGER}_project_id", "project_id"),
        *extra,
    )


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.add_column(LEDGER, sa.Column(COLUMN, GUID(), nullable=True))
        op.drop_constraint(op.f(_name(VERSION_CHECK)), LEDGER, type_="check")
        op.create_check_constraint(op.f(_name(VERSION_CHECK)), LEDGER, VERSIONS_AFTER)
        op.create_check_constraint(
            op.f(_name(IMPERSONATOR_CHECK[0])), LEDGER, IMPERSONATOR_CHECK[1]
        )
        return
    frozen = _frozen_ledger(with_impersonator=False)
    with op.batch_alter_table(LEDGER, copy_from=frozen, recreate="always") as batch:
        batch.add_column(sa.Column(COLUMN, GUID(), nullable=True))
        batch.drop_constraint(op.f(_name(VERSION_CHECK)), type_="check")
        batch.create_check_constraint(op.f(_name(VERSION_CHECK)), sa.text(VERSIONS_AFTER))
        batch.create_check_constraint(
            op.f(_name(IMPERSONATOR_CHECK[0])), sa.text(IMPERSONATOR_CHECK[1])
        )


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        # First, so a v3 entry stops the downgrade before anything is dropped.
        op.drop_constraint(op.f(_name(VERSION_CHECK)), LEDGER, type_="check")
        op.create_check_constraint(op.f(_name(VERSION_CHECK)), LEDGER, VERSIONS_BEFORE)
        op.drop_constraint(op.f(_name(IMPERSONATOR_CHECK[0])), LEDGER, type_="check")
        op.drop_column(LEDGER, COLUMN)
        return
    frozen = _frozen_ledger(with_impersonator=True)
    with op.batch_alter_table(LEDGER, copy_from=frozen, recreate="always") as batch:
        batch.drop_constraint(op.f(_name(VERSION_CHECK)), type_="check")
        batch.create_check_constraint(op.f(_name(VERSION_CHECK)), sa.text(VERSIONS_BEFORE))
        batch.drop_constraint(op.f(_name(IMPERSONATOR_CHECK[0])), type_="check")
        batch.drop_column(COLUMN)
