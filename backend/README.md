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
