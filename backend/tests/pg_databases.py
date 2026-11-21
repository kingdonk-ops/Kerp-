"""Per-test PostgreSQL databases cloned from per-worker templates (T-0082).

Before T-0082 every PostgreSQL test migrated the one shared ``kerp_test`` database up to head
and back down again (~1 s per test, strictly serial). Now:

* the source database named in ``KERP_DATABASE_URL`` (``kerp_test``: the ``ltree`` / ``postgis``
  extensions and the ``public`` schema owned by ``kerp_app``, exactly as
  ``infra/postgres/initdb/20-kerp-app.sql`` leaves it) is only ever used as a template;
* each test process (each xdist worker) builds two templates once: ``kerp_tmpl_empty_<worker>``
  (a copy of the source) and ``kerp_tmpl_head_<worker>`` (migrated to head **as the runtime
  role**, fixture tenants seeded);
* each test that touches the database gets its own ``CREATE DATABASE … TEMPLATE …`` copy
  (empty by default, migrated for ``migrated_database``), dropped at teardown.

Every test still runs on real, committed PostgreSQL as the NOSUPERUSER NOBYPASSRLS runtime role;
the RLS policies, REVOKEs, ledger triggers and grants are the ones the migrations created, copied
byte for byte. Nothing wraps tests in an outer transaction (rejected in T-0082: ``SET LOCAL``
would leak across units of work and side-write connections could not see uncommitted rows).

Needs ``KERP_TEST_OWNER_URL`` (a role that may create databases; CI and ``make test-pg`` set
it). Without it the suite falls back to the shared database (``PER_TEST`` is false).
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Callable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool

RUNTIME_URL = os.environ.get("KERP_DATABASE_URL", "")
OWNER_URL = os.environ.get("KERP_TEST_OWNER_URL", "")
PER_TEST = RUNTIME_URL.startswith("postgresql") and OWNER_URL.startswith("postgresql")

# "gw0", "gw1", … under pytest-xdist; "main" in a plain run.
WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")
EMPTY_TEMPLATE = f"kerp_tmpl_empty_{WORKER}"
HEAD_TEMPLATE = f"kerp_tmpl_head_{WORKER}"

_counter = itertools.count(1)
_current: str | None = None  # the running test's database, created on first use
_templates_built: set[str] = set()


def _with_database(url: str, name: str) -> str:
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def runtime_url(name: str) -> str:
    """URL of database ``name`` as the runtime role (``kerp_app``)."""
    return _with_database(RUNTIME_URL, name)


def owner_url(name: str) -> str:
    """URL of database ``name`` as the owner role (tamper helpers, role checks)."""
    return _with_database(OWNER_URL, name)


def _admin() -> Engine:
    """Owner connection to the ``postgres`` maintenance database, in autocommit mode
    (``CREATE DATABASE`` cannot run inside a transaction)."""
    return create_engine(
        _with_database(OWNER_URL, "postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )


def _quote(name: str) -> str:
    # Names are built here from the worker id and a counter, never from input; still check.
    assert name.replace("_", "").isalnum(), name
    assert name.islower(), name
    return f'"{name}"'


def _create(name: str, template: str) -> None:
    runtime_role = make_url(RUNTIME_URL).username
    engine = _admin()
    try:
        with engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {_quote(name)} WITH (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {_quote(name)} TEMPLATE {_quote(template)}"))
            # Database-level ACLs live in pg_database and are not copied from the template;
            # the init script grants them on the source, so grant them again here.
            conn.execute(text(f"GRANT ALL ON DATABASE {_quote(name)} TO {_quote(runtime_role)}"))
    finally:
        engine.dispose()


def drop(name: str) -> None:
    engine = _admin()
    try:
        with engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {_quote(name)} WITH (FORCE)"))
    finally:
        engine.dispose()


def _build_templates(migrate_and_seed: Callable[[str], None]) -> None:
    """Build this worker's two templates (once per process)."""
    if HEAD_TEMPLATE in _templates_built:
        return
    source = make_url(RUNTIME_URL).database
    assert source, "KERP_DATABASE_URL must name the source database"
    _create(EMPTY_TEMPLATE, source)
    _templates_built.add(EMPTY_TEMPLATE)
    _create(HEAD_TEMPLATE, EMPTY_TEMPLATE)
    migrate_and_seed(runtime_url(HEAD_TEMPLATE))  # disposes its engines before returning
    _templates_built.add(HEAD_TEMPLATE)


def begin_test() -> None:
    global _current
    _current = None


def end_test() -> None:
    """Drop the running test's database, if it made one (``WITH (FORCE)`` ends any pooled
    connections a test forgot to dispose)."""
    global _current
    if _current is not None:
        drop(_current)
    _current = None


def drop_templates() -> None:
    for name in sorted(_templates_built, reverse=True):  # head before empty
        drop(name)
    _templates_built.clear()


def current_name() -> str | None:
    return _current


def test_database_url(migrate_and_seed: Callable[[str], None]) -> str:
    """The running test's database (a copy of the empty template), created on first use."""
    global _current
    if _current is None:
        _build_templates(migrate_and_seed)
        _current = f"kerp_t_{WORKER}_{next(_counter)}"
        _create(_current, EMPTY_TEMPLATE)
    return runtime_url(_current)


def migrated_test_database_url(migrate_and_seed: Callable[[str], None]) -> tuple[str, bool]:
    """The running test's database at head with the fixture tenants seeded.

    Returns ``(url, ready)``. ``ready`` is false only when the test had already created its
    (empty) database through ``database_url`` before asking for a migrated one; the caller
    then migrates and seeds it itself.
    """
    global _current
    if _current is not None:
        return runtime_url(_current), False
    _build_templates(migrate_and_seed)
    _current = f"kerp_t_{WORKER}_{next(_counter)}"
    _create(_current, HEAD_TEMPLATE)
    return runtime_url(_current), True
