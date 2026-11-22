# notifications — in-app notifications behind a channel interface (T-0019), broadcast e-mail (T-0228)

A notification is one row per recipient in one project. The row is the recipient's in-app
inbox entry. Delivery goes through named channels: `in_app` is the only one built here. FCM
push (T-0144) and e-mail (TD-084) plug in as further channels without changing any caller
(TD-168 decision 5a).

| File | What |
|---|---|
| `models.py` | `oe_notifications_notification` (`BaseRecordMixin`, project RLS, migration `0010`) |
| `channels.py` | `NotificationChannel` protocol, `Recipient`, `DeliveryResult`, `InAppChannel`, the channel registry (`register_channel`, `get_channel`) |
| `schemas.py` | `NotificationCreate` (service, may pin), `NotificationPost` (API, no pin), `NotificationRead`, `NotificationSent` |
| `service.py` | `notify`, `list_notifications`, `get_notification`, `mark_read`, `clear_pins` |
| `router.py` | `/api/v1/notifications/projects/{project_id}/notifications...` |

## Record

| Column | Rule |
|---|---|
| `recipient_id` | a user with a membership in the project (else `RecipientNotMemberError`, API 422) |
| `kind`, `params` | message key (`handover.received`) and its values (≤ 20 scalar values). No text is stored: the client renders `t("notifications.kind.<kind>", params)` |
| `label` | optional label key (`handover`), rendered with `t()` too |
| `priority` | `low`, `normal` (default), `high`, `critical` |
| `pinned`, `pin_key` | a pin needs the key of the action that clears it, e.g. `handover.ack:<id>`; `unpinned_at` is set when it is cleared |
| `read_at` | set by the recipient once; reading does not unpin |
| `subject_type`, `subject_id` | optional reference to the record the notification is about (no FK) |

The list is the caller's own notifications in the project: **pinned first, then priority
(critical first), then newest** (E-04-11 handover, `docs/spec/modules/handover.md` §6).

## Channels

```python
class NotificationChannel(Protocol):
    name: str

    def deliver(self, notification: Notification, recipient: Recipient) -> DeliveryResult: ...
```

`KERP_NOTIFICATION_CHANNELS` (JSON list, default `["in_app"]`; must contain `in_app`) names the
channels every notification goes to. A channel module registers itself with
`register_channel("fcm", FcmChannel)`. `notify` stores the row, then runs `in_app` first and the
others once each in the configured order. A channel that raises, returns `ok=False` or is not
registered gives a failed `DeliveryResult` (detail: a code or the exception type, never its
text) and a log line; the in-app row stays and the remaining channels still run. All channels
run synchronously inside the caller's transaction for now; remote channels move to an
after-commit job with a delivery log and retries (TD-238).

## Using it from another module

```python
from app.modules.notifications import service as notifications
from app.modules.notifications.schemas import NotificationCreate

notifications.notify(
    session,
    NotificationCreate(
        recipient_id=user_id,
        kind="handover.received",
        priority="high",
        label="handover",
        pinned=True,
        pin_key=f"handover.ack:{handover.id}",
        subject_type="handover.handover",
        subject_id=handover.id,
    ),
    actor_id=actor_id,
)
# later, when the recipient acknowledges:
notifications.clear_pins(session, pin_key=f"handover.ack:{handover.id}", recipient_id=user_id)
```

The session must be bound to the project.

## Kinds sent by other modules

| Kind | Sent by |
|---|---|
| `validation.override_requested`, `validation.override_approved`, `validation.override_rejected` | `validation` (T-0035, ADR 0004 Amendment A.3) |

## API

The project is the one in the path; a non-member or a deleted project is 404.

| Endpoint | Who |
|---|---|
| `POST .../notifications` | `admin` (Project Administrator, PER-09); body `NotificationPost` (no pin: an API caller has no action to clear it); answers the row and each channel's result |
| `GET .../notifications?unread_only=&limit=&offset=` | any member; their own notifications only (`limit` 1–200, default 50) |
| `POST .../notifications/{id}/read` | the recipient; anybody else gets 404 |

## Broadcast e-mail (T-0228, diff §4.7, TD-399)

| File | What |
|---|---|
| `models.py` | `oe_notifications_broadcast`, `oe_notifications_broadcast_recipient`, `oe_notifications_broadcast_optout` (`BaseRecordMixin`, tenant-level, standard RLS; migration `0043`) |
| `broadcasts.py` | `Sender`, `Audience`, `check_audience`, `resolve`, `preview`, `create`, `get_broadcast`, `list_broadcasts`, `delivery_counts`, `dispatch` (the job body), `unsubscribe`; registers the mail template `notifications.broadcast` |
| `jobs.py` | `notifications.broadcast_dispatch` on the **batch** queue (3 attempts, resumable) |

