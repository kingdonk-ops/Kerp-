"""Projects: DB-side defaults for ``status`` and ``site_timezone`` (T-0063, master spec §4.1).

Additive: ``oe_projects_project.status`` gets ``DEFAULT 'ACTIVE'`` and ``site_timezone``
``DEFAULT 'Australia/Perth'``. They are the shipped fallback literals of
``app.modules.projects.models`` (``DEFAULT_STATUS``, ``DEFAULT_TIMEZONE``), which equal the
deployment settings' shipped defaults (T-0044). The service still passes both values
explicitly (taken from the deployment settings); the defaults only cover a write that leaves
them out. ``currency_code`` and ``locale`` get no DB default (tenant settings, TD-185).

No row changes and no policy changes. On PostgreSQL it is ``ALTER COLUMN ... SET DEFAULT``.
SQLite cannot alter a column default, so batch mode copies the table; it gets the frozen
definition below (no reflection, so offline ``--sql`` works and later model changes stay out).

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_projects_project"
# Frozen literals (spec §4.1); equal to the models' fallback literals (tested).
DEFAULTS = {"status": "ACTIVE", "site_timezone": "Australia/Perth"}
_TYPES = {"status": sa.String(length=32), "site_timezone": sa.String(length=64)}


def _frozen_project(*, with_defaults: bool) -> sa.Table:
    """``oe_projects_project`` as of revision 0032 (SQLite), with or without the defaults."""

    def default(column: str) -> sa.TextClause | None:
        return sa.text(f"'{DEFAULTS[column]}'") if with_defaults else None

    name = f"ck_{TABLE}"
    return sa.Table(
        TABLE,
        sa.MetaData(),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", _TYPES["status"], nullable=False, server_default=default("status")),
        sa.Column(
            "site_timezone",
            _TYPES["site_timezone"],
            nullable=False,
            server_default=default("site_timezone"),
        ),
        sa.Column("spatial_bounds", sa.Text(), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("locale", sa.String(length=35), nullable=False),
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
        sa.Column("visible_in_lobby", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id", name=f"pk_{TABLE}"),
        sa.CheckConstraint("status IN ('ACTIVE', 'ON_HOLD', 'CLOSED')", name=f"{name}_status"),
        sa.UniqueConstraint("tenant_id", "code", name=f"uq_{TABLE}_tenant_code"),
        sa.CheckConstraint("project_id IS NULL", name=f"{name}_tenant_level"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{TABLE}_tenant_id_tenants"
        ),
        sa.Index(f"ix_{TABLE}_project_id", "project_id"),
        sa.Index(f"ix_{TABLE}_tenant_id", "tenant_id"),
    )


def _set_defaults(*, on: bool) -> None:
    if op.get_context().dialect.name == "postgresql":
        for column, value in DEFAULTS.items():
            op.alter_column(
                TABLE,
                column,
                existing_type=_TYPES[column],
                existing_nullable=False,
                server_default=sa.text(f"'{value}'") if on else None,
            )
        return
    frozen = _frozen_project(with_defaults=not on)
    with op.batch_alter_table(TABLE, copy_from=frozen, recreate="always") as batch:
        for column, value in DEFAULTS.items():
            batch.alter_column(
                column,
                existing_type=_TYPES[column],
                existing_nullable=False,
                server_default=sa.text(f"'{value}'") if on else None,
            )


def upgrade() -> None:
    _set_defaults(on=True)


def downgrade() -> None:
    _set_defaults(on=False)
