# app/core/mail — outbound e-mail (T-0058, diff §4.6)

One core service for every transactional e-mail: core auth (magic links; password reset,
T-0077) and modules (invites, T-0059; access-request decisions, T-0063). AWS SES in
production, always asynchronous, i18n templates, a send log that is also the delivery ledger
(T-0226, ADR 0020), and bounce/complaint suppression.

| File | What |
|---|---|
| `service.py` | `send_mail(session, template_key, to, context, locale=None, *, recipient_user_id=None, broadcast_id=None)`; worker `deliver`; `suppress`, `suppressed_addresses` (which of these addresses are suppressed: a broadcast's audience, T-0228), `record_ses_event`; admin `list_sends(SendFilter)`, `send_stats(SendFilter)`, `list_suppressions`, `remove_suppression` (audited) |
| `queue.py` | `QueuedMail`, the post-commit hook `set_mail_enqueuer` (installed by the jobs module) |
| `templates.py` | `MailTemplate` (audience, `required`), `register_template`, `TEMPLATES`, `render` (text + simple HTML, optional override), `override_problems`, `sample_context` |
| `overrides.py` | Template overrides (T-0227, ADR 0021): `list_templates`, `save_override`, `reset_override`, `list_revisions`, `effective_override` (used by `deliver`), `preview`, `send_test` |
| `i18n.py`, `locales/*.json` | `t(key, locale, **params)` over flat JSON catalogs; `en-AU` is the base |
| `transport.py` | `MailTransport` protocol: `SesTransport`, `FakeTransport`, `LogTransport`; `check_mail_config`, `transport_from_settings` |
| `sns.py` | SNS message verification for the SES delivery/bounce/complaint endpoint |
| `models.py` | `mail_send_log`, `mail_suppression` (`BaseRecordMixin`, standard RLS; migration `0024`; the ledger columns: `0040`), `mail_template_override` (`0042`) |
| `router.py`, `schemas.py` | `/api/v1/mail`: admin reads (history, stats), suppression removal, template overrides, the public SNS endpoint |

## Sending

```python
from app.core.mail.service import send_mail

send_mail(session, "auth.magic_link", user.email, {"token": token, "expires_minutes": 15})
# Core templates: auth.magic_link, auth.password_reset (link /reset-password#<token>),
# auth.password_changed (the notice after a reset; no parameters) - T-0077.
# Module templates: admin.project_invite (link /invite#<invite_token>.<setup_token>) - T-0059;
# access_requests.approved | rejected | rejected_no_reason (public link /) - T-0063.
# notifications.broadcast (subject, multi-line message, opt-out link + List-Unsubscribe) - T-0228.

# The delivery ledger (T-0226): name the recipient's user when you know it; a broadcast
# (T-0228) passes its id. Neither replaces the address, which is kept hashed + masked only.
send_mail(session, "auth.password_changed", user.email, {}, recipient_user_id=user.id)
```

- Call it inside the caller's unit of work. It checks the template and the address, adds a
  `queued` row to the send log and stages the mail on the session. Only **after the commit**
  is it handed to the queue; a rollback (or a rolled-back SAVEPOINT) drops it.
- Nothing is sent from a request: the jobs module installed the enqueuer at start-up
  (`app.modules.jobs.mail.install`; core never imports a module). It stores the payload in
  Redis (`kerp:mail:<send id>`, 24 h TTL) and enqueues `kerp_send_mail` on the SAQ
  **interactive** queue with ids only, so a token never lands in SAQ job kwargs (which SAQ
  logs), in the database or in a log line. Without Redis (no enqueuer) the row stays `queued`
  and a warning is logged.
- A row still `queued` an hour past the 24 h payload lifetime can never be sent (its enqueue
  was lost, or the payload expired before a job ran; the payload is never persisted, so it
  cannot be re-enqueued). The jobs module's hourly cron `expire_mail_cron` (batch worker)
  marks it `failed` with `payload_expired` through `expire_queued` / `fail_unavailable`, per
  scope under RLS (T-0346, TD-336). `fail_unavailable` only changes a row still `queued`: a
  send that was already settled keeps its status.
- The worker (`deliver`) skips a suppressed address (`suppressed`), renders the template,
  sends, and records `sent` with the provider message id. A transient error (throttling,
  5xx, network) is retried by SAQ with exponential back-off (6 attempts, 15 s doubling to
  10 min); a permanent error or the last attempt records `failed` with a short code
  (`ses:MessageRejected`). The caller's business action committed long before: a failed send
  never rolls it back.
- The SES client is bounded (T-0346): 5 s connect, 20 s read, at most 2 attempts, so one send
  ends well inside the mail job's 60 s timeout and never goes out again after the job timed
  out and SAQ retried it.
- Delivery is at-least-once: a second run of a send that is no longer `queued` does nothing.

## Templates

A template is the i18n keys `mail.<key>.subject|intro|outro` (+ `action` with a link) plus
the context parameters it shows. Register one with
`register_template(MailTemplate("<area>.<name>", params=(...), link_path="/x", secret="token"))`
and add its keys to `locales/en-AU.json` (`tests/test_mail.py` renders every registered
template in `en-AU` and fails on a missing key).

- **Secrets only inside link fragments**: a template with a link builds it as
  `<KERP_WEB_ORIGIN><link_path>#<secret>`; the secret is used nowhere else and may not equal
  any other context value. A link to an ordinary page of the app carries no secret and must
  be declared so: `MailTemplate(..., link_path="/", public_link=True)` gives
  `<KERP_WEB_ORIGIN>/` (the access-request decision e-mails, T-0063). A `link_path` without a
  secret and without `public_link` is refused.
- Plain text plus simple HTML: no images, no remote styles or fonts, no scripts, no tracking
  pixel; every value is HTML-escaped. No project data beyond code and name.
- Locale: the recipient's, else `KERP_MAIL_DEFAULT_LOCALE` (`en-AU`); a missing catalog or
  key falls back to the language (`en`), then the default, then `en-AU`.
- **Long plain text** (T-0228): a template may name one parameter `multiline_param`; that
  value may hold line breaks and tabs and up to 10 000 characters (no other control
  characters). It is escaped like every value; line breaks become `<br>` in the HTML. Every
  other value stays one line of at most 500 characters.
- **One-click unsubscribe** (T-0228, RFC 8058): a template with a secret may declare
  `list_unsubscribe_path` (an `/api/v1/...` path). The rendered mail then carries
  `List-Unsubscribe: <<KERP_WEB_ORIGIN><path>?token=<secret>>` and
  `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (`RenderedMail.headers`; SES gets them
  as message headers). This header is the one other place the secret goes; it is in a query
  string, which the access log never records. Used by `notifications.broadcast`.
- Each template declares its **audience** group for the editor (`internal_staff | client |
  external_party | deployment_wide`, default `deployment_wide`) and whether it is
  **`required`** (sign-in, password reset, invite; RFI call-outs once registered): an override
  may reword a required template but never leave a text block empty.

## Template overrides (T-0227, ADR 0021, TD-400)

The catalogue texts are the shipped defaults. A System admin rewords, per template and locale,
the **subject, intro, outro and action label** as plain text (`mail_template_override`,
tenant-level, one row per revision, `version` 1, 2, ...; at most one `is_current` row per
template and locale). Never raw HTML: the renderer still escapes every value and builds the
link and footer itself, so an override can neither move nor reveal a secret.

- **Checks** (`override_problems`, on save and again at send time): no HTML, no URL (`://`,
  `www.`, `mailto:`, `javascript:`), no control characters, no line break in the subject or
  action label; at most 200 / 2000 / 2000 / 80 characters; placeholders only as a bare
  `{name}` from `template.placeholders()` (its parameters plus `{app_name}`), never the secret,
  never `{x.attr}`, `{x[0]}`, `{x!r}` or `{x:spec}`. The subject and the action label are never
  empty; the intro and outro of a `required` template neither. Otherwise 422, one entry per
  field (`loc: ["body", "<field>"]`).