**Who may send to whom.** A System admin (planner default pending TD-399): every active user of
the tenant (`scope: tenant`) or the users of any of the tenant's projects (`scope: projects`). A
PER-09: only `scope: projects` naming its own PER-09 projects; any other project id (another
project, an unknown or deleted one, another tenant's) is **404**, the whole tenant **403**.
Anyone else: 403. Filters narrow the set: `category` (`internal` = internal staff,
`external` = not internal, `client` = holds PER-15) and `personas` (holds any of PER-01–PER-16),
both on the targeted projects (any project for `tenant`).

**Resolution** runs on the server through core auth's service (`list_users`, `users_by_id`,
`memberships.member_grants`; never another module's tables) under the caller's own RLS: a
PER-09's session reads only its projects' memberships. Deactivated users, opted-out users and
addresses on the suppression list (`mail.service.suppressed_addresses`) are left out. The
sender sees a count, never an address.

**Queueing** (`POST /broadcasts`): the broadcast row (scope, project ids, filter, plain-text
subject and body, status `queued`), one `pending` recipient row per user (user ids only), the
audit entry and the dispatch job, in one unit of work. At most **5 broadcasts per sender per
24 h** (planner default), else 429 with `Retry-After`. An audience of nobody is 422. Refused
while impersonating (403 `IMPERSONATION_ACTION_FORBIDDEN`; TD-602 moves it to T-0278's guard).
Status: `queued → sending → sent` (or `failed`; a retried job resumes the `pending` rows).
`draft` is reserved for a composer that saves drafts; nothing creates one yet.

**Dispatch** (worker): per chunk of 100 pending recipients, in its own unit of work, each user
is checked again (still active, not opted out, not suppressed, else `skipped` with
`inactive | opted_out | suppressed | no_address`) and `send_mail("notifications.broadcast", ...,
recipient_user_id=..., broadcast_id=...)` is called with a fresh 256-bit token. The send log
rows carry the `broadcast_id` (T-0226); the commit hands the mails to the interactive mail
queue. Counts (`queued_count`, `skipped_count`) and `finished_at` are set at the end.

**Template `notifications.broadcast`** (`deployment_wide`, not required, overridable like any
template, T-0227): subject `{subject}`, intro `{message}` (multi-line plain text, escaped), the
action link `<KERP_WEB_ORIGIN>/unsubscribe#<token>` (SPA page: TD-601) and the headers
`List-Unsubscribe: <<KERP_WEB_ORIGIN>/api/v1/notifications/broadcasts/unsubscribe/one-click?token=<token>>`
and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058).

**Opt-out** (`POST /broadcasts/unsubscribe` with `{"token"}` in the body, for the SPA page; `POST /broadcasts/unsubscribe/one-click?token=`, RFC 8058, for mail clients, kept out of the OpenAPI schema like the SNS endpoint because the token has to be in that URI): no sign-in. The token is random
(256 bits), single-purpose (broadcast opt-out only), stored only as its SHA-256 on the recipient
row, and names no user or address; in the one-click URI it travels in the query string, which the access log
never records. Valid, used, unknown, malformed, missing tokens and a `?user_id=` all get the same
`200 {"status": "ok"}`; only a valid unused one records the opt-out (one live
`oe_notifications_broadcast_optout` row per user) and marks the token used. GET never
unsubscribes (link scanners). Rows are read under `KERP_DEFAULT_TENANT_ID`, as the SNS
endpoint. The opt-out applies to broadcasts only: no transactional e-mail ever reads it. Opting
back in: TD-603.

**Reads.** A System admin reads every broadcast of the tenant; a PER-09 only those whose
projects are all its own PER-09 projects (never a tenant-wide one); others 403 on the list and
404 on one. `GET /broadcasts/{id}` adds the delivery counts from the send log (`send_stats`
filtered by `broadcast_id`).

**Audit** (tenant-level chain `notifications-broadcast:<id>`): `broadcast_queued` (sender,
sender role, scope, project ids, filter, recipient count) and `broadcast_dispatched` (queued,
skipped). Never an address, the subject or the body.

| Endpoint (`/api/v1/notifications`) | Who |
|---|---|
| `POST /broadcasts/preview` | System admin, PER-09 (own projects); body `{scope, project_ids, category, personas}`; `{recipient_count}` |
| `POST /broadcasts` | the same; body adds `subject` (≤ 200, one line) and `body` (≤ 10 000, plain text); 202 `BroadcastRead`; 403 / 404 / 422 / 429 as above |
| `GET /broadcasts?limit=&offset=` | System admin (all), PER-09 (its projects'); newest first |
| `GET /broadcasts/{id}` | as above; with `delivery` counts |
| `POST /broadcasts/unsubscribe` | public; body `{token}`; always `200 {"status": "ok"}` (503 without a deployment tenant) |
| `POST /broadcasts/unsubscribe/one-click?token=` | public, mail clients (RFC 8058), not in the OpenAPI schema; the same answers |

## Tests

`tests/test_notifications.py` (SQLite and PostgreSQL): contract, validation, channel registry,
fan-out to a fake second channel, a failing channel, order, pins, tenancy, API.
`tests/test_notifications_broadcast.py` (SQLite and PostgreSQL): contracts, the template and its
headers (SES), validation, audience by scope / category / persona, PER-09 limited to its own
projects (preview, create, reads), opt-out and suppression, queueing + audit, rate limit,
dispatch (skips, hashed tokens, rerun), the job handler, the API, the one-click endpoint's
identical answers, impersonation, migration `0043`.
