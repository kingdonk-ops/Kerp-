"""Projects: per-project legal hold (T-0223, diff §4.7 Legal hold).

Additive: ``oe_projects_project`` gets ``legal_hold`` (``BOOLEAN NOT NULL DEFAULT false``), and
the nullable ``legal_hold_reason`` (``VARCHAR(500)``), ``legal_hold_set_by`` (a user id, no FK,
like ``created_by``) and ``legal_hold_set_at``, plus the check
``ck_oe_projects_project_legal_hold_reason``: a hold that is on has its reason and time.
Every existing row gets ``legal_hold = false``, which the check accepts.

No row changes and no policy changes: the membership-based policies of the project row
(migrations ``0015``, ``0035``) name none of the new columns, so this revision carries no
``FROZEN_RLS_DDL``. The ``SECURITY DEFINER`` lobby functions (``0021``, ``0023``) return
fixed columns and are untouched.

On PostgreSQL it is ``ADD COLUMN`` and ``ADD CONSTRAINT``. SQLite cannot add a check
constraint, so batch mode copies the table; it gets the frozen definition below (no
reflection, so offline ``--sql`` works and later model changes stay out).

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_projects_project"
CHECK = f"ck_{TABLE}_legal_hold_reason"
CHECK_SQL = "NOT legal_hold OR (legal_hold_reason IS NOT NULL AND legal_hold_set_at IS NOT NULL)"
HOLD_COLUMNS = ("legal_hold", "legal_hold_reason", "legal_hold_set_by", "legal_hold_set_at")


def _hold_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("legal_hold_reason", sa.String(length=500), nullable=True),
        sa.Column("legal_hold_set_by", GUID(), nullable=True),
        sa.Column("legal_hold_set_at", sa.DateTime(timezone=True), nullable=True),
    ]


def _frozen_project(*, with_hold: bool) -> sa.Table:
    """``oe_projects_project`` as of revision 0036 (SQLite), or as of this one."""
    name = f"ck_{TABLE}"
    hold: list[sa.SchemaItem] = []
    if with_hold:
        hold = [*_hold_columns(), sa.CheckConstraint(CHECK_SQL, name=CHECK)]
    return sa.Table(
        TABLE,
        sa.MetaData(),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default=sa.text("'ACTIVE'")
        ),
        sa.Column(
            "site_timezone",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'Australia/Perth'"),
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
        *hold,
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


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        for column in _hold_columns():
            op.add_column(TABLE, column)
        op.create_check_constraint(op.f(CHECK), TABLE, CHECK_SQL)
        return
    frozen = _frozen_project(with_hold=False)
    with op.batch_alter_table(TABLE, copy_from=frozen, recreate="always") as batch:
        for column in _hold_columns():
            batch.add_column(column)
        batch.create_check_constraint(op.f(CHECK), sa.text(CHECK_SQL))


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.drop_constraint(op.f(CHECK), TABLE, type_="check")
        for name in reversed(HOLD_COLUMNS):
            op.drop_column(TABLE, name)
        return
    frozen = _frozen_project(with_hold=True)
    with op.batch_alter_table(TABLE, copy_from=frozen, recreate="always") as batch:
        batch.drop_constraint(op.f(CHECK), type_="check")
        for name in reversed(HOLD_COLUMNS):
            batch.drop_column(name)
