# admin — users, project memberships, admin grants (T-0014)

The administration surface of the access model (diff §4.1; TD-120, TD-184, TD-151). The tenant
stays in code and RLS but is never taken or returned by this API.

| File | What |
|---|---|
| `models.py` | `oe_admin_grant` (`BaseRecordMixin`, tenant-level, own `admin_grant` RLS policy; migration `0014`) |
| `schemas.py` | `UserCreate` / `UserUpdate` / `UserRead`, `UserLookupRequest` / `UserSummary`, `GrantIn` / `MemberAssign` / `MemberRead` / `MemberAssigned` |
| `service.py` | `own_admin_grants` (registered as core's admin-grant lookup), `grant` / `revoke`, `create_user` / `update_user` / `lookup_user`, `project_members` / `assign_member` / `remove_member` |
| `router.py` | `/api/v1/admin/...` (below) |

## Admin grants

`oe_admin_grant` holds one live row per user and grant: `system_admin` or `create_projects`.
Neither is a persona or a membership, and neither confers any capability (no `A`/`S`, no hold
release; TD-151). Revoking soft-deletes (partial unique index on live rows). Policy
(edge spec §6 amendments): a user reads their own rows (`app.current_user_id`); an
`app.is_system_admin` session reads and writes all of them; nobody else writes. The ORM
guard refuses the same writes on SQLite. The appointment API is T-0039; `service.grant` /
`service.revoke` (audited) are what it and provisioning scripts call, from a session bound
with `is_system_admin=True`.

**At authentication** `app.core.auth.access.resolve_request_scope` loads the caller's own
grants in the same unit of work that reads their memberships and binds `system_admin` as
`app.is_system_admin` (and the token's `sub` as `app.current_user_id`). Core never imports a
module: this module registers `own_admin_grants` with `access.register_admin_grant_lookup`
at import (the module loader imports the router at start-up; without it nobody is a System
admin). On PostgreSQL the lookup is the `SECURITY DEFINER` function
`kerp_caller_admin_grants()`, which takes no argument and returns only the session user's
rows. Routers read the flag with `access.CallerIsSystemAdmin` (the bound session's flag),
never from a claim, parameter or header.

## API (`/api/v1/admin`)

| Endpoint | Who | What |
|---|---|---|
| `GET /users` | `system_admin` | every account, with the read-only `is_internal` flag |
| `POST /users` | `system_admin` | create an account without credentials (`email`, `display_name`); 409 if the e-mail exists |
| `PATCH /users/{id}` | `system_admin` | `display_name`, `is_active` (deactivate / reactivate; a deactivated user's tokens stop at once) |
| `POST /users/lookup` | PER-09 of any project, `system_admin` | exact e-mail → `{id, email, display_name}` of an active account, else 404 |
| `GET /projects/{p}/members` | PER-09 of `p`, `system_admin` | members of `p` with their personas and sites |
| `PUT /projects/{p}/members/{user}` | PER-09 of `p`, `system_admin` | set the user's complete persona set on `p` |
| `DELETE /projects/{p}/members/{user}` | PER-09 of `p`, `system_admin` | revoke all of the user's personas on `p` |

Everyone else gets 403; a project the caller is not a member of (or a deleted one) is 404 for
everyone but a `system_admin` (deleted: 404 for all). Request bodies forbid unknown fields
(422): no `tenant_id`, and `is_internal`, `entra_oid`, `entra_tid` are derived, never input
(ADR 0009, TD-265). New accounts go through `app.core.auth.service.create_user`, which derives
`is_internal`; users are read only through `app.core.auth.service`.

**E-mail lookup** (so a PER-09 can assign an existing account without enumerating the
tenant): exact, case-insensitive match only; inactive accounts are not found; per-caller
rate limit (`LOOKUP_POLICY`: 20 back to back, then 1 min doubling to 15 min, per hour; 429
with `Retry-After`); every lookup, hit or miss, is audited with the e-mail as a SHA-256
digest. Invites by e-mail are T-0052.

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
the response carries `sod_warning: true` and the audit entry `sod_warning: true`. T-0073 feeds
team-derived grants into the same function. Assigning PER-09 never adds hold or signing
capability: capabilities stay those of each persona (`access.PERSONAS`).

## Audit

Tenant-level chains: `admin-user:<user>` (create, update), `admin-user-lookup:<caller>` (every
lookup), `project-members:<project>` (`assign` with added / removed / grants / `sod_warning`,
`remove`), `admin-grant:<user>` (grant, revoke).

## Tests

`tests/test_admin.py` (SQLite and PostgreSQL), `tests/test_rls_admin.py` (PostgreSQL as
`kerp_app`: grant visibility and writes, client-portal user vs. System admin on projects).