- **Sending**: `deliver` reads the current override in its own unit of work (no cache), along
  the recipient's locale chain (an override at a locale wins over that locale's catalogue). The
  next send uses a save or reset at once, in every process. An override that no longer fits
  (a parameter was removed since) is skipped with a warning.
- **Revisions** are kept: a save retires the current row and adds the next version; a reset
  (`DELETE`) only retires it. Saves, resets and test sends are written to the audit ledger
  (`mail-template:<key>:<locale>`).
- **Preview** renders with synthetic sample values (`sample_context`: `SAMPLE-001`,
  `Sam Sample`, a fake link secret); a draft body is checked like a save and never stored.
- **Test send** queues the template with the sample values to the **signed-in caller's own
  address** (the request names no recipient), in the path's locale, rendered by the worker like
  any send. At most 5 per 10 minutes per caller (counted from the send log: e-mails the caller
  queued to itself), else 429 with `Retry-After`. It is an ordinary send-log row.

## Send log (delivery ledger) and suppression list

`mail_send_log`: template key, recipient SHA-256 + masked form (`b***@example.test`), locale,
status `queued | sent | delivered | bounced | complained | failed | suppressed`, attempts,
provider message id, short error code, `created_at` / `sent_at` / `status_at`, and (T-0226,
ADR 0020) `recipient_user_id`, `broadcast_id` and the record's own `project_id` (the sending
session's project, `NULL` for tenant-level sends). **Never** the address in clear, the link,
the token or the body.

