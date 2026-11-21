"""Project invites (T-0052, diff §4.1 Invites; edge spec §6 amendments).

Additive:

* New tenant-level table ``oe_admin_project_invite``: one row per invitation of an e-mail
  address to a project (``invited_project_id``, no FK: the project belongs to the projects
  module), with the persona(s) and site scope (``grants``), the inviter, the SHA-256 of the
  invite token (``token_hash``, unique; the token itself is never stored), ``expires_at``,
  ``last_sent_at`` and the send log ``sent_at``, status ``pending | accepted | revoked |
  expired`` and who accepted it when. Partial unique index: at most one ``pending`` row per
  project and e-mail.
* Its own RLS policy (``project_invite``): a row is readable by the PER-09 holders of the
  invited project (``app.admin_project_ids``, bound from the caller's memberships at
  authentication) and by ``app.is_system_admin`` sessions; nobody else, the invitee included
  (an invite is accepted by its token through the admin module's narrow system path). Inserts
  and updates need the exact tenant-level scope and that same visibility; there is no DELETE
  policy, so nobody deletes an invite. The DDL is inlined (frozen at this revision;
  ``app.core.tenancy.rls`` emits the same).

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_admin_project_invite"
PENDING_INDEX = "uq_oe_admin_project_invite_pending"
TOKEN_INDEX = "uq_oe_admin_project_invite_token_hash"
PENDING = "status = 'pending'"

_TENANT = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_PROJECT = "NULLIF(current_setting('app.current_project_id', true), '')::uuid"
_ADMIN = "COALESCE(NULLIF(current_setting('app.is_system_admin', true), '')::boolean, false)"
_ADMIN_PROJECTS = (
    "string_to_array(NULLIF(current_setting('app.admin_project_ids', true), ''), ',')::uuid[]"
)
_EXACT = f"tenant_id = {_TENANT} AND project_id IS NOT DISTINCT FROM {_PROJECT}"
_VISIBLE = f"tenant_id = {_TENANT} AND (invited_project_id = ANY({_ADMIN_PROJECTS}) OR {_ADMIN})"
_POLICIES = (
    f"CREATE POLICY project_invite_read_policy ON {TABLE} FOR SELECT USING ({_VISIBLE})",
    f"CREATE POLICY project_invite_insert_policy ON {TABLE} FOR INSERT"
    f" WITH CHECK ({_EXACT} AND {_VISIBLE})",
    f"CREATE POLICY project_invite_update_policy ON {TABLE} FOR UPDATE"
    f" USING ({_EXACT} AND {_VISIBLE}) WITH CHECK ({_EXACT} AND {_VISIBLE})",
)

# The RLS DDL this revision applies, per table (TD-060, T-0033): frozen here, replayed by
# tests/test_migration_frozen_ddl.py against the live helpers.
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    TABLE: (
        f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY",
        *_POLICIES,
    ),
}


def _check(sql: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(sql, name=op.f(f"ck_{TABLE}_{name}"))


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("invited_project_id", GUID(), nullable=False),
        sa.Column("invited_email", sa.String(length=320), nullable=False),
        sa.Column("grants", JSONType(), nullable=False),
        sa.Column("inviter_id", GUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", JSONType(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("accepted_by", GUID(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
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
        _check("project_id IS NULL", "tenant_level"),
        _check("status IN ('pending', 'accepted', 'revoked', 'expired')", "status"),
        _check("length(token_hash) = 64", "token_hash_sha256"),
        _check(
            "(status = 'accepted') = (accepted_at IS NOT NULL)"
            " AND (status = 'accepted') = (accepted_by IS NOT NULL)",
            "accepted",
        ),
        sa.ForeignKeyConstraint(
            ["inviter_id"], ["auth_user.id"], name=op.f(f"fk_{TABLE}_inviter_id_auth_user")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f(f"fk_{TABLE}_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    for column in ("invited_project_id", "invited_email", "tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])
    op.create_index(
        PENDING_INDEX,
        TABLE,
        ["tenant_id", "invited_project_id", "invited_email"],
        unique=True,
        postgresql_where=sa.text(PENDING),
        sqlite_where=sa.text(PENDING),
    )
    op.create_index(TOKEN_INDEX, TABLE, ["token_hash"], unique=True)
    if op.get_context().dialect.name != "postgresql":
        return
    for statement in FROZEN_RLS_DDL[TABLE]:
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)  # drops its indexes, constraints and policies with it
