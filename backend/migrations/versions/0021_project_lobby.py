"""Lobby: ``visible_in_lobby`` on projects and the narrow lobby read functions (T-0040).

* ``oe_projects_project.visible_in_lobby`` BOOLEAN NOT NULL DEFAULT false (master spec §4.1
  amendment; diff §4.1). Additive.
* ``kerp_lobby_projects()``: id, code and name of the live lobby-visible projects of the bound
  tenant, and only when the session user (``app.current_user_id``) is an active internal user
  (``auth_user.is_internal``); for anyone else no rows (edge spec §6 amendments: checked inside
  the function too, defence in depth).
* ``kerp_lobby_project_by_code(text)``: id, code and name of the one live project of the bound
  tenant with exactly that code, for any active user of the tenant.

The membership-based policy on ``oe_projects_project`` (``0015``) is not widened: both
functions are ``SECURITY DEFINER`` with a pinned ``search_path`` and return three columns of
the rows their ``WHERE`` clause names, never the full row. In CI and local dev the definer is
the app role, which ``FORCE ROW LEVEL SECURITY`` subjects to the policy too (TD-056); so the
function body raises ``app.is_system_admin`` transaction-locally for its own read and puts the
caller's value back before it returns (an error aborts the transaction, which reverts it as
well). A definer that bypasses RLS (split roles, TD-056) reads the same rows. ``EXECUTE`` is
revoked from ``PUBLIC`` and granted to ``CURRENT_USER``, as in ``0015`` (split-role deploys
grant it to the runtime role, TD-275).

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_projects_project"
COLUMN = "visible_in_lobby"
LIST_FUNCTION = "kerp_lobby_projects"
CODE_FUNCTION = "kerp_lobby_project_by_code"

_TENANT = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_ADMIN_SETTING = "app.is_system_admin"


def _caller(*, internal: bool) -> str:
    """The session user is an active (optionally: internal) user of the bound tenant."""
    extra = " AND u.is_internal" if internal else ""
    return (
        f"EXISTS (SELECT 1 FROM auth_user u WHERE u.tenant_id = {_TENANT} AND u.id = {_USER}"
        f" AND u.deleted_at IS NULL AND u.is_active{extra})"
    )


def _function_sql(name: str, args: str, caller: str, where: str) -> str:
    """A narrow read: id, code, name of the rows ``where`` names, if ``caller`` holds.

    ``app.is_system_admin`` is raised for the read only (see the module docstring) and the
    caller's own value is restored before returning.
    """
    return f"""
CREATE OR REPLACE FUNCTION {name}({args}) RETURNS TABLE (id uuid, code text, name text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE
    prior text := current_setting('{_ADMIN_SETTING}', true);
BEGIN
    IF {_TENANT} IS NULL OR {_USER} IS NULL THEN
        RETURN;
    END IF;
    PERFORM set_config('{_ADMIN_SETTING}', 'true', true);
    IF {caller} THEN
        RETURN QUERY
            SELECT p.id, p.code::text, p.name::text FROM {TABLE} p
            WHERE p.tenant_id = {_TENANT} AND p.deleted_at IS NULL AND {where}
            ORDER BY p.name, p.code;
    END IF;
    PERFORM set_config('{_ADMIN_SETTING}', COALESCE(prior, ''), true);
END
$fn$
"""


_FUNCTIONS = (
    (LIST_FUNCTION, "", "", _caller(internal=True), f"p.{COLUMN}"),
    (CODE_FUNCTION, "lookup_code text", "text", _caller(internal=False), "p.code = lookup_code"),
)


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()))
    if op.get_context().dialect.name != "postgresql":
        return
    for name, args, signature, caller, where in _FUNCTIONS:
        op.execute(_function_sql(name, args, caller, where))
        # Nobody but the app role may call it (functions default to EXECUTE for PUBLIC).
        op.execute(f"REVOKE EXECUTE ON FUNCTION {name}({signature}) FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {name}({signature}) TO CURRENT_USER")


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        for name, _args, signature, _caller_sql, _where in _FUNCTIONS:
            op.execute(f"DROP FUNCTION IF EXISTS {name}({signature})")
    op.drop_column(TABLE, COLUMN)
