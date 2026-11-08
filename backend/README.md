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

## Develop

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows; `source .venv/bin/activate` elsewhere
pip install -e ".[dev]"

ruff check . && mypy app && pytest
uvicorn app.main:create_app --factory --reload
```

## Endpoints

- `GET /health` → `200 {"status": "ok", "version": "<version>"}`
