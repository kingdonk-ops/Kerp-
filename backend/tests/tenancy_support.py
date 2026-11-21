"""Synthetic two-tenant, three-project data set for the tenancy and RLS tests.

Rows are seeded through bound units of work, so seeding also works on PostgreSQL under
FORCE RLS as the non-superuser CI role.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import inspect, select

from app.core.db import TENANTS_TABLE, Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import apply_rls_policies
from tests import pg_databases
from tests.db_support import (
    ON_POSTGRES,
    Gadget,
    ScratchBase,
    Widget,
    database_url,
    seed_tenants,
)

T1 = uuid.UUID("00000000-0000-4000-8000-000000000001")
T2 = uuid.UUID("00000000-0000-4000-8000-000000000002")
PA = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
PB = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
PC = uuid.UUID("00000000-0000-4000-8000-0000000000c1")

CTX_A = TenancyContext(tenant_id=T1, project_id=PA)
CTX_B = TenancyContext(tenant_id=T1, project_id=PB)
CTX_T1 = TenancyContext(tenant_id=T1)  # tenant-level only
CTX_C = TenancyContext(tenant_id=T2, project_id=PC)

# (context, widget names, gadget labels)
SEED: list[tuple[TenancyContext, list[str], list[str]]] = [
    (CTX_A, ["a1", "a2"], ["ga"]),
    (CTX_B, ["b1", "b2", "b3"], ["gb1", "gb2"]),
    (CTX_T1, ["t1-library"], []),
    (CTX_C, ["c1"], ["gc"]),
]
VISIBLE_TO_A = {"a1", "a2", "t1-library"}
# System admin sessions (ADR 0016, T-0274): bound to project A, tenant-level, and an admin of
# the other tenant. None holds a membership; the flag alone widens their project scope.
ADMIN_A = TenancyContext(tenant_id=T1, project_id=PA, is_system_admin=True)
ADMIN_T1 = TenancyContext(tenant_id=T1, is_system_admin=True)
ADMIN_T2 = TenancyContext(tenant_id=T2, is_system_admin=True)
# Every live widget of tenant T1 (projects A and B, and the tenant-level row).
VISIBLE_TO_T1_ADMIN = {"a1", "a2", "b1", "b2", "b3", "t1-library"}
DELETED_IN_A = "a-deleted"


def seeded_database(tmp_path: Path) -> Iterator[Database]:
    """Create the scratch tables (under RLS on PostgreSQL), seed them, drop them after."""
    url = database_url(tmp_path)
    db = Database(url)
    # Over a migrated schema the real ``tenants`` directory already exists: reuse it and leave
    # it to the migration's downgrade (T-0079).
    existing = set(inspect(db.engine).get_table_names())
    created = [t for t in ScratchBase.metadata.sorted_tables if t.name not in existing]
    # The scratch tables reference ``tenants``, so over a migrated schema the role that owns it
    # creates them: in CI's backend-pg-roles that is kerp_owner, whose default privileges
    # (migration 0039) grant them to the runtime role (T-0343). Elsewhere: the runtime role.
    ddl = db
    if ON_POSTGRES and TENANTS_TABLE in existing:
        ddl = Database(pg_databases.migration_url(url))
    ScratchBase.metadata.create_all(ddl.engine, tables=created)
    with ddl.engine.begin() as conn:
        apply_rls_policies(conn, ScratchBase.metadata)
    try:
        seed_tenants(db, T1, T2)
        for ctx, widgets, gadgets in SEED:
            with db.unit_of_work(ctx) as s:
                s.add_all(
                    Widget(tenant_id=ctx.tenant_id, project_id=ctx.project_id, name=n)
                    for n in widgets
                )
                s.add_all(
                    Gadget(tenant_id=ctx.tenant_id, project_id=ctx.project_id, label=g)
                    for g in gadgets
                )
        with db.unit_of_work(CTX_A) as s:
            deleted = Widget(tenant_id=T1, project_id=PA, name=DELETED_IN_A)
            deleted.soft_delete()
            s.add(deleted)
        yield db
    finally:
        ScratchBase.metadata.drop_all(ddl.engine, tables=created)
        if ddl is not db:
            ddl.dispose()
        db.dispose()


def widget_id(db: Database, ctx: TenancyContext, name: str) -> uuid.UUID:
    with db.unit_of_work(ctx) as s:
        return s.scalars(
            select(Widget.id).where(Widget.name == name).execution_options(include_deleted=True)
        ).one()
