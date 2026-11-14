"""PER-16 Project Viewer: widen the membership persona CHECK (T-0076, TD-251, ADR 0008).

Additive core-table change covered by ADR 0008: the ``persona`` CHECK on
``auth_project_membership`` is replaced by one that also allows ``PER-16``. No existing row
changes.

The downgrade restores the PER-01..PER-15 CHECK. It refuses to run while an active
(non-deleted) PER-16 membership exists, so it never revokes access silently: revoke those
memberships first. Revoked (soft-deleted) PER-16 rows cannot satisfy the old CHECK, so the
downgrade purges them and logs how many.

On PostgreSQL the table's row-level security is forced, so a migration role that is not a
superuser (``kerp_app`` in CI) would see no rows. The checks run with RLS lifted for the
table owner inside the migration's transaction (``NO FORCE`` ... ``FORCE``).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-14
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "auth_project_membership"
CHECK = "persona"  # ck_auth_project_membership_persona via the naming convention
VIEWER = "PER-16"
_ACTIVE_VIEWERS = f"persona = '{VIEWER}' AND deleted_at IS NULL"
_REVOKED_VIEWERS = f"persona = '{VIEWER}' AND deleted_at IS NOT NULL"

log = logging.getLogger("alembic.runtime.migration")


def _persona_check(last: int) -> str:
    codes = ", ".join(f"'PER-{n:02d}'" for n in range(1, last + 1))
    return f"persona IN ({codes})"


def _frozen_table(last: int) -> sa.Table:
    """The table as of this revision, persona CHECK up to ``PER-<last>``.

    SQLite cannot alter a CHECK, so batch mode copies the table; handing it this frozen
    definition skips reflection (offline ``--sql`` works) and keeps later model changes out.
    SQLite only, so the PostgreSQL-only ``NULLS NOT DISTINCT`` of the unique key is omitted.
    """
    name = f"ck_{TABLE}"
    return sa.Table(
        TABLE,
        sa.MetaData(),
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("member_project_id", GUID(), nullable=False),
        sa.Column("persona", sa.String(length=6), nullable=False),
        sa.Column("site_node_id", GUID(), nullable=True),
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
        sa.CheckConstraint("project_id IS NULL", name=f"{name}_tenant_level"),
        sa.CheckConstraint(_persona_check(last), name=f"{name}_{CHECK}"),
        sa.CheckConstraint(
            "persona <> 'PER-07' OR site_node_id IS NOT NULL", name=f"{name}_inspector_site"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["auth_user.id"], name=f"fk_{TABLE}_user_id_auth_user"
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{TABLE}"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "member_project_id",
            "persona",
            "site_node_id",
            name=f"uq_{TABLE}_grant",
        ),
        *(
            sa.Index(f"ix_{TABLE}_{column}", column)
            for column in ("tenant_id", "project_id", "user_id", "member_project_id")
        ),
    )


def _replace_check(old: int, new: int) -> None:
    constraint = op.f(f"ck_{TABLE}_{CHECK}")
    if op.get_context().dialect.name == "postgresql":
        op.drop_constraint(constraint, TABLE, type_="check")
        op.create_check_constraint(constraint, TABLE, _persona_check(new))
        return
    with op.batch_alter_table(TABLE, copy_from=_frozen_table(old), recreate="always") as batch:
        batch.drop_constraint(constraint, type_="check")
        batch.create_check_constraint(constraint, sa.text(_persona_check(new)))


def upgrade() -> None:
    _replace_check(15, 16)


def _refuse_message(count: int | str) -> str:
    return (
        f"cannot downgrade below 0012: {count} active {VIEWER} (Project Viewer) "
        "membership(s) exist; revoke them first (the downgrade never deletes active "
        "memberships)"
    )


def downgrade() -> None:
    context = op.get_context()
    postgres = context.dialect.name == "postgresql"
    if postgres:
        op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    if context.as_sql:
        if postgres:  # offline SQL: the guard runs when the script is applied
            op.execute(
                f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM {TABLE} WHERE {_ACTIVE_VIEWERS}) "
                f"THEN RAISE EXCEPTION '{_refuse_message('some')}'; END IF; END $do$"
            )
        op.execute(f"DELETE FROM {TABLE} WHERE {_REVOKED_VIEWERS}")
    else:
        bind = op.get_bind()
        active = bind.execute(sa.text(f"SELECT count(*) FROM {TABLE} WHERE {_ACTIVE_VIEWERS}"))
        count = int(active.scalar_one())
        if count:
            raise RuntimeError(_refuse_message(count))
        purged = bind.execute(sa.text(f"DELETE FROM {TABLE} WHERE {_REVOKED_VIEWERS}")).rowcount
        if purged:
            log.warning("0012 downgrade: purged %d revoked %s membership(s)", purged, VIEWER)
    if postgres:
        op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    _replace_check(16, 15)
