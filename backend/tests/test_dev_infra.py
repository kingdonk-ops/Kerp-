"""Guards for the local dev stack (T-0003, TD-052): it must run the PostgreSQL tests as CI does.

``docker-compose.yml`` and the ``Makefile`` live at the repository root;
the app-role SQL in ``infra/postgres/initdb/`` is shared by compose (as an init script) and
CI's backend job (``psql -f``). These tests only read files, so they run everywhere.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml  # PyYAML: installed with cel-python (runtime dep), see requirements.lock

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
MAKEFILE = ROOT / "Makefile"
INIT_SQL_REL = "infra/postgres/initdb/20-kerp-app.sql"
INIT_SQL = ROOT / INIT_SQL_REL
PG_TESTS = ("tests/test_rls_bypass.py", "tests/test_audit_ledger_pg.py")


def _yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return _yaml(COMPOSE)["services"]


@pytest.fixture(scope="module")
def ci_backend() -> dict[str, Any]:
    return _yaml(CI)["jobs"]["backend"]


def _ci_step(ci_backend: dict[str, Any], name_prefix: str) -> dict[str, Any]:
    steps = [s for s in ci_backend["steps"] if str(s.get("name", "")).startswith(name_prefix)]
    assert len(steps) == 1, f"expected one CI step named {name_prefix!r}"
    return steps[0]


def _make_defaults() -> dict[str, str]:
    """``KEY ?= VALUE`` defaults in the Makefile (overridable by ``.env`` or the shell)."""
    pairs = re.findall(r"^(KERP_[A-Z_]+)\s*\?=\s*(\S*)\s*$", MAKEFILE.read_text("utf-8"), re.M)
    return dict(pairs)


def _sql_statements() -> list[str]:
    body = "\n".join(
        line.split("--", 1)[0] for line in INIT_SQL.read_text(encoding="utf-8").splitlines()
    )
    return [" ".join(s.split()) for s in body.split(";") if s.strip()]


# --- compose mirrors CI's service containers ------------------------------------------------


def test_compose_postgres_uses_the_ci_postgis_16_image(
    compose: dict[str, Any], ci_backend: dict[str, Any]
) -> None:
    ci_image = ci_backend["services"]["postgres"]["image"]
    assert ci_image.startswith("postgis/postgis:16-")
    assert compose["postgres"]["image"] == ci_image


def test_compose_postgres_superuser_and_database_match_ci(
    compose: dict[str, Any], ci_backend: dict[str, Any]
) -> None:
    ci_env = ci_backend["services"]["postgres"]["env"]
    env = compose["postgres"]["environment"]
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        assert env[key] == ci_env[key], key


def test_compose_redis_uses_the_ci_redis_7_image(
    compose: dict[str, Any], ci_backend: dict[str, Any]
) -> None:
    ci_image = ci_backend["services"]["redis"]["image"]
    assert ci_image.startswith("redis:7")
    assert compose["redis"]["image"] == ci_image


def test_compose_has_minio_with_pinned_release() -> None:
    services = _yaml(COMPOSE)["services"]
    image = services["minio"]["image"]
    assert re.fullmatch(r"minio/minio:RELEASE\.[0-9TZ:-]+", image), image


def test_compose_services_have_healthchecks(compose: dict[str, Any]) -> None:
    # `docker compose up --wait` (make up) relies on these.
    for name in ("postgres", "redis", "minio"):
        assert "healthcheck" in compose[name], name


def test_postgres_healthcheck_uses_tcp_not_the_init_socket(compose: dict[str, Any]) -> None:
    # During initdb the entrypoint's temporary server listens on the Unix socket only, so a
    # socket check would report "ready" before the init script (kerp_app) has run.
    check = " ".join(compose["postgres"]["healthcheck"]["test"])
    assert "pg_isready" in check
    assert "-h 127.0.0.1" in check


def test_compose_mounts_the_init_script_as_a_file(compose: dict[str, Any]) -> None:
    # A file mount (not the whole directory) keeps the PostGIS image's own 10_postgis.sh.
    volumes = compose["postgres"]["volumes"]
    target = f"./{INIT_SQL_REL}:/docker-entrypoint-initdb.d/{INIT_SQL.name}:ro"
    assert target in volumes
    assert not any(str(v).split(":")[1] == "/docker-entrypoint-initdb.d" for v in volumes)


# --- one app-role script, used by CI and compose ------------------------------------------


def test_init_script_creates_kerp_app_without_superuser_or_bypassrls() -> None:
    roles = [s for s in _sql_statements() if s.upper().startswith("CREATE ROLE KERP_APP")]
    assert len(roles) == 1
    words = set(roles[0].upper().split())
    assert {"LOGIN", "NOSUPERUSER", "NOBYPASSRLS"} <= words
    assert not words & {"SUPERUSER", "BYPASSRLS", "CREATEROLE"}


def test_init_script_gives_kerp_app_the_database_and_public_schema() -> None:
    sql = " ".join(_sql_statements()).upper()
    assert "GRANT ALL ON DATABASE %I TO KERP_APP" in sql
    assert "CURRENT_DATABASE()" in sql
    assert "ALTER SCHEMA PUBLIC OWNER TO KERP_APP" in sql


def test_init_script_enables_ltree_and_postgis() -> None:
    statements = {s.upper() for s in _sql_statements()}
    assert "CREATE EXTENSION IF NOT EXISTS LTREE" in statements
    assert "CREATE EXTENSION IF NOT EXISTS POSTGIS" in statements


def test_ci_creates_the_app_role_from_the_shared_script(ci_backend: dict[str, Any]) -> None:
    run = _ci_step(ci_backend, "Create non-superuser app role")["run"]
    assert "-v ON_ERROR_STOP=1" in run
    assert f"-f ../{INIT_SQL_REL}" in run  # job working-directory is backend/
    assert "CREATE ROLE" not in run  # no second, drifting copy of the SQL


# --- the Makefile exports the CI connection settings -------------------------------------


def test_make_defaults_match_ci_postgres_step(ci_backend: dict[str, Any]) -> None:
    ci_env = _ci_step(ci_backend, "Tests (PostgreSQL + RLS)")["env"]
    env = _make_defaults()
    for key in ("KERP_DATABASE_URL", "KERP_TEST_OWNER_URL", "KERP_REDIS_URL"):
        assert env[key] == ci_env[key], key
    assert "://kerp_app:" in env["KERP_DATABASE_URL"]
    assert "://kerp:" in env["KERP_TEST_OWNER_URL"]


def test_make_defaults_hold_no_signing_key() -> None:
    assert not any(key.startswith("KERP_JWT_") for key in _make_defaults())
    assert "PRIVATE KEY" not in MAKEFILE.read_text(encoding="utf-8")


def test_makefile_exports_its_settings_and_reads_an_optional_local_env() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert re.search(r"^-include \.env$", text, re.MULTILINE)
    assert re.search(r"^export$", text, re.MULTILINE)
    # .env must be read before the ?= defaults, or it could not override them.
    assert text.index("-include .env") < text.index("KERP_DATABASE_URL ?=")


def test_make_test_pg_runs_the_rls_and_ledger_tests_with_no_skips() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    recipe = re.search(r"^test-pg:.*\n((?:\t.*\n)+)", text, re.MULTILINE)
    assert recipe, "Makefile has no test-pg target"
    body = recipe.group(1)
    assert "KERP_FAIL_ON_SKIP=1" in body
    assert "--no-cov" in body
    pg_tests = re.search(r"^PG_TESTS\s*[:?]?=\s*(.+)$", text, re.MULTILINE)
    assert pg_tests
    assert set(PG_TESTS) <= set(pg_tests.group(1).split())


def test_makefile_recipes_use_tabs() -> None:
    # POSIX make (Git Bash on Windows too) needs tab-indented recipes.
    for n, line in enumerate(MAKEFILE.read_text(encoding="utf-8").splitlines(), 1):
        assert not line.startswith("    "), f"Makefile:{n} indented with spaces"
