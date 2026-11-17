# app/core/mail — outbound e-mail (T-0058, diff §4.6)

One core service for every transactional e-mail: core auth (magic links; password reset,
T-0077) and modules (invites, T-0059; access-request decisions, T-0056). AWS SES in
production, always asynchronous, i18n templates, a send log, and bounce/complaint suppression.

| File | What |
|---|---|
| `service.py` | `send_mail(session, template_key, to, context, locale=None)`; worker `deliver`; `suppress`, `record_ses_event`; admin `list_sends`, `list_suppressions`, `remove_suppression` (audited) |
| `queue.py` | `QueuedMail`, the post-commit hook `set_mail_enqueuer` (installed by the jobs module) |
| `templates.py` | `MailTemplate`, `register_template`, `TEMPLATES`, `render` (text + simple HTML) |
| `i18n.py`, `locales/*.json` | `t(key, locale, **params)` over flat JSON catalogs; `en-AU` is the base |
| `transport.py` | `MailTransport` protocol: `SesTransport`, `FakeTransport`, `LogTransport`; `check_mail_config`, `transport_from_settings` |
| `sns.py` | SNS message verification for the SES bounce/complaint endpoint |
| `models.py` | `mail_send_log`, `mail_suppression` (`BaseRecordMixin`, standard RLS; migration `0024`) |
| `router.py`, `schemas.py` | `/api/v1/mail`: admin reads, suppression removal, the public SNS endpoint |

## Sending

```python
from app.core.mail.service import send_mail

send_mail(session, "auth.magic_link", user.email, {"token": token, "expires_minutes": 15})
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
- The worker (`deliver`) skips a suppressed address (`suppressed`), renders the template,
  sends, and records `sent` with the provider message id. A transient error (throttling,
  5xx, network) is retried by SAQ with exponential back-off (6 attempts, 15 s doubling to
  10 min); a permanent error or the last attempt records `failed` with a short code
  (`ses:MessageRejected`). The caller's business action committed long before: a failed send
  never rolls it back.
- Delivery is at-least-once: a second run of a send that is no longer `queued` does nothing.

## Templates

A template is the i18n keys `mail.<key>.subject|intro|outro` (+ `action` with a link) plus
the context parameters it shows. Register one with
`register_template(MailTemplate("<area>.<name>", params=(...), link_path="/x", secret="token"))`
and add its keys to `locales/en-AU.json` (`tests/test_mail.py` renders every registered
template in `en-AU` and fails on a missing key).

- **Secrets only inside link fragments**: a template with a link builds it as
  `<KERP_WEB_ORIGIN><link_path>#<secret>`; the secret is used nowhere else and may not equal
  any other context value.
- Plain text plus simple HTML: no images, no remote styles or fonts, no scripts, no tracking
  pixel; every value is HTML-escaped. No project data beyond code and name.
- Locale: the recipient's, else `KERP_MAIL_DEFAULT_LOCALE` (`en-AU`); a missing catalog or
  key falls back to the language (`en`), then the default, then `en-AU`.

## Send log and suppression list

`mail_send_log`: template key, recipient SHA-256 + masked form (`b***@example.test`), locale,
status `queued | sent | bounced | complained | failed | suppressed`, attempts, provider
message id, short error code, `created_at` / `sent_at` / `status_at`. **Never** the address in
clear, the link, the token or the body.

`mail_suppression` (tenant-level): recipient hash + masked form, reason `bounce | complaint`,
SES detail, at most one live row per address. A **permanent** bounce or a complaint adds the
address; soft (transient) bounces are only logged. A System admin removes an address with
`DELETE /api/v1/mail/suppressions/{id}` (soft delete + audit-ledger entry
`mail-suppression:<id>`).

The magic-link (and later reset and invite) flows answer neutrally whatever happens to the send.

## API (`/api/v1/mail`)

| Method | Path | Who |
|---|---|---|
| GET | `/sends?status=&limit=&offset=` | `system_admin` (else 403) |
| GET | `/suppressions?limit=&offset=` | `system_admin` |
| DELETE | `/suppressions/{id}` | `system_admin`; 404 for unknown / removed / another tenant's |
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
check). Notifications are applied under `KERP_DEFAULT_TENANT_ID`; no tenant or account value
of the payload is ever used. Refusals are logged with a reason code, never the body.

## Configuration

| Variable | Meaning |
|---|---|
| `KERP_MAIL_TRANSPORT` | `ses` (required in production) or `log` (development; refused in production). Unset: `log` outside production |
| `KERP_MAIL_FROM_ADDRESS` | Sender, e.g. `KERP <no-reply@mail.company.example>` (a verified SES identity) |
| `KERP_MAIL_SES_CONFIGURATION_SET` | SES configuration set (event publishing, suppression settings) |
| `KERP_MAIL_SNS_TOPIC_ARN` | The topic SES publishes bounces and complaints to; must be in `KERP_AWS_REGION` |
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
admin API). Both run on SQLite and PostgreSQL.
