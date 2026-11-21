"""system_admin global project visibility (ADR 0016, T-0274; diff §4.1, edge spec §6).

Additive: ``project_isolation_policy`` (``FOR ALL``, ``USING`` and ``WITH CHECK``) is dropped
and re-created on every table that has it at ``0034`` (every table under the standard project
policy), from

    tenant_id = app.current_tenant_id AND project_id IS NOT DISTINCT FROM app.current_project_id

to

    tenant_id = app.current_tenant_id AND (project_id IS NOT DISTINCT FROM
    app.current_project_id OR (project_id IS NOT NULL AND app.is_system_admin))

A ``system_admin`` session (``app.is_system_admin``, bound only from the caller's own admin
grant) reads and writes the project rows of every project of its own tenant. The tenant clause
stays outside the ``OR``, so the flag never crosses tenants, and the admin disjunct never
matches a tenant-level row (``project_id IS NULL``), so tenant-level writes keep the exact rule.
``tenant_level_read_policy``, ``allowed_projects_read_policy`` and the special policies
(membership, admin grant, access request, invite, team, the tenants census) do not change. No
table, column or row changes.

The DDL is frozen here (TD-060, T-0033): built from constants local to this revision, never
from ``app.core.tenancy.rls``. ``FROZEN_RLS_DDL`` is what ``rls_policy_sql`` emits for this
policy at this revision; the downgrade re-creates the policy frozen by the earlier revisions
(``FROZEN_RLS_DOWNGRADE_DDL``). On SQLite there is no RLS; the ORM scope mirrors the rule.

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "project_isolation_policy"

# Every table carrying project_isolation_policy at revision 0034 (the standard project policy;
# checked against pg_policy after ``alembic upgrade 0034``). A table created by a later
# revision gets the new policy from that revision's own frozen DDL.
TABLES: tuple[str, ...] = (
    "audit_ledger",
    "auth_user",
    "mail_send_log",
    "mail_suppression",
    "oe_identity_device",
    "oe_jobs_job",
    "oe_notifications_notification",
    "oe_saved_view",
    "oe_saved_view_team",
    "oe_settings_deployment",
    "oe_uploads_file",
    "oe_validation_finding",
    "oe_validation_override",
    "oe_validation_rule",
    "oe_validation_rule_set",
    "oe_validation_unlock_request",
    "project_module_setting",
    "tenants",
)

_TENANT = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_PROJECT = "NULLIF(current_setting('app.current_project_id', true), '')::uuid"
_ADMIN = "COALESCE(NULLIF(current_setting('app.is_system_admin', true), '')::boolean, false)"
# Before (every earlier revision that created one of TABLES): the exact scope.
_EXACT = f"tenant_id = {_TENANT} AND project_id IS NOT DISTINCT FROM {_PROJECT}"
# After (ADR 0016): the exact scope, or a project row of the bound tenant for a system_admin.
_ADMIN_WIDE = (
    f"tenant_id = {_TENANT} AND (project_id IS NOT DISTINCT FROM {_PROJECT}"
    f" OR (project_id IS NOT NULL AND {_ADMIN}))"
)


def _policy_sql(table: str, rule: str) -> tuple[str, ...]:
    return (
        f"DROP POLICY IF EXISTS {POLICY} ON {table}",
        f"CREATE POLICY {POLICY} ON {table} FOR ALL USING ({rule}) WITH CHECK ({rule})",
    )


# Frozen DDL (TD-060, T-0033): exactly the project_isolation_policy statements that
# ``app.core.tenancy.rls.rls_policy_sql`` returned for each table at this revision. Never edit
# it: a helper change ships as a NEW revision (tests/test_migration_frozen_ddl.py).
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {t: _policy_sql(t, _ADMIN_WIDE) for t in TABLES}
# The downgrade: the policy as the earlier revisions froze it.
FROZEN_RLS_DOWNGRADE_DDL: dict[str, tuple[str, ...]] = {t: _policy_sql(t, _EXACT) for t in TABLES}


def upgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    for table in TABLES:
        for statement in FROZEN_RLS_DDL[table]:
            op.execute(statement)


def downgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    for table in TABLES:
        for statement in FROZEN_RLS_DOWNGRADE_DDL[table]:
            op.execute(statement)
