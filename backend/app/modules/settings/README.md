# settings — deployment (tenant) settings (T-0044)

One settings record per deployment (diff §4.4; TD-170, TD-185): edited by System admins, read
by sync, field clients and project creation. A deployment is one hidden tenant (diff §4.1), so
"deployment" and "tenant" settings are the same record. The tenant stays in code and RLS and
is never taken or returned by the API.

| File | What |
|---|---|
| `models.py` | `oe_settings_deployment` (`BaseRecordMixin`, tenant-level, standard RLS; migration `0017`), `SHIPPED_DEFAULTS`, bounds |
| `schemas.py` | `DeploymentSettingsRead`, `DeploymentSettingsUpdate` (partial, strict integers, no nulls, no unknown fields) |
| `service.py` | `get_settings`, `project_defaults`, `update_settings`, `stored_record`, `ensure_record` (first-run, T-0023) |
| `router.py` | `/api/v1/settings/deployment` (below) |

## Settings

| Field | Shipped default | Bounds | Read by |
|---|---|---|---|
| `offline_window_days` | 7 | 1–14 | field clients, sync (diff §4.2) |
| `offline_grace_hours` | 24 | 1 to 24 × `offline_window_days` (planner default, TD-257) | field clients: how long after the server session ended an offline device may keep unlocking with the PIN and queueing changes (diff §4.2) |
| `max_checkout_clock_skew_s` | 5 | 1–300 (planner default) | field clients, sync: largest device clock skew allowed when taking work offline (diff §6.4.1) |
| `default_status` | `ACTIVE` | `ACTIVE`, `ON_HOLD`, `CLOSED` (master §4.1 amendment) | project creation |
| `default_site_timezone` | `Australia/Perth` | IANA zone from `tzdata` | project creation |
| `default_currency_code` | `AUD` | active ISO 4217 | project creation |
| `default_locale` | `en-AU` | BCP 47 `ll[-Ssss][-RR\|-nnn]` | project creation |

Zone, currency and locale use the same validators as a project's own settings
(`app.core.locale`). The grace bound is checked on the result of the update: lowering the
window below `offline_grace_hours / 24` without lowering the grace is refused (422), never
clamped.

Until the record is created there is **no row**: `get_settings` answers `SHIPPED_DEFAULTS` and
reading never writes. First-run provisioning (T-0023, `app/modules/onboarding`) creates it with
the shipped defaults (`ensure_record`, audited `{"action": "provision", "values": …}`; an
existing record is left alone); otherwise a System admin's first change creates it (at most one
live row per tenant, partial unique index; a concurrent first write loses with 409).

**Region is not a setting here.** It is deployment configuration (environment, like the
database URL): AWS `ap-southeast-2` for Australian deployments, `eu-central-1` only if an EU
deployment is ever needed; S3, SES and other regional clients read it from configuration
(diff §4.4).

## New projects

`projects.create_project` calls `project_defaults(session)` and fills `status`,
`site_timezone`, `currency_code` and `locale` that the request left out, so it always passes
all four values explicitly. The column defaults on `oe_projects_project` (and the DB-side
defaults for `site_timezone` and `status` that T-0063 adds) are only fallback literals; they
equal `SHIPPED_DEFAULTS` (tested). Currency and locale get no DB default. Changing a default
never touches existing projects.

The projects module depends on this one (`depends_on=("settings",)`); this module depends on
nothing, which is why it is its own module and not part of `admin` (which depends on
`projects`; TD-295).

## API (`/api/v1/settings`)

| Endpoint | Who | What |
|---|---|---|
| `GET /deployment` | any signed-in user (no membership needed) | the effective settings |
| `PATCH /deployment` | `system_admin` (else 403) | change the given fields; 422 for invalid values, nulls, unknown fields (`tenant_id`, `region`) or an empty body |

The `system_admin` flag is the one the request session was bound with at authentication
(`access.CallerIsSystemAdmin`), never from the request. RLS is the standard policy: every
session of the tenant reads the row and only a tenant-level session writes it; the router
restricts writes to System admins (a System-admin-only write policy in the database is
TD-296).

## Audit

Tenant-level chain `deployment-settings:<tenant>`: `{"action": "update", "changes": {field:
[old, new]}}` with the actor. A change that changes nothing writes nothing.

## Tests

`tests/test_deployment_settings.py` (SQLite and PostgreSQL).
