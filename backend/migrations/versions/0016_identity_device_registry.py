"""Device registry ``oe_identity_device`` + ``device_id`` in the audit ledger (T-0043, ADR 0010).

* New tenant-level table ``oe_identity_device`` (diff §4.3): one enrolled field device of a
  user (client-generated ``device_id``, unique per tenant), platform, OS and app version,
  enrolment / last-seen / last-sync times, ``active | revoked`` with who revoked it when, and
  when the client confirmed its wipe. Standard project-scoped RLS (T-0007); who may see or
  revoke a device is decided by the service (owner, ``system_admin``, PER-09 of a shared
  project).
* ``audit_ledger`` (core table, ADR 0010): additive nullable ``device_id`` and
  ``hash_version`` (not null, server default ``1``, so every existing row keeps verifying
  with the v1 hash). New rows are written with ``hash_version = 2``, whose content hash also
  covers ``device_id``. CHECKs: ``hash_version IN (1, 2)``; a v1 row carries no device. On
  PostgreSQL ``ADD COLUMN`` with a constant default rewrites nothing and fires no row
  trigger, so the append-only guard of ``0002`` is untouched.

Downgrade drops the table and the two ledger columns. Entries written as v2 lose their
``device_id`` and ``hash_version`` with it and no longer verify: a downgrade past ``0016`` is
for development databases only (ADR 0010).

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_identity_device"
LEDGER = "audit_ledger"
LEDGER_CHECKS = (
    ("hash_version", "hash_version IN (1, 2)"),
    ("v1_no_device", "hash_version >= 2 OR device_id IS NULL"),
)

# Frozen DDL (TD-060, T-0033): exactly what ``app.core.tenancy.rls.rls_policy_sql``
# returned for this revision's table(s). Never edit it: a helper change ships as a NEW
# revision with the new output frozen in it (tests/test_migration_frozen_ddl.py fails until
# then).
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    TABLE: (
        "ALTER TABLE oe_identity_device ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE oe_identity_device FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS project_isolation_policy ON oe_identity_device",
        (
            "CREATE POLICY project_isolation_policy ON oe_identity_device FOR ALL USING (tenant_id"
            " = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id IS"
            " NOT DISTINCT FROM NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
            " WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true),"
            " '')::uuid AND project_id IS NOT DISTINCT FROM"
            " NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
        ),
        "DROP POLICY IF EXISTS tenant_level_read_policy ON oe_identity_device",
        (
            "CREATE POLICY tenant_level_read_policy ON oe_identity_device FOR SELECT USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " project_id IS NULL)"
        ),
        "DROP POLICY IF EXISTS allowed_projects_read_policy ON oe_identity_device",
        (
            "CREATE POLICY allowed_projects_read_policy ON oe_identity_device FOR SELECT USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " project_id = ANY(string_to_array(NULLIF(current_setting('app.allowed_project_ids',"
            " true), ''), ',')::uuid[]))"
        ),
    ),
}


def _ledger_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("device_id", GUID(), nullable=True),
        sa.Column("hash_version", sa.SmallInteger(), nullable=False, server_default="1"),
    ]


def _create_device_table() -> None:
    op.create_table(
        TABLE,
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("device_id", GUID(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("os_version", sa.String(length=64), nullable=False),
        sa.Column("app_version", sa.String(length=64), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("revoked_by", GUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wipe_confirmed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("project_id IS NULL", name=op.f("ck_oe_identity_device_tenant_level")),
        sa.CheckConstraint(
            "platform IN ('android', 'ios', 'web')", name=op.f("ck_oe_identity_device_platform")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name=op.f("ck_oe_identity_device_status")
        ),
        sa.CheckConstraint(
            "(status = 'active' AND revoked_at IS NULL AND wipe_confirmed_at IS NULL)"
            " OR (status = 'revoked' AND revoked_at IS NOT NULL)",
            name=op.f("ck_oe_identity_device_revocation"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["auth_user.id"], name=op.f("fk_oe_identity_device_user_id_auth_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_identity_device")),
        sa.UniqueConstraint(
            "tenant_id", "device_id", name=op.f("uq_oe_identity_device_tenant_device")
        ),
    )
    for column in ("tenant_id", "project_id", "user_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])
    if op.get_context().dialect.name == "postgresql":
        for statement in FROZEN_RLS_DDL[TABLE]:
            op.execute(statement)


NIL_UUID = "00000000-0000-0000-0000-000000000000"  # scope_id of tenant-level chains


def _frozen_ledger(*, with_device: bool) -> sa.Table:
    """``audit_ledger`` as of ``0002`` (``with_device=False``) or this revision.

    SQLite cannot add a CHECK in place, so batch mode copies the table; handing it this
    frozen definition skips reflection (offline ``--sql`` works) and keeps later model
    changes out. SQLite has no append-only guard to preserve (that is PostgreSQL only).
    """
    name = f"ck_{LEDGER}"
    extra: list[sa.SchemaItem] = []
    if with_device:
        extra = [
            *_ledger_columns(),
            *(sa.CheckConstraint(sql, name=f"{name}_{check}") for check, sql in LEDGER_CHECKS),
        ]
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
        sa.CheckConstraint("seq >= 1", name=f"{name}_seq_positive"),
        sa.CheckConstraint(
            f"scope_id = COALESCE(project_id, '{NIL_UUID}')", name=f"{name}_scope_id"
        ),
        sa.CheckConstraint("deleted_at IS NULL", name=f"{name}_not_deleted"),
        sa.CheckConstraint("revision = 1", name=f"{name}_first_revision"),
        sa.CheckConstraint("custom_fields = '{}'", name=f"{name}_no_custom_fields"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{LEDGER}"),
        sa.UniqueConstraint(
            "tenant_id", "scope_id", "chain_key", "seq", name=f"uq_{LEDGER}_chain_seq"
        ),
        sa.Index(f"ix_{LEDGER}_tenant_id", "tenant_id"),
        sa.Index(f"ix_{LEDGER}_project_id", "project_id"),
        *extra,
    )


def upgrade() -> None:
    _create_device_table()
    if op.get_context().dialect.name == "postgresql":
        for column in _ledger_columns():
            op.add_column(LEDGER, column)
        for name, condition in LEDGER_CHECKS:
            op.create_check_constraint(op.f(f"ck_{LEDGER}_{name}"), LEDGER, condition)
        return
    frozen = _frozen_ledger(with_device=False)
    with op.batch_alter_table(LEDGER, copy_from=frozen, recreate="always") as batch:
        for column in _ledger_columns():
            batch.add_column(column)
        for name, condition in LEDGER_CHECKS:
            batch.create_check_constraint(op.f(f"ck_{LEDGER}_{name}"), sa.text(condition))


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        for name, _condition in LEDGER_CHECKS:
            op.drop_constraint(op.f(f"ck_{LEDGER}_{name}"), LEDGER, type_="check")
        op.drop_column(LEDGER, "hash_version")
        op.drop_column(LEDGER, "device_id")
    else:
        frozen = _frozen_ledger(with_device=True)
        with op.batch_alter_table(LEDGER, copy_from=frozen, recreate="always") as batch:
            for name, _condition in LEDGER_CHECKS:
                batch.drop_constraint(op.f(f"ck_{LEDGER}_{name}"), type_="check")
            batch.drop_column("hash_version")
            batch.drop_column("device_id")
    op.drop_table(TABLE)  # drops the table's indexes, constraints and policies with it
