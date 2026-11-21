# me — the caller's own access (T-0014)

| File | What |
|---|---|
| `models.py` | no table |
| `schemas.py` | `MyProject` (`id`, `code`, `name`, `personas`, `access`) |
| `service.py` | `my_projects(session, grants, *, system_admin)` |
| `router.py` | `GET /api/v1/me/projects` |

`GET /api/v1/me/projects` lists the caller's live (non-deleted) projects with their personas
on each, by name, for the project picker (T-0042, diff §6.1). It is built from the caller's
memberships (`access.CallerGrants`, the grants the request scope was resolved from) and reads
the projects through `projects.service.list_projects`; under the membership-based policy the
session sees exactly those project rows (`app.member_project_ids`); each has `access: member`.
For a `system_admin` (the flag the session was bound with, ADR 0016, T-0275) it lists every
live project of the tenant (`projects.service.tenant_projects`); those without a membership
have `access: system_admin` and no personas. Tests: `tests/test_admin.py`,
`tests/test_system_admin_capabilities.py`.
