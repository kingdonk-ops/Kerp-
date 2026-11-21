# ADR 0013 — Public inspection capture links (token-authenticated, no JWT)

- **Status:** **proposed** (planner, 2026-11-21; decision TD-410 is open; nothing may be built on
  it until the user accepts it)
- **Date:** 2026-11-21
- **Affects:** T-0252, T-0253, T-0254, T-0255 (blocked by TD-410), `RECORD_SOURCES` in
  `app/core/db.py` (new value `public_link`), CLAUDE.md §6 wording on tenant resolution,
  diff §4.2 (who may capture without an account). Second instance of the pattern: the
  calendar iCal feed of E-04-15 (TD-419).

## Context

The user's 2026-11-21 paste (pasted T-0179) asks for public "magic-link" capture: a QR sticker
on a vehicle (for example "LV-042 Daily Pre-Start QR") opens a mobile form at
`/public/inspect/:token`; a worker without a KERP account picks the vehicle and their name,
answers a pre-start with photos and submits. The API is `/api/v1/public/inspections/{token}`
with no JWT.

That conflicts with three binding rules:

1. **CLAUDE.md §6:** "Tenant comes from the JWT only; cross-tenant → 404."
2. **Diff §4.2 / TD-136:** sign-in magic links are for Field Workers (PER-14) only; there is no
   anonymous principal anywhere in KERP.
3. **Diff §4.1 and the RLS model** (`app/core/tenancy/README.md`): a session reads and writes
   only the project it is bound to; project scope comes from the caller's memberships, never
   from request parameters; System admins read every project of the tenant (ADR 0016,
   superseding the earlier membership rule), which does not concern anonymous callers.
   No row-returning RLS bypass exists, and count-only bypasses need their own ADR (ADR 0012).

The capture is wanted for real cases (drivers, subcontractor operators and visitors who do a
daily pre-start but hold no KERP account). The authenticated alternative is to give them PER-14
accounts with magic link + PIN (existing T-0057/T-0009 path).

## Decision (proposed)

A **public capture link** (`oe_inspections_public_link`, inspections module, `BaseRecordMixin`
+ the standard project policies) is a capability for **one** action: submit one inspection of
one pinned template revision in one project, optionally pre-bound to one anchor (node, Item or,
if ADR 0014 is accepted, equipment).

1. **Tenant.** The public router never reads a tenant from the request. It binds the
   deployment's hidden tenant from configuration (`KERP_DEFAULT_TENANT_ID`, diff §4.1), exactly
   as the SNS bounce endpoint does (diff §4.6). CLAUDE.md §6 gets an explicit exception:
   "Tenant comes from the JWT only, except the token-authenticated endpoints listed in ADR 0013,
   which bind the deployment tenant from configuration."
2. **Project without an RLS bypass.** The URL token is opaque:
   `base64url(project_id (16 bytes) ‖ secret (32 random bytes))`. The server decodes it, binds
   the session to (deployment tenant, that project, no user, `allowed_project_ids` = that
   project only), and looks up `token_hash = SHA-256(token)` under the **normal project
   policies**. No `SECURITY DEFINER` function, no census flag, no cross-project query. A token
   for another project, a tampered project id, an unknown hash, an inactive, revoked, expired
   or exhausted link all give the same **404** with an identical body and comparable timing
   (constant-time hash compare; the lookup always runs). Only the hash is stored; the token is
   shown once at creation (like `invite_token`, diff §4.1).
3. **Principal.** A request-scoped `PublicLinkPrincipal(link_id, project_id)` with no user id,
   no persona and no capability outside the three endpoints below. It cannot read any other
   record, list inspections, or reach any authenticated route. `created_by` on written rows is
   `NULL`; `source = "public_link"` (new `RECORD_SOURCES` value, additive) and
   `public_link_id` identify the origin.
4. **Endpoints** (`/api/v1/public/inspections/{token}`):
   - `GET /` — the pinned template revision with its localised strings, the pre-bound anchor
     header (code and description only) and the lookups the link allows.
   - `POST /uploads/presign` — presigned uploads through T-0016, scoped to the link's project and
     the inspections module, size capped by the runtime setting `max_upload_size_mb` (diff §4.7)
     and a per-link per-submission count cap; the file is `available` only after the real
     malware scanner (T-0218, ClamAV) passes; the stub scanner is refused for public uploads even
     where `KERP_UPLOAD_SCANNER_ALLOW_STUB` is set.
   - `POST /submit` — validates the answers with the template's CEL rules (T-0110) server-side,
     creates the inspection (`source = "public_link"`, `public_link_id`,
     `submitted_by_display_name`, optional `submitter_contact`, client IP, user agent),
     increments `submission_count` atomically against `max_submissions`, appends a ledger entry
     (T-0006, `source = "public_link"`), then runs the post-submit automation (T-0180) like an
     authenticated submit.
5. **Lookups** (`allowed_lookup_scopes`, default `{"equipment": false, "staff": false}`): fleet
   asset numbers + descriptions of the link's project and/or **display names** of active
   project members (no e-mail, phone, user id, persona or internal id; opaque per-response
   handles map a pick back on submit). Only records that are active and approved for selection
   are listed (no out-of-service or decommissioned equipment).
6. **Controls** (acceptance criteria of T-0253):
   - per-client-IP rate limits (client address from the trusted-proxy configuration, T-0068 /
     TD-138), plus per-link limits on GET, presign and submit, `429` with `Retry-After`;
   - token hashing (SHA-256, stored hash only, never logged; access logs and Sentry scrub the
     path segment; the public route sets `Referrer-Policy: no-referrer` and `Cache-Control:
     no-store`);
   - abuse and spam controls: per-link `max_submissions` and daily cap, submission size limits,
     answer-count and string-length limits, duplicate-submission suppression (same link + same
     idempotency key), a honeypot field, and a project switch that disables all public links;
   - no PII in lookups (above) and no echo of other submissions;
   - every create, revoke, use and refusal-by-rate-limit is audited (`source = "public_link"`
     for submissions; actor = the creating user for link management).
7. **Who creates links.** PER-06 and PER-09 of the project (link management is authenticated,
   T-0252). Revocation is immediate. Permanent links (`expires_at` null, fleet stickers) need
   an explicit flag at creation and appear in a "permanent links" review list.
8. **Privacy** (planner default, TD-410): IP and user agent kept 90 days then nulled by a batch
   job; `submitter_contact` kept with the record; both visible only to project members with read
   access to the inspection.

## Consequences

- One documented exception to "tenant from the JWT only"; every token-authenticated endpoint
  (this one, and the iCal feed of E-04-15 if accepted) must follow points 1–3 and be listed here.
- Public submissions are records without a signing user: they can never reach
  `INSPECTED_PASSED` by themselves. They land as `SUBMITTED` and need review (T-0150) unless the
  template is standalone and its category's rule set says otherwise (open in TD-410).
- The RLS bypass contract tests (diff §4) gain a fifth case: the public router binds exactly the
  token's project and nothing else.

## Alternatives

- **Authenticated capture only** (PER-14 accounts, magic link + PIN): no exception to the
  rules; every worker needs an account, an e-mail address and a device sign-in.
- **SECURITY DEFINER token lookup** across projects (count-free, row-returning): rejected, it is
  a row-returning RLS bypass.
- **Project code in the URL** (`/public/inspect/{project_code}/{token}`): works without a
  bypass too, but leaks the project code on every sticker.
