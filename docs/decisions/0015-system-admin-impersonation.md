# ADR 0015 — Support impersonation by a system_admin

- **Status:** **accepted** (user decision, 2026-11-22, TD-426: option (a) full impersonation;
  proposed by the planner 2026-11-21 with option (c) as default, rejected)
- **Date:** 2026-11-21 (proposed), 2026-11-22 (accepted)
- **Affects:** T-0277, T-0278, T-0279, T-0280; `app/core/auth` (`tokens.py`,
  `bearer.py`, `deps.py`, `access.py`, `revocation.py`); the `audit_ledger` core table (hash v3,
  migration after the ADR 0010 one); the runtime settings registry (T-0230,
  `impersonation_max_minutes`); diff §4.2 *Support impersonation*; edge spec §5. Builds on
  ADR 0016 (system_admin global visibility).

## Context

The user's 2026-11-21 paste ("T-0076"; that id is the done PER-16 Project Viewer task) asks for
a `system_admin` to log in as another user for support: a time-boxed token for the target user,
the target's exact RLS scope, a sign-off lock, dual-identity audit and a persistent banner.

When it was raised, diff §4.1 said System admins read no project record without a membership,
so impersonation would have been a break-glass on confidentiality. ADR 0016 (accepted the same
day) gives a `system_admin` every project of the tenant anyway, and the impersonation session
binds `app.is_system_admin = false`: the target's view is a **subset** of the admin's own, so
impersonation narrows visibility and never widens it. What remains:

1. **Acting under another name.** Records written during impersonation carry the target in
   `created_by` / `updated_by` (`BaseRecordMixin`); only the ledger can carry the admin. Since
   ADR 0016 an admin can create and edit on every project under their own identity, so support
   has no need to write as the target.
2. **Authority of the target.** The target may hold `A` (hold release, inspection approval,
   override decisions) or `S` (co-sign, signing). Diff §4.1: admin never confers quality or
   signing authority. Credentials (password, PIN, devices) and admin grants are the person's own.
3. **Session model.** T-0008 (RS256/EdDSA, 15-minute access, rotating refresh within a 12-hour
   session, `jti`/`sid` revocation in Redis) and T-0043 (sessions bound to enrolled devices) must
   not gain a second, weaker path.

## Decision (accepted 2026-11-22: TD-426 option (a), full impersonation)

The user decided on 2026-11-22: **option (a)**. Mutations are allowed while impersonating and
are attributed to the target, with the admin as `impersonated_by_user_id` in the ledger; the
central guardrail blocks the enumerated actions with `403 IMPERSONATION_ACTION_FORBIDDEN`. The
three sub-questions of TD-426 were not answered separately; the planner defaults below
(§8–§10) apply and can be confirmed or revised by the user later.

1. **Start.** `POST /api/v1/admin/users/{id}/impersonate` with a required reason, `system_admin`
   only. Refused: deactivated targets (422), `system_admin` targets
   (`403 CANNOT_IMPERSONATE_SYSTEM_ADMIN`), oneself, and nested impersonation.
