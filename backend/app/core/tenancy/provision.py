"""Tenant provisioning: make sure a tenant's directory row exists (T-0079, ADR 0011).

``ensure_tenant`` is the one runtime path that writes a ``tenants`` row. It is idempotent: a
second call finds the row (soft-deleted or not) and creates nothing, and two processes
starting at once (API replicas, worker) do not fail: the insert runs in a savepoint and a
primary-key conflict with a row that appeared meanwhile counts as "present". It runs in a
tenant-level unit of work bound to that tenant, so it works under ``FORCE ROW LEVEL
SECURITY`` as the runtime role. Callers: the API start-up (``ensure_default_tenant``, for
``KERP_DEFAULT_TENANT_ID``), this module's command line
(``python -m app.core.tenancy.provision``) and T-0023's first-run command
(``app/modules/onboarding``), which first counts the directory (``tenant_directory_count``,
through the census function of migration ``0029``) to refuse a second tenant in a deployment.
Migration ``0026`` inserts the configured default tenant too, and a directory row for every
tenant id already in use.
"""

from __future__ import annotations

import logging
import sys
import uuid
from typing import Final

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import TENANTS_TABLE, Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.models import Tenant
from app.core.tenancy.rls import TENANT_CENSUS_FUNCTION

DEFAULT_DISPLAY_NAME: Final = "Default tenant"

logger = logging.getLogger(__name__)


def ensure_tenant(
    database: Database, tenant_id: uuid.UUID, display_name: str = DEFAULT_DISPLAY_NAME
) -> bool:
    """Create the directory row of ``tenant_id`` unless it exists. True if it was created."""
    with database.unit_of_work(TenancyContext(tenant_id=tenant_id)) as session:
        if _exists(session, tenant_id):
            return False
        try:
            with session.begin_nested():
                row = Tenant(
                    id=tenant_id, tenant_id=tenant_id, display_name=display_name, source="system"
                )
                session.add(row)
        except IntegrityError:
            # Another process provisioned it between our look and our insert.
            if _exists(session, tenant_id):
                return False
            raise
    return True


def _exists(session: Session, tenant_id: uuid.UUID) -> bool:
    """True if the directory row exists, soft-deleted or not."""
    found = session.scalars(
        select(Tenant.id).where(Tenant.id == tenant_id).execution_options(include_deleted=True)
    ).first()
    return found is not None


def tenant_directory_count(database: Database) -> int:
    """How many directory rows exist, of any tenant, soft-deleted ones included (T-0023).

    Only a number, never an id or a name. On PostgreSQL through the ``SECURITY DEFINER``
    census function of migration ``0029`` (the runtime role sees only its own tenant's row
    under RLS); on SQLite, which has no RLS, straight from the table.
    """
    with database.engine.connect() as connection:
        if connection.dialect.name == "postgresql":
            census = text(f"SELECT {TENANT_CENSUS_FUNCTION}()")
            return int(connection.execute(census).scalar_one())
        query = select(func.count()).select_from(Tenant.__table__)
        return int(connection.execute(query).scalar_one())


def tenant_exists(database: Database, tenant_id: uuid.UUID) -> bool:
    """True if ``tenant_id`` has a directory row, soft-deleted or not (T-0023)."""
    with database.unit_of_work(TenancyContext(tenant_id=tenant_id)) as session:
        return _exists(session, tenant_id)


def ensure_default_tenant(database: Database | None, settings: Settings) -> bool:
    """Start-up hook: provision ``KERP_DEFAULT_TENANT_ID`` before any tenant-scoped insert.

    A schema without the directory (not migrated to ``0026`` yet) holds no tenant-scoped
    table that references it either: nothing to provision, logged (like the team-grant census).
    """
    if database is None or settings.default_tenant_id is None:
        return False
    with database.engine.connect() as connection:
        if not inspect(connection).has_table(TENANTS_TABLE):
            logger.warning(
                "tenant provisioning skipped: no %s table (run the migrations)", TENANTS_TABLE
            )
            return False
    return ensure_tenant(database, settings.default_tenant_id)


def main(argv: list[str] | None = None) -> int:
    """``python -m app.core.tenancy.provision``: provision the configured default tenant."""
    del argv
    settings = get_settings()
    if settings.default_tenant_id is None or not settings.database_url:
        sys.stderr.write("KERP_DEFAULT_TENANT_ID and KERP_DATABASE_URL must be set\n")
        return 2
    database = Database(settings.database_url)
    try:
        created = ensure_default_tenant(database, settings)
    finally:
        database.dispose()
    sys.stdout.write("default tenant created\n" if created else "default tenant present\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main(sys.argv[1:]))
