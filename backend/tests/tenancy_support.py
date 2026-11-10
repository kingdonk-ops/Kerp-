"""Synthetic two-tenant, three-project data set for the tenancy and RLS tests.

Rows are seeded through bound units of work, so seeding also works on PostgreSQL under
FORCE RLS as the non-superuser CI role.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import select

from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import apply_rls_policies
from tests.db_support import Gadget, ScratchBase, Widget, database_url

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
DELETED_IN_A = "a-deleted"


def seeded_database(tmp_path: Path) -> Iterator[Database]:
    """Create the scratch tables (under RLS on PostgreSQL), seed them, drop them after."""
    db = Database(database_url(tmp_path))
    ScratchBase.metadata.create_all(db.engine)
    with db.engine.begin() as conn:
        apply_rls_policies(conn, ScratchBase.metadata)
    try:
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
        ScratchBase.metadata.drop_all(db.engine)
        db.dispose()


def widget_id(db: Database, ctx: TenancyContext, name: str) -> uuid.UUID:
    with db.unit_of_work(ctx) as s:
        return s.scalars(
            select(Widget.id).where(Widget.name == name).execution_options(include_deleted=True)
        ).one()
