# access_requests — ask for access to a project (T-0050)

The stateful access-request lifecycle of diff §4.1 (product owner, 2026-11-14): a user files,
follows, withdraws and re-files requests; the project's PER-09 holders (and System admins)
approve or reject them; the requester, who is not a member, learns the outcome from the
lobby's "Active requests" panel. Discovery (lobby list, code lookup, lookup tickets) is the
`lobby` module (T-0040); invites are T-0052. Approve and reject also e-mail the requester
(*Decision e-mails* below, T-0063).

| File | What |
|---|---|
| `models.py` | `oe_access_requests_request` (`BaseRecordMixin`, tenant-level row, own `access_request` RLS policy; migration `0023`) |
| `schemas.py` | `AccessRequestCreate`, `AccessRequestRead`, `PendingAccessRequest` / `Requester`, `AccessRequestApprove` (= admin `MemberAssign`), `AccessRequestReject`, `AccessRequestDecided`, `AccessRequestError` |
| `service.py` | `file_request`, `withdraw`, `list_mine`, `list_pending`, `approve`, `reject` |
| `router.py` | `/api/v1/access-requests/...` (below) |

## Record

| Column | Rule |
|---|---|
| `requester_id` | the user who filed it (from the token); the row's owner |
| `requested_project_id` | the project, by id (no FK across modules) |
| `message` | optional, ≤ 1000 characters (trimmed; empty = none) |
| `status` | `pending` → `approved` / `rejected` (a decider) or `withdrawn` (the requester); nothing else |
| `decided_by`, `decided_at` | the decider and when (approve / reject); never the requester (CHECK) |
| `rejection_reason` | optional, ≤ 500 characters, `rejected` only; shown to the requester |
| `granted` | on approval: the persona(s) and site chosen, `[{"persona", "site_node_id"}]` |
| `withdrawn_at` | when the requester withdrew it |

At most one `pending` row per (tenant, user, project) (partial unique index
`uq_oe_access_requests_request_pending`); decided and withdrawn rows are kept as history.

## API (`/api/v1/access-requests`)

| Endpoint | Who | What |
|---|---|---|
| `POST /` | any signed-in user | `{project_id, message?, lookup_ticket?}` → 201 `pending` (rules below) |
| `GET /mine` | any signed-in user | the caller's "Active requests" (below) |
| `POST /{id}/withdraw` | the requester | `pending` → `withdrawn`; someone else's 404; not pending 409 |
| `GET /pending[?project_id=]` | PER-09 holders, `system_admin` | pending requests of the projects the caller administers (all projects for a System admin), oldest first, with project code/name and the requester's id, e-mail and display name. With `project_id`: non-member (or deleted project) 404, member without PER-09 403; without it, a caller who administers nothing 403 |
| `POST /{id}/approve` | PER-09 of the request's project, `system_admin` | `{grants: [{persona, site_node_id?}], confirm_sod}` → `approved`; the membership is created in the same transaction |
| `POST /{id}/reject` | PER-09 of the request's project, `system_admin` | `{reason?}` → `rejected` |

Approve and reject: a request the caller cannot see is 404 (anyone but the requester, the
project's PER-09 holders and System admins); one they see but may not decide (their own, also
for a System admin) 403; not `pending` 409.

### Filing a request

In this order:

1. The project must exist and be live: read through the narrow read
   `projects.lobby_projects_by_id` (id, code, name, `visible_in_lobby`; the `projects` policy is
   not widened). Otherwise 404.
2. The caller already has effective access (`access.CallerGrants`: direct grants, and
   team-derived ones once T-0073 lands): 409 `ACCESS_REQUEST_ALREADY_MEMBER`.
3. The caller already has a `pending` request for it: 409 `ACCESS_REQUEST_PENDING` (also when
   a concurrent request wins the partial unique index).
