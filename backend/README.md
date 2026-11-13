# KERP backend

FastAPI modular monolith (see `docs/spec/` and `CLAUDE.md` §6).

## Layout

| Path | What |
|---|---|
| `app/main.py` | `create_app()` application factory |
| `app/core/config.py` | Typed settings (`pydantic-settings`, env prefix `KERP_`) |
| `app/core/db.py` | `Base`, `BaseRecordMixin`, portable `GUID`/`JSONType`, `Database` + `get_db`, metadata contract check |
| `migrations/`, `alembic.ini` | Alembic (URL from settings, metadata from every module's `models.py`) |
| `app/api/health.py` | `GET /health` liveness endpoint |
| `app/core/tenancy/` | Project-scoped RLS, tenancy context, ORM scope + soft-delete filter — see its `README.md` |
| `app/core/cel/` | CEL rule evaluation (celpy, lazy) with the KERP error taxonomy — see its `README.md` |
| `app/core/audit/` | Hash-chained, append-only audit ledger (`append`, `verify_chain`; REVOKE + triggers) — see its `README.md` |
| `app/core/auth/` | Users, scrypt passwords, EdDSA/RS256 JWTs + JWKS, rotating refresh, Redis `jti` revocation — see its `README.md` |
| `app/core/module_loader/` | Module loader + manifest contract, per-project enable/disable, toggle API `/api/v1/modules` — see its `README.md` |
| `app/modules/<name>/` | Domain modules (`manifest.py`, `models.py`, `router.py`, `service.py`, `schemas.py`, `README.md`), mounted at `/api/v1/<name>` |
| `app/modules/validation/` | Verification engine: `run_checks(context, rule_set)` and the single rule store (kinds, engines, compile-on-save, findings log) — see its `README.md` |
| `app/openapi.py` | `python -m app.openapi <file>`: writes the OpenAPI schema (used by the frontend's `npm run gen:api`) |
| `tests/` | pytest suite |

Endpoints take their session through `DbSession` only;
`tests/test_lint_get_db.py` fails on any other use of `get_db` (TD-036). Parts of the core are ported
from Inspection-ERP. ADR 0001 (`docs/decisions/0001-port-inspection-erp-core.md`) lists
which parts.

## Settings

All configuration comes from environment variables prefixed `KERP_` (no
hardcoded config, no `.env` file is read).

| Variable | Default | Meaning |
|---|---|---|
| `KERP_APP_NAME` | `KERP` | Service name (OpenAPI title) |
| `KERP_ENVIRONMENT` | `development` | `development` / `test` / `production` |
| `KERP_DEBUG` | `false` | FastAPI debug mode |
| `KERP_VERSION` | installed package version | Reported by `/health` |
| `KERP_DATABASE_URL` | unset | SQLAlchemy URL of the runtime role (e.g. `postgresql+psycopg://…`, `sqlite:///…`) |
| `KERP_MIGRATION_DATABASE_URL` | unset (= `KERP_DATABASE_URL`) | Schema-owner URL Alembic runs as |
| `KERP_REDIS_URL` | unset | Redis URL (auth token revocation; job queue later). Required in production |
| `KERP_JWT_*`, `KERP_ACCESS_TOKEN_MINUTES`, `KERP_REFRESH_TOKEN_DAYS`, `KERP_DEFAULT_TENANT_ID` | see `app/core/auth/README.md` | Token signing keys and lifetimes |
| `KERP_STORAGE_BUCKET`, `KERP_S3_ENDPOINT_URL`, `KERP_S3_PUBLIC_ENDPOINT_URL`, `KERP_S3_ACCESS_KEY_ID`, `KERP_S3_SECRET_ACCESS_KEY`, `KERP_UPLOAD_*`, `KERP_DOWNLOAD_URL_SECONDS` | see `app/modules/uploads/README.md` | Object storage (S3 / MinIO) and upload limits |
| `KERP_S3_REGION` | `ap-southeast-2` | S3 region, set per deployment (TD-171 decision 13a, diff §4.4); MinIO ignores it |
| `KERP_LOG_LEVEL` | `INFO` | Root log level (`DEBUG`…`CRITICAL`, case-insensitive) |
| `KERP_CONTENT_SECURITY_POLICY` | `default-src 'none'; frame-ancestors 'none'` | CSP for API responses |
| `KERP_HSTS_ENABLED` | unset (= on only in `production`) | Force `Strict-Transport-Security` on/off |

## Observability and security headers

Wired in `create_app()`; values are provisional pending sign-off (see the T-0021 spec-gap TODO TD-022).

- **Request id** (`app/core/request_context.py`, pure ASGI): an incoming `X-Request-ID`
  is honoured if it is 1–128 chars of `[A-Za-z0-9._-]`, otherwise a uuid4 is generated.
  It is echoed on every response and available via `get_request_id()` / in every log line.
- **Logs** (`app/core/logging.py`): stdlib `logging`, one JSON object per line on stdout with
  `timestamp` (UTC ISO 8601), `level`, `logger`, `message`, `request_id`, plus any `extra=` fields.
  Logger `kerp.access` writes one line per request: `method`, `path` (never the query string),
  `status`, `duration_ms`. Request headers (Authorization, cookies) are never logged.
  uvicorn's own access log is disabled (it prints query strings); its other logs go through the JSON handler.
- **Unhandled errors** before the response starts are logged (`kerp.error`, with traceback) and
  answered with a generic `500 {"detail": "Internal Server Error"}` carrying the request id and
  security headers. With `KERP_DEBUG=true` they fall through to Starlette's traceback page instead.
- **Security headers** (`app/core/security_headers.py`) on every response, unless the route set
  them itself: `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, and `Strict-Transport-Security: max-age=31536000; includeSubDomains`
  when HSTS is active. `/docs`, `/redoc` and the OAuth2 redirect get a docs CSP that allows the
  Swagger UI / ReDoc CDN assets.

## Heavy libraries are imported lazily

Heavy work (PDF, OCR, CAD, P6 parse, rollups) runs in the background worker, and
heavy libraries must never be imported at module level, so the API process stays
small and starts fast (`CLAUDE.md` §6). A banned module (or any of its submodules)
may only be imported inside a function / worker task or an `if TYPE_CHECKING:` body.
Two checks share the job, both reading the list under
`[tool.ruff.lint.flake8-tidy-imports]` in `pyproject.toml`:

- ruff rule **TID253** (`banned-module-level-imports`): `ruff check .` fails on a plain
  top-level import (fast editor and CI feedback). It does **not** see imports nested
  at module scope.
- the AST contract test in `tests/test_lint_heavy_imports.py` (part of `pytest`) walks
  every file under `app/` and fails on banned imports nested at module scope too:
  inside `try`/`except`, a plain `if` (or the `else` of `if TYPE_CHECKING:`), `with`,
  or a class body.

Neither check detects dynamic imports such as module-scope
`importlib.import_module("fitz")` or `__import__("fitz")`, so don't use them for heavy libs.

Banned at module level: `celpy`, `cv2`, `fitz`, `lark`, `pymupdf`, `pyhanko`, `pytesseract`,
`rectpack`. Add a new heavy dependency to that list in the same PR that introduces it.

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:          # fine: type-only, never imported at runtime
    import celpy


def evaluate(expr: str, activation: dict[str, object]) -> celpy.Value:
    import celpy           # fine: imported when the function / worker task runs

    ...
```

`tests/test_lint_heavy_imports.py` also proves TID253 fires for every listed module.

## Develop

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows; `source .venv/bin/activate` elsewhere
pip install -e ".[dev]"

ruff check . && mypy app && pytest
uvicorn app.main:create_app --factory --reload
```

## Container image

`Dockerfile` (build context `backend/`) is multi-stage: the `build` stage installs the
runtime dependencies into `/opt/venv` and builds the `kerp-backend` wheel with hatchling in a
throwaway venv; the `runtime` stage (`python:3.12-slim-bookworm`, pinned by tag + index digest;
Dependabot proposes digest refreshes, `.github/dependabot.yml`) copies only
`/opt/venv` and runs as the non-root user `kerp` (uid/gid `10001`). Every `pip install` uses
`--only-binary=:all:` (no sdists, no compiler) and the lock files are installed with
`--require-hashes`. `.dockerignore` is an allowlist (`app/`, `pyproject.toml`, `README.md`,
lock files), so `.venv`, tests, caches and `.env*` never reach the build.

```bash
docker build -t kerp-backend backend
docker run --rm -p 8000:8000 -e KERP_ENVIRONMENT=development kerp-backend
```

Behind a reverse proxy, also set `FORWARDED_ALLOW_IPS=<proxy address>` (read by uvicorn) so the
login lockout sees the real client address (`app/core/auth/README.md`, T-0068).

The image defaults to `KERP_ENVIRONMENT=production` (HSTS on); it listens on port 8000
(`uvicorn app.main:create_app --factory`) and has a `HEALTHCHECK` against `/health` using the
standard library only. CI's `backend-image` job builds and smoke-tests the image on every run
with a read-only token. On pushes to `dev`/`main`, the separate `backend-publish` job is the only
job with `packages: write`. It runs after the tests and the smoke test pass, and pushes
`ghcr.io/kingdonk-ops/kerp-backend:<short-sha>` and `:<branch>`.

### Lock files

| File | Contents | Installed into |
|---|---|---|
| `requirements.lock` | `[project.dependencies]` + transitive deps, exact pins + sha256 hashes | `/opt/venv` (runtime) |
| `requirements-build.lock` | `requirements-build.in` (= `[build-system].requires`) | build stage only |

Both target the image platform (CPython 3.12, `x86_64-manylinux_2_28`). Regenerate after
changing dependencies in `pyproject.toml` (or `requirements-build.in`) with
[uv](https://docs.astral.sh/uv/); add `--upgrade` to move to newer releases:

```bash
cd backend
uv pip compile pyproject.toml --python-version 3.12 --python-platform x86_64-manylinux_2_28 \
  --generate-hashes --only-binary :all: -o requirements.lock
uv pip compile requirements-build.in --python-version 3.12 --python-platform x86_64-manylinux_2_28 \
  --generate-hashes --only-binary :all: -o requirements-build.lock
```

`tests/test_container.py` fails if a `pyproject.toml` dependency is missing from the lock or
pinned to a version outside its specifier, if a pin has no hash, or if the Dockerfile loses
`--only-binary=:all:`, `--require-hashes` or its non-root `USER`. The local dev install
(`pip install -e ".[dev]"`) is not locked.

## Endpoints

- `GET /health` → `200 {"status": "ok", "version": "<version>"}`
- `GET /.well-known/jwks.json`, `POST /api/v1/auth/{login,refresh,logout}`, `GET /api/v1/auth/me` — see `app/core/auth/README.md`

## Database (T-0005)

Every model derives from `Base` and `BaseRecordMixin`. The mixin gives each table `id`
(UUID PK), `tenant_id` (NOT NULL), `project_id` (`NULL` means a tenant-level row),
`created_at`, `updated_at`, `deleted_at` (soft delete), `custom_fields` (JSONB),
`created_by`, `updated_by`, `source` (`ui|api|import|sync|ai|system`) and `revision`
(bumped on every update). `tests/test_db_contract.py` fails CI if any table is missing
these columns.

```bash
pip install -e ".[dev]"              # psycopg (PostgreSQL driver) is a core dependency
alembic upgrade head                 # uses KERP_MIGRATION_DATABASE_URL / KERP_DATABASE_URL
alembic revision --autogenerate -m "..."   # after adding models; commit the revision
alembic check                        # also enforced by tests/test_migrations.py
```

Endpoints get a session through `DbSession` (`Annotated[Session, Depends(get_db,
scope="function")]`), never a bare `Depends(get_db)`. That way the unit of work commits
before the response is sent, and a write that fails at commit returns a 500, never a 200.

Reads hide soft-deleted rows unless `execution_options(include_deleted=True)` is set, and every
table is under project-scoped RLS (`app/core/tenancy/README.md`).

Tests run on a temporary SQLite file. When `KERP_DATABASE_URL` points at PostgreSQL, as
in CI's second backend step, the same DB tests run against PostgreSQL 16.

## Local PostgreSQL stack (T-0003)

RLS, roles and `REVOKE` do not exist on SQLite, so `tests/test_rls_bypass.py`,
`tests/test_audit_ledger_pg.py` and `tests/test_pg_extensions.py` skip without PostgreSQL
(skip reason points at TD-052). To run them locally the way CI does, use the
docker-compose stack at the repository root (needs Docker with Compose v2 and GNU make; on
Windows run it from Git Bash, `make` is not bundled with Git for Windows, e.g.
`choco install make`):

```bash
# from the repository root, with the backend venv active (or PYTHON=<absolute path to python>)
make test-pg        # docker compose up -d --wait, then the PG tests; any skip fails the run
make test-pg-all    # the whole backend suite on PostgreSQL, like CI's "Tests (PostgreSQL + RLS)"
make down           # stop (keeps data); `make reset` also drops the volumes
```

| Piece | What |
|---|---|
| `docker-compose.yml` | `postgres` (`postgis/postgis:16-3.4`, same image, user, password and database as CI), `redis` (`redis:7-alpine`, as CI), `minio` (S3-compatible storage; console on `:9001`). Ports bind to `127.0.0.1` only |
| `infra/postgres/initdb/20-kerp-app.sql` | Creates `ltree` + `postgis` and the login role `kerp_app` (`NOSUPERUSER NOBYPASSRLS`), gives it the database and owns `public` for it. CI's backend job runs **the same file** (`psql -f`); compose mounts it into `/docker-entrypoint-initdb.d/`, so it runs once on an empty volume (`make reset` to re-run) |
| `Makefile` | Exports dev-only defaults matching CI: `KERP_DATABASE_URL` (as `kerp_app`), `KERP_TEST_OWNER_URL` (superuser `kerp`), `KERP_REDIS_URL`. Override in an optional, git-ignored `.env` (plain `KEY=VALUE`) or on the command line. No JWT key is set: outside production an ephemeral one is generated |
| `KERP_FAIL_ON_SKIP=1` | Set by `make test-pg` (hook in `tests/skip_guard.py`): a run that skips anything exits non-zero, so a missing stack cannot look green |

`tests/test_dev_infra.py` (part of the normal suite, no Docker needed) fails if the compose
images, superuser settings or Makefile URLs drift from `.github/workflows/ci.yml`, if the
init script stops creating `kerp_app` as `NOSUPERUSER NOBYPASSRLS`, or if CI stops running
the shared script.
