# lobby — find a project to request access to (T-0040)

The entry point to an access request (diff §4.1, edge spec §6 amendments, TD-202). The request
lifecycle itself is T-0050; invites are T-0052.

| File | What |
|---|---|
| `models.py` | no table (access requests, T-0050, add the first) |
| `schemas.py` | `LobbyProject` (`id`, `code`, `name`, `member`), `LobbyCodeMatch` (+ `lookup_ticket`, `lookup_ticket_expires_at`), `LobbyError` |
| `service.py` | `lobby_projects`, `find_by_code`, `throttle_code_lookup`, `issue_lookup_ticket` / `verify_lookup_ticket` |
| `router.py` | `GET /api/v1/lobby/projects`, `GET /api/v1/lobby/projects/by-code/{code}` |

## API (`/api/v1/lobby`)

| Endpoint | Who | What |
|---|---|---|
| `GET /projects` | internal users (`is_internal`, T-0049) | the live projects with `visible_in_lobby`, by name then code; anyone else 403 `{"detail": ..., "code": "LOBBY_INTERNAL_ONLY"}` and no project data |
| `GET /projects/by-code/{code}` | every signed-in user | the one live project with exactly that code (case-sensitive), lobby-visible or not, plus a lookup ticket; unknown or deleted: 404; throttled (429 with `Retry-After`) |

`member` is true when the caller already has effective access to the project (the grants the
request scope was resolved from, `access.CallerGrants`: direct memberships, and team-derived
grants once T-0073 lands, ADR 0008), so the UI opens the project instead of offering a
request. A lookup reads only: it never creates a membership or any other row (TD-202).

`visible_in_lobby` is set by `PUT /api/v1/projects/{id}/lobby` (projects module): PER-09 of
the project or a System admin.

## Narrow read

The lobby reads projects the caller is not a member of only through
`projects.service.lobby_projects` / `project_by_code`, which return id, code and name and
nothing else. On PostgreSQL these call the `SECURITY DEFINER` functions
`kerp_lobby_projects()` and `kerp_lobby_project_by_code(text)` (migration `0021`); the
membership-based `projects` policy is not widened, and a non-member still reads no project
row. The list function returns rows only when the session user is an active **internal** user
of the bound tenant: checked inside the function as well as by the endpoint (defence in
depth). On SQLite the projects service runs the same rule.

## Lookup ticket

A code lookup returns a signed JWT bound to the caller (`sub`, `tid`) and the project (`pid`),
valid 15 minutes (`LOOKUP_TICKET_TTL`, planner default). T-0050 requires it, through
`verify_lookup_ticket`, on a user's **first** access request for a project when the user is
external or the project is not lobby-visible (re-requests after a rejected or withdrawn request
need none), so a leaked project id alone is not enough. It is signed with the deployment's
token key set but carries `type: lobby_lookup` and `typ: kerp-lookup+jwt`, so it is never
accepted as an access token and an access token is never accepted as a ticket.

## Throttling

Every code lookup counts, hit or miss (`ProgressiveLockout` on the auth store, shared by all
workers): per user `USER_LOOKUP_POLICY` (30 back to back, then 1 min doubling to 15 min, per
hour) and deployment-wide `DEPLOYMENT_LOOKUP_POLICY` (1000 per hour, then the same escalation).
The user limit is checked first, so a locked-out user does not spend the deployment budget.
Per-client-IP limits follow TD-138.

## Tests

`tests/test_lobby.py` (SQLite and PostgreSQL), `tests/test_rls_lobby.py` (PostgreSQL as
`kerp_app`: the narrow read functions, the unchanged project policy, `EXECUTE` privileges).
