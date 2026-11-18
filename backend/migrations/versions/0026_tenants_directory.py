"""Tenants directory table and ``tenant_id`` foreign keys, step 1 of 2 (T-0079, ADR 0011).

Core-table change covered by ADR 0011 (master spec §1.1, CLAUDE.md §6).

* ``tenants``: the directory. ``BaseRecordMixin`` columns plus ``display_name``; its
  ``tenant_id`` is its own ``id`` (CHECK ``own_tenant``, and the same FK as every other
  table), it is tenant-level (CHECK ``tenant_level``) and it is under the standard RLS
  policies, so a session sees only its own tenant's row.
* Backfill: one directory row for every ``tenant_id`` already stored in any table, and for
  ``KERP_DEFAULT_TENANT_ID`` when it is set (idempotent: ``ON CONFLICT DO NOTHING``). On
  PostgreSQL the read runs with ``FORCE ROW LEVEL SECURITY`` lifted (as in ``0012``), because
  the migration role owns the tables and would otherwise see no rows; it is forced again in
  the same transaction, before the foreign keys are added. The ``FORCE`` toggle holds an
  ``ACCESS EXCLUSIVE`` lock on each table until this revision commits (ADR 0011).
* ``fk_<table>_tenant_id_tenants`` on every table carrying ``tenant_id``. On PostgreSQL it is
  added ``NOT VALID`` (no per-row check of existing rows); revision ``0027``
  validates it in its own transaction (``env.py`` commits per revision). On SQLite the
  tables are rebuilt in batch mode (online only; ``--sql`` output for SQLite skips them).

Additive. The downgrade drops the foreign keys and the table.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-17
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings
from app.core.db import GUID, JSONType
from app.core.tenancy.rls import rls_policy_sql

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "tenants"
BACKFILL_NAME = "Default tenant"


def fk_name(table: str) -> str:
    return f"fk_{table}_tenant_id_{TABLE}"


# Every table carrying ``tenant_id`` at revision 0025. Fixed here (not reflected) so the
# offline SQL is the same; ``test_every_tenant_id_references_the_directory`` catches a table
# a later revision adds without the FK.
TABLES: tuple[str, ...] = (
    "audit_ledger",
    "auth_project_membership",
    "auth_user",
    "mail_send_log",
    "mail_suppression",
    "oe_access_requests_request",
    "oe_admin_grant",
    "oe_identity_device",
    "oe_jobs_job",
    "oe_notifications_notification",
    "oe_projects_project",
    "oe_saved_view",
    "oe_settings_deployment",
    "oe_uploads_file",
    "oe_validation_finding",
    "oe_validation_override",
    "oe_validation_rule",
    "oe_validation_rule_set",
    "oe_validation_unlock_request",
    "project_module_setting",
)
_DIRECTORY_COLUMNS = (
    "id, tenant_id, created_at, updated_at, custom_fields, source, revision, display_name"
)


def _directory_rows_sql(source: str) -> str:
    """INSERT one directory row per distinct ``tenant_id`` produced by ``source``."""
    return (
        f"INSERT INTO {TABLE} ({_DIRECTORY_COLUMNS})"
        " SELECT used.tenant_id, used.tenant_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '{}',"
        f" 'system', 1, '{BACKFILL_NAME}' FROM ({source}) AS used WHERE true"
        " ON CONFLICT (id) DO NOTHING"
    )


def _backfill(postgres: bool) -> None:
    """A directory row for every tenant id in use, and for ``KERP_DEFAULT_TENANT_ID``."""
    if postgres:
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    in_use = " UNION ".join(f"SELECT tenant_id FROM {table}" for table in TABLES)
    op.execute(_directory_rows_sql(in_use))
    if postgres:
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    default = get_settings().default_tenant_id
    if default is not None:
        cast = "::uuid" if postgres else ""
        op.execute(_directory_rows_sql(f"SELECT '{uuid.UUID(str(default))}'{cast} AS tenant_id"))


def upgrade() -> None:
    postgres = op.get_context().dialect.name == "postgresql"
    op.create_table(
        TABLE,
        sa.Column("display_name", sa.String(length=200), nullable=False),
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
        sa.CheckConstraint("tenant_id = id", name=op.f(f"ck_{TABLE}_own_tenant")),
        sa.CheckConstraint("project_id IS NULL", name=op.f(f"ck_{TABLE}_tenant_level")),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{TABLE}.id"], name=op.f(fk_name(TABLE))),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    for column in ("tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])

    _backfill(postgres)

    if postgres:
        for statement in rls_policy_sql(TABLE):
            op.execute(statement)
        for table in TABLES:
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {fk_name(table)} FOREIGN KEY (tenant_id)"
                f" REFERENCES {TABLE} (id) NOT VALID"
            )
    elif _sqlite_rebuild_possible():
        for table in TABLES:
            with op.batch_alter_table(table) as batch:
                batch.create_foreign_key(op.f(fk_name(table)), TABLE, ["tenant_id"], ["id"])


def _sqlite_rebuild_possible() -> bool:
    """SQLite adds a foreign key only by rebuilding the table, which batch mode does from a
    reflection of the live table. Offline SQL (``--sql``) has none, so it leaves the SQLite
    tables as they are; SQLite is the fast test backend only, never a deployment target."""
    if op.get_context().as_sql:
        op.execute("-- 0026: SQLite tenant_id foreign keys need a live database (batch mode)")
        return False
    return True


def downgrade() -> None:
    postgres = op.get_context().dialect.name == "postgresql"
    if postgres or _sqlite_rebuild_possible():
        for table in TABLES:
            with op.batch_alter_table(table) as batch:
                batch.drop_constraint(fk_name(table), type_="foreignkey")
    op.drop_table(TABLE)  # indexes, constraints and policies go with the table
