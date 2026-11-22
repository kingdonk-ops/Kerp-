# ADR 0021 — Automated e-mail template overrides (`mail_template_override`)

- **Status:** accepted (2026-11-22; records the new core table diff §4.7 *Automated
  templates* needs)
- **Date:** 2026-11-22
- **Affects:** T-0227 (this change, migration `0041_mail_template_override.py`), T-0229 (the
  Communications UI that edits them); `app/core/mail` (T-0058); diff §4.6 *Templates*, §4.7
  *Automated templates* and *Editor details*; TD-400 (text blocks, not raw HTML). Builds on
  ADR 0019 (owner/runtime roles) and ADR 0016 (system_admin global visibility).

## Context

Diff §4.6 ships every transactional e-mail as a registered template whose texts are i18n
catalogue keys (`mail.<key>.subject|intro|action|outro`), rendered as plain text plus simple
HTML, every value escaped, the one link built by the renderer with any secret only in its
fragment. Changing the wording needs a redeploy.

Diff §4.7 (user, 2026-11-21) lets a System admin override, per template and locale, the
subject and the text blocks as plain text with only the placeholders the template declares,
keep revisions, preview with synthetic values, test-send to themselves, reset to the shipped
default, and have the next send use the change without a redeploy. The pasted table
`oe_notification_template` held raw HTML bodies; TD-400 decided text blocks instead, so an
admin can never move or reveal a link secret or add remote content. Templates live in core
mail, not in the notifications module, so the table is a core table and this ADR records it.

## Decision

1. **One core table, `mail_template_override`** (`BaseRecordMixin`, standard tenant RLS,
   tenant-level: `CHECK (project_id IS NULL)`): `template_key`, `locale`, `version` (the
   revision number, ≥ 1), `subject`, `intro`, `outro`, `action_label` (null for a template
   without a link), `is_current`. Unique `(tenant_id, template_key, locale, version)`; a
   partial unique index allows at most one current row per `(tenant_id, template_key,
   locale)`. The revision number is `version` because the mixin's `revision` is already the
   row's own update counter. No row is ever deleted: a save retires the current row and adds
   the next version; a reset only retires the current row. No current row: the shipped
   catalogue text.
2. **Text blocks, checked on save and again at send time** (`templates.override_problems`):
   plain text only (no HTML tag, comment or doctype; no URL, `www.`, `mailto:` or
   `javascript:`; no control characters; no line break in the subject or the action label;
   length caps 200 / 2000 / 2000 / 80); placeholders only as a bare `{name}` from the
   template's declaration (its parameters plus `app_name`), never the link secret and never an
   attribute, index, conversion or format spec (`{app_name.__class__}`, `{x!r}`, `{x:>9}` are
   refused). The subject and the action label are never empty; the intro and outro of a
   template marked `required` (sign-in, password reset, invite; RFI call-outs when they are
   registered) neither. Anything else is a 422 with one entry per refused field.
3. **The renderer keeps control.** `render(..., override=)` formats the override's text with
   the same values as the catalogue's, escapes every value in the HTML, and places the action
   link (`<origin><link_path>#<secret>`) and the footer itself. An empty intro or outro of a
   non-required template leaves that paragraph out.
4. **No cache.** The worker's `deliver` reads the current override in the unit of work it
   already opens per send (one indexed query), walking the recipient's locale chain (an
   override at a locale wins over that locale's catalogue; the first locale with either
   decides). The next send after a save or reset therefore uses it in every API and worker
   process at once; a mail queued before the change and delivered after it uses the change.
   An override that no longer fits its template (a parameter was removed later) is skipped
   with a warning and the shipped text goes out.
5. **API under `/api/v1/mail/templates`, `system_admin` only (403 otherwise):** list (every
   registered template with audience group, required flag, declared placeholders, shipped
   text and current override per locale), `PUT|DELETE /{key}/{locale}` (save a revision /
   reset; both audited in the tenant-level chain `mail-template:<key>:<locale>`), `GET
   .../revisions`, `POST .../preview` (synthetic sample values; an optional draft body is
   checked like a save and not stored), `POST .../test-send` (queued through `send_mail` to
   the signed-in caller's own address only, never an address from the request; at most 5 per
   10 minutes per caller, 429 with `Retry-After`; audited).
6. **Audience groups** are declared on `MailTemplate` (`internal_staff | client |
   external_party | deployment_wide`) for the editor's grouping (diff §4.7 Editor details).
7. **Runtime role.** The table is created by the migrating role after `0039`, so `kerp_app`
   gets `SELECT, INSERT, UPDATE, DELETE` through `0039`'s default privileges (ADR 0019);
   `tests/test_runtime_role_grants_pg.py` checks it.

## Consequences

- A test send is an ordinary send-log row (template key, the caller's hashed address and user
  id); it counts in the delivery-ledger stats of that template.
- A template registered later (handover, inspection returned/overdue, co-sign, archive) is
  overridable as soon as it is registered; it declares its audience and whether it is required.
- The shared footer and the link-fallback sentence (`mail.common.*`) are not overridable.
- Locales without a shipped catalogue may be overridden (`de`, `fr-FR`); a recipient in that
  locale gets the override, other blocks fall back along the usual chain.

## Alternatives considered

- **Raw HTML bodies** (the paste). Rejected by TD-400: an admin could move or reveal the link
  secret, add remote images or tracking pixels.
- **Editing the catalogue JSON at runtime.** Rejected: catalogues are shipped code, shared by
  every tenant, and would lose the revision history.
- **A process cache with invalidation on write** (as the runtime settings registry does).
  Not needed: the worker reads once per send inside a unit of work it opens anyway, and a
  cache in the worker process could not be invalidated by a write in the API process without
  Redis pub/sub.
- **A separate counter table for the test-send rate limit.** Not needed: the send log already
  holds every e-mail the caller queued to itself.
