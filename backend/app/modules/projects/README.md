# projects — the project record and its settings (T-0012)

A project holds the site's IANA timezone (clients show every UTC timestamp in it), the
site's spatial bounds (PostGIS, EPSG:4326) and the currency/locale used to present the
read-only money mirror (master spec §1.2, §4.1). Every project-scoped row of the other
modules carries the project's `id` as its `project_id`.

| File | What |
|---|---|
| `models.py` | `oe_projects_project` (`BaseRecordMixin`, tenant-level row; migration `0008`, membership-based RLS since `0015`, `visible_in_lobby` since `0021`) |
| `schemas.py` | `ProjectCreate` / `ProjectUpdate` / `ProjectRead`, `LobbyVisibility`, `PolygonGeoJSON`; timezone, ISO 4217, BCP 47 validation from `app.core.locale` (shared with the settings module) |
| `service.py` | `create_project`, `get_project`, `list_projects`, `live_project_ids` (the jobs scheduler, T-0214), `update_project`, `delete_project`, `site_zone`, `bind_project` (project-scoped routers, T-0217), `set_lobby_visibility`, the narrow lobby read `lobby_projects` / `project_by_code` (T-0040) |
| `router.py` | `GET/POST /api/v1/projects`, `GET/PATCH/DELETE /api/v1/projects/{id}`, `PUT /api/v1/projects/{id}/lobby` |

## Record

| Column | Rule |
|---|---|
| `code`, `name` | `code` unique per tenant (409 on a clash), `[A-Za-z0-9][A-Za-z0-9._-]{0,31}` |
| `status` | `ACTIVE`, `ON_HOLD`, `CLOSED` (TD-185); default: the deployment setting |
| `site_timezone` | an IANA zone from the `tzdata` package (not the host's zoneinfo: no `localtime`, `posixrules`, `Factory`); default: the deployment setting |
| `spatial_bounds` | GeoJSON `Polygon`, 2D `[lon, lat]`, closed rings, at most 10,000 positions across all rings, or `null` |
| `currency_code` | active ISO 4217 code; default: the deployment setting |
| `locale` | BCP 47 `ll[-Ssss][-RR|-nnn]`; default: the deployment setting |
| `visible_in_lobby` | listed in the lobby for internal users (T-0040, diff §4.1); default `false`; changed only through `PUT /projects/{id}/lobby`, not `PATCH` (422) |

**Defaults for new projects** (T-0044, diff §4.4). `POST /projects` may omit `status`,
`site_timezone`, `currency_code` and `locale` (never send `null`, 422); `create_project` then
takes them from the deployment settings (`app.modules.settings.service.project_defaults`;
shipped `ACTIVE`, `Australia/Perth`, `AUD`, `en-AU`, changed by System admins through
`PATCH /api/v1/settings/deployment`). The service always passes all four values explicitly;
the column defaults in `models.py` (`DEFAULT_*`, and the DB-side `site_timezone`/`status`
defaults of T-0063) are fallback literals equal to the shipped defaults (tested). Changing a
deployment default never changes existing projects. This module depends on `settings`.

`spatial_bounds` uses `app.core.geo.GeoJSONGeometry`: `geometry(Polygon,4326)` on PostgreSQL
(written with `ST_GeomFromGeoJSON`, read with `ST_AsGeoJSON`, so spatial SQL works on the
column), GeoJSON text on SQLite. Python always sees a GeoJSON dict. No GiST index or
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

- list and read: projects the caller is a member of; everything else is 404;
- edit and delete: the `admin` capability (Project Administrator, PER-09) on that project, else 403;
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
rebound to the project scope and a deleted project is 404. It returns the caller's access:

```python
from app.core.auth.access import Capability
from app.modules.projects import service as projects

access = projects.bind_project(session, principal, grants, project_id, require=Capability.CREATE)
```

## Tests

`tests/test_projects.py` (SQLite and PostgreSQL; the PostGIS geometry check runs only on
PostgreSQL, TD-052). The lobby flag and narrow read: `tests/test_lobby.py`,
`tests/test_rls_lobby.py`.