Status changes from SES notifications only move forward:
`sent → delivered → bounced → complained` (`STATUS_RANK`). A repeated notification, a delivery
arriving after the bounce or complaint of the same message, or a notification for a `queued`,
`failed` or `suppressed` row changes nothing, so the result does not depend on the order SNS
delivers in.

**Stats** (`send_stats`, `GET /stats`) count the sends created in `[from, to)` (of one template
when given), in total and per template: `total`, `queued`, `sent` (accepted by SES: now `sent`,
`delivered`, `bounced` or `complained`), `delivered` (now `delivered` or `complained`: a
complaint comes from someone who received it), `bounced`, `complained`, `failed`,
`suppressed`, and `delivery_rate = delivered / sent` (4 decimals, `null` when nothing was
sent). Counts only, never a row. **History** (`list_sends`, `GET /sends`): newest first,
filtered by template, status, period, broadcast and an exact address (normalised and hashed
server-side, matched on `recipient_hash`; never stored, echoed or logged).

`mail_suppression` (tenant-level): recipient hash + masked form, reason `bounce | complaint`,
SES detail, at most one live row per address. A **permanent** bounce or a complaint adds the
address; soft (transient) bounces are only logged. A System admin removes an address with
`DELETE /api/v1/mail/suppressions/{id}` (soft delete + audit-ledger entry
`mail-suppression:<id>`).

The magic-link (and later reset and invite) flows answer neutrally whatever happens to the send.

## API (`/api/v1/mail`)

| Method | Path | Who |
|---|---|---|
| GET | `/sends?template=&status=&from=&to=&broadcast_id=&address=&limit=&offset=` | `system_admin` (else 403); `from` inclusive, `to` exclusive, both with a UTC offset; `from` after `to`, a bad status or a non-address: 422 |
| GET | `/stats?from=&to=&template=` | `system_admin`; `{created_from, created_to, template_key, totals, by_template[]}` |
| GET | `/suppressions?limit=&offset=` | `system_admin` |
| DELETE | `/suppressions/{id}` | `system_admin`; 404 for unknown / removed / another tenant's |
| GET | `/templates` | `system_admin`; every registered template: `key`, `audience`, `required`, `has_action`, `placeholders`, `locales[]` (`locale`, `shipped`, `override`) |
| PUT | `/templates/{key}/{locale}` | `system_admin`; body `{subject, intro, outro, action_label}`; a new current revision (audited); 404 unknown template, 422 refused text or bad locale, 409 lost a concurrent save |
| DELETE | `/templates/{key}/{locale}` | `system_admin`; back to the shipped default (revisions kept; audited); idempotent |
| GET | `/templates/{key}/{locale}/revisions` | `system_admin`; every revision, newest first |
| POST | `/templates/{key}/{locale}/preview` | `system_admin`; optional draft body; `{subject, text, html}` with sample values |
| POST | `/templates/{key}/{locale}/test-send` | `system_admin`; 202 `{send_id}`; to the caller's own address only; 429 when rate-limited |
| POST | `/ses-events` | public (SNS); not in the OpenAPI schema |

