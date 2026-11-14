# projects — the project record and its settings (T-0012)

A project holds the site's IANA timezone (clients show every UTC timestamp in it), the
site's spatial bounds (PostGIS, EPSG:4326) and the currency/locale used to present the
read-only money mirror (master spec §1.2, §4.1). Every project-scoped row of the other
modules carries the project's `id` as its `project_id`.

| File | What |
|---|---|
| `models.py` | `oe_projects_project` (`BaseRecordMixin`, tenant-level row; migration `0008`, membership-based RLS since `0014`) |
| `schemas.py` | `ProjectCreate` / `ProjectUpdate` / `ProjectRead`, `PolygonGeoJSON`; timezone, ISO 4217, BCP 47 validation |
| `service.py` | `create_project`, `get_project`, `list_projects`, `update_project`, `delete_project`, `site_zone` |
| `router.py` | `GET/POST /api/v1/projects`, `GET/PATCH/DELETE /api/v1/projects/{id}` |

## Record

| Column | Rule |
|---|---|
| `code`, `name` | `code` unique per tenant (409 on a clash), `[A-Za-z0-9][A-Za-z0-9._-]{0,31}` |
| `status` | `ACTIVE` (default), `ON_HOLD`, `CLOSED` (TD-185) |
| `site_timezone` | an IANA zone from the `tzdata` package (not the host's zoneinfo: no `localtime`, `posixrules`, `Factory`), default `Australia/Perth` |
| `spatial_bounds` | GeoJSON `Polygon`, 2D `[lon, lat]`, closed rings, at most 10,000 positions across all rings, or `null` |
| `currency_code` | active ISO 4217 code, default `AUD` |
| `locale` | BCP 47 `ll[-Ssss][-RR|-nnn]`, default `en-AU` |

`spatial_bounds` uses `app.core.geo.GeoJSONGeometry`: `geometry(Polygon,4326)` on PostgreSQL
(written with `ST_GeomFromGeoJSON`, read with `ST_AsGeoJSON`, so spatial SQL works on the
column), GeoJSON text on SQLite. Python always sees a GeoJSON dict. No GiST index or
`ST_IsValid` check yet (TD-186).

A project is a **tenant-level row** (`project_id IS NULL`, CHECK constraint). It is written
from a session bound to the tenant with no project; a project-bound session cannot create or
change it. It is under the membership-based policy (T-0014, TD-053, migration `0014`): the row
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
- create: the `admin` capability on at least one project of the tenant (interim, TD-184). The
  first project of a tenant is created through `service.create_project`. Creating grants no
  membership.

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

## Tests

`tests/test_projects.py` (SQLite and PostgreSQL; the PostGIS geometry check runs only on
PostgreSQL, TD-052).
