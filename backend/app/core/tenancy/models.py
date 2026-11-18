"""The tenants directory table (T-0079, ADR 0011, master spec §1.1; migration ``0026``).

Every table's ``tenant_id`` is a foreign key to ``tenants.id`` (declared once on
``BaseRecordMixin``). The directory row follows the record contract itself: its
``tenant_id`` is its own ``id`` (CHECK ``own_tenant``) and it is tenant-level (CHECK
``tenant_level``), so the standard RLS policies let a session read and write only the row of
its own tenant. The tenant is hidden from users (diff §4.1, TD-120): no screen, response or
log shows its display name or id.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import CheckConstraint, Connection, String, inspect, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TENANTS_TABLE, Base, BaseRecordMixin

DISPLAY_NAME_LENGTH: Final = 200


class Tenant(Base, BaseRecordMixin):
    """One tenant of the deployment (one per deployment, diff §4.1)."""

    __tablename__ = TENANTS_TABLE
    __table_args__ = (
        CheckConstraint("tenant_id = id", name="own_tenant"),
        CheckConstraint("project_id IS NULL", name="tenant_level"),
    )

    display_name: Mapped[str] = mapped_column(String(DISPLAY_NAME_LENGTH), nullable=False)


def tenant_fk_problems(connection: Connection) -> list[str]:
    """Why each live table carrying ``tenant_id`` breaks the directory contract (empty = OK).

    Every such table needs a foreign key ``tenant_id -> tenants.id``; on PostgreSQL it must
    also be validated. The migration test runs this at head, so a later table cannot skip it.
    """
    inspector = inspect(connection)
    problems: list[str] = []
    for table in sorted(inspector.get_table_names()):
        if not any(column["name"] == "tenant_id" for column in inspector.get_columns(table)):
            continue
        if not any(
            fk["constrained_columns"] == ["tenant_id"]
            and fk["referred_table"] == TENANTS_TABLE
            and fk["referred_columns"] == ["id"]
            for fk in inspector.get_foreign_keys(table)
        ):
            problems.append(f"{table}: tenant_id has no foreign key to {TENANTS_TABLE}.id")
    if connection.dialect.name == "postgresql":
        rows = connection.execute(
            text(
                "SELECT conrelid::regclass::text FROM pg_constraint WHERE contype = 'f'"
                " AND confrelid = to_regclass(:directory) AND NOT convalidated ORDER BY 1"
            ),
            {"directory": TENANTS_TABLE},
        )
        problems.extend(f"{table}: tenant_id foreign key not validated" for (table,) in rows)
    return problems