4. **Discovery** (a leaked project id alone is not enough): an earlier `rejected` or
   `withdrawn` request of the caller for the project proves it ("Request again" needs no ticket;
   coordinator decision, PR #73 review round 2); otherwise an **internal** user (`is_internal`)
   may name a lobby-visible project; anyone else needs a valid **lookup ticket** of the code
   lookup issued to this user for this project (`lobby.verify_lookup_ticket`, 15 minutes). A
   missing, expired, forged or someone else's ticket: 404, the same answer as an unknown
   project. An earlier `approved` request does not count: a user whose membership was removed
   after an approval needs a fresh lookup (or the lobby) to ask again.
5. **Re-request cooldown**: after a `rejected` request, a new one before
   `decided_at + KERP_ACCESS_REQUEST_COOLDOWN_HOURS` (deployment configuration, default 24,
   `0` disables) is 409 `{"code": "ACCESS_REQUEST_COOLDOWN", "retry_after": "<UTC time>"}`;
   at `retry_after` it is allowed. A withdrawn request has no cooldown.

The project's PER-09 holders then get an in-app notification (`access_requests.requested`,
params `project_code`, `requester`; subject `access_requests.request:<id>`). Notifications
are project-scoped, so the service re-binds its session to the project for that write and back,
in the same transaction (the pattern of the validation module's project seeding); the
requester reads nothing of the project through it. The requester gets no notification: they
are not a member, and `GET /mine` is the authoritative outcome channel; the decision e-mail
(below) is a courtesy copy.

### Active requests (`GET /mine`)

Per project the caller's latest request, when it is `pending` or was decided or withdrawn in the
last 30 days (planner default), newest first: so every pending request, plus the latest decided
request of each other project. Each entry has the project's code and name (narrow read; a
project deleted since is left out), the status, the message, the times, the rejection reason
(on `rejected` only) and `request_again_at`: on a `rejected` entry the end of the cooldown
while it runs, otherwise `null` ("Request again" is allowed now; pending and approved entries
offer no "Request again").

### Approval

`approve` adds the chosen grants to any direct grants the requester already holds on the
project and saves them through `admin.service.assign_member`, so the rules of the member
endpoint apply: PER-07 names its site, nobody else does (422); PER-09 together with a
hold-authority persona (PER-02, PER-06, PER-07, PER-11) needs `confirm_sod: true`, else 409
`{"code": "sod_confirmation_required", "sod_warning": true}`; with it the response and both
audit entries carry `sod_warning: true` (TD-151). Assigning PER-09 adds no hold or signing
capability.

## Decision e-mails (T-0063, diff §4.1, §4.6)

`approve` and `reject` queue a transactional e-mail to the requester through the core mail
service (`app.core.mail.service.send_mail`, T-0058), in the deciding unit of work:

| Outcome | Template | Context |
|---|---|---|
| approved | `access_requests.approved` | `project_code`, `project_name` |
| rejected with a reason | `access_requests.rejected` | `project_code`, `project_name`, `reason` |
| rejected without one | `access_requests.rejected_no_reason` | `project_code`, `project_name` |

- Texts are the i18n keys `mail.access_requests.<template>.subject|intro|action|outro` in
  `app/core/mail/locales/en-AU.json`; the locale is the deployment default (accounts carry no
  locale yet). Project name and reason are put on one line first (a mail text holds none).
- One link, `<KERP_WEB_ORIGIN>/`: the app itself, where the signed-in requester lands in the
  lobby or the project picker. It carries no secret (`MailTemplate(public_link=True)`).
  Nothing of the project beyond code and name, no personas, ids or the decider's name.
- The mail is handed to the queue only **after the commit** and sent by the SAQ worker
  (interactive queue); every send is a `mail_send_log` row (`queued`, then `sent`, `failed`,
  `suppressed`, ...). Everything the mail does in the request (the project and requester
  reads, `send_mail`'s log row and staged mail) runs inside a SAVEPOINT: an address or a text
  the mail service refuses, or a database error on the way (which on PostgreSQL aborts the
  transaction), is logged with its type only and rolled back to it, and the decision stands. A failed or bounced send happens later in the worker and never
  touches the decision. A rolled-back decision (e.g. 409 `sod_confirmation_required`) queues
  nothing.
- No e-mail when the requester's account is gone or deactivated, or the project was deleted.

## Visibility (RLS, edge spec §6 amendments)

Policy `access_request` (`rls.RlsPolicy("access_request", owner_column="requester_id",
project_column="requested_project_id")`, migration `0023`):

| | Rule |
|---|---|
| read | `requester_id = app.current_user_id`, or `requested_project_id` in `app.admin_project_ids` (the projects where the caller holds PER-09, bound from their memberships at authentication), or `app.is_system_admin` |
| insert | exact tenant-level scope and `requester_id = app.current_user_id` |
| update | exact scope and readable (old and new row) |
| delete | no policy: nobody deletes a request |

So another user, a member of the project without PER-09, and a PER-09 of a different project
see nothing. The ORM scope mirrors it on SQLite (`app/core/tenancy/scope.py`).

## Audit

Tenant-level chain `access-requests:<project>`: `request` (`has_message`), `withdraw`,
`approve` (`grants`, `sod_warning`), `reject` (`reason`), each with `request_id`, `user_id`
(the requester) and the actor (requester or decider). The resulting membership is also on
`project-members:<project>` (`assign`, from the admin service).

## Tests

`tests/test_access_requests.py` (SQLite and PostgreSQL: discovery and tickets, lifecycle,
cooldown boundary, panel, deciders, ORM visibility, decision e-mails with a capturing queue
and the fake mail transport), `tests/test_rls_access_requests.py`
(PostgreSQL as `kerp_app`: the policy in raw SQL, the narrow read function).