## The SNS endpoint (`POST /api/v1/mail/ses-events`)

A valid SNS signature alone does not prove a message is ours. In order, before anything is
fetched or written (`sns.py`):

1. JSON body (max 256 KiB) of type `Notification`, `SubscriptionConfirmation` or
   `UnsubscribeConfirmation`.
2. `TopicArn` equals `KERP_MAIL_SNS_TOPIC_ARN`; anything else is refused (403) and nothing
   is fetched, so a foreign `SubscriptionConfirmation` is never confirmed.
3. `SignatureVersion` `2`; `SigningCertURL` exactly `https://sns.<KERP_AWS_REGION>.amazonaws.com/…pem`
   (no other host, scheme, port or user info). The certificate is fetched only from there
   (5 s timeout, 64 KiB cap) and cached per URL for 24 h. SNS topics sign with version 1 by
   default: the topic's `SignatureVersion` attribute must be set to `2`, or every message is
   refused with `sns_signature_version` (see `docs/deploy/outbound-email.md` §3).
4. The SHA256withRSA signature verifies over the SNS canonical string.

Our topic's `SubscriptionConfirmation` is confirmed by a GET of its `SubscribeURL` (same host
check). Notifications (`Delivery`, `Bounce`, `Complaint`; others are logged and ignored) are
applied under `KERP_DEFAULT_TENANT_ID`; no tenant or account value of the payload is ever used.
The unit of work is that tenant with `is_system_admin` (ADR 0016, never crossing the tenant),
so the row of a project's send (an invite) is updated too, while the suppression list is still
written at tenant level. Refusals are logged with a reason code, never the body.

## Configuration

| Variable | Meaning |
|---|---|
| `KERP_MAIL_TRANSPORT` | `ses` (required in production) or `log` (development; refused in production). Unset: `log` outside production |
| `KERP_MAIL_FROM_ADDRESS` | Sender, e.g. `KERP <no-reply@mail.company.example>` (a verified SES identity) |
| `KERP_MAIL_SES_CONFIGURATION_SET` | SES configuration set (event publishing, suppression settings) |
| `KERP_MAIL_SNS_TOPIC_ARN` | The topic SES publishes deliveries, bounces and complaints to; must be in `KERP_AWS_REGION` |
| `KERP_AWS_REGION` | The deployment region (diff §4.4), default `ap-southeast-2` |
| `KERP_MAIL_DEFAULT_LOCALE` | Locale when the recipient has none, default `en-AU` |
| `KERP_WEB_ORIGIN` | Base of every link in an e-mail (required in production) |

Credentials come from the AWS default chain (the instance / task role, or the secret store),
never from settings or the repository. Production start-up (API and worker) fails unless
SES, the sender, the topic, the web origin and the deployment tenant are set
(`check_mail_config`). Deployment prerequisites: `docs/deploy/outbound-email.md`.

## Tests

`tests/test_mail.py` (boundary, contract, templates, enqueue-after-commit, delivery with the
fake transport, SES error mapping, start-up config, the jobs hook) and
`tests/test_mail_sns.py` (SNS verification with a generated certificate, suppression,
admin API) and `tests/test_mail_ledger.py` (T-0226: migration `0040`, signed `Delivery`
notifications, forward-only status changes, project sends, stats and history filters) and
`tests/test_mail_templates.py` (T-0227: every template with and without an override, the
override checks, revisions and reset, the next send, locale chain, preview, test send and its
rate limit, the API, migration `0042`). All run on SQLite and PostgreSQL.
