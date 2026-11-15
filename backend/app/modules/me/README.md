# me — the caller's own access (T-0014)

| File | What |
|---|---|
| `models.py` | no table |
| `schemas.py` | `MyProject` (`id`, `code`, `name`, `personas`) |
| `service.py` | `my_projects(session, grants)` |
| `router.py` | `GET /api/v1/me/projects` |

`GET /api/v1/me/projects` lists the caller's live (non-deleted) projects with their personas
on each, by name, for the project picker (T-0042, diff §6.1). It is built from the caller's
memberships (`access.CallerGrants`, the grants the request scope was resolved from) and reads
the projects through `projects.service.list_projects`; under the membership-based policy the
session sees exactly those project rows (`app.member_project_ids`). A `system_admin` grant
adds no project here. Tests: `tests/test_admin.py`.
