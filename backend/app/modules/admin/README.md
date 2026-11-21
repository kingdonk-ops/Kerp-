# admin — users, project memberships, admin grants (T-0014, T-0039), project invites (T-0052), System console (T-0218, T-0219)

The administration surface of the access model (diff §4.1; TD-120, TD-184, TD-151). The tenant
stays in code and RLS but is never taken or returned by this API.

| File | What |
|---|---|
| `models.py` | `oe_admin_grant` (`BaseRecordMixin`, tenant-level, own `admin_grant` RLS policy; migration `0015`) |
| `schemas.py` | `UserCreate` / `UserUpdate` / `UserRead`, `UserLookupRequest` / `UserSummary`, `GrantIn` / `MemberAssign` / `MemberRead` / `MemberAssigned`, `AdminGrantKind` / `AdminGrantIn` / `AdminGrantRead` |
| `service.py` | `own_admin_grants` (registered as core's admin-grant lookup), `list_grants` / `grant` / `revoke`, `create_user` / `update_user` / `lookup_user`, `project_members` / `assign_member` / `remove_member` |
| `router.py` | `/api/v1/admin/...` (below) |
| `system_router.py` | System console (diff §4.7): the queue monitor (T-0218, *System console* below); it calls the jobs service only |
| `system_uploads_router.py` | System console: the upload quarantine monitor (T-0221, *System console* below); it calls the uploads service only |
| `stats_router.py`, `system_stats.py`, `health.py`, `jobs.py` | System console deployment stats and health probes (T-0219, *Deployment stats and health* below): counts from core auth, the projects service and the module stats providers; object-store totals from the `admin.object_store_totals` batch job; probes through the uploads service |
| `invites.py`, `invites_router.py` | Project invites (T-0052, *Project invites* below): `oe_admin_project_invite` (`ProjectInvite` in `models.py`, own `project_invite` RLS policy; migration `0030`), the invite service, the post-authentication hook and the magic-link eligibility provider registered with core auth, and the invite endpoints |
| `audit_explorer.py`, `audit_router.py`, `audit_jobs.py`, `jobs.py` | Audit explorer (T-0222, *Audit explorer* below): the service, its endpoints and the `admin.audit_verify_all` batch job (`jobs.py` is what the module loader imports) |

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
never from a `User` already loaded in the session. `update_user` also refuses any other
self-deactivation (`actor_id == user_id`), even while other System admins exist
(`SelfDeactivationError`, 409 `{"code": "CANNOT_DEACTIVATE_SELF", "detail": ...}`; T-0085,
TD-463); the last-admin check runs first, so the last admin still gets the T-0211 answer. The
refusal is audited (`admin-user:<id>`, `{"action": "update_refused", "reason":
"self_deactivation"}`) by `_append_refusal`, in its own unit of work on the side-write pool,
before anything is written: the entry survives the request's rollback. A user create racing a duplicate hits the
unique constraint: 409, and the unit of work rolls back. A grant racing a duplicate raises
`GrantRaceError`; the router rolls back its write-free unit of work and answers 200 already
held. No SAVEPOINT is used (on SQLite one opened before any write commits early).

**What the grants allow** (diff §4.1, T-0039). `system_admin`: this module's System-admin
endpoints, and on every live project of the tenant `V`, `C`, `E` and `admin` (PER-09
equivalent) without a membership, never `A` or `S` (ADR 0016, T-0275:
`access.project_access(..., system_admin=True)`). `create_projects` ("Project Admin"): `POST /api/v1/projects`, nothing else; the
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
| `PATCH /users/{id}` | `system_admin` | `display_name`, `is_active` (deactivate / reactivate; a deactivated user's tokens stop at once); 409 for the last active `system_admin`; 409 `CANNOT_DEACTIVATE_SELF` when the caller deactivates themselves (audited, T-0085) |
| `POST /users/lookup` | PER-09 of any project, `system_admin` | exact e-mail → `{id, email, display_name}` of an active account, else 404 |
| `GET /projects/{p}/members` | PER-09 of `p`, `system_admin` | members of `p` with their personas and sites, each grant with `source` (`direct` \| `team`), `source_id` (the team link) and `team_name` (the team of a team-derived grant, from the teams service, T-0013) |
| `PUT /projects/{p}/members/{user}` | PER-09 of `p`, `system_admin` | set the user's complete **individual** persona set on `p`; team-derived grants are untouched, and a persona also held through a team gets its own individual row (it outlives the team) |
| `DELETE /projects/{p}/members/{user}` | PER-09 of `p`, `system_admin` | revoke all of the user's individual personas on `p`; 409 `managed_through_team` (with the link ids) when the user's access is only team-derived |
| `GET /grants[?user_id=]` | `system_admin` | live tenant-level grants `{user_id, email, display_name, grant}`, by e-mail |
| `POST /grants` | `system_admin` | `{user_id, grant, confirm_sod?}` with `grant` `system_admin` or `create_projects`: 201, 200 if already held, 404 unknown user; the response carries `sod_warning`. `system_admin` for a user holding a hold-authority persona on a live project: 409 `sod_confirmation_required` without `confirm_sod` (ADR 0016) |
| `DELETE /grants/{user}/{grant}` | `system_admin` | revoke: 204, 404 if not held, 409 for the last active `system_admin` |

Everyone else gets 403; a project the caller is not a member of (or a deleted one) is 404 for
everyone but a `system_admin` (deleted: 404 for all). Request bodies forbid unknown fields
(422): no `tenant_id`, and `is_internal`, `entra_oid`, `entra_tid` are derived, never input
(ADR 0009, TD-265). New accounts go through `app.core.auth.service.create_user`, which derives
`is_internal`; users are read only through `app.core.auth.service`.

Deployment settings (offline window, offline grace, clock-skew limit, new-project defaults) are
edited by System admins too, but live in the `settings` module (`/api/v1/settings/deployment`,
T-0044), which the projects module depends on. The same module holds the runtime settings
registry and the read-only deployment configuration (`/api/v1/settings/registry`,
`/api/v1/settings/deployment-config`, T-0230).

**E-mail lookup** (so a PER-09 can assign an existing account without enumerating the
tenant): exact, case-insensitive match only; inactive accounts are not found; per-caller
rate limit (`LOOKUP_POLICY`: 20 back to back, then 1 min doubling to 15 min, per hour; 429
with `Retry-After`); every lookup, hit or miss, is audited with the e-mail as a SHA-256
digest. Invites by e-mail: *Project invites* below. Access requests (a user asks, a PER-09 approves or rejects) are the
`access_requests` module (T-0050); approving one calls `assign_member` with the request's
personas added to the requester's direct grants, so the SoD rule below applies there too. The
requester is e-mailed the decision by that module (T-0063), not by this one.

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

**`system_admin` and hold authority** (ADR 0016, diff §4.1, T-0275). `sod_warning(personas,
system_admin=)` is also true for a hold-authority persona held by a user with `system_admin`:
the member PUT (and so access-request approval and invite acceptance, which go through it), the
invite creation (for an existing account) and the team link / team member endpoints answer the
same 409 unless confirmed, and audit `sod_warning: true`. Whether the target holds
`system_admin` is read by `access.system_admin_holders`: in the caller's session when it is a
`system_admin` one, else in a short read-only side session bound to that user (the own-rows
policy), so a PER-09 caller never reads anyone's grants. Granting `system_admin`
(`POST /grants`) to a user holding PER-02/06/07/11 on a live project (any source) needs
`confirm_sod` and is audited on `admin-grant:<user>` with `sod_warning: true` and
`sod_project_ids`.

## Project invites (T-0052, diff §4.1 Invites)

A PER-09 of a project (or a System admin) invites an e-mail address to the project with
persona(s) and site scope. The account is created **without credentials** if none exists
(`service.create_user`); **the membership is created only when the invite is accepted**.
Existing users join with one click; a user who is not signed in joins right after
authentication, in the same request, without the lobby.

| Endpoint (`/api/v1/admin`) | Who | What |
|---|---|---|
| `POST /projects/{p}/invites` | PER-09 of `p`, `system_admin` | `{email, grants: [{persona, site_node_id?}], confirm_sod?}` → `201` with the invite and, **once**, `invite_token` + `invite_link` (`<KERP_WEB_ORIGIN>/invite#<token>`); creates the account if needed; revokes every earlier open invite of that e-mail on `p`; `409 sod_confirmation_required` (the PER-09 + hold-authority rule of T-0014, counted over the invited personas and the grants the account already holds on `p`); `429` + `Retry-After` at the send cap (nothing created or revoked); `409 INVITE_CONFLICT` when a concurrent request changed the same invite |
| `GET /projects/{p}/invites` | PER-09 of `p`, `system_admin` | the open invites: `pending`, and `expired` never accepted, each with `email`, `grants`, `status`, `expires_at`, `last_sent_at`, `resend_available_at` (from the send cap; `null`: a re-send is allowed now). Accepted and revoked invites are not listed. Never a token |
| `DELETE /projects/{p}/invites/{id}` | PER-09 of `p`, `system_admin` | revoke an open invite: `204`; `409 INVITE_NOT_RESENDABLE` for an accepted or revoked one |
| `POST /projects/{p}/invites/{id}/resend` | PER-09 of `p`, `system_admin` (any of them, not only the inviter) | for a `pending` or `expired` (never accepted) invite: a fresh token (the old link gets the neutral `410` at once), `expires_at` = now + `KERP_INVITE_TTL_HOURS`, `last_sent_at` = now, status `pending`; e-mail, persona(s) and site scope are kept; the response carries the new `invite_token` + `invite_link` once; `409 INVITE_NOT_RESENDABLE` (accepted or revoked); `429` + `Retry-After` at the send cap |
| `POST /invites/preview` | any signed-in user | `{invite_token}` → `{project_id, project_code, project_name, inviter_name}` for the "Join project" confirmation; grants nothing |
| `POST /invites/accept` | any signed-in user | `{invite_token}` → `{joined_project_id}`: the direct membership (added to any direct grants the user holds there) and the invite's `accepted` mark in one transaction |
| `POST /invites/setup-preview` | **not signed in** (T-0059) | `{invite_token, setup_token}` (both from the e-mailed link) → `{can_set_password: true, project_code, project_name}` for an account without credentials; `{can_set_password: false}` (nothing about the project) for an account that has credentials: sign in; `410 INVITE_GONE` for any token problem; grants nothing |
| `POST /invites/accept-with-password` | **not signed in** (T-0059) | `{invite_token, setup_token, new_password}` → the normal token response of a sign-in (refresh cookie, or `refresh_token` in the body for a native app; `session_expires_at`, `joined_project_id`): first password, membership and session in one transaction; `410 INVITE_GONE` (any token problem, a deactivated account, the loser of two parallel calls); `409 INVITE_SIGN_IN_REQUIRED` (the account has credentials); `422 {detail, code, min_length, max_length}` when the password policy refuses (the invite stays pending); `401 {code: DEVICE_REVOKED}` when `X-KERP-Device-Id` names a revoked device (only after the tokens and the password passed; nothing is written) |

A non-member of `p` (PER-09 of another project included) gets `404`, a member without PER-09
`403`: the rule of the member endpoints. Preview and accept answer `403 {code:
INVITE_WRONG_USER}` when the session's e-mail is not the invited one (checked first, so a
stranger learns nothing about the invite's state), `410 {code: INVITE_GONE}` with one neutral
message for an expired, revoked, used or unknown token or a deleted project, and the loser of
two parallel accepts `409 {code: INVITE_CONFLICT}`. Every refusal is audited and grants
nothing (a per-user throttle on refusals: TD-372). Accepting never asks for `confirm_sod`:
the inviter confirmed at creation, and the accept is audited with `sod_warning` (TD-373).

**Paths.** The spec names `/api/v1/projects/{id}/invites` and `/api/v1/invites/...`; the module
loader mounts every module under `/api/v1/<module>`, so the endpoints live under
`/api/v1/admin`, next to the member endpoints (TD-368).

**The token** is `secrets.token_urlsafe(32)` (256 random bits). Only its SHA-256 is stored
(`token_hash`); the token is returned once to the inviter and is never logged or audited. It
travels in the link's URL **fragment** and in POST bodies only: no endpoint takes it in a path
or query (`GET /auth/sso/entra/start` ignores an `invite_token` query parameter; the form post
of the same path carries it, TD-369), and `app/core/logging.py` scrubs `invite_token` / `setup_token`
fields from log lines (an error reporter must do the same once one exists, TD-371). Expiry: `KERP_INVITE_TTL_HOURS` (default 168 = 7 days) from creation
or the latest re-send. `expired` is stored lazily: a pending row past `expires_at` already
counts as expired.

**Single use, atomic.** Accept, revoke, re-send and the lazy `expired` mark are each one
conditional `UPDATE ... WHERE id AND token_hash AND status IN (...)`
(`invites._transition`). Of two racing statements the database lets exactly one match: two
parallel accepts give one `200` and one `409`; a re-send racing an accept with the old token
has exactly one winner, and once the rotation has committed the old token never matches again
(`410`).

**Send cap** per project + normalised invited e-mail, counting invite creations and re-sends
together: 1 per 60 s and 5 per 24 h (`429` with `Retry-After`). It is counted from the
`sent_at` log of *every* row of that project and e-mail (accepted and revoked rows are kept),
so revoking and re-creating cannot reset it.

**Row-level security.** `oe_admin_project_invite` is tenant-level (it names its project in
`invited_project_id`) and is visible only to the PER-09 holders of that project
(`app.admin_project_ids`) and to `app.is_system_admin` sessions; they are also the only ones
who insert and update, and nobody deletes (`app/core/tenancy/README.md`). **The invitee never
reads the table.** Preview, accept, the post-authentication hook and the eligibility provider
run on a narrow system path (`invites._SystemPath`): the session is re-bound with
`is_system_admin` for exactly the lookup by token hash (eligibility: by the caller's own
e-mail), the checks (hash, expiry, single use, bound e-mail) and the writes, and bound back.

**Not signed in: the carry-through.** `POST /auth/login`, the magic-link request and the
Entra ID SSO start (form post) accept an optional `invite_token`. After a successful
authentication core auth runs the **post-authentication hook** this module registers at
start-up (`invites.HOOK`, `app.core.auth.post_auth`): the invite is accepted in the same
request and the token response (the SSO landing: the query) carries `joined_project_id`, or
`invite_error` (`INVITE_WRONG_USER`, `INVITE_GONE`, `INVITE_CONFLICT`, `INVITE_UNAVAILABLE`);
a bad token never blocks the sign-in. Where a sign-in spans two requests (the magic-link
record, the SSO `state`, the native exchange code) core keeps only the invite's *reference*
(the token's SHA-256, `InviteHook.reference`), never the token; the magic-link URL never
carries either. Core never imports this module or reads its table.

**Magic links only for Field Worker invites** (diff §4.2, TD-136). The module registers
`invites.invited_field_worker` as an eligibility provider (the T-0057 extension point): an
account with a valid pending invite for its e-mail that names PER-14, on a live project, may
sign in by magic link and join. An invite naming only other personas gets the neutral `202`
and no link; that invitee joins through password login or, for company members, Entra ID SSO.
The provider gets the account, not the request's token, so eligibility is by e-mail: a
request without `invite_token` gets a link too and the user joins from the invite link
after signing in (TD-370).

**External invitees never use SSO** (product owner, 2026-11-14; diff §4.1, §4.5). Clients,
subcontractors and other externals are not on the company Entra tenant. A brand-new invitee
without credentials sets their first password at acceptance from the **e-mailed** link, which
carries the `invite_token` and a second secret, the `setup_token` (next section). The copyable
link in the create and re-send responses carries the **`invite_token` only**: it can never set
a password, so an inviter who copies the link cannot take over the invitee's account.

### The invite e-mail, the `setup_token` and the first password (T-0059)

**The e-mail.** Every send (creation and re-send) queues the template `admin.project_invite`
through `app.core.mail.service.send_mail` in the same unit of work: the worker sends it after
the commit, a rolled-back request sends nothing, and nothing is ever sent from the request.
It names the project (code and name), the sender and the expiry, and its one link is
`<KERP_WEB_ORIGIN>/invite#<invite_token>.<setup_token>` (both tokens in the fragment, joined
by a dot; neither token contains one). The audit entry of the send carries `email_queued`
(`false` when the mail service refuses the address or a text, e.g. one that is too long: the
invite still stands and the copyable link is the fallback; a mail problem never fails an
invite). Project and sender names are put on one line first (they may hold line breaks, a
mail text may not); a sender whose account row is gone is named "A project administrator".
The mail context key of the two tokens is `link_token`, a scrubbed field name.

**The `setup_token`** is a second `secrets.token_urlsafe(32)` (256 random bits). Only its
SHA-256 is stored (`setup_token_hash`, migration `0031`). It exists in clear only inside
`invites.create_invite` / `resend_invite` and the queued e-mail: it is not part of
`IssuedInvite`, so no response (create, re-send, list), audit entry or log line can carry it,
and `app/core/logging.py` scrubs `setup_token` fields (log lines, and `scrub_sentry_event` for
an error reporter, TD-371). It lives and dies with the invite: same expiry; a re-send rotates
both tokens (the old e-mailed link gets `410` on both endpoints); accept and revoke
(re-invite included) clear the hash.

**Accept with password** (`invites.accept_with_password`, one request, one transaction):

1. Both tokens are checked on the narrow system path; nobody is signed in. Any problem
   (unknown invite token, missing or wrong `setup_token`, expired, revoked, used, deleted
   project, deactivated account) is the same neutral `410 INVITE_GONE`, audited.
2. An account that already has credentials (a password, or a linked Entra ID identity:
   `auth.service.has_credentials`) gets `409 INVITE_SIGN_IN_REQUIRED`: it signs in and joins
   through the carry-through. A first password is never set over existing credentials.
3. The invite row is locked with a conditional no-op update. Of two parallel calls the second
   waits and then finds the invite used: one `200`, one `410`.
4. **Core auth** checks the password policy (T-0077), stores the hash and issues the normal
   session: `AuthService.start_session_with_first_password`. It locks the account row and
   refuses when the account got credentials or was deactivated meanwhile. **This module
   never hashes a password or writes the users table.** A policy refusal (`422` with the
   policy `code`) changes nothing: the invite stays pending and the link still works.
5. The invite is marked `accepted` (which clears the `setup_token`) and the membership is
   created, as in a signed-in accept.

**The copied link cannot set a password.** It has no `setup_token`, and nothing derives one.
The client offers "Send me an e-mail to set my password" (the self-service reset of T-0077,
`POST /auth/password-reset/request`); after the reset the user signs in with the
`invite_token` and the carry-through accepts the invite. The PER-09 never sees and can never
set the invitee's password. Screens: T-0078.

**Team links need no invite or user action** (ADR 0008). A System admin linking a team to a
project, or adding a user to a linked team, grants access directly; nobody is invited and
nobody accepts anything.

## System console (`/api/v1/admin/system`, diff §4.7)

`system_admin` only: everyone else, a PER-09 of every project included, gets **403** (an admin
surface, not a project record). Queue monitor (T-0218, absorbed T-0220; detail in
`app/modules/jobs/README.md` *Queue monitor*):

| Endpoint | Answer |
|---|---|
| `GET /system/queues` | `queues`: per queue (`interactive`, `batch`) `queued`, `active`, `scheduled` (SAQ, deployment-wide), `failed` (job rows), `oldest_queued_age_s`; `cron_jobs`: `name`, `schedule`, `last_run_at`, `last_finished_at`, `last_outcome`, `next_run_at`. 503 without Redis |
| `GET /system/queues/failed?limit=&offset=` | failed job rows: `id`, `kind`, `queue`, `project_id`, `project_code`, `attempts`, `error_class`, `message` (<= 500 characters). Never a payload, result or SAQ kwargs |
| `POST /system/queues/failed/{job_id}/retry` | 202 `{id, kind, status: queued}`; 409 unless `failed`; 404 unknown |
| `POST /system/queues/{job_id}/abort` | `{id, kind, status: failed}` for a `queued` or `running` job (SAQ abort, best effort); 409 otherwise; 404 unknown |

SAQ's own web UI is not mounted (it has no KERP auth; a test checks every route).

Upload quarantine monitor (T-0221; detail in `app/modules/uploads/README.md` *Quarantine
monitor*). Rows of every project of the tenant under the normal project RLS (ADR 0016); no
bypass role, no `SECURITY DEFINER` function, no purge (TD-397):

| Endpoint | Answer |
|---|---|
| `GET /system/uploads?status=&reason=&project=&limit=&offset=` | `{items, total, limit, offset}`; items `verifying`, `rejected`, `expired` uploads: `id`, `project_id`, `project_code`, `module`, `filename`, `content_type`, `size_bytes`, `sha256`, `status`, `reason`, `scan_signature`, `uploader_id`, `object_deleted`, `created_at`, `updated_at`; most recently changed first |
| `GET /system/uploads/counts?status=&reason=&project=` | `[{project_id, project_code, status, reason, count}]` |
| `POST /system/uploads/{file_id}/recheck` | 202 `{job_id, project_id, kind, status: queued}` for a stuck `verifying` file (its verify job is not `queued`/`running`) whose bytes still exist; 409 `NOT_VERIFYING` / `CHECK_RUNNING` / `OBJECT_GONE`; 404 unknown |
| `POST /system/uploads/sweep?project=` | 202 `{job_id, project_id, kind: uploads.sweep_orphans, status: queued}`; 404 unknown or deleted project |

### Deployment stats and health (T-0219)

| Endpoint | Answer |
|---|---|
| `GET /system/stats` | `projects` (`active`, `soft_deleted`), `users` (`active`, `deactivated`), `devices` (`active`, `revoked`), `modules` (per loaded module the counts its stats provider returns, e.g. `asset_management.asset_nodes`, `uploads.files_<status>` and `available_bytes`, `jobs.jobs_<status>`, `teams.teams`), `object_store` (`as_of`, `refreshing`, `storage_classes`: `STANDARD`, `GLACIER_IR`, `DEEP_ARCHIVE`, `other` with `objects` and `bytes`). Numbers only: never an id, name or row |
| `POST /system/stats/object-store/refresh` | 202 `{job_id, status: queued}`: the `admin.object_store_totals` job on the `batch` queue; **409** with the run in progress (`queued` or `running`): one run at a time per tenant (transaction lock, `jobs.create_single_run_job`) |
| `GET /system/health` | `status` (the worst probe), `checked_at`, `probes`: `postgresql` (`SELECT 1` on a pool connection), `redis` (`PING`), `object_store` (`HeadBucket`), `malware_scanner` (clamd `PING`, T-0218), each `{name, status, latency_ms}`. Never a host name, connection string, bucket or credential |

**Counts under normal RLS** (ADR 0016, T-0274): every count is read in the caller's
tenant-level request session, which shows a `system_admin` every project of the tenant without a
membership; no `SECURITY DEFINER` function, no bypass role. Each count also filters on the
session's tenant explicitly ("visibility is not scope"). The admin module never reads another
module's table: users and devices come from core auth (`user_counts`, `device_counts`), projects
from `projects.service.project_counts`, everything else from the **stats-provider registry**
(`app/core/stats/`): each module registers `@register_stats_provider("<module>")` in its own
`service.py`; only the modules this app loaded are collected. A table that still hid rows from a
`system_admin` session would get a TODO, never a bypass.

**Object-store totals**: listing the bucket is slow, so `admin.object_store_totals` (batch,
tenant-level, `jobs.py`) lists the tenant's key prefixes (content, `staging/`, `quarantine/`)
through `uploads.service.object_store_totals` (paginated S3 listing). The newest succeeded run's
`result` is the cache; its `finished_at` is `as_of`. Only tenant-level runs of the kind count
(a project row of the same kind neither blocks a refresh nor feeds the cache).

**Health** (`health.py`): the probes run concurrently in a bounded thread pool with a deadline
of `PROBE_TIMEOUT_S` = 2 s (planner default). `ok`: healthy within `DEGRADED_AFTER_S` = 1 s;
`degraded`: healthy but slower, or nothing real to probe (the always-clean stub scanner);
`down`: failed, raised, timed out or not configured (no Redis URL). A probe's exception text is
never returned, audited or logged (only its class): it can name a host or DSN. Each probe is
bounded by its own timeout as well, so a hung dependency frees its pool thread within about
2 s and cannot starve the other probes: PostgreSQL uses a separate pool-less engine (one fresh
connection, libpq `connect_timeout` and a server `statement_timeout` of 2 s; it never waits
for a checkout from the API pool), Redis socket connect/read timeouts, the object store a
dedicated S3 probe client (`connect_timeout` and `read_timeout` 2 s, one attempt, no retry;
`uploads.storage.HEALTH_PROBE_TIMEOUT_S`), clamd `PING` with its timeout cut to 2 s.

Every call (stats, refresh, refused refresh, health) is audited in `admin-system:<tenant>`
(`stats_viewed`, `object_store_refresh_queued` / `object_store_refresh_running` with the job
id, `health_checked` with each probe's status). A call refused with 403 (not a
`system_admin`) or 401 is **not** audited, like the queue monitor: nothing was read or changed.

The single-run guard (`jobs.create_single_run_job`) takes a transaction-scoped advisory lock and
then looks for a pending run. It relies on PostgreSQL's default **READ COMMITTED** isolation (the
request sessions' setting): the check after the lock takes a fresh snapshot and sees a run that
the previous lock holder committed. Under REPEATABLE READ or SERIALIZABLE the snapshot could
predate that commit and a second run could be queued.

Legal hold (T-0223; detail in `app/modules/projects/README.md` *Legal hold*), through the
projects service:

| Endpoint | Answer |
|---|---|
| `PUT /system/projects/{project_id}/legal-hold` | body `{on, reason}` (reason 1–500 characters, both ways; 422 otherwise). `{project_id, legal_hold, legal_hold_reason, legal_hold_set_by, legal_hold_set_at}`; 404 unknown, deleted or other tenant's project. While on, `DELETE /api/v1/projects/{id}` answers 409 `LEGAL_HOLD_ACTIVE` |

## Audit

Tenant-level chains: `admin-user:<user>` (create, update), `admin-user-lookup:<caller>` (every
lookup), `project-members:<project>` (`assign` with added / removed / grants / `sod_warning`,
`remove`), `admin-grant:<user>` (grant, revoke), `project-invites:<project>` (`create` and
`resend` with `email_queued`, `revoke`, `first_password_set`, `accept` and `refuse` with stage
and reason; the invitee by account id) and `project-invites` (`refuse` of a token that matches
no invite), `admin-system:<tenant>` (System console: `job_retried`, `job_aborted` with job id,
kind, queue and project id; T-0218; `upload_rechecked` with file, project and job id,
`upload_sweep_queued` with project and job id; T-0221; `stats_viewed`,
`object_store_refresh_queued`, `object_store_refresh_running`, `health_checked`;
T-0219; `legal_hold_set`, `legal_hold_cleared` with project id, code and reason; T-0223).
Invite entries never carry a token, a hash, a password or an e-mail address. An accepted invite
also writes the usual `assign` entry on `project-members:<project>`.

## Audit explorer (`/api/v1/admin/system/audit`, T-0222, diff §4.7)

Part of the System console: `system_admin` only, 403 for everyone else (a PER-09 of every
project included). Read-only over the hash-chain ledger: there is no endpoint that updates or
deletes a ledger row (a test checks the route table), and every read goes through
`app/core/audit/service.py` (*Reading across scopes* in its README). Entries are read under the
caller's normal RLS: the tenant-level chains and the chains of every project of the tenant,
without a membership (ADR 0016), never another tenant's.

| Endpoint | Answer |
|---|---|
| `GET /system/audit` | `{items, next_cursor}`, newest first. Filters: `actor_id`, `device_id`, `chain_key_prefix` (entity type, e.g. `admin-grant:`), `event` (payload `event` key), `flag` (payload key that is `true`, e.g. `sod_warning`; TD-402), `created_from` (inclusive) / `created_to` (exclusive), both with a UTC offset. Keyset paging: `limit` (1–200, default 50) and `cursor` (the previous `next_cursor`; opaque). An item: `id`, `project_id` (`null` = tenant level), `chain_key`, `seq`, `created_at`, `actor_id`, `device_id`, `source`, `payload`, `prev_hash`, `content_hash`, `hash_version`. Invalid filter or cursor: 422 |
| `POST /system/audit/verify` | body `{chain_key, project_id}` (`project_id` `null` = the tenant-level chain; the same key in two scopes is two chains). Synchronous report: `ok`, `length`, `head_hash`, `first_broken_seq`, `problem_count`, `problems` (at most 100 texts). Checks contiguous `seq`, the `prev_hash` links and the recomputed `content_hash`. No visible entry: 404 |
| `POST /system/audit/verify-all` | 202 `{id, kind: admin.audit_verify_all, status: queued}`: a `batch` job; poll `GET /api/v1/jobs/{id}`. Its `result`: `ok`, `chains`, `entries`, `broken_chains`, `first_broken` (`{project_id, chain_key, seq}` or `null`; first in scope and chain-key order, tenant level first) |

The job is tenant-level, and the worker binds it without any admin flag. The handler re-checks
at job start that the user who asked (`requested_by`, set by the endpoint, never by a client:
the kind is not an `api` kind) still holds `system_admin`, and only then binds
`is_system_admin` for the read (ADR 0016); otherwise the job fails. It runs for up to an hour
in one pass (TD-495). The job row is tenant-level, so until the jobs API has a per-kind check
(TD-156) a tenant user who knows the job id can poll its report (counts and one chain key).

Listing is not ledgered. Both verifications are (diff §4.7: every console action; TD-494), on
`admin-system:<tenant>` with the caller as actor: `audit_chain_verified` (chain key, project
id, `ok`, `length`, `first_broken_seq`), `audit_verify_all_requested` (job id; same
transaction as the job row) and `audit_verify_all_finished` (job id and the report). The
impersonator filter arrives with T-0279; anchoring chain heads outside the database is TD-058.

Tests: `tests/test_audit_explorer.py` (SQLite and PostgreSQL; the tampered chains are edited
behind the triggers as in `tests/test_audit_ledger.py`).

## Tests

`tests/test_admin.py` (SQLite and PostgreSQL), `tests/test_rls_admin.py` (PostgreSQL as
`kerp_app`: grant visibility and writes, client-portal user vs. System admin on projects),
`tests/test_invites.py` (SQLite and PostgreSQL: the whole invite life cycle, carry-through,
send cap, races, log scrubbing), `tests/test_invite_setup.py` (the invite e-mail, the
`setup_token`, first password at acceptance, its races, scrubbing; capturing mail queue),
`tests/test_rls_invites.py` (PostgreSQL as `kerp_app`), `tests/test_queue_monitor.py` (System
console queue monitor; SQLite and PostgreSQL), `tests/test_uploads_quarantine.py` (upload
quarantine monitor; SQLite and PostgreSQL as `kerp_app`), `tests/test_admin_system_stats.py`
(deployment stats, the stats-provider registry and import boundary, object-store totals,
health probes; SQLite and PostgreSQL).
