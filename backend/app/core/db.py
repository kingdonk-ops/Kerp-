"""Database foundation: portable column types, ``Base``, ``BaseRecordMixin``, sessions.

Adapted from Inspection-ERP ``backend/app/database.py`` (ADR 0001).

Every KERP table derives from ``BaseRecordMixin`` (CLAUDE.md §6). The metadata contract
test (``tests/test_db_contract.py``) enforces this through ``contract_violations``.

The column types are dialect-portable: native ``UUID``/``JSONB`` on PostgreSQL, ``CHAR(36)``
and ``JSON`` elsewhere. That lets the fast suite run on SQLite, while CI also runs the
suite on PostgreSQL 16. Tenant/project RLS lives in ``app.core.tenancy``. The hash-chained
audit ledger lives in ``app.core.audit`` (T-0006).
"""

from __future__ import annotations

import importlib
import pkgutil
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Final

from fastapi import Depends, Request
from sqlalchemy import (
    CHAR,
    JSON,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    TypeDecorator,
    create_engine,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine import Dialect, Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    UOWTransaction,
    mapped_column,
    sessionmaker,
)
from sqlalchemy.types import TypeEngine

from app.core.config import Settings, get_settings
from app.core.tenancy.context import TenancyContext, get_tenancy_context, use_tenancy
from app.core.tenancy.rls import bind_tenancy, install_pool_reset

# Deterministic constraint names, so Alembic autogenerate produces stable diffs.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Allowed values of ``BaseRecordMixin.source``. ``ai`` writes need human confirmation
# before they are applied (CLAUDE.md §6).
RECORD_SOURCES: Final[frozenset[str]] = frozenset({"ui", "api", "import", "sync", "ai", "system"})

# The dedicated side-write pool (see ``Database.side_write_sessionmaker``). A write that must
# outlive the caller's transaction — the verification findings log — runs here, on a pool that
# is separate from and smaller than the request pool, so it never waits on a connection the
# caller's request already holds. ``Database.session`` stashes its ``Database`` under this key
# on every session it opens, so service code can reach the pool from the session alone.
SIDE_WRITE_DB_KEY: Final = "kerp_side_write_db"
SIDE_WRITE_POOL_SIZE: Final = 5
SIDE_WRITE_MAX_OVERFLOW: Final = 10
SIDE_WRITE_POOL_TIMEOUT: Final = 10
SIDE_WRITE_LOCK_TIMEOUT_MS: Final = 3000


