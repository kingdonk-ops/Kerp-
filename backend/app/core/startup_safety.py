"""Production refuses to start on an unsafe database role or a missing runtime (T-0342).

One set of checks, called by the API ``lifespan`` (``app.main``) and the SAQ worker's
``serve`` (``app.modules.jobs.worker``), so both processes apply the same rule:

* ``check_database_role``: the role in ``KERP_DATABASE_URL`` must not skip row-level security
  (``SUPERUSER`` / ``BYPASSRLS``, ``rls_enforcement_problems``), every RLS table must have RLS
  enabled **and forced** with exactly its expected policies (``rls_coverage_problems``; an owner
  of a table without ``FORCE`` is not subject to its policies), and ``audit_ledger`` must stay
  append-only for the role (no ``UPDATE`` / ``DELETE`` / ``TRUNCATE``, triggers enabled,
  ``ledger_guard_problems``). In production any problem refuses the start
  (``StartupRefusedError``, one error log line per problem); elsewhere each is a warning.
  Without PostgreSQL (SQLite, no database) there is nothing to check: one log line says why.
* ``require_redis``: production needs ``KERP_REDIS_URL`` (token revocation, the job queue,
  the settings cache).

ADR 0001 (migrations as the schema owner, the app as a ``NOSUPERUSER NOBYPASSRLS`` role),
TD-056, TD-059, TD-124; ``docs/deploy/first-run.md`` explains how to read the refusal.
"""

from __future__ import annotations

import logging
import sys

from app.core.audit.guard import ledger_guard_problems
from app.core.config import Settings, get_settings
from app.core.db import Base, Database, import_all_models
from app.core.tenancy.rls import rls_coverage_problems, rls_enforcement_problems

logger = logging.getLogger("kerp.startup")


class StartupRefusedError(RuntimeError):
    """Production configuration the process must not run with."""


def database_role_problems(database: Database) -> list[str]:
    """Why the database role of ``database`` could bypass tenant isolation or the ledger guard
    (empty = safe). Every module's tables are checked, enabled for a project or not."""
    import_all_models()
    problems = rls_enforcement_problems(database.engine)
    with database.engine.connect() as connection:
        problems.extend(rls_coverage_problems(connection, Base.metadata))
        problems.extend(ledger_guard_problems(connection))
    return problems


def check_database_role(database: Database | None, settings: Settings) -> list[str]:
    """Run ``database_role_problems``; in production raise on any, elsewhere warn.

    Returns the problems found (always empty in production, which raises instead).
    """
    if database is None:
        logger.warning("database role checks skipped: KERP_DATABASE_URL is not set")
        return []
    dialect = database.engine.dialect.name
    if dialect != "postgresql":
        logger.info(
            "database role checks skipped: %s has no row-level security (PostgreSQL only)",
            dialect,
        )
        return []
    problems = database_role_problems(database)
    production = settings.environment == "production"
    for problem in problems:
        if production:
            logger.error("unsafe database role: %s", problem)
        else:
            logger.warning("unsafe database role (refused in production): %s", problem)
    if problems and production:
        raise StartupRefusedError(
            f"refusing to start: the database role in KERP_DATABASE_URL is unsafe "
            f"({len(problems)} problem(s)): {'; '.join(problems)}. Connect as the runtime role "
            "(NOSUPERUSER NOBYPASSRLS, owns no table); see docs/deploy/first-run.md"
        )
    if not problems:
        logger.info("database role checks passed")
    return problems


def require_redis(settings: Settings) -> None:
    """Production needs Redis: token revocation, the job queue and the settings cache."""
    if settings.environment == "production" and not settings.redis_url:
        raise StartupRefusedError(
            "KERP_REDIS_URL must be set in production (token revocation, job queue, settings cache)"
        )


def main(argv: list[str] | None = None) -> int:
    """``python -m app.core.startup_safety``: the database role check, as a pre-flight.

    Checks the role of ``KERP_DATABASE_URL`` against a migrated database (whatever
    ``KERP_ENVIRONMENT`` says) and prints each problem; exit code 1 if there is any, 2 without
    a PostgreSQL URL. CI's ``backend-pg-roles`` runs it after migrating as the owner (T-0343).
    """
    del argv
    settings = get_settings()
    if not (settings.database_url or "").startswith("postgresql"):
        sys.stderr.write("KERP_DATABASE_URL must name a PostgreSQL database\n")
        return 2
    database = Database(settings.database_url or "")
    try:
        problems = database_role_problems(database)
    finally:
        database.dispose()
    for problem in problems:
        sys.stdout.write(f"unsafe database role: {problem}\n")
    if not problems:
        sys.stdout.write("database role checks passed\n")
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main(sys.argv[1:]))