2. **Token.** A separate token type `impersonation` (own `typ`) from the T-0008 key set: `sub` =
   target, `tid` = admin's tenant, own `sid` / `jti`, `imp` = admin, `imp_sid` = admin's session.
   `exp` = min(`impersonation_max_minutes` (registry, default 30, bounds 5–60), the admin
   session's remaining time). **No refresh token**; `/auth/refresh` refuses it. No device binding;
   device-bound field endpoints refuse it, and the SPA stores nothing of it offline. Each request
   re-checks the `jti`, `sid` and `imp_sid` revocations and that the admin still holds
   `system_admin` and is active.
3. **Exit and expiry.** `POST /api/v1/auth/impersonation/exit` revokes `jti` and `sid`; the
   admin's own tokens were never replaced, so the SPA resumes the admin session. Admin logout,
   force sign-out or loss of the grant ends the impersonation too.
4. **Scope.** `resolve_request_scope` builds the context from the target's memberships
   (`app.current_user_id`, `app.allowed_project_ids`, `app.member_project_ids`,
   `app.admin_project_ids`, `app.current_project_id`, `TenancyContext.site_node_ids`) with
   `app.is_system_admin = false`. The target's `create_projects` grant is not usable.
5. **Guardrail, central and fail-closed.** `ProjectAccess` of an impersonation principal never
   contains `A` or `S`. Every route is classified `allow | forbid` through one shared
   dependency; mutating routes without a classification are `forbid`; refusals answer
   `403 IMPERSONATION_ACTION_FORBIDDEN` and are ledgered. Always `forbid`: A/S authority,
   T-0119 signing, client co-sign, inspection submit/approve, hold release, override decisions,
   password/PIN/device changes, admin grants, memberships, invites, access-request decisions,
   teams, settings, project creation, `/api/v1/admin/*` mutations, impersonation start. A
   contract test enumerates `app.routes`. Every other mutating route is classified `allow`
   explicitly (a router-level default per module is allowed, with per-route overrides), and the
   records it writes are attributed as in §6. Exit and logout are always `allow`.
6. **Audit.** Tenant chain `admin-impersonation:<tenant>`: `impersonation.started` (admin,
   target, reason, IP, user agent, expiry, admin device), `impersonation.ended`
   (`exit | expired | revoked`), `impersonation.denied`. **Core-table change:** `audit_ledger`
   gains a nullable `impersonated_by_user_id` that is hashed (hash v3 = v2 body +
   `impersonated_by_user_id`, `v: 3`; v1 and v2 keep verifying; CHECK `hash_version >= 3 OR
   impersonated_by_user_id IS NULL`), set only from the request context. `created_by` stays the
   acting (target) user, which is the paste's `acted_as_user_id`. `BaseRecordMixin` rows get no
   second column.
7. **No side effects on the target.** Impersonation does not change the target's last login,
   last active, presence, session index or last-used project.
8. **Targets holding `create_projects`** (planner default, TD-426 sub-question 1; confirmable):
   may be impersonated; project creation is in the `forbid` list, so the grant is unusable while
   impersonating.
9. **PER-15 Client Representatives** (planner default, TD-426 sub-question 2; confirmable): may
   be impersonated, **read-only**. When the target holds a PER-15 membership on any project
   (evaluated per request from the memberships loaded with the grants), every mutating route
   except exit and logout is `forbid` for that session, as under the rejected option (c).
10. **Target notice** (planner default, TD-426 sub-question 3; confirmable): when an
    impersonation ends (`exit`, `expired` or `revoked`), the target receives one e-mail through
    core mail (diff §4.6, `send_mail`, after commit on the interactive queue): the admin's name,
    start and end time, and that actions taken were recorded under the target's name and
    audited as impersonated. The reason is not included. A failed send never affects the
    session or the ledger.

## Consequences

- Records written while impersonating carry the target in `created_by` / `updated_by`; the
  ledger entry (hash v3) is the only place that names the admin. Investigations of "who really
  did this" go through the audit explorer filter on `impersonated_by_user_id`.
- The target learns of every impersonation by e-mail after it ends.
- The ledger change (T-0279) is migration `0044`; `audit.append` reads the admin from the
  request context only (set by the authenticated principal), and outside impersonation entries
  stay v2. Like ADR 0010, its downgrade is for development databases only: it drops
  `impersonated_by_user_id` and restores `hash_version IN (1, 2)`, and fails while any v3 entry
  exists, so the admin can never be stripped from an entry silently. A production rollback keeps
  the column (roll the code back, not the schema).
- Every future mutating endpoint has to be classified, or it is refused while impersonating
  (the contract test tells its author).
- The impersonation token outlives the 15-minute access-token rule (up to 30 minutes by
  default) but is single-session, unrefreshable and revocable at once; the rule in diff §4.2
  names this exception.

## Alternatives (TD-426)

- **(a) Impersonation as specified** (mutations allowed, attributed to the target with
  `impersonated_by_user_id` in the ledger, guardrails as above): **chosen** by the user,
  2026-11-22.
- **(b) Only targets in projects where the admin is a member.** Moot under ADR 0016: the admin
  sees every project, so it narrows nothing.
- **(c) Read-only "view as"** (every mutating route except exit and logout refused): the
  planner's default while TD-426 was open, **rejected** by the user on 2026-11-22. Its
  reasoning, kept for the record: least privilege; it served the support need ("what does
  this user see and why can they not press X") without ever writing a record under someone
  else's name, kept A/S authority unreachable by construction rather than by an enumerated
  list, and since ADR 0016 an admin can already create and edit on every project under their
  own identity. The user chose (a) because support also needs to act as the user (the paste's
  "Mutations are attributed to the target"). (c) survives only as the mode for PER-15 targets
  (§9).
- **(d) Target consent with a one-time code** e-mailed to the target; combinable with (a) or
  (c); it fails the "user cannot sign in" support case.
- **Second column on `BaseRecordMixin`** (`impersonated_by` on every row): rejected, a core-mixin
  change on every table; the ledger is the attribution record.
- **Re-using the normal access/refresh pair with an extra claim:** rejected; it would be
  refreshable and could outlive the admin's session.