class GUID(TypeDecorator[uuid.UUID]):
    """Platform-independent UUID: native ``UUID`` on PostgreSQL, ``CHAR(36)`` elsewhere."""

    impl = CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(
        self, value: uuid.UUID | str | None, dialect: Dialect
    ) -> uuid.UUID | str | None:
        if value is None:
            return None
        as_uuid = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return as_uuid if dialect.name == "postgresql" else str(as_uuid)

    def process_result_value(self, value: object, dialect: Dialect) -> uuid.UUID | None:
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class JSONType(TypeDecorator[Any]):
    """``JSONB`` on PostgreSQL, ``JSON`` elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class Base(DeclarativeBase):
    """Declarative base for every KERP model (single ``MetaData`` for Alembic)."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    """Timezone-aware UTC now. All timestamps are stored as ``TIMESTAMPTZ`` in UTC."""
    return datetime.now(UTC)


class BaseRecordMixin:
    """The KERP record contract. Every table carries these columns.

    * ``id``: UUID primary key.
    * ``tenant_id``: mandatory and indexed. It comes from the JWT only and is the RLS key.
    * ``project_id``: indexed. ``NULL`` marks a tenant-level row, such as the project row
      itself or a tenant-wide library. A model that must always belong to a project
      redeclares it as ``nullable=False``.
    * ``created_at`` / ``updated_at``: UTC ``TIMESTAMPTZ``.
    * ``deleted_at``: soft-delete marker. Rows are never hard-deleted by services.
    * ``custom_fields``: JSONB attribute bag, validated by the field registry. The spec
      has no EAV tables.
    * ``created_by`` / ``updated_by`` / ``source``: provenance (``source="ai"`` marks writes
      that need human confirmation).
    * ``revision``: incremented on every update. It is the base revision that offline sync
      compares against. Nothing enforces optimistic locking on it yet (see TD-035).
    """

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONType(), nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="api")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, *, at: datetime | None = None) -> None:
        """Mark the record deleted. Calling it on an already-deleted record keeps the first time."""
        if self.deleted_at is None:
            self.deleted_at = at or utcnow()


# Tables created by PostgreSQL extensions (PostGIS ``spatial_ref_sys``; ``postgis_topology``
# ``topology``/``layer``, visible through the PostGIS image's search_path). They are not ours,
# so Alembic autogenerate and the migration check must not try to drop them.
EXTENSION_TABLES: Final[frozenset[str]] = frozenset({"spatial_ref_sys", "topology", "layer"})


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object | None
) -> bool:
    """Alembic ``include_object`` hook: ignore extension tables found only in the database.

    Only a *reflected* table with no model counterpart is skipped, so a KERP model that
    happened to reuse one of these names would still be compared.
    """
    return not (type_ == "table" and reflected and compare_to is None and name in EXTENSION_TABLES)


# Column name → whether it must be NOT NULL. The contract test checks every table against it.
MIXIN_COLUMNS: Final[dict[str, bool]] = {
    "id": True,
    "tenant_id": True,
    "project_id": False,  # a model may tighten this to NOT NULL
    "created_at": True,
    "updated_at": True,
    "deleted_at": False,
    "custom_fields": True,
    "created_by": False,
    "updated_by": False,
    "source": True,
    "revision": True,
}


def contract_violations(metadata: MetaData, *, exempt: frozenset[str] = frozenset()) -> list[str]:
    """Return why each table in ``metadata`` breaks the record contract (empty = OK)."""
    problems: list[str] = []
    for name, table in sorted(metadata.tables.items()):
        if name in exempt:
            continue
        problems.extend(_table_violations(table))
    return problems


def _table_violations(table: Table) -> list[str]:
    problems: list[str] = []
    for column_name, must_be_not_null in MIXIN_COLUMNS.items():
        column = table.columns.get(column_name)
        if column is None:
            problems.append(f"{table.name}: missing mixin column '{column_name}'")
        elif must_be_not_null and column.nullable:
            problems.append(f"{table.name}: '{column_name}' must be NOT NULL")
    id_column = table.columns.get("id")
    if id_column is not None and not id_column.primary_key:
        problems.append(f"{table.name}: 'id' must be the primary key")
    return problems


@event.listens_for(Session, "before_flush")
def _maintain_record_contract(
    session: Session, flush_context: UOWTransaction, instances: object
) -> None:
    """Validate ``source`` on every write, and bump ``revision`` on every real update."""
    for obj in session.new:
        if isinstance(obj, BaseRecordMixin):
            _check_source(obj)
    for obj in session.dirty:
        if isinstance(obj, BaseRecordMixin) and session.is_modified(obj, include_collections=False):
            _check_source(obj)
            obj.revision = (obj.revision or 1) + 1


def _check_source(obj: BaseRecordMixin) -> None:
    # ``None`` means "not set yet": the column default ("api") applies at INSERT.
    if obj.source is not None and obj.source not in RECORD_SOURCES:
        raise ValueError(f"{type(obj).__name__}.source must be one of {sorted(RECORD_SOURCES)}")


def import_all_models(package: str = "app.modules") -> list[str]:
    """Import ``<package>.<module>.models`` for every module, registering its tables.

    Alembic and the contract test call this, so ``Base.metadata`` sees the whole schema.
    It returns the imported module paths.
    """
    pkg = importlib.import_module(package)
    imported: list[str] = []
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        if not info.ispkg:
            continue
        name = f"{package}.{info.name}.models"
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name == name:
                continue
            raise
        imported.append(name)
    return imported


# --- engine / sessions -----------------------------------------------------------------


def create_db_engine(
    url: str,
    *,
    connect_args: dict[str, Any] | None = None,
    pool_size: int | None = None,
    max_overflow: int | None = None,
    pool_timeout: float | None = None,
) -> Engine:
    """Create an engine. SQLite connections may be shared across FastAPI's worker threads.

    The optional pool arguments size a dedicated pool (the findings side-write pool, or a test
    that deliberately exhausts one); unset, SQLAlchemy's defaults apply.
    """
    args: dict[str, Any] = {"check_same_thread": False} if url.startswith("sqlite") else {}
    if connect_args:
        args.update(connect_args)
    options: dict[str, Any] = {"connect_args": args, "pool_pre_ping": True}
    if pool_size is not None:
        options["pool_size"] = pool_size
    if max_overflow is not None:
        options["max_overflow"] = max_overflow
    if pool_timeout is not None:
        options["pool_timeout"] = pool_timeout
    return create_engine(url, **options)


class Database:
    """One engine and session factory per configured URL. ``app.state.db`` holds one."""

    def __init__(
        self,
        url: str,
        *,
        pool_size: int | None = None,
        max_overflow: int | None = None,
        pool_timeout: float | None = None,
    ) -> None:
        self.url = url
        self.engine = create_db_engine(
            url, pool_size=pool_size, max_overflow=max_overflow, pool_timeout=pool_timeout
        )
        install_pool_reset(self.engine)
        self.sessionmaker: sessionmaker[Session] = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )
        self._side_write_lock = threading.Lock()
        self._side_write_engine: Engine | None = None
        self._side_write_sessionmaker: sessionmaker[Session] | None = None

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Database:
        settings = settings or get_settings()
        if not settings.database_url:
            raise RuntimeError("KERP_DATABASE_URL is not set")
        return cls(settings.database_url)

    @property
    def side_write_sessionmaker(self) -> sessionmaker[Session]:
        """A session factory on a dedicated pool, for a write that must outlive the caller's
        transaction and must never wait on the request pool — the verification findings log.

        Sharing the request pool here risks a client-side deadlock the database cannot see:
        under load every request could hold its own connection while sitting inside a
        side-write that waits for a second one, so all of them time out (30s by default) and
        the gated transition returns 500. This pool is separate and small; on PostgreSQL a
        short ``lock_timeout`` makes an FK ``KEY SHARE`` wait on a row the caller still holds
        fail fast instead of hanging. Created once per ``Database`` (lazily).
        """
        with self._side_write_lock:
            if self._side_write_sessionmaker is None:
                connect_args = (
                    {"options": f"-c lock_timeout={SIDE_WRITE_LOCK_TIMEOUT_MS}"}
                    if self.url.startswith("postgresql")
                    else None
                )
                engine = create_db_engine(
                    self.url,
                    connect_args=connect_args,
                    pool_size=SIDE_WRITE_POOL_SIZE,
                    max_overflow=SIDE_WRITE_MAX_OVERFLOW,
                    pool_timeout=SIDE_WRITE_POOL_TIMEOUT,
                )
                install_pool_reset(engine)
                self._side_write_engine = engine
                self._side_write_sessionmaker = sessionmaker(
                    bind=engine, autoflush=False, expire_on_commit=False
                )
            return self._side_write_sessionmaker

    def session(self, tenancy: TenancyContext | None = None) -> Iterator[Session]:
        """Unit of work: commit on success, roll back on any error, always close.

        With ``tenancy`` the session is bound for RLS and the ORM scope before first use.
        """
        session = self.sessionmaker()
        session.info[SIDE_WRITE_DB_KEY] = self
        bind_tenancy(session, tenancy)
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def unit_of_work(self, tenancy: TenancyContext) -> Iterator[Session]:
        """A bound unit of work outside a request: SAQ jobs, scripts, service calls.

        A job never inherits the enqueuing request's context; it binds the tenant and
        project recorded on the job explicitly. The context variables are set too.
        """
        with use_tenancy(tenancy), contextmanager(self.session)(tenancy) as session:
            yield session

    def dispose(self) -> None:
        self.engine.dispose()
        if self._side_write_engine is not None:
            self._side_write_engine.dispose()


def get_db(
    request: Request,
    tenancy: Annotated[TenancyContext | None, Depends(get_tenancy_context)],
) -> Iterator[Session]:
    """FastAPI dependency: one unit-of-work session per request.

    Always use it through ``DbSession``. That alias pins ``scope="function"``, so the
    commit runs *before* the response is sent, and a write that fails at commit returns
    a 500, never a 200. A failed commit can come from a constraint, an RLS ``WITH CHECK``
    rule or a serialization failure. The default scope commits after the response has
    gone. The session is bound to the tenancy context from ``get_tenancy_context`` (the
    seam auth fills in T-0008); without one it is unbound and RLS shows it nothing.
    """
    database: Database | None = getattr(request.app.state, "db", None)
    if database is None:
        raise RuntimeError("database not configured (set KERP_DATABASE_URL)")
    yield from database.session(tenancy)


# The only sanctioned way for endpoints to obtain a database session.
DbSession = Annotated[Session, Depends(get_db, scope="function")]

# Imported last because they need ``Base``/``BaseRecordMixin`` from this module.
# ``audit.models`` (T-0006), ``auth.models`` (T-0008): core tables live outside ``app.modules``;
# importing them here puts them on ``Base.metadata`` wherever the DB layer is used (Alembic,
# contract tests).
# ``module_loader.models``: the per-project module settings table (T-0011).
# ``tenancy.scope``: the soft-delete / tenancy read filters and write guard on every Session.
from app.core.audit import models as _audit_models  # noqa: E402, F401
from app.core.auth import models as _auth_models  # noqa: E402, F401
from app.core.module_loader import models as _module_models  # noqa: E402, F401
from app.core.tenancy import scope as _tenancy_scope  # noqa: E402, F401
