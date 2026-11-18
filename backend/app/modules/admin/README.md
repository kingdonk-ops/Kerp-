# admin — users, project memberships, admin grants (T-0014, T-0039)

The administration surface of the access model (diff §4.1; TD-120, TD-184, TD-151). The tenant
stays in code and RLS but is never taken or returned by this API.

| File | What |
|---|---|
| `models.py` | `oe_admin_grant` (`BaseRecordMixin`, tenant-level, own `admin_grant` RLS policy; migration `0015`) |
| `schemas.py` | `UserCreate` / `UserUpdate` / `UserRead`, `UserLookupRequest` / `UserSummary`, `GrantIn` / `MemberAssign` / `MemberRead` / `MemberAssigned`, `AdminGrantKind` / `AdminGrantIn` / `AdminGrantRead` |
| `service.py` | `own_admin_grants` (registered as core's admin-grant lookup), `list_grants` / `grant` / `revoke`, `create_user` / `update_user` / `lookup_user`, `project_members` / `assign_member` / `remove_member` |
| `router.py` | `/api/v1/admin/...` (below) |

## Admin grants

`oe_admin_grant` holds one live row per user and grant: `system_admin` or `create_projects`.
Neither is a persona or a membership, and neither confers any capability (no `A`/`S`, no hold
release; TD-151). Revoking soft-deletes (partial unique index on live rows). Policy
(edge spec §6 amendments): a user reads their own rows (`app.current_user_id`); an
`app.is_system_admin` session reads and writes all of them; nobody else writes. The ORM
guard refuses the same writes on SQLite. `service.grant` / `service.revoke` (audited) are
what the appointment API (`/grants`, below) and provisioning scripts call, from a session
bound with `is_system_admin=True`. `revoke` refuses the last `system_admin` held by an
*active* user, and `update_user` refuses to deactivate that user (`LastSystemAdminError`, 409;
T-0211, TD-274): a deactivated user's grant runs nothing. Both lock the live `system_admin`
rows first (same query, same order), so two System admins removing or deactivating each other
at once cannot both succeed; the active flag is read fresh (`auth.service.active_user_ids`),
never from a `User` already loaded in the session. A user create racing a duplicate hits the
unique constraint: 409, and the unit of work rolls back. A grant racing a duplicate raises
`GrantRaceError`; the router rolls back its write-free unit of work and answers 200 already
held. No SAVEPOINT is used (on SQLite one opened before any write commits early).

**What the grants allow** (diff §4.1, T-0039). `system_admin`: this module's System-admin
endpoints. `create_projects` ("Project Admin"): `POST /api/v1/projects`, nothing else; the
projects module reads it through `access.CallerAdminGrants` (loaded with the scope at
authentication) and grants the creator PER-09 on the new project in the creating
transaction. Revoking `create_projects` keeps the user's PER-09 memberships. Deleting a project
revokes every membership on it (`memberships.revoke_project`), so memberships of a deleted
project confer nothing (`load_grants`, allowed project list, admin checks; TD-184).

**At authentication** `app.core.auth.access.resolve_request_scope` loads the caller's own
grants in the same unit of work that reads their memberships and binds `system_admin` as
`app.is_system_admin` (and the token's `sub` as `app.current_user_id`). Core never imports a
module: this module registers `own_admin_grants` with `access.register_admin_grant_lookup`
at import (the module loader imports the router at start-up; without it nobody is a System
admin). On PostgreSQL the lookup is the `SECURITY DEFINER` function
`kerp_caller_admin_grants()`, which takes no argument and returns only the session user's
rows (its `WHERE` clause, not RLS, guarantees that). `EXECUTE` is revoked from `PUBLIC` and
granted only to the app role running the migrations (`CURRENT_USER`, TD-056; split-role
deploys: TD-275). Without a registered lookup nobody is a System admin (fail closed, tested).
Routers read the flag with `access.CallerIsSystemAdmin` (the bound session's flag),
never from a claim, parameter or header.

## API (`/api/v1/admin`)

| Endpoint | Who | What |
|---|---|---|
| `GET /users` | `system_admin` | every account, with the read-only `is_internal` flag |
| `POST /users` | `system_admin` | create an account without credentials (`email`, `display_name`); 409 if the e-mail exists |
| `PATCH /users/{id}` | `system_admin` | `display_name`, `is_active` (deactivate / reactivate; a deactivated user's tokens stop at once); 409 for the last active `system_admin` |
| `POST /users/lookup` | PER-09 of any project, `system_admin` | exact e-mail → `{id, email, display_name}` of an active account, else 404 |
| `GET /projects/{p}/members` | PER-09 of `p`, `system_admin` | members of `p` with their personas and sites, each grant with `source` (`direct` \| `team`), `source_id` (the team link) and `team_name` (the team of a team-derived grant, from the teams service, T-0013) |
| `PUT /projects/{p}/members/{user}` | PER-09 of `p`, `system_admin` | set the user's complete **individual** persona set on `p`; team-derived grants are untouched, and a persona also held through a team gets its own individual row (it outlives the team) |
| `DELETE /projects/{p}/members/{user}` | PER-09 of `p`, `system_admin` | revoke all of the user's individual personas on `p`; 409 `managed_through_team` (with the link ids) when the user's access is only team-derived |
| `GET /grants[?user_id=]` | `system_admin` | live tenant-level grants `{user_id, email, display_name, grant}`, by e-mail |
| `POST /grants` | `system_admin` | `{user_id, grant}` with `grant` `system_admin` or `create_projects`: 201, 200 if already held, 404 unknown user |
| `DELETE /grants/{user}/{grant}` | `system_admin` | revoke: 204, 404 if not held, 409 for the last active `system_admin` |

Everyone else gets 403; a project the caller is not a member of (or a deleted one) is 404 for
everyone but a `system_admin` (deleted: 404 for all). Request bodies forbid unknown fields
(422): no `tenant_id`, and `is_internal`, `entra_oid`, `entra_tid` are derived, never input
(ADR 0009, TD-265). New accounts go through `app.core.auth.service.create_user`, which derives
`is_internal`; users are read only through `app.core.auth.service`.

Deployment settings (offline window, offline grace, clock-skew limit, new-project defaults) are
edited by System admins too, but live in the `settings` module (`/api/v1/settings/deployment`,
T-0044), which the projects module depends on.

**E-mail lookup** (so a PER-09 can assign an existing account without enumerating the
tenant): exact, case-insensitive match only; inactive accounts are not found; per-caller
rate limit (`LOOKUP_POLICY`: 20 back to back, then 1 min doubling to 15 min, per hour; 429
with `Retry-After`); every lookup, hit or miss, is audited with the e-mail as a SHA-256
digest. Invites by e-mail are T-0052. Access requests (a user asks, a PER-09 approves or rejects) are the
`access_requests` module (T-0050); approving one calls `assign_member` with the request's
personas added to the requester's direct grants, so the SoD rule below applies there too.

**Memberships.** Personas are `PER-01`…`PER-16` (`access.Persona`, TD-256); PER-07 needs a
`site_node_id`, other personas must not have one (422). PER-09 may be assigned by a PER-09
(co-admins). Tenant-level grants are not personas and cannot be assigned here. Writes go
through `app.core.auth.memberships` (the membership store's write side); revoking
soft-deletes, re-granting revives the row.

**Separation of duties** (TD-151, edge spec §5 Admins). `access.sod_warning(personas)` takes the
user's *full* persona set on the project and is true for PER-09 together with a persona that
has `A` (PER-02, PER-06, PER-07, PER-11; derived from `PERSONAS`, so PER-16 never warns).
Without `confirm_sod: true` such a PUT answers 409
`{"code": "sod_confirmation_required", "sod_warning": true}`; with it the assignment is saved,
the response carries `sod_warning: true` and the audit entry `sod_warning: true`. The set
counts the user's team-derived grants on the project too (T-0073, ADR 0008): individual PER-09
for a user with a team-derived PER-02/06/07/11 needs the same confirmation. Assigning PER-09 never adds hold or signing
capability: capabilities stay those of each persona (`access.PERSONAS`).

## Audit

Tenant-level chains: `admin-user:<user>` (create, update), `admin-user-lookup:<caller>` (every
lookup), `project-members:<project>` (`assign` with added / removed / grants / `sod_warning`,
`remove`), `admin-grant:<user>` (grant, revoke).

## Tests

`tests/test_admin.py` (SQLite and PostgreSQL), `tests/test_rls_admin.py` (PostgreSQL as
`kerp_app`: grant visibility and writes, client-portal user vs. System admin on projects).
