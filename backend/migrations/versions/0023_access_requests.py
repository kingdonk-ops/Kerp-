"""Access requests (T-0050, diff §4.1 access-request lifecycle; edge spec §6 amendments).

Additive:

* New tenant-level table ``oe_access_requests_request`` (one row per request; status
  ``pending | approved | rejected | withdrawn``; decided and withdrawn rows kept as history) with
  a partial unique index: at most one ``pending`` row per user and project.
* Its own RLS policy (``access_request``): a row is readable by its requester
  (``app.current_user_id``), by the PER-09 holders of the requested project
  (``app.admin_project_ids``, bound from the caller's memberships at authentication) and by
  ``app.is_system_admin`` sessions; nobody else. Only the requester inserts (exact tenant-level
  scope); updates need the exact scope and visibility; there is no DELETE policy, so nobody
  deletes a request. The DDL is inlined (frozen at this revision).
* ``kerp_lobby_projects_by_id(uuid[])``: the narrow read behind access requests. id, code, name
  and ``visible_in_lobby`` of the live projects of the bound tenant among the given ids, for any
  active user of the tenant; the membership-based ``projects`` policy is not widened. Built like
  the ``0021`` functions: ``SECURITY DEFINER`` with a pinned ``search_path``, raising
  ``app.is_system_admin`` for its own read only and restoring the caller's value; ``EXECUTE``
  revoked from ``PUBLIC`` and granted to ``CURRENT_USER`` (split-role deploys: TD-275).

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_access_requests_request"
PENDING_INDEX = "uq_oe_access_requests_request_pending"
PENDING = "status = 'pending'"
FUNCTION = "kerp_lobby_projects_by_id"
PROJECTS = "oe_projects_project"

_TENANT = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_PROJECT = "NULLIF(current_setting('app.current_project_id', true), '')::uuid"
_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_ADMIN = "COALESCE(NULLIF(current_setting('app.is_system_admin', true), '')::boolean, false)"
_ADMIN_PROJECTS = (
    "string_to_array(NULLIF(current_setting('app.admin_project_ids', true), ''), ',')::uuid[]"
)
_EXACT = f"tenant_id = {_TENANT} AND project_id IS NOT DISTINCT FROM {_PROJECT}"
_VISIBLE = (
    f"tenant_id = {_TENANT} AND (requester_id = {_USER}"
    f" OR requested_project_id = ANY({_ADMIN_PROJECTS}) OR {_ADMIN})"
)
_POLICIES = (
    f"CREATE POLICY access_request_read_policy ON {TABLE} FOR SELECT USING ({_VISIBLE})",
    f"CREATE POLICY access_request_insert_policy ON {TABLE} FOR INSERT"
    f" WITH CHECK ({_EXACT} AND requester_id = {_USER})",
    f"CREATE POLICY access_request_update_policy ON {TABLE} FOR UPDATE"
    f" USING ({_EXACT} AND {_VISIBLE}) WITH CHECK ({_EXACT} AND {_VISIBLE})",
)

_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {FUNCTION}(project_ids uuid[])
RETURNS TABLE (id uuid, code text, name text, visible_in_lobby boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE
    prior text := current_setting('app.is_system_admin', true);
BEGIN
    IF {_TENANT} IS NULL OR {_USER} IS NULL THEN
        RETURN;
    END IF;
    PERFORM set_config('app.is_system_admin', 'true', true);
    IF EXISTS (SELECT 1 FROM auth_user u WHERE u.tenant_id = {_TENANT} AND u.id = {_USER}
               AND u.deleted_at IS NULL AND u.is_active) THEN
        RETURN QUERY
            SELECT p.id, p.code::text, p.name::text, p.visible_in_lobby FROM {PROJECTS} p
            WHERE p.tenant_id = {_TENANT} AND p.deleted_at IS NULL AND p.id = ANY(project_ids)
            ORDER BY p.name, p.code;
    END IF;
    PERFORM set_config('app.is_system_admin', COALESCE(prior, ''), true);
END
$fn$
"""


def _check(sql: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(sql, name=op.f(f"ck_{TABLE}_{name}"))


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("requester_id", GUID(), nullable=False),
        sa.Column("requested_project_id", GUID(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decided_by", GUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("granted", JSONType(), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
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
        _check("status IN ('pending', 'approved', 'rejected', 'withdrawn')", "status"),
        _check("message IS NULL OR length(message) <= 1000", "message_length"),
        _check(
            "rejection_reason IS NULL OR (status = 'rejected' AND length(rejection_reason) <= 500)",
            "rejection_reason",
        ),
        _check("(status IN ('approved', 'rejected')) = (decided_at IS NOT NULL)", "decided"),
        _check("status NOT IN ('approved', 'rejected') OR decided_by IS NOT NULL", "decider"),
        _check("decided_by IS NULL OR decided_by <> requester_id", "never_self_decided"),
        _check("(status = 'withdrawn') = (withdrawn_at IS NOT NULL)", "withdrawn"),
        sa.ForeignKeyConstraint(
            ["requester_id"], ["auth_user.id"], name=op.f(f"fk_{TABLE}_requester_id_auth_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    for column in ("requester_id", "requested_project_id", "tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])
    op.create_index(
        PENDING_INDEX,
        TABLE,
        ["tenant_id", "requester_id", "requested_project_id"],
        unique=True,
        postgresql_where=sa.text(PENDING),
        sqlite_where=sa.text(PENDING),
    )
    if op.get_context().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    for statement in _POLICIES:
        op.execute(statement)
    op.execute(_FUNCTION_SQL)
    # Nobody but the app role may call it (functions default to EXECUTE for PUBLIC).
    op.execute(f"REVOKE EXECUTE ON FUNCTION {FUNCTION}(uuid[]) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION}(uuid[]) TO CURRENT_USER")


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}(uuid[])")
    op.drop_table(TABLE)  # drops its indexes, constraints and policies with it
