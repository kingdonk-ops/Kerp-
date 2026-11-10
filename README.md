# KERP — Field Operations Platform

An operational delivery / field-execution engine: asset & quality backbone
(Site → Area → Asset → RSW → Items), inspections/ITP/NCR, welding quality, logistics,
execution, documents, claims and controls — exporting verified actuals to P6 and
the corporate ERP.

Built by an AI agent pipeline under one coordinator; every change is
independently reviewed and CI-gated.

| | |
|---|---|
| Plan | [docs/DEVELOPMENT-PLAN.md](docs/DEVELOPMENT-PLAN.md) |
| Spec | [docs/spec/](docs/spec/README.md) |
| Task board | [docs/TASKS.md](docs/TASKS.md) |
| TODO / decisions | [docs/TODO.md](docs/TODO.md) |
| How the agents work | [docs/WORKFLOW.md](docs/WORKFLOW.md) · rules in [CLAUDE.md](CLAUDE.md) |

Branches: `task/*` → PR → **`dev`** (CI + independent review) → auto-promoted to **`main`** every 5 merges.

Local stack (PostgreSQL 16 + PostGIS + ltree, Redis 7, MinIO): `make help`, and
`make test-pg` runs the PostgreSQL RLS tests as CI does — see
[backend/README.md](backend/README.md#local-postgresql-stack-t-0003).

```bash
python scripts/tracker.py next        # what's ready
python scripts/tracker.py render      # regenerate boards
# in Claude Code:  /kerp-run   /kerp-plan <phase>
```
