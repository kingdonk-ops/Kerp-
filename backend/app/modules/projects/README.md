# projects — the project record and its settings (T-0012)

A project holds the site's IANA timezone (clients show every UTC timestamp in it), the
site's spatial bounds (PostGIS, EPSG:4326) and the currency/locale used to present the
read-only money mirror (master spec §1.2, §4.1). Every project-scoped row of the other
modules carries the project's `id` as its `project_id`.

| File | What |
|---|---|
| `models.py` | `oe_projects_project` (`BaseRecordMixin`, tenant-level row; migration `0008`, membership-based RLS since `0015`, `visible_in_lobby` since `0021`, DB-side `status`/`site_timezone` defaults since `0033`, legal hold since `0037`) |
| `schemas.py` | `ProjectCreate` / `ProjectUpdate` / `ProjectRead`, `LobbyVisibility`, `LegalHoldChange` / `LegalHoldRead`, `PolygonGeoJSON`; timezone, ISO 4217, BCP 47 validation from `app.core.locale` (shared with the settings module) |
| `service.py` | `create_project`, `get_project`, `list_projects`, `live_project_ids` (the jobs scheduler, T-0214), `update_project`, `delete_project`, `site_zone`, `bind_project` (project-scoped routers, T-0217), `tenant_projects` (every live project a `system_admin` session sees, T-0275), `set_lobby_visibility`, `set_legal_hold` / `is_on_legal_hold` (T-0223), the narrow lobby read `lobby_projects` / `project_by_code` (T-0040) and `lobby_projects_by_id` (access requests, T-0050) |
| `router.py` | `GET/POST /api/v1/projects`, `GET/PATCH/DELETE /api/v1/projects/{id}`, `PUT /api/v1/projects/{id}/lobby`, the module toggle `GET /api/v1/projects/{id}/modules`, `PUT /api/v1/projects/{id}/modules/{name}` (T-0084) |

## Record

