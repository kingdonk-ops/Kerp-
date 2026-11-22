# ADR 0020 — The mail send log becomes the delivery ledger (`mail_send_log`)

- **Status:** accepted (2026-11-22; records the core-table change that diff §4.7 *Delivery
  ledger* requires an ADR for)
- **Date:** 2026-11-22
- **Affects:** T-0226 (this change, migration `0039_mail_delivery_ledger.py`), T-0228
  (broadcasts, sets `broadcast_id`), T-0302 (usage snapshot, per-project e-mail counts); the
  core table `mail_send_log` of `app/core/mail` (T-0058, migration `0024`); diff §4.6, §4.7
  *Delivery ledger*. Builds on ADR 0016 (system_admin global visibility).

## Context

Diff §4.6 gave KERP one outbound e-mail service with a send log (`mail_send_log`: template,
recipient SHA-256 + masked form, status `queued | sent | bounced | complained | failed |
suppressed`, provider message id, timestamps; never the address in clear, the link, the token
or the body) and SES bounce/complaint notifications through a hardened SNS endpoint.

Diff §4.7 (*Communications*, user 2026-11-21) turns it into a delivery ledger: a `delivered`
status from SES delivery notifications through the same endpoint; nullable
`recipient_user_id`, `project_id`, `broadcast_id`; stats (totals, delivery rate, bounces,
complaints, failures by period and template) and a searchable history (template, status, date,
exact-address lookup by hash). The pasted spec's `recipient_email` column is refused (§4.6
wins: hashed plus masked only). `mail_send_log` is a core table, so the change is recorded
here.

## Decision

1. **Extend, do not duplicate.** Migration `0039` changes `mail_send_log` additively:
   - `recipient_user_id` UUID NULL and `broadcast_id` UUID NULL, plain ids without a foreign
     key (like `created_by`; the broadcast table of T-0228 does not exist yet and a user row may
     be deactivated), each indexed;
   - indexes on `template_key` and `(tenant_id, created_at)` for the stats and the history;
   - the status CHECK `ck_mail_send_log_status` gains `delivered`.
   **`project_id` is not added:** every row already has it from `BaseRecordMixin` (the sending
   session's project, `NULL` for tenant-level sends such as the auth e-mails), and it is the
   RLS scope of the row. A second project column would disagree with the RLS one.
2. **Additive.** PostgreSQL: `ADD COLUMN`, re-created CHECK (a superset of the old one, so
   every existing row passes), `CREATE INDEX`. No row is rewritten and no RLS policy changes,
   so the revision carries no `FROZEN_RLS_DDL`. SQLite (tests) copies the table from a frozen
   0038 definition (tenants FK included); a test pins it to the live model. The downgrade turns
   `delivered` rows back into `sent` (what they were before the notification; the table's
   forced RLS is lifted for the owner meanwhile, as in `0020`), restores the old CHECK and drops
   the columns and indexes.
3. **`delivered` from SES delivery notifications.** `record_ses_event` handles `Delivery`
   (`notificationType` or the configuration-set `eventType`) next to `Bounce` and `Complaint`,
   behind the unchanged endpoint checks (exact topic, SignatureVersion 2, certificate from the
   regional SNS host only, signature over the canonical string, the deployment tenant from
   settings). A delivery suppresses nothing.
4. **Forward-only status changes.** Notifications move a row along
   `sent → delivered → bounced → complained` and never back (`STATUS_RANK`). A repeated
   notification (SNS is at-least-once), a late delivery after a bounce or complaint of the same
   message, or one for a `queued`, `failed` or `suppressed` row changes nothing. The final state
   is the same whatever order SNS delivers in.
5. **The endpoint reaches project rows.** The SNS endpoint has no project. Its unit of work is
   now the deployment tenant with `is_system_admin` (ADR 0016: never crosses the tenant; a
   tenant-level session still writes tenant-level rows only at tenant level), so the send-log
   row of a project-scoped send (an invite) is updated too. Before this change such a row was
   invisible to the endpoint and stayed `sent` on a bounce or complaint.
6. **Recipient privacy unchanged.** `send_mail(..., recipient_user_id=None, broadcast_id=None)`
   takes the new ids; the address is still kept only as SHA-256 + masked form. The core auth
   e-mails (magic link, password reset/changed) and the access-request decisions pass the
   recipient's user id; the invite does not (the invitee usually has no account yet).
7. **Reads, `system_admin` only.** `GET /api/v1/mail/stats?from=&to=&template=` returns counts
   only: `total`, `queued`, `sent` (accepted by SES: now `sent`, `delivered`, `bounced` or
   `complained`), `delivered` (now `delivered` or `complained`: a complaint comes from a
   recipient who received the mail), `bounced`, `complained`, `failed`, `suppressed` and
   `delivery_rate = delivered / sent` (`null` when nothing was sent), in total and per template.
   `GET /api/v1/mail/sends` gains `template`, `status` (incl. `delivered`), `from` (inclusive) /
   `to` (exclusive, both with a UTC offset), `broadcast_id` and `address`. The address is
   normalised and hashed server-side and matched exactly on `recipient_hash`; it is never
   stored, echoed in an error or logged (the request log never records query strings).
   PER-09's view of broadcast stats is served by T-0228 from the broadcast record.

## Consequences

- Deployments must publish SES *Delivery* events to the mail topic
  (`docs/deploy/outbound-email.md` §3); without them sends stay `sent` and the delivery rate
  is 0.
- `send_stats` is the counting seam for T-0228 (broadcast stats by `broadcast_id`) and T-0302
  (per-project counts by `project_id`).
- Periods are by the send's `created_at` (when it was queued), not by the time of the
  notification.
- A later status (opened, clicked) would need a new ADR: KERP sends no tracking pixels and no
  tracked links (diff §4.6, TD-400).

## Alternatives considered

- **A separate ledger table** (`oe_notification_email_log` of the paste). Rejected: it would
  duplicate the send log row for row; T-0058's table already holds every send.
- **Store the address in clear for search** (the paste's `recipient_email`). Rejected by §4.6;
  an exact lookup by hash covers the admin's "what happened to the mail for x" question.
- **A new `project_id` column.** Rejected: `BaseRecordMixin.project_id` already is it.
- **Last notification wins.** Rejected: SNS does not order messages, so a delayed `Delivery`
  could overwrite a `Bounce` or `Complaint`.
- **A `delivered_at` column.** Not needed now: `status_at` holds the time of the last change;
  a delivery-latency metric would add it in a later additive migration.
- **POST search to keep the address out of the URL.** Not chosen: the request log never
  records query strings, and the acceptance criteria name the GET filter; the address is
  hashed before any query. A load balancer or proxy in front of KERP that writes access logs
  with full URLs would still record the address; that deployment risk is tracked as a TODO
  (raised with T-0226).
