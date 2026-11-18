"""Shared database test helpers: the target URL and an isolated test model.

The suite runs on a throwaway SQLite file by default. CI's PostgreSQL step sets
``KERP_DATABASE_URL`` and the same tests run against PostgreSQL 16.
"""

import os
import uuid
from pathlib import Path

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.db import NAMING_CONVENTION, TENANTS_TABLE, BaseRecordMixin, Database
from app.core.tenancy.provision import ensure_tenant

POSTGRES_URL = os.environ.get("KERP_DATABASE_URL", "")
ON_POSTGRES = POSTGRES_URL.startswith("postgresql")


def database_url(tmp_path: Path) -> str:
    if ON_POSTGRES:
        return POSTGRES_URL
    return "sqlite:///" + (tmp_path / "kerp.db").as_posix()


class ScratchBase(DeclarativeBase):
    """Separate metadata, so test tables never leak into ``Base.metadata`` / migrations."""


ScratchBase.metadata.naming_convention = NAMING_CONVENTION


class ScratchTenant(ScratchBase, BaseRecordMixin):
    """The ``tenants`` directory table every scratch ``tenant_id`` references (T-0079).

    Same columns as ``app.core.tenancy.models.Tenant``, so ``seed_tenants`` (which writes
    through the real provisioning path) works on a scratch schema too.
    """

    __tablename__ = TENANTS_TABLE

    display_name: Mapped[str] = mapped_column(String(200), nullable=False)


# The synthetic tenant ids the shared fixtures use (``tenancy_support``, ``auth_support``, the
# jobs schedule and security header tests). ``migrated_database`` seeds a directory row for
# each, since every ``tenant_id`` is a foreign key to ``tenants`` (T-0079).
FIXTURE_TENANTS: tuple[uuid.UUID, ...] = tuple(
    uuid.UUID(f"00000000-0000-4000-8000-{suffix:0>12}")
    for suffix in ("1", "2", "e1", "e2", "51e1", "51e2", "f1")
)


def seed_tenants(db: Database, *tenant_ids: uuid.UUID) -> None:
    """Create the synthetic directory rows the fixtures' tenant ids reference (idempotent)."""
    for tenant_id in tenant_ids:
        ensure_tenant(db, tenant_id, f"Synthetic tenant {str(tenant_id)[-4:]}")


class Widget(ScratchBase, BaseRecordMixin):
    __tablename__ = "test_widget"

    name: Mapped[str] = mapped_column(String(50), nullable=False)


class Gadget(ScratchBase, BaseRecordMixin):
    """A second table, standing in for another module's data in the cross-module tests."""

    __tablename__ = "test_gadget"

    label: Mapped[str] = mapped_column(String(50), nullable=False)
