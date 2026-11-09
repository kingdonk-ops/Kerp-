# KERP backend

FastAPI modular monolith (see `docs/spec/` and `CLAUDE.md` §6).

## Layout

| Path | What |
|---|---|
| `app/main.py` | `create_app()` application factory |
| `app/core/config.py` | Typed settings (`pydantic-settings`, env prefix `KERP_`) |
| `app/api/health.py` | `GET /health` liveness endpoint |
| `app/modules/<name>/` | Domain modules (`manifest.py`, `models.py`, `router.py`, `service.py`, `schemas.py`, `README.md`) — added by later tasks |
| `tests/` | pytest suite |

The module loader, database layer, auth and RLS are later Phase 0 tasks. Whether
the core is ported from Inspection-ERP or rewritten is open decision **TD-001**.

## Settings

All configuration comes from environment variables prefixed `KERP_` (no
hardcoded config, no `.env` file is read).

| Variable | Default | Meaning |
|---|---|---|
| `KERP_APP_NAME` | `KERP` | Service name (OpenAPI title) |
| `KERP_ENVIRONMENT` | `development` | `development` / `test` / `production` |
| `KERP_DEBUG` | `false` | FastAPI debug mode |
| `KERP_VERSION` | installed package version | Reported by `/health` |
| `KERP_DATABASE_URL` | unset | SQLAlchemy URL (used once the DB layer exists) |
| `KERP_REDIS_URL` | unset | Redis URL (used once the job queue exists) |
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
small and starts fast (`CLAUDE.md` §6). This is enforced by ruff rule **TID253**
(`banned-module-level-imports`, configured under
`[tool.ruff.lint.flake8-tidy-imports]` in `pyproject.toml`); `ruff check .` fails on
a top-level import of any listed module or its submodules.

Banned at module level: `celpy`, `cv2`, `fitz`, `pymupdf`, `pyhanko`, `pytesseract`,
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

`tests/test_lint_heavy_imports.py` proves the rule fires for every listed module.

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
throwaway venv; the `runtime` stage (`python:3.12-slim-bookworm`, pinned by tag) copies only
`/opt/venv` and runs as the non-root user `kerp` (uid/gid `10001`). Every `pip install` uses
`--only-binary=:all:` (no sdists, no compiler) and the lock files are installed with
`--require-hashes`. `.dockerignore` is an allowlist (`app/`, `pyproject.toml`, `README.md`,
lock files), so `.venv`, tests, caches and `.env*` never reach the build.

```bash
docker build -t kerp-backend backend
docker run --rm -p 8000:8000 -e KERP_ENVIRONMENT=development kerp-backend
```

The image defaults to `KERP_ENVIRONMENT=production` (HSTS on); it listens on port 8000
(`uvicorn app.main:create_app --factory`) and has a `HEALTHCHECK` against `/health` using the
standard library only. CI (`backend-image` job) builds and smoke-tests the image on every PR and
pushes `ghcr.io/kingdonk-ops/kerp-backend:<short-sha>` and `:<branch>` on pushes to `dev`/`main`.

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