| Column | Rule |
|---|---|
| `code`, `name` | `code` unique per tenant (409 on a clash; on PostgreSQL recognised by the constraint name `uq_oe_projects_project_tenant_code`, on SQLite by the message), `[A-Za-z0-9][A-Za-z0-9._-]{0,31}` |
| `status` | `ACTIVE`, `ON_HOLD`, `CLOSED` (TD-185); default: the deployment setting |
| `site_timezone` | an IANA zone from the `tzdata` package (not the host's zoneinfo: no `localtime`, `posixrules`, `Factory`); default: the deployment setting |
| `spatial_bounds` | GeoJSON `Polygon`, 2D `[lon, lat]`, closed rings, at most 10,000 positions across all rings, or `null` |
| `currency_code` | active ISO 4217 code; default: the deployment setting |
| `locale` | BCP 47 `ll[-Ssss][-RR|-nnn]`; default: the deployment setting |
| `visible_in_lobby` | listed in the lobby for internal users (T-0040, diff §4.1); default `false`; changed only through `PUT /projects/{id}/lobby`, not `PATCH` (422) |
| `legal_hold`, `legal_hold_reason`, `legal_hold_set_by`, `legal_hold_set_at` | legal hold (T-0223, diff §4.7); default off; changed only by a System admin through `PUT /api/v1/admin/system/projects/{id}/legal-hold`. Reason (≤ 500), setter and time describe the active hold and are cleared with it; CHECK `ck_oe_projects_project_legal_hold_reason`: on needs a reason and a time. `ProjectRead` shows only `legal_hold` |

**Defaults for new projects** (T-0044, diff §4.4). `POST /projects` may omit `status`,
`site_timezone`, `currency_code` and `locale` (never send `null`, 422); `create_project` then
takes them from the deployment settings (`app.modules.settings.service.project_defaults`;
shipped `ACTIVE`, `Australia/Perth`, `AUD`, `en-AU`, changed by System admins through
`PATCH /api/v1/settings/deployment`). The service always passes all four values explicitly;
the column defaults in `models.py` (`DEFAULT_*`) and the DB-side defaults of spec §4.1
(`status DEFAULT 'ACTIVE'`, `site_timezone DEFAULT 'Australia/Perth'`, migration `0033`,
T-0063) are fallback literals equal to the shipped defaults (tested). `currency_code` and
`locale` have no DB-side default (TD-185). Changing a
deployment default never changes existing projects. This module depends on `settings`.

`spatial_bounds` uses `app.core.geo.GeoJSONGeometry`: `geometry(Polygon,4326)` on PostgreSQL
(written with `ST_GeomFromGeoJSON`, read with `ST_AsGeoJSON(…, 15)`, so spatial SQL works on
the column), GeoJSON text on SQLite. The read asks for 15 decimals (the PostGIS maximum,
`GEOJSON_MAX_DECIMALS`) instead of the default 9, so a position reads back as it was written
(T-0063). Python always sees a GeoJSON dict. No GiST index or
`ST_IsValid` check yet (TD-186).

A project is a **tenant-level row** (`project_id IS NULL`, CHECK constraint). It is written
from a session bound to the tenant with no project; a project-bound session cannot create or
change it. It is under the membership-based policy (T-0014, TD-053, migration `0015`): the row
is visible to sessions bound to the project, allowed to read it, holding any membership in it
(`app.member_project_ids`), or with `app.is_system_admin`; a plain tenant-level session sees no
project. Scripts that need every project bind `TenancyContext(tenant_id, is_system_admin=True)`. Changes go to the audit chain `project:<id>` (action and the
changed field names). Delete is a soft delete; the code stays taken (TD-186).

## Access

The request binds no project yet (TD-122), so the router authorises against the project in
the path using the caller's memberships (`app.core.auth.access.CallerGrants`: the grants the
request's scope was resolved from, so they are read once per request, T-0071):

- list and read: projects the caller is a member of; everything else is 404. A `system_admin`
  lists and reads every live project of its tenant (ADR 0016, T-0275); every `ProjectRead`
  carries `access`: `member`, or `system_admin` where the caller holds no membership. Deleted,
  unknown and other tenants' projects stay 404 for everyone;
- edit and delete: the `admin` capability (Project Administrator, PER-09, or a `system_admin`)
  on that project, else 403; a non-member who is not a `system_admin` (also a PER-09 or
  Project Admin of another project) gets 404;
- lobby flag (`PUT /{id}/lobby`): PER-09 of that project or a System admin (diff §4.1); a
  non-member who is not a System admin 404, a member without PER-09 403; audited as an
  `update` with `fields: ["visible_in_lobby"]`;
- create: the tenant-level `create_projects` grant ("Project Admin", appointed by a System
  admin through `/api/v1/admin/grants`; `access.CallerAdminGrants`), else 403 (diff §4.1,
  TD-184, T-0039). The creator is granted PER-09 on the new project in the same transaction
  (`create_project(..., creator_id=)`, through `app.core.auth.memberships`), so they read and
  edit it from the next request on. Provisioning scripts call `service.create_project`
  without a creator.
- delete: also revokes every membership on the project (`memberships.revoke_project`;
  audited as `revoked_memberships`), so memberships of a deleted project confer nothing.
  While team-derived members remain (T-0073, ADR 0008) it answers 409 `team_links_present`:
  a System admin unlinks the project's teams first, so derived grants never outlive their link.
  While the project is on legal hold it answers 409 `{code: LEGAL_HOLD_ACTIVE}` and nothing
  changes.

## Legal hold (T-0223, diff §4.7)

A System admin puts a project on legal hold, or takes it off, through the System console:
`PUT /api/v1/admin/system/projects/{id}/legal-hold` with `{on, reason}` (route in
`app/modules/admin/system_projects_router.py`; everyone else, PER-09 included, 403; a reason of
1–500 characters after trimming is required both ways, 422 otherwise; unknown, deleted or other
tenant's project 404). It answers `LegalHoldRead`: `project_id`, `legal_hold`,
`legal_hold_reason`, `legal_hold_set_by`, `legal_hold_set_at`.

`set_legal_hold(session, project_id, on, reason, actor_id=)` needs a tenant-level session. Each
change appends `{event: legal_hold_set | legal_hold_cleared, project_id, code, reason}` to the
tenant-level System console chain `admin-system:<tenant>` (`LEGAL_HOLD_CHAIN`, equal to
`jobs.service.CONSOLE_CHAIN`) in the same transaction; a System admin need not be a member, so
the project's own chain gets nothing. A call that changes nothing (off to off, on with the same
reason) writes nothing; a new reason while on is a change.

While on, `delete_project` raises `LegalHoldActiveError` (API: 409 `LEGAL_HOLD_ACTIVE`) before
touching the row or its memberships. **Other modules** that delete project data (the project
archive, T-0233) must call `is_on_legal_hold(session, project_id)` first and refuse while it is
`True`; any session that sees the project row may ask (`OutOfScopeError` for an unknown or
deleted project). There is no hard purge (TD-401); retention rules stay TD-175.

## Module toggle (T-0084)

`GET /api/v1/projects/{id}/modules` lists every loaded module in dependency order with
`name`, `version`, `depends_on`, `toggleable` and `enabled` for that project;
`PUT /api/v1/projects/{id}/modules/{name}` with `{"enabled": bool}` switches one on or off and
returns its new state. The router binds the request session to the path's project with
`bind_project` (`V` to read, `admin` to change: PER-09 or a `system_admin`, ADR 0016) and
calls `app.core.module_loader.service` (`project_states`, `set_enabled`) with the app's
registry; the shapes are `app.core.module_loader.schemas`. A non-member and an unknown,
deleted or other tenant's project get 404, a member without `admin` 403. An unknown module is
404; a module that cannot be disabled, or a change that would break a dependency, 409 naming
the module (to change first). Each change goes to the project's `project_modules` audit chain
with the caller's user id, and the enable listeners run (the validation module copies the
module's default rule sets). The semantics are T-0011's: `app/core/module_loader/README.md`.
This module owns the route because core never imports `app.modules`.

## Narrow lobby read (T-0040)

The lobby module finds projects the caller is not a member of through `lobby_projects(session)`
(live lobby-visible projects, only when the session user is an active internal user) and
`project_by_code(session, code)` (the live project with exactly that code, for any active
user). Both return `ProjectSummary(id, code, name)`, never the row, and need a session bound to
the caller (`user_id`). On PostgreSQL they call the `SECURITY DEFINER` functions
`kerp_lobby_projects()` / `kerp_lobby_project_by_code(text)` of migration `0021`, which check
the caller (tenant, user, active, internal) against `auth_user` themselves; the membership
policy is not widened (edge spec §6 amendments). Because the definer is the app role in CI and
dev, which `FORCE` RLS also binds (TD-056), the function body raises `app.is_system_admin`
transaction-locally for its own read and restores the caller's value before returning. On
SQLite the service runs the same rule as a Core query past the ORM scope.

`lobby_projects_by_id(session, ids)` (T-0050) is the access requests' narrow read: id, code,
name and `visible_in_lobby` (`LobbyRef`) of the live projects among `ids`, for any active user
of the tenant (`kerp_lobby_projects_by_id(uuid[])`, migration `0023`, built like the `0021`
functions). Knowing a project id is not enough to file a request (the access-request module
checks the lookup ticket or lobby visibility first), and the API shows its result only for the
caller's own requests or to the project's administrators.

## Setting a new project up

`add_created_listener(listener)` registers `listener(session, project)`; `create_project`
calls it in the creating session and transaction once the row is flushed, so a failure rolls
the project back. The validation module copies the tenant's default rule sets into the new
project this way (ADR 0004 §2.3, T-0034).

## Using it from another module

```python
from app.modules.projects.service import get_project, site_zone

project = get_project(session, project_id)  # OutOfScopeError -> 404
local = some_utc_timestamp.astimezone(site_zone(project))
```

A project-scoped endpoint (`/projects/{project_id}/...`) binds its request session with
`bind_project` (T-0217): non-member 404, member without `require` 403, then the session is
rebound to the project scope and a deleted project is 404. A `system_admin` (the session's
flag, ADR 0016, T-0275) is answered as a PER-09 member without a membership: `V`/`C`/`E`/
`admin`, never `A`/`S` unless its own personas give them; for an unknown, deleted or other
tenant's project it gets 404 before any 403. It returns the caller's access:

```python
from app.core.auth.access import Capability
from app.modules.projects import service as projects

access = projects.bind_project(session, principal, grants, project_id, require=Capability.CREATE)
```

## Tests

`tests/test_projects.py` (SQLite and PostgreSQL; the PostGIS geometry check runs only on
PostgreSQL, TD-052). The module toggle: `tests/test_module_toggle.py`. The lobby flag and narrow read: `tests/test_lobby.py`,
`tests/test_rls_lobby.py`. The legal hold: `tests/test_project_legal_hold.py`.
