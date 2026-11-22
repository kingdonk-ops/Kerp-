"""Migration ``0039``: the runtime role's grants when the owner migrates (T-0343, ADR 0019).

Each test migrates its own empty database **as the owner** (``KERP_TEST_OWNER_URL``), with
``KERP_RUNTIME_DB_ROLE`` naming the role of ``KERP_DATABASE_URL`` (CI: ``kerp_app``), and then
works as that runtime role, which owns nothing. Holds in both CI layouts (the shards, where the
runtime role normally migrates, and ``backend-pg-roles``). Synthetic data only.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.core.db import Database
from app.core.startup_safety import database_role_problems
from app.core.tenancy.context import TenancyContext
from tests import pg_databases
from tests.audit_support import ALEMBIC_INI, OWNER_URL
from tests.db_support import ON_POSTGRES, database_url

pytestmark = pytest.mark.skipif(
    not (ON_POSTGRES and OWNER_URL),
    reason="needs PostgreSQL and KERP_TEST_OWNER_URL; runs in CI's PostgreSQL jobs (TD-052)",
)

TENANT = uuid.UUID("00000000-0000-4000-8000-0000000003a1")
LOBBY_FUNCTIONS = (
    "kerp_lobby_projects()",
    "kerp_lobby_project_by_code(text)",
    "kerp_lobby_projects_by_id(uuid[])",
    "kerp_caller_admin_grants()",
    "kerp_live_team_link_grant_count()",
    "kerp_tenant_directory_count()",
)


def _config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=url, configure_logger=False)
    return cfg


class Split:
    """One test database: migrated by the owner, used by the runtime role."""

    def __init__(self, runtime_url: str) -> None:
        self.runtime_url = runtime_url
        name = make_url(runtime_url).database
        assert name
        self.owner_url = pg_databases.owner_url(name) if pg_databases.PER_TEST else OWNER_URL
        role = make_url(runtime_url).username
        assert role
        self.role = role
        self.owner = create_engine(self.owner_url)
        self.runtime = create_engine(runtime_url)

    def migrate(self, target: str = "head") -> None:
        command.upgrade(_config(self.owner_url), target)

    def downgrade(self, target: str) -> None:
        command.downgrade(_config(self.owner_url), target)

    def dispose(self) -> None:
        self.owner.dispose()
        self.runtime.dispose()


@pytest.fixture
def split(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Split]:
    runtime_url = database_url(tmp_path)
    role = make_url(runtime_url).username
    monkeypatch.setenv("KERP_RUNTIME_DB_ROLE", str(role))
    get_settings.cache_clear()
    database = Split(runtime_url)
    try:
        yield database
    finally:
        try:
            if not pg_databases.PER_TEST:  # the shared database must be left empty again
                database.downgrade("base")
        finally:
            database.dispose()


def _privilege(conn: Connection, role: str, table: str, privilege: str) -> bool:
    return bool(
        conn.execute(
            text("SELECT has_table_privilege(:role, :table, :privilege)"),
            {"role": role, "table": table, "privilege": privilege},
        ).scalar_one()
    )


def _function_privilege(engine: Engine, role: str, signature: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text("SELECT has_function_privilege(:role, :fn, 'EXECUTE')"),
                {"role": role, "fn": signature},
            ).scalar_one()
        )


def _default_acl_count(engine: Engine, role: str) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_default_acl d, aclexplode(d.defaclacl) a"
                    " WHERE a.grantee = (SELECT oid FROM pg_roles WHERE rolname = :role)"
                ),
                {"role": role},
            ).scalar_one()
        )


def test_the_runtime_role_owns_nothing_and_has_what_it_needs(split: Split) -> None:
    split.migrate()
    with split.runtime.connect() as conn:
        owned = conn.execute(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = current_schema() AND c.relowner ="
                " (SELECT oid FROM pg_roles WHERE rolname = current_user)"
            )
        ).scalar_one()
        assert owned == 0
        # Created after 0039, granted by its default privileges: mail_template_override (0042,
        # T-0227) and the broadcast tables (0043, T-0228).
        tables = ("oe_projects_project", "auth_user", "tenants", "oe_jobs_job")
        later = (
            "mail_template_override",
            "oe_notifications_broadcast",
            "oe_notifications_broadcast_recipient",
            "oe_notifications_broadcast_optout",
        )
        for table in (*tables, *later):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert _privilege(conn, split.role, table, privilege), (table, privilege)
            assert not _privilege(conn, split.role, table, "TRUNCATE"), table
        assert not _privilege(conn, split.role, "alembic_version", "SELECT")
    for signature in LOBBY_FUNCTIONS:
        assert _function_privilege(split.runtime, split.role, signature), signature
    # The three startup checks pass for the runtime role (T-0342).
    database = Database(split.runtime_url)
    try:
        assert database_role_problems(database) == []
    finally:
        database.dispose()


def test_the_runtime_role_can_only_append_to_the_ledger(split: Split) -> None:
    split.migrate()
    with split.runtime.connect() as conn:
        assert _privilege(conn, split.role, "audit_ledger", "SELECT")
        assert _privilege(conn, split.role, "audit_ledger", "INSERT")
        for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
            assert not _privilege(conn, split.role, "audit_ledger", privilege), privilege
    # Refused by the missing privilege itself (not only by the trigger): permission denied.
    for statement in ("UPDATE audit_ledger SET seq = seq", "DELETE FROM audit_ledger"):
        with (
            pytest.raises(DBAPIError, match="permission denied for table audit_ledger"),
            split.runtime.begin() as conn,
        ):
            conn.execute(text(statement))


def test_the_app_works_as_the_runtime_role(split: Split) -> None:
    from app.core.tenancy.provision import ensure_tenant
    from app.modules.projects import service as projects
    from app.modules.projects.schemas import ProjectCreate

    split.migrate()
    database = Database(split.runtime_url)
    try:
        ensure_tenant(database, TENANT, "Synthetic split tenant")
        root = TenancyContext(tenant_id=TENANT, is_system_admin=True)
        with database.unit_of_work(root) as session:
            project = projects.create_project(
                session, ProjectCreate(code="SPL", name="Synthetic split")
            )
            project_id = project.id
        with database.unit_of_work(root) as session:
            assert projects.get_project(session, project_id).code == "SPL"
    finally:
        database.dispose()


def test_later_owner_objects_get_the_default_privileges(split: Split) -> None:
    split.migrate()
    with split.owner.begin() as conn:
        conn.execute(text("CREATE TABLE kerp_t_later (id integer PRIMARY KEY)"))
        conn.execute(text("CREATE SEQUENCE kerp_t_later_seq"))
        conn.execute(
            text("CREATE FUNCTION kerp_t_later_fn() RETURNS integer LANGUAGE sql AS 'SELECT 1'")
        )
        conn.execute(text("REVOKE EXECUTE ON FUNCTION kerp_t_later_fn() FROM PUBLIC"))
    try:
        with split.runtime.connect() as conn:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert _privilege(conn, split.role, "kerp_t_later", privilege), privilege
            assert conn.execute(text("SELECT nextval('kerp_t_later_seq')")).scalar_one() == 1
        assert _function_privilege(split.runtime, split.role, "kerp_t_later_fn()")
    finally:
        with split.owner.begin() as conn:
            conn.execute(text("DROP FUNCTION kerp_t_later_fn()"))
            conn.execute(text("DROP SEQUENCE kerp_t_later_seq"))
            conn.execute(text("DROP TABLE kerp_t_later"))


def test_the_downgrade_revokes_exactly_the_grants(split: Split) -> None:
    split.migrate("0038")
    with split.owner.connect() as conn:
        before_tables = _privilege(conn, split.role, "oe_projects_project", "SELECT")
    before_functions = {s: _function_privilege(split.owner, split.role, s) for s in LOBBY_FUNCTIONS}
    assert not before_tables
    assert _default_acl_count(split.owner, split.role) == 0
    split.migrate("0039")
    # SELECT, INSERT, UPDATE, DELETE on tables; USAGE, SELECT on sequences; EXECUTE on functions.
    assert _default_acl_count(split.owner, split.role) == 7
    split.downgrade("0038")
    with split.owner.connect() as conn:
        assert not _privilege(conn, split.role, "oe_projects_project", "SELECT")
        assert not _privilege(conn, split.role, "audit_ledger", "INSERT")
    # 0025 and 0029 grant their census functions to kerp_app themselves; 0039 takes back
    # exactly its own grant, so those two stay as they were before it.
    after_functions = {s: _function_privilege(split.owner, split.role, s) for s in LOBBY_FUNCTIONS}
    assert after_functions == before_functions
    assert _default_acl_count(split.owner, split.role) == 0


def test_a_missing_role_is_skipped(
    split: Split, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("KERP_RUNTIME_DB_ROLE", "kerp_t_no_such_role")
    get_settings.cache_clear()
    with caplog.at_level("INFO", logger="kerp.migrations"):
        split.migrate()
    assert "runtime role grants skipped: role kerp_t_no_such_role does not exist" in caplog.text
    with split.owner.connect() as conn:
        assert not _privilege(conn, split.role, "oe_projects_project", "SELECT")


def test_an_empty_setting_is_skipped(
    split: Split, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("KERP_RUNTIME_DB_ROLE", "")
    get_settings.cache_clear()
    with caplog.at_level("INFO", logger="kerp.migrations"):
        split.migrate()
    assert "runtime role grants skipped: KERP_RUNTIME_DB_ROLE is empty" in caplog.text
    assert _default_acl_count(split.owner, split.role) == 0


def test_the_migrating_role_itself_is_skipped(
    split: Split, caplog: pytest.LogCaptureFixture
) -> None:
    # The runtime role migrating its own database (local dev, CI's shards): nothing to grant.
    with caplog.at_level("INFO", logger="kerp.migrations"):
        command.upgrade(_config(split.runtime_url), "head")
    assert f"runtime role grants skipped: {split.role} is the migrating role" in caplog.text
    assert _default_acl_count(split.owner, split.role) == 0
    command.downgrade(_config(split.runtime_url), "base")  # as it was migrated
