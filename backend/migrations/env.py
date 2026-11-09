"""Alembic environment (adapted from Inspection-ERP ``backend/alembic/env.py``, ADR 0001).

The target metadata is ``Base.metadata`` after importing every module's models, so
``alembic revision --autogenerate`` sees the whole schema. Migrations run as the schema
owner (``KERP_MIGRATION_DATABASE_URL``) when one is configured, so the runtime role can stay
``NOSUPERUSER NOBYPASSRLS``. A caller may pass a URL instead via
``config.attributes["url"]`` (used by the tests).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.core.db import Base, import_all_models, include_object

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

import_all_models()
target_metadata = Base.metadata


def _database_url() -> str:
    url = config.attributes.get("url")
    if url:
        return str(url)
    settings = get_settings()
    resolved = settings.migration_database_url or settings.database_url
    if not resolved:
        raise RuntimeError("set KERP_MIGRATION_DATABASE_URL or KERP_DATABASE_URL")
    return resolved


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
