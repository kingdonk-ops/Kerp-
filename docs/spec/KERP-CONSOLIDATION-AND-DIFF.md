# KERP — Consolidation & Merge Decisions (authoritative)

This document is the **binding reconciliation** of the legacy specs (now in
`../archive-legacy-specs/`: `architecture-suite-reference/`, `openbusinesserp-analysis/`,
`field-operations-platform/`) with the new architecture (`KERP-MASTER-SPEC.md` +
`KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md`). Where any legacy text disagrees with a
decision here, **this document wins**; where this document is silent, the master spec
and edge spec win, then the preserved legacy detail (see §8).

Build target is **KAEFER-style industrial field operations** (construction, fabrication,
remediation, turnaround) — **not** ongoing plant maintenance and **not** back-office ERP.

---

## 1. Hard scope exclusions — culled modules & domains

These are **removed** from scope. Do not build them; delete their influence from any
ported legacy text; raise no tasks for them.

| Culled | Retained replacement |
|---|---|
| **Accounting / General Ledger** — chart of accounts, double-entry journals, bank reconciliation, depreciation schedules, fiscal-year close, tax/BAS, direct invoicing, AR/AP | External-ERP **reference links** (`external_source`, `external_id`, `synced_at`) + **read-only** money mirrors (`amount_mirror`, `currency_code`). Money is never authored in KERP. |
| **HR / Payroll / time-billing** — employee contracts, PAYG/tax withholding, wage/salary calc, deductions, leave entitlements | **Physical labour logging only** — `field_time._line` (worker, equipment, RSW code, crew hours); export to corporate payroll. |
| **Generic e-commerce / Retail POS / CRM pipelines** — carts, storefront catalogs, POS registers, lead scoring, opportunity funnels, marketing automation | **Unified Party Directory** (`contacts`): Client, Contractor, Subcontractor, Supplier, Authority. |
| **Duplicated workflow engines** — `enterprise_workflows` + its redundant state tables | **Single** `approval_routes` engine (multi-party quorum, delegation, SLA timers). |
| **Runtime no-code module authoring** — `module_builder` / runtime dynamic-table generator | None — out of scope for a single-company contractor tool. Extensibility is custom fields/forms on fixed modules (MOD-12), not runtime table generation. |
| **Interactive 3D CAD/BIM viewers & native `.dwg` parsers** — WebGL IFC/BIM renderers, AutoCAD binary parsers, automated 3D takeoff | **PDF/drawing plan viewer** + vector **markups** overlay, plus a **pluggable background parser adapter** (`app/core/integrations/parsers.py`) for neutral formats (`.dxf`, `.pdf`, `.xer`). |

---

## 2. Data-model overhauls

### 2.1 One-engine inspection & schema consolidation (TD-002 — adopted)
Replaces the duplicated designers spread across `forms`, `asset_management`
(`oe_asset_layout`/`oe_asset_field_def`), `inspections` (thin), `qms`, and
`construction_control`.

- **Single field-type registry** in `core/fields/` — a shared palette: `numeric`, `text`,
  unit-bound `measurement`, `photo`, `signature`, **instrument reference**, `calculated`.
- **Unified inspection record** `oe_inspections_inspection` — the single store across all
  kinds: `kind IN ('inspection','itp','rfi')`.
- **`qms`** → strictly **master ITP template authoring**, audit registers, and calibration
  records. No parallel inspection store.
- **`construction_control`** → conformance criteria + hold/witness point evaluation via
  `run_checks`. **Drop `construction_control._inspection` entirely.**
- **`eligibility`** (new) → **stateless** service answering "is this person / instrument /
  material valid right now?" across WPS, credentials and calibration registers. **Owns no
  tables.**
- **`inspection_requests`** (new) → RFI / call-out workflow:
  `draft → requested → confirmed → witnessed | waived | no_show | failed`, with automatic
  **client delay attribution**.

### 2.2 Asset hierarchy storage (`oe_asset_node`)
- **Drop** string `path` querying and integer `depth`.
- **Add** PostgreSQL-native `tree_path ltree NOT NULL` with a **GiST** index (`CREATE
  EXTENSION ltree`).
- `parent_id` remains the **relational source of truth**; a move updates the whole subtree
  in a single `ltree` statement.
- **Add** read-only `display_path text` strictly for breadcrumb UI.

### 2.3 Dynamic asset attributes
- **Delete** the EAV table `oe_asset_field_value`.
- Attribute values live **exclusively** in `custom_fields jsonb` on `oe_asset_node` and
  `oe_asset_item`.
- Index with **GIN `jsonb_path_ops`** plus **functional B-tree** expression indexes on hot
  query attributes.
- `oe_asset_field_def` is **purely the JSONSchema definition registry**.

### 2.4 Reserved CML stubs (`oe_asset_item`)
- **Cull** the condition-monitoring calculation engine, wall-thickness trending, API 580/581
  degradation, and automatic re-inspection intervals (construction/remediation focus, not
  ongoing maintenance).
- **Reserve** fields only, in the Item JSONSchema: `cml_tag`, `nominal_thickness_mm`,
  `minimum_structural_thickness_mm`, `design_corrosion_allowance_mm`,
  `active_corrosion_rate_mm_yr`, `last_measured_thickness_mm`, `last_inspection_date`.

---

## 3. Engine, infrastructure & stack

- **Rule evaluator — CEL (Common Expression Language):** standardized for all dynamic form
  logic (`visible_if`, `required_if`, dynamic ranges, calculations). **Replaces JSONLogic /
  ad-hoc JS.**
  - Client/device: **JavaScript CEL** evaluator, offline in browser/IndexedDB.
  - Server: **Python CEL (`celpy`)** validating sync payloads.
  - CI parity against a shared fixture file `tests/contract/conformance_fixtures.json`.
- **Background queue — SAQ (Simple Async Queue) over Redis.** **Replaces ARQ.** Separate
  priority queues: `interactive` (real-time docket OCR, barcode lookups) and `batch`
  (200-page MDR PDF compile, P6 imports).
- **Cutting & nesting:** **ban `libnest2d` / `nest2D`** (GPL copyleft). 1D structural
  steel/pipe cutting via an **in-house First-Fit-Decreasing (FFD)** algorithm; 2D sheet/plate
  nesting via **`rectpack`** (MIT).
- **Primavera P6 ingest idempotency:** unique composite `(project_id,
  external_activity_id)`; mandatory **Unmatched-Row Linking Screen** in the P6 Import Wizard
  so schedule updates never sever existing inspection/RSW ties.

---

## 4. Security, isolation & identity

- **Project-scoped RLS:** keep `tenant_id` + PostgreSQL RLS on **every** table (TD-007 = yes),
  with `tenant_id` **scoped to the individual Project ID** — no cross-project leakage for
  external clients/subcontractors. **Four RLS-bypass contract tests** in CI cover: background
  SAQ workers, cross-module service calls, raw-SQL aggregations, and DB migrations.
- **Cryptographic signing (phased):**
  - Phase 0/1: **append-only hash-chained** event ledgers (`seq`, `chain_key`, `prev_hash`,
    `content_hash`, `payload`) with DB `REVOKE UPDATE, DELETE FROM app_role`.
    Ledger design (TD-057, accepted 2026-11-10 from the T-0006 implementation; detail in
    `backend/app/core/audit/README.md`): a chain is `(tenant, scope, chain_key)` where scope is
    the session's project or the tenant level (`scope_id` = `project_id` or the nil UUID;
    unique `(tenant_id, scope_id, chain_key, seq)`; keys embed the entity id, e.g.
    `inspection:<uuid>`). `content_hash` = SHA-256 lower-case hex over
    `b"kerp-audit-ledger\n"` + canonical JSON (sorted keys, compact separators, UTF-8; floats and
    NUL rejected) of `{v, tenant_id, project_id, chain_key, seq, prev_hash, created_at,
    created_by, source, payload}`; genesis `prev_hash` = 64 zeros; `seq` contiguous from 1.
    Verification checks contiguous `seq`, `prev_hash` links and the recomputed hash. Enforced
    by ORM guard, `REVOKE UPDATE, DELETE, TRUNCATE`, row/statement triggers and CHECKs; appends
    to one chain serialise on an advisory lock. Changing the hash format needs a new `v`.
  - Final turnover: MDRs and commercial Delay Notices signed with **pyHanko** →
    **ETSI PAdES-B-LTA** + **RFC 3161** trusted timestamp tokens.
- **Auth hardening:** **RS256 / EdDSA** JWTs via a **JWKS** endpoint (replaces symmetric
  HS256). 15-minute access tokens, rotating refresh tokens, server-side `jti` revocation
  blacklist in Redis. **Field-worker auth:** magic-link + **6-digit PIN** as a *local session
  unlock only*, with progressive lockout throttling.
- **Persona matrix — 15 roles** (base 13 + **Field Worker** (magic-link) + **Client Portal
  User**), **16 since 2026-11-13** with **PER-16 Project Viewer** (read-only; edge spec §5.2,
  TD-251), plus an **Asset-Site scoping** filter for Site Inspectors (visibility limited to
  their physical site within a multi-site project).

### 4.1 Access model (user, 2026-11-13; TD-120, TD-184, TD-151)

Wins over any earlier text on tenant selection, tenant administrators and project creation.

- **One hidden tenant per deployment.** `tenant_id` and RLS stay on every table (TD-007), but
  a deployment serves exactly one tenant and users never see, pick or type it. Login resolves
  the tenant from deployment configuration (`KERP_DEFAULT_TENANT_ID`, set by first-run
  provisioning); the login request carries no tenant field. Another company is another
  deployment. A person who works for several companies (MOD-01 "client inspector across EPC
  contractors") holds one account per deployment.
- **Two tenant-level admin grants**, stored per user in the admin module (not personas, not
  project memberships):
  - **`system_admin`** — a few people who run the deployment: appoint and remove System admins
    and Project Admins, create and deactivate user accounts, revoke devices (§4.3), edit
    deployment settings (§4.4), and grant PER-09 on any project (e.g. to recover a project whose
    admins left). The last System admin cannot be removed. System admins can list every project
    row and every membership (the "tenant administrator" of edge spec §6).
    - **system_admin global project visibility (user, 2026-11-21; ADR 0016, TD-396 resolved).**
      A `system_admin` can list and open every project of the tenant (top-bar picker and the
      All-Projects portfolio scope) and read and administer project-scoped operational records
      across all projects without a membership row. `app.is_system_admin` is bound only on the
      server from the verified JWT's user and the admin-grants table, never from a client value,
      and never applies across tenants. Writes stay attributed to the admin as for any user;
      whether non-member reads are access-logged is TD-427 (default: no).
    - **Quality and signing authority unchanged.** `system_admin` alone grants `V`, `C`, `E` and
      project administration (PER-09 equivalent) across all projects, **never `A` or `S`**: no
      hold release, no inspection approval, no cryptographic co-sign. To act as an approver, a
      System admin must hold the qualified persona (PER-02, PER-06, PER-07, PER-11) and the
      credentials on that project; that assignment triggers the audited `sod_warning: true`
      record (below).
  - **`create_projects`** ("Project Admin") — granted and revoked by a System admin only. Allows
    `POST /api/v1/projects`. The creator is granted **PER-09 Project Administrator** on the new
    project automatically, in the same transaction. Revoking `create_projects` removes no
    existing PER-09 membership.
- **PER-09 is the per-project admin persona.** A Project Admin "controls" exactly the projects
  where they hold PER-09; one person can hold PER-09 on several projects, and a project can have
  several PER-09 holders. PER-09 on a project may: edit its settings, toggle *visible in lobby*,
  assign and remove users on that project (any persona, including PER-09, with site scope),
  invite a user by e-mail (creates the account if none exists), and decide its access requests.
  It may not grant tenant-level grants. Memberships on soft-deleted projects are ignored
  everywhere (`load_grants`, allowed project list, admin checks).
- **Admin never confers quality or signing authority.** Neither `system_admin`,
  `create_projects` nor PER-09 gives hold release, inspection sign-off or any `A`/`S` capability.
  Those come only from a qualified persona on that project (edge spec §5.2) **and** the
  credentials the action requires (TD-099/TD-100), checked at the action. An admin who also holds
  such a persona acts under that persona. Global visibility (ADR 0016) changes
  none of this.
- **PER-09 combined with a hold-authority persona** (PER-02, PER-06, PER-07, PER-11) on the same
  project is allowed. The assignment API returns a warning the UI must show before saving, and
  the assignment is written to the audit ledger with `sod_warning: true` (TD-151). The same
  applies to a hold-authority persona assigned to a `system_admin`, and to the `system_admin`
  grant given to a holder of one (ADR 0016).
- **One URL, no subdomains.** Every project is reached under the same origin; the active project
  is chosen in the app, never by host name.
- **Landing after sign-in.** One project → straight into it. Several → the project last used
  per user on this client (falls back to the first by name if it is no longer allowed), with a
  **project picker in the top bar** to switch; portfolio personas keep the All-Projects scope
  (§6). No project → the **lobby**. Users with projects reach the lobby from the picker
  (**"Browse / join another project"**; product owner, 2026-11-14), so joining a further
  project uses the same lobby, code entry and access-request flow as a first project.
- **Lobby and access requests.** The lobby lists only projects whose PER-09 holders set
  *visible in lobby* (default off): code and name only, and only to **internal** users (see
  *Internal staff* below; product owner, 2026-11-14). A user requests access with an optional
  message; the request goes to that project's PER-09 holders (in-app notification), who approve
  it — choosing the persona(s) and site scope — or reject it. One open request per user per
  project; the requester may withdraw it. Approval creates the membership; request, decision and
  decider go to the audit ledger. Projects not visible in the lobby are reached only by an
  **invite** (see *Invites* below), a direct assignment by a PER-09, a team link, or by **project code**: entering an exact project code
  finds that one project and lets the user file an access request; a code never grants access
  by itself; users who already have projects can open the lobby from the picker (user,
  2026-11-13; TD-202). The lobby and code lookup read through a narrow path (code, name, id of
  matching projects only); the membership-based `projects` policy is not widened.
- **Access-request lifecycle (product owner, 2026-11-14).** The lobby is stateful. An
  `access_requests` row is keyed to user + project and has status
  `pending | approved | rejected | withdrawn`; `pending → approved | rejected` by a PER-09 of the
  project (or a System admin), `pending → withdrawn` by the requester; decided and withdrawn rows
  are kept as history. At most one `pending` request per user per project. The lobby shows an
  **"Active requests"** panel: every pending request, plus the latest decided request per project
  from the last 30 days (planner default), each with a status chip. A **rejected** request shows
  the decider's optional rejection reason (≤ 500 characters, visible to the requester) and a
  **"Request again"** action that is enabled once the **re-request cooldown** has passed since the
  rejection: `KERP_ACCESS_REQUEST_COOLDOWN_HOURS`, deployment configuration, **default 24 h**
  (planner default, 0 disables); earlier attempts get 409 with the time they become allowed. A
  withdrawn request has no cooldown. An approved request links to the project. This is how a
  non-member requester learns the outcome: project-scoped notifications cannot reach a user who
  is not a member, so the lobby panel is the authoritative channel. A request names a project
  the user found through the lobby list (internal users) or through a code lookup: the code
  lookup returns a short-lived, user-bound **lookup ticket** (signed, bound to user and
  project, 15 minutes, planner default). A **first** request for a project needs that ticket
  when the caller is external, or internal and the project is not lobby-visible, so a leaked
  project id alone is not enough. A **new request on a project where the caller already has an
  earlier `rejected` or `withdrawn` request needs no ticket** (the earlier row proves the
  project was found legitimately; coordinator decision, PR #73 review round 2), so
  "Request again" after the cooldown works without a fresh lookup. Code lookup is rate-limited per user and deployment-wide; per-client-IP limits follow TD-138. In addition, approve
  and reject also send a transactional e-mail to the requester through the outbound e-mail
  service (§4.6; follow-up task T-0056, not a Pilot-0 dependency: the lobby panel stays the
  authoritative channel).
- **Invites (product owner, 2026-11-14).** A PER-09 (or a System admin) invites a person by
  e-mail to one project with persona(s) and site scope; the account is created (no credentials)
  if none exists, but **the membership is created only when the invite is accepted**. An invite
  carries a random **`invite_token`**: stored only as a hash, **single-use**, **expiring**
  (`KERP_INVITE_TTL_HOURS`, default 168 = 7 days, planner default), **bound to the invited
  e-mail** (accepting it from a session whose user e-mail differs is refused), revocable by the
  inviter's project admins; a re-invite revokes the earlier token. Creation, acceptance, refusal
  (expired, used, revoked, wrong user) and revocation go to the audit ledger.
  - **Existing user, signed in:** opening the invite link shows a one-click **"Join project"**
    confirmation; accepting creates the membership and opens the project.
  - **Existing or brand-new user, not signed in:** the token survives the sign-in, the
    first-password set (below), the magic link (only for invites naming PER-14, §4.2) and, for
    company members only, the Entra ID SSO round trip (§4.5); the server grants the
    membership **immediately after authentication**, in the same request that completes it, and
    the client opens the project. The lobby is skipped. Sign-in lives in core auth, so this runs
    through a core **post-authentication hook** (empty by default; receives the authenticated
    user and the opaque `invite_token`, returns `joined_project_id` or `invite_error`) that the
    admin module registers at start-up, like the magic-link eligibility hook (§4.2); core never
    imports `app.modules` (planner rule, PR #79 review).
  - **External invitees create their own password (product owner, 2026-11-14).** Clients,
    subcontractors and other external users **never use Microsoft sign-in** (they are not on the
    company domain and there is no federation with their tenants, §4.5). They are invited by
    their e-mail address, and a brand-new account (no credentials yet) **sets its first password
    as part of accepting the invite**, from the invite link: one request validates the invite,
    sets the password (policy §4.2 *Local passwords*), accepts the invite (membership) and signs
    the user in. Afterwards they sign in with e-mail + password (Field Workers may also use a
    magic link) and use self-service password reset (§4.2). The proof is the single-use invite
    token **as delivered to the invited mailbox**: the invite e-mail (§4.6) carries the
    `invite_token` and a second secret, the **`setup_token`** (random, stored only as a hash,
    single-use, same expiry as the invite, revoked with it), both in the URL fragment; the
    first-password set needs both. The invite response shown to the inviter contains only the
    `invite_token`, so an inviter who copies the link can never set the invitee's password
    (**product owner, 2026-11-14, confirming the PR #79 planner security rule**: only the
    e-mailed invite can set a first password; the inviter must not be able to take over the
    account). A brand-new invitee who opens a copied link is offered "Send me an e-mail to set
    my password", which runs the self-service reset (§4.2) and then accepts the invite after
    sign-in. Accounts that already have credentials ignore the `setup_token` and sign in as
    usual.
  - The token is a secret in a URL: the invite link carries it in the URL **fragment**
    (`/invite#<token>`, never sent to servers or in `Referer`), the SPA sends it only in POST
    bodies (preview and accept), the invite route sets `Referrer-Policy: no-referrer`, and
    access logs and Sentry scrub `invite_token` fields (planner rule, PR #73 review).
  - **Delivery.** Creating an invite e-mails the link to the invited address (§4.6). The
    inviter also sees the copyable link once (`invite_token` only) as a fallback, e.g. when a
    mail bounced.
  - **Re-send (product owner, 2026-11-14).** For a pending invite (status `pending`, or
    `expired` and never accepted) the project's PER-09 holders and a System admin (the same
    people who may list and revoke the project's invites; any PER-09 of the project, not only
    the original inviter; product owner, 2026-11-14) can **re-send** it
    (`POST /api/v1/projects/{id}/invites/{invite_id}/resend`); expired, never-accepted invites may be re-sent (product owner, 2026-11-14). Re-sending keeps the invited
    e-mail, persona(s) and site scope, **invalidates the previous `invite_token` and
    `setup_token`** (an old link then gets the neutral 410), issues fresh ones with a new expiry
    (`KERP_INVITE_TTL_HOURS` from the re-send), e-mails the invitee again (§4.6, off the request
    path) and shows the re-sender the new copyable link once (`invite_token` only, never the
    `setup_token`). Each re-send is audited (actor, invite, without tokens). Rate limit per
    **project + invited e-mail**, counting invite creations and re-sends together (so revoking
    and re-creating does not reset it): at most one send per 60 s and 5 per 24 h (product
    owner, 2026-11-14), 429 with `Retry-After` beyond that; per-actor limits follow TD-138. Rotation is
    atomic against acceptance (row lock or conditional update): the old token never succeeds
    after a re-send. The project's invite list shows pending and expired (never accepted)
    invites with expiry, last sent time and when re-send is next allowed. Accepted and revoked invites
    cannot be re-sent (409); a new invite is created instead. **No invite code**: invites are links
    only (product owner, 2026-11-14; TD-264 resolved).
  - **Pilot-0 (product owner, 2026-11-14; TD-262 resolved):** invites, the invite e-mail, the
    first-password set at acceptance and self-service password reset are part of the Pilot-0
    gate (T-0146); outbound e-mail (§4.6) is therefore a Pilot-0 dependency.
  - **Team links need no user action.** A System admin linking a team to a project (or adding a
    user to a linked team) grants access directly (ADR 0008); there is no invite or click.
- **Internal staff (product owner, 2026-11-14).** Each user account carries a derived
  `is_internal` flag, recomputed at every sign-in and at account creation: true when the user
  has signed in through the company Entra ID tenant as a **member** (not a B2B guest) and the
  account is linked to that member's Entra object id (§4.5; guest tokens are refused and never
  linked) **or** the account e-mail's domain is on the deployment's list
  `KERP_INTERNAL_EMAIL_DOMAINS` (comma-separated, exact case-insensitive domain match, no
  wildcards). It is never set by hand or from request input. Only internal users see the lobby
  project list. External users (client representatives, subcontractors) reach a project only by
  **project code** (then an access request) or by **invite**. The flag grants no access by
  itself and is not a persona. The e-mail-domain rule works without SSO; the Entra half arrives
  with §4.5.
- **Teams (user, 2026-11-13; TD-228, TD-229, TD-230, ADR 0008).** A team is a **tenant-level**
  named group of user accounts. Only a `system_admin` creates, renames and deletes teams,
  manages their members and links them to projects. A team can be linked to several projects.
  **Each team-project link carries one role for that project**, i.e. one persona of edge spec §5.2,
  e.g. a Trade Supervisor role on project A and a different role on project B. Every member of the
  team gets that access on that project. Links may carry PER-01 to PER-14 except PER-09, plus
  **PER-16 Project Viewer** (read-only, edge spec §5.2). A PER-07 link names its site. **PER-09 and
  PER-15 are strictly individual assignments** (user, 2026-11-13; TD-251). **Effective access to a project
  = the user's individual memberships ∪ the grants from teams linked to it.** Team-derived
  grants count everywhere individual ones do: allowed project list, site filter, 404 for
  non-members, membership-based RLS. Grants are stored in the project membership table keyed by
  project, user, `source_type` (`direct` | `team_link`) and `source_id` (the team link id), so
  direct and team-derived grants coexist. Effective permissions are the union of the active rows
  for (project, user). Only the teams module maintains `team_link` rows, and an idempotent
  reconciliation job heals drift (ADR 0008, accepted). PER-09 still manages the project's individual members. It
  sees team-derived members, marked with their team, but cannot edit or remove them. The
  PER-09 + hold-authority SoD warning above applies to **effective** grants, whatever their
  source: a team link or member add that gives a user with individual PER-09 a hold persona on
  the project (or an individual PER-09 given to a user with a team-derived hold persona) needs
  the same confirmation and is audited with `sod_warning: true` (ADR 0008). Teams are
  for access only. They are not planner crews, rosters or shift teams (TD-073, TD-083), and
  "project team" anywhere in the spec means everyone with access to the project. Saved views
  can be shared with several teams linked to the view's project (register-views §3).
- **Manual notifications (user, 2026-11-13; TD-237).** Only a project's PER-09 may send a
  notification through the API. Modules notify through the notifications service.

### 4.2 Sessions (user, 2026-11-13; TD-121, TD-135, TD-170)

- **Who signs in how (product owner, 2026-11-14).** Company staff: Entra ID SSO (promoted) or
  password (§4.5). External users (clients, subcontractors, other non-members of the company
  tenant): e-mail + password only, never SSO; first password set at invite acceptance (§4.1).
  Field Workers (PER-14), internal or external: additionally magic link (below).
- **Local passwords (product owner, 2026-11-14; NIST SP 800-63B rev. 4 style).** Minimum length
  `KERP_PASSWORD_MIN_LENGTH`, **default 8** characters (product owner, 2026-11-14, replacing
  the earlier planner default of 15; configurable upwards, never below 8), maximum at least 64
  (accepted up to 256); all printable ASCII, spaces and Unicode allowed, NFKC-normalised before
  hashing; **no composition rules, no periodic expiry, no hints or security questions**. Every
  new password is checked against a bundled blocklist of common and compromised passwords plus
  context words (the e-mail and its local part, "kerp", the deployment name); a breached-password
  lookup (k-anonymity range API) is optional and off by default
  (`KERP_PASSWORD_BREACH_CHECK=false`). Rejections say why (too short, too common). Stored as
  scrypt (unchanged). Login failures keep the existing per-account and per-client lockout
  (T-0068). The rules apply to every password set: first password at invite acceptance, reset,
  change, and admin-created accounts.
- **Self-service password reset (product owner, 2026-11-14).** `POST
  /api/v1/auth/password-reset/request` (e-mail) always answers the **same neutral 202**
  (unknown, deactivated or known e-mail alike), with delivery off the request path and equal
  work on every path (as for magic links below). For an active account it e-mails a reset link
  `/reset-password#<token>` (token in the fragment, random >= 256 bits, stored only as a hash,
  **single-use**, **expiring** `KERP_PASSWORD_RESET_TTL_MINUTES`, default 60, planner default; a
  new request invalidates earlier tokens). `POST /api/v1/auth/password-reset/confirm` (token, new
  password) sets the password under the policy above and **revokes every existing session and
  refresh token of the user** (all devices; device enrolment and PIN stay, §4.3); it does not
  sign the user in (they sign in with the new password; planner default). A "your password was
  changed" notice goes to the account's address. Requests are rate-limited per e-mail address
  (the same per-mailbox cap as magic links, keyed by tenant + e-mail: T-0068, product owner
  2026-11-15, TD-224) and per client (TD-138); confirm
  attempts per token are limited. An account without a password (brand-new invitee) can use
  reset to set its first password. Request, confirm and refusals are audited; tokens are scrubbed
  from logs and Sentry.

- **Access token** 15 minutes (unchanged). **Refresh token** lifetime **12 hours**
  (`KERP_REFRESH_TOKEN_HOURS=12`, user decision). Counted from sign-in: rotation issues a new
  single-use token but never extends the session past sign-in + 12 h (user, 2026-11-13). Users sign in again
  daily; Entra ID SSO (§4.5) is the Pilot-0 path for company staff, password for external users,
  and magic link (e-mailed, §4.6) for Field Workers (product owner, 2026-11-14). Every token response (login, magic-link redeem, refresh, PIN
  unlock, SSO callback) states the session end as `session_expires_at`, so a field client knows
  when its offline grace starts (product owner, 2026-11-14).
- **Refresh-token transport (user, 2026-11-13; TD-203).** The web SPA receives and sends the
  refresh token only as an `httpOnly`, `Secure`, `SameSite` cookie scoped to the auth endpoints;
  it never sees it in JavaScript or storage, and `/auth/refresh` is protected against CSRF
  (`SameSite=Strict` plus an Origin check). The Capacitor Android build keeps the JSON-body
  transport, with the token in the keystore-backed store. The server returns or accepts the
  refresh token in a body **only** for requests whose `Origin` is on an explicit native-app
  allow-list in deployment configuration (the Capacitor app origin), which must never contain the
  deployment's own web origin. It never decides by the client-reported device platform, so
  script running on the web origin cannot obtain a refresh token in a body (planner mechanism
  for TD-203; device-bound attestation may be added later).
  **Confirmed (product owner, 2026-11-15; TD-285):** (a) `KERP_NATIVE_APP_ORIGINS` requires
  `KERP_WEB_ORIGIN`: settings refuse a native allow-list without it, because the startup check
  cannot otherwise know the origin the SPA is served from; the API's own origin never gets the
  body transport. (b) The same `Origin` rule selects the body transport (token out and in) on
  **every** token endpoint the Android app uses: login, refresh, magic-link redeem and PIN
  unlock (field workers sign in by magic link and rotate by PIN unlock). On the web origin
  `/auth/pin/unlock` refuses a refresh token in the body (422) and `/auth/refresh` ignores one.
- **Field PIN, hybrid (TD-135, decision 8a).** The PIN belongs to the enrolled device (§4.3),
  not to a server session:
  - **Local unlock.** The app unlocks on the device with the 6-digit PIN against a key held in
    the platform keystore (Capacitor Android build; web PWA local unlock: TD-207). It needs no network and **does not depend on a valid access or
    refresh token**: an expired or revoked-by-expiry session does not stop a worker opening the
    app, reading downloaded work and queueing changes offline.
  - **Renewal after idle.** Online, the server asks for the PIN only when a session is renewed
    after it was idle longer than `KERP_PIN_IDLE_MINUTES` (default 30 (user, 2026-11-13)). Ordinary rotation inside
    the idle limit needs no PIN. A PIN alone never starts a session.
  - **Lockout** numbers unchanged from T-0009 (4 immediate tries, then 30 s doubling to 15 min),
    with the attempt counter kept on the server per device (user decision). The 10th wrong PIN
    ends PIN unlock until an online sign-in, without revoking the session or device; failures
    made offline are counted locally with the same numbers and reported on the next contact;
    PIN failures never delete local data, only a remote wipe does (user, 2026-11-13).
- **Offline window (TD-170).** A tenant setting (§4.4), default **7 days**, maximum **14**. While
  inside the window the device works offline with local unlock. Once the window passes without a
  successful server contact, the app keeps all queued data but allows no new edits until the user
  signs in online (user, 2026-11-13, TD-206; interim until the native Android app with a normal login launches, TD-210). Sync and the outbox are built for 14 days of queued work.
- **Daily re-login vs offline.** The 12-hour session limit applies only to server calls. On
  reconnect the user signs in (password, SSO, or magic link) and the outbox drains under the new
  session. Queued mutations carry their author; the server accepts them only from a session of
  the same user and device.
- **Offline grace for field tablets (product owner, 2026-11-14; wins over the "whole offline
  window" wording that stood here before).** When the 12-hour server session ends while the
  device is offline, the device PIN keeps unlocking cached work and queueing mutations **until the
  end of the next shift: the offline grace, default 24 h after the server session ended**
  (`offline_grace_hours`, deployment setting §4.4, 1 to 24 × `offline_window_days`, planner
  bounds). After the grace has passed, or as soon as the device is back online with the session
  ended, a fresh sign-in (magic link, SSO or password) is required before anything syncs. Until
  then the app allows no new edits (downloaded work stays readable, as for the offline window);
  queued mutations are **kept** and sync after re-authentication. The effective edit limit while
  offline is the earlier of *session end + offline grace* and *last successful contact + offline
  window*; with the defaults (12 h session, 24 h grace, 7-day window) the grace binds, i.e. a
  worker can keep working offline for at most about 36 h after their last sign-in. The grace is
  enforced on the device with a **non-decreasing elapsed-time counter that survives reboots**
  (planner rule, PR #73 review): the device persists `offline_elapsed_ms` (time elapsed since
  `session_expires_at`, as of the last server time anchor or check) and `last_seen_wall_time`.
  Within one clock session (§6.4.1) the counter advances by monotonic time; after a reboot
  (new `clock_session_id`, no anchor) it advances by `max(0, wall_now − last_seen_wall_time)`.
  Both values are updated at every unlock, every queued action and at least once a minute while
  the app runs. A clock moved back therefore adds nothing (grace not extended), a clock moved
  forward only shortens the grace, and a reboot neither blocks field work nor resets the
  counter. The server does not reject queued mutations because of the grace (they carry author
  and device and arrive under a fresh session); it **flags** a mutation whose reconstructed
  `occurred_at` (§6.4.1) falls after `session_expires_at + offline_grace_hours` for review
  (`grace_exceeded`, possible tampered or rooted device) without dropping it. **Interaction with
  the offline window confirmed (product owner, 2026-11-14; TD-257 resolved):** the grace binds
  edits; `offline_window_days` stays only as an upper bound (and the sizing of sync and outbox
  for 14 days). With the defaults about 36 h offline after the last sign-in is enough; the window
  does not extend the grace for multi-day offline sites.
- **Magic links for Field Workers only (product owner, 2026-11-14; TD-136).** Sign-in by magic
  link (`POST /api/v1/auth/magic-link`, redeem) is limited to users who hold **PER-14 Field
  Worker** on at least one project (effective grants, direct or team-derived, §4.1; memberships
  on soft-deleted projects ignored), or who carry a valid pending invite (§4.1 Invites) for
  their e-mail that names PER-14. For anyone else the request gets the **same neutral 202** as
  an unknown e-mail and no link is issued or sent (no account or persona enumeration; the
  refusal is logged and audited without the token). Redeem re-checks eligibility, so a link
  issued before the user lost their last PER-14 grant is refused. In the Pilot-0 gate (product
  owner confirmed, 2026-11-14; T-0057). **No timing oracle (planner rule, PR #79 review):** link delivery runs off the request path (SAQ job or post-response
  task), so the 202 never waits for the mail transport, and every path (unknown, inactive,
  ineligible, eligible) does the same work inside the request, including an equal (dummy)
  store write or deferring the store write with delivery. Eligibility is read in core auth
  (`load_grants`); the invite branch is a core extension point that the module owning invites
  registers, so core never imports a module (CLAUDE.md §6). All other users sign in with
  password or, for internal staff, Entra ID SSO (§4.5). The rule covers the KERP sign-in magic
  link only; portal-scoped record links for people without an account
  (`modules/portal-plan-review.md` §1.1) are a separate mechanism and stay as specified.
  **Client Portal Users (PER-15) sign in by password**, or by Entra ID SSO if they are company
  members; they get no KERP sign-in magic links (product owner confirmed the planner reading,
  2026-11-15; TD-261).
  **Request lockout keyed per mailbox (product owner, 2026-11-15; TD-224).** The magic-link
  request lockout stays keyed by tenant + e-mail (T-0068): 4 immediate requests, then a lock
  doubling from 1 minute to 15 minutes, counted over a 1-hour window. It is also the
  mailbox-flood cap (how many e-mails one address can be sent), so it is not re-keyed per
  client. Accepted consequence: anyone who knows a Field Worker's e-mail can delay that
  worker's link; the worker can still sign in by password where they have one, or unlock
  offline with the PIN within the grace (offline grace, above).

- **Support impersonation (user paste "T-0076", 2026-11-21) — pending decision TD-426; ADR 0015
  proposed; tasks T-0277–T-0280 blocked.** Nothing below may be built until TD-426 is decided.
  A `system_admin` may start a time-boxed session as a non-admin user to see that user's exact
  projects, personas, capabilities and site scope:
  - `POST /api/v1/admin/users/{id}/impersonate` (`system_admin`, reason required): never a
    deactivated user (422) or another `system_admin` (`403 CANNOT_IMPERSONATE_SYSTEM_ADMIN`), never
    nested. It issues a separate **impersonation token** (T-0008 keys; `sub` = target, `imp` =
    admin, own `sid`/`jti`) that expires after `impersonation_max_minutes` (runtime registry
    §4.7, default 30, 5–60) or with the admin's session, whichever is earlier, and has **no refresh
    token** (an exception to the 15-minute access rule above, single-session and revocable at
    once). It is bound to no device and refused by device-bound field endpoints (§4.3).
  - The request context is built from the target's memberships with `app.is_system_admin` false,
    so impersonation never widens what the admin already sees (ADR 0016).
  - `POST /api/v1/auth/impersonation/exit` revokes the token's `jti` and `sid` (Redis); the
    admin's own session was never replaced and resumes. Admin logout, force sign-out (§4.8) or
    loss of the grant ends it as well.
  - **Guardrail**, enforced centrally (one route classification plus the T-0010 capability
    check, covered by a contract test over all routes; unclassified mutating routes are refused):
    `403 IMPERSONATION_ACTION_FORBIDDEN` for any `A` or `S` action (sign-off, signing ledger,
    client co-sign, inspection submit/approve, hold release, override decisions), password, PIN
    and device changes, admin grants, memberships, invites, access-request decisions, teams,
    settings, project creation and `/api/v1/admin/*` mutations. **Planner default (TD-426 option
    (c)): read-only "view as"**, every mutating request refused except exit and logout.
  - **Audit**: `impersonation.started` (admin, target, reason, IP, user agent, expiry),
    `impersonation.ended`, `impersonation.denied` in the chain `admin-impersonation:<tenant>`;
    ledger entries written during impersonation carry `created_by` = target and
    `impersonated_by_user_id` = admin inside the hash (ledger hash v3, ADR 0015). Impersonation
    never updates the target's last login, activity, presence or last-used project.

### 4.3 Devices (user, 2026-11-13; TD-164, decision 17a — Pilot-0 scope)

- **Device registry.** Every field client (Capacitor Android build, field PWA) enrols on first
  sign-in: a client-generated `device_id` (UUID), platform, OS and app version, enrolled and
  last-seen/last-sync times, status `active | revoked`. Sessions are bound to the device.
- **Remote revoke and wipe.** A System admin, a PER-09 holder of a project the device's user
  belongs to, or the user may revoke a device (user, 2026-11-13). A wipe clears the whole device, so a PER-09
  of one project can wipe unsynced work of the user's other projects. As a planner addition
  (mitigation for Q4a), the revoke dialog warns about that when the device's user has other
  projects, and every revoke is audited with the actor and their role. Revoke kills every session of the device at once;
  on its next contact the server answers `401` with `code: DEVICE_REVOKED` and the client wipes
  its local store (records, outbox, media, keys) and confirms the wipe. Unsynced work on a
  revoked device is lost; the admin UI shows the last-sync time before confirming.
- **One account per device id, Pilot-0 (product owner, 2026-11-15; TD-283).** A `device_id`
  belongs to the account that enrolled it; another account enrolling the same id gets `409`, and
  a session is bound to at most one device. The client keys its local store (records, outbox,
  media, keys) **per account**, so a revoke wipes only that account's data on the device; a
  tablet used by several accounts uses one `device_id` per account. Whether pilot crews share
  tablets is to be confirmed with the pilot team (TD-293); if they do, a shared-device model
  (a device row per `(device_id, user)`, and a revoke that wipes the whole physical device with
  a warning listing every account on it) is added before the pilot.
- **Device hardening before the pilot (product owner, 2026-11-15; TD-286, TD-291; T-0080).**
  Accepted for development, fixed before pilot go-live: enrolment issues a **per-device wipe
  secret** once (returned only then, stored only as a hash) and the unauthenticated wipe
  confirmation requires it (a wrong or missing secret gets the same answer as an unknown
  device); sign-in checks credentials **before** any device-revocation check, and refusals for
  unknown, active and revoked device ids are uniform to unauthenticated callers (no device-status
  oracle). `DEVICE_REVOKED` for authenticated or device-bound sessions is unchanged.
- **Audit.** Ledger entries made through a device carry its `device_id`.
- **After the pilot:** biometric unlock, certificate pinning and per-device sync-health views
  (TD-204).

### 4.4 Deployment settings and region (user, 2026-11-13; TD-170, TD-171, TD-185)

- **Tenant (deployment) settings**, edited by System admins: offline window (days, default 7,
  1–14), and the defaults for new projects: status `ACTIVE`, site timezone `Australia/Perth`,
  currency `AUD`, locale `en-AU`. Also `max_checkout_clock_skew_s` (default 5; user, 2026-11-13):
  the largest device clock skew allowed when taking work offline (§6.4.1). Also
  `offline_grace_hours` (default 24; product owner, 2026-11-14): how long after the server
  session ended a device that is still offline may keep unlocking with the PIN and queueing
  changes (§4.2). Project status values are `ACTIVE`, `ON_HOLD`, `CLOSED`.
- **Region is a per-deployment setting**, not code: AWS `ap-southeast-2` (Sydney) for Australian
  deployments; `eu-central-1` (Frankfurt) only if an EU deployment is ever needed. S3, SES and
  any other regional AWS client read the deployment's region; nothing hard-codes a region.

### 4.5 Entra ID SSO — identity only (product owner, 2026-11-14; TD-043, TD-003)

Staff sign in with the company Microsoft Entra ID tenant. For the pilot SSO proves **who** the
user is and nothing else. **SSO is for company staff only (product owner, 2026-11-14):**
external users (clients, subcontractors) never use Microsoft sign-in; there is no federation
with their tenants, and KERP never offers SSO to them as a way in; they sign in with e-mail +
password (§4.1, §4.2).

- **Protocol.** OpenID Connect authorization-code flow with **PKCE** (S256) against the
  company tenant only: the authority and issuer are pinned to
  `https://login.microsoftonline.com/<KERP_ENTRA_TENANT_ID>/v2.0` (never the `common` or
  `organizations` endpoints). Configuration (deployment, never in the repo):
  `KERP_ENTRA_TENANT_ID`, `KERP_ENTRA_CLIENT_ID`, the redirect URI(s), and the client secret or
  certificate from the secret store. Unset tenant or client id → the SSO provider is disabled
  and the sign-in screen does not offer it.
- **App registration.** One Entra app registration per deployment, single-tenant, one web
  redirect URI `https://<deployment>/api/v1/auth/sso/entra/callback` (also used by the Android
  build, see *Native handoff*), scopes `openid profile email`, and the **optional ID-token
  claims `email`, `acct` and `xms_edov`** (needed for account matching below). No API
  permissions, no group or app-role claims are requested. (Security review of PR #73,
  2026-11-14: the hardening in this section is the planner's implementation of the product
  owner's identity-only decision.)
- **Flow.** `GET /api/v1/auth/sso/entra/start` creates `state`, `nonce` and the PKCE verifier,
  keeps them server-side (Redis, ≤ 10 minutes, single-use) together with the optional
  `invite_token` (§4.1 Invites) and the device id of an enrolling field client, and redirects to
  Entra. **Browser binding (login-CSRF protection):** `/start` also sets a random pre-auth
  cookie (`httpOnly; Secure; SameSite=Lax`, path `/api/v1/auth/sso`, lifetime = the state's)
  and stores only its hash in the `state` entry; the callback refuses (and consumes the state)
  when the cookie is missing or its hash does not match, and clears the cookie either way. So a
  callback URL started and completed by someone else cannot sign a victim's browser in, nor run
  an `invite_token` under another account. The callback exchanges the code (with the verifier)
  and **verifies the ID token**: signature against the tenant's JWKS (cached; on an unknown
  `kid` refetched at most once per minute deployment-wide, so forged `kid`s cannot hammer the
  Microsoft endpoint), `iss` equal to
  the pinned issuer, `tid` equal to `KERP_ENTRA_TENANT_ID`, `aud` equal to the client id,
  `exp`/`nbf`/`iat` with at most 2 minutes of clock leeway, and `nonce` equal to the stored one.
  Any failure → sign-in refused, nothing created, the attempt logged without token contents.
- **Members only.** Only **member** accounts of the company tenant may sign in through SSO. A
  token is treated as a guest, and the sign-in refused ("sign in with the method from your
  invitation"), when the `acct` claim is not `0` (missing counts as not proven) or an `idp`
  claim is present and differs from the pinned issuer. Guests (client representatives,
  subcontractors added to the company tenant) use password (or magic link if they are Field
  Workers, §4.2) like other external users and never become internal through Entra. **Deployment prerequisite:** the `acct`
  optional claim must be enabled on the app registration; without it **every** SSO sign-in is
  refused. Such refusals are logged with the distinct reason `acct_claim_missing`; the server
  logs a start-up warning naming the required optional claims whenever SSO is enabled, and the
  health check reports a warning when an SSO sign-in was refused for `acct_claim_missing` in the
  last 24 h. The deploy docs and the Pilot-0 runbook (T-0146) include a test sign-in that checks
  the optional claims.
- **Account matching by verified e-mail.** The first SSO sign-in matches an **existing active**
  KERP account by the `email` claim (case-insensitive) **only when that e-mail is verified**:
  `xms_edov` is `true`, or its domain is on `KERP_INTERNAL_EMAIL_DOMAINS` (§4.1). There is
  **no `preferred_username` fallback** (Microsoft does not support it for authorization) and no
  match on an unverified or missing `email`; such sign-ins are refused. A match links the
  account by storing the Entra object id (`oid`, with `tid`); later sign-ins match by `oid`
  only and ignore the e-mail claim. An account already linked to a different `oid` is refused. **No account → sign-in
  refused** with "No KERP account for this e-mail, ask your administrator"; KERP never creates
  accounts from SSO (safer default: accounts come from System admins, invites and provisioning,
  §4.1). Deactivated account → refused. Link, refusal and sign-in go to the audit ledger.
- **No group-to-role mapping.** Entra groups and app roles are ignored. Personas, PER-09, the
  tenant-level grants and team links stay in KERP (§4.1). A successful (member) SSO sign-in sets
  `is_internal` (§4.1 Internal staff) and nothing else about access.
- **Native handoff (Capacitor Android build).** The app opens `/start` in the system browser
  (Custom Tabs) with its `device_id` and the S256 hash of an **app-generated verifier** (kept in
  the keystore-backed store), both stored in the `state` entry; the pre-auth cookie lives in that
  browser. After the callback verified everything above, the server never puts tokens in a
  redirect: it creates a **single-use exchange code** (random, stored hashed, ≤ 60 s, bound to
  the user, `device_id` and the verifier hash) and redirects to the app's **verified Android
  App Link** `https://<deployment>/app/sso-return?code=…` (https, `/.well-known/assetlinks.json`
  with the app's signing-certificate fingerprint; never a custom URL scheme). The app redeems it
  with `POST /api/v1/auth/sso/entra/exchange` (`code`, `verifier`, `device_id`) from an
  allow-listed native origin (§4.2, T-0043) and receives the native body transport. A code
  redeemed twice, after expiry, with a wrong verifier or a different `device_id` is refused,
  and a reuse attempt is audited.
- **Session.** SSO is a second `AuthProvider` behind the existing seam (TD-003); on success it
  issues the **same KERP session** as a password sign-in: 15-minute access token, refresh token
  ending 12 h after sign-in (§4.2), cookie or native body transport, device enrolment and PIN
  set-up for field clients (§4.3).
- **SSO promoted, password kept (product owner, 2026-11-14).** Internal staff **should** sign in
  with Entra ID SSO, but SSO is **not enforced**: password sign-in stays allowed for internal
  users too, and there is no SSO-only mode and no lockout of password login by e-mail domain or
  `is_internal`. When SSO is enabled the sign-in screen (web and Android) shows "Sign in with
  Microsoft" as the **primary, default option**, with password sign-in available on the same
  screen as a secondary option, together with "Forgot password?" (§4.2); the magic-link request
  stays available for Field Workers (§4.2). External users use password (and magic link if
  PER-14), never SSO.
- **Pilot-0.** Live SSO in the pilot deployment satisfies the daily re-login dependency of §4.2
  for company staff and is part of the Pilot-0 gate (T-0146).

### 4.6 Outbound e-mail (user, 2026-11-10, TD-084; Pilot-0 dependency since 2026-11-14)

The user chose AWS SES with the company domain's DKIM, i18n templates, bounce handling and a
send log (2026-11-10). Since the product owner put invite and password-reset e-mails into the
Pilot-0 gate (2026-11-14), the transport is a Pilot-0 dependency. Planner defaults:

- **Core service, not a module:** `app/core/mail/` with one `send_mail(template_key, to,
  context, locale)` entry point usable by core auth (magic links, password reset) and by modules
  (invites, access-request decisions). Transport behind a protocol: **SES** (region from the
  deployment, §4.4; sender address and configuration set from deployment configuration;
  credentials from the instance role / secret store, never the repo) in production, a
  **fake transport** (in-memory, inspectable) in tests, and a log transport in development
  that never runs in production. Production start-up fails if mail is not configured.
- **Always asynchronous:** callers enqueue a send on the SAQ **interactive** queue after commit;
  no request ever waits for SES (timing, §4.2). The SAQ client lives in the `jobs` module today;
  core never imports a module, so the enqueue reaches core through a hook the jobs module
  registers at start-up (or a core-level queue client), never by a core import of `app.modules`.
  Retries with back-off on transient SES errors;
  a failed send never rolls back the business action.
- **Templates** through the i18n layer (`t()` keys, locale of the recipient or the deployment
  default), plain text plus simple HTML, no remote images or tracking pixels, no project data
  beyond code and name, secrets only inside link fragments.
- **Send log** table (`BaseRecordMixin`, tenant RLS; System admins read it): template key,
  recipient (hashed for lookups plus masked for display), status (`queued | sent | bounced |
  complained | failed | suppressed`), provider message id, timestamps; never the link or the
  token. **Bounces and complaints** arrive through SES notifications (an SQS poll job, or SNS
  to a public HTTPS endpoint) and put the address on a **suppression list** (also
  `BaseRecordMixin` with RLS); sends to a suppressed address are skipped and logged as
  `suppressed`. A valid SNS signature alone does not prove the message came from our SES
  (anyone can sign messages from their own topic), so the SNS endpoint (planner rule, PR #79
  review): accepts only messages whose `TopicArn` equals the configured topic
  (`KERP_MAIL_SNS_TOPIC_ARN`); verifies the signature (SignatureVersion 2, SHA-256) with a
  signing certificate fetched only from an `https://sns.<region>.amazonaws.com/` URL (host
  checked exactly, region = the deployment's); honours a `SubscriptionConfirmation` only for the
  configured topic (others are refused and logged, never confirmed); and writes suppression
  and send-log rows under the deployment's hidden tenant from settings
  (`KERP_DEFAULT_TENANT_ID`, §4.1), never a tenant or account value from the payload (the
  endpoint has no JWT). Refusals are logged without the payload body. The magic-link, reset and invite
  flows still answer neutrally when a send is suppressed.
- **Deployment prerequisites** (runbook, T-0146): verified sending domain with DKIM (and SPF /
  DMARC alignment), SES production access (out of the sandbox) in the deployment region, the
  bounce/complaint notification wiring.
- Portal extras of TD-084 (PDF attachments, digests: `modules/portal-plan-review.md`) build on
  this service in their own epics.
- **Channels** are in-app, push (Android, T-0144) and e-mail. No SMS (TD-413). **No
  Teams/WhatsApp messaging bridge** (user decision 2026-11-21): KERP neither reads field input
  from nor sends notifications to chat platforms.

### 4.7 Admin operations, communications and runtime settings (user, 2026-11-21; planner defaults)

The user supplied a spec for four features on 2026-11-21 (it asked for "§4.5"; numbered §4.7
because §4.5 is Entra ID SSO). Where it disagreed with earlier sections, the earlier sections win
and the difference is an open TODO; the planner defaults below apply until the user decides.
Tasks: T-0218–T-0232 and T-0248; the user directory is §4.8; the project archive is in `modules/documents-and-mdr.md` (epic E-01-02).

**System console** (`/api/v1/admin/system/*`, T-0219–T-0225). Only `system_admin`; every other
caller, PER-09 included, gets **403** (an admin surface, not a project record). Every action is
written to the audit ledger. Record reads use the normal project RLS, which since ADR 0016
shows a `system_admin` every project of the tenant without a membership (§4.1; TD-396
resolved); no console endpoint uses a bypass role. The project archive follows the same rule
(`system_admin` exports, downloads and restores any project of the tenant;
`modules/documents-and-mdr.md` §Access). Simplified by ADR 0016 (system_admin global
visibility).

- **Deployment stats** (T-0219): counts and byte totals only, never rows: projects (active,
  soft-deleted), users (active, deactivated), enrolled devices, and per module the counts it
  contributes (asset nodes, Items, inspections when those modules exist; TD-403). Each module
  contributes through its own `service.py` (a stats-provider registry, no cross-module table
  access). Counts run under the caller's normal RLS, which shows a `system_admin` every project
  (ADR 0016), so no `SECURITY DEFINER` function is needed; a table that still hid rows would get
  a TODO, never a bypass. Object-store totals (object count and bytes per storage class
  `STANDARD | GLACIER_IR | DEEP_ARCHIVE`) come from a `batch` SAQ job (listing is slow) and are
  shown with their "as of" time. **Health probes**: PostgreSQL, Redis, object store, malware
  scanner (ClamAV `PING`); each with a short timeout, answering status and latency only, never
  host names, connection strings or credentials.
- **Queue monitor** (T-0220): KERP's own endpoints, not SAQ's web UI (it runs its own server
  without KERP auth). Per queue (`interactive`, `batch`): depth, active, scheduled and failed
  counts; the cron jobs (they run on the batch worker; there is no separate cron queue) with
  last and next run; failed jobs with kind, queue, project code, attempts, error class and a
  truncated message, **never job kwargs or payloads** (mail payloads already stay out of kwargs,
  §4.6). Retry and abort are audited.
- **Upload quarantine monitor** (T-0218, T-0221): uploads in `verifying` (scan running or
  stuck), `rejected` (reason code: `infected` with the scanner signature, `size_mismatch`,
  `type_mismatch`, `sha256_mismatch`, `missing_object`, `verify_failed`, and the media-probe codes
  of T-0169) and `expired`. The real scanner is ClamAV (`clamd`, `INSTREAM`) in the worker,
  replacing the stub (TD-211). Rejected bytes stay deleted at rejection (T-0016): nothing
  infected is kept at rest, so there is nothing to purge (TD-397). Actions: re-queue the check of
  a file stuck in `verifying` whose staging object still exists; run a project's orphan sweep
  now. The list reads `oe_uploads_file` under the normal project RLS, which shows a
  `system_admin` full rows (project code, file id, original filename, size, SHA-256, status,
  reason, signature, uploader id, times) of **every project** of the tenant (ADR 0016), plus
  counts per project, status and reason code from the same RLS read; no bypass role and no
  count-only function (simplified by ADR 0016).
- **Audit explorer** (T-0222): read-only over the hash-chain ledger (§4); no endpoint ever
  changes a ledger row. Reads under normal RLS: tenant-level chains (grants, users, devices,
  deployment settings, mail suppression, teams, the system console) and the project chains
  of every project (ADR 0016; TD-396 resolved). Filters: actor, device, chain-key
  prefix (entity type), payload `event` key or flag such as `sod_warning` (TD-402), impersonator
  (`impersonated_by_user_id`, ADR 0015, once built), date range.
  **Verify chain integrity** recomputes the ledger rules (contiguous `seq`, `prev_hash` links,
  recomputed `content_hash`): one chain synchronously, all readable chains as a `batch` SAQ job
  with a report. Anchoring chain heads outside the database stays TD-058.
- **Legal hold** (T-0223): a per-project `legal_hold` flag with reason, set by and set at, on the
  project table (core-table change: ADR). Set and cleared only by a System admin, reason
  required, audited in the tenant-level chain `admin-system:<tenant>` (like
  `deployment-settings:<tenant>`; a System admin need not be a member of the project). While
  it is on: soft-deleting the project answers **409 `LEGAL_HOLD_ACTIVE`**, and the project's archives (documents-and-mdr) cannot be deleted. KERP
  has no hard purge of project data (retention rules: TD-175; TD-401).

**Communications** (Admin → Communications, T-0226–T-0229), on the outbound e-mail service
(§4.6):

- **Delivery ledger** (T-0226): the §4.6 send log, extended additively (core table: ADR):
  status `delivered` from SES delivery notifications through the same hardened SNS endpoint;
  nullable `recipient_user_id`, `project_id`, `broadcast_id`. The recipient stays hashed plus
  masked, never stored in clear (§4.6 wins over the pasted `recipient_email` column). Stats:
  totals, delivery rate, bounces, complaints and failures by period and template; a searchable
  history (template, status, date, exact-address lookup by hash). System admins see all;
  PER-09 sees only the stats of broadcasts to its own projects.
- **Automated templates** (T-0227): the registered templates (§4.6) stay the shipped defaults
  in the i18n catalogues. A System admin overrides, per template and locale, the subject and the
  text blocks (intro, outro, action label) as plain text with only the placeholders the template
  declares (for example `{user_name}`, `{project_code}`); the renderer still escapes every value
  and places the action link, so an admin can never move or reveal a secret; no raw HTML,
  remote images or tracking pixels (TD-400). Revisions are kept; preview with synthetic sample
  values; a test send goes only to the admin's own address; reset restores the shipped default;
  the next send uses the change without a redeploy. Templates added by later tasks (handover,
  inspection returned/overdue, co-sign confirmation/reminder, archive ready/restored) appear when
  they are registered. The magic-link template is sent only to Field Workers (PER-14, §4.2).
  Editor details (product intake 2026-11-21, planner default; user to confirm): templates are
  listed grouped by audience (internal staff, client, witness / external party, deployment-wide);
  the placeholders a template declares are listed beside the editor and can be inserted from
  there; a template marked required (sign-in, invite, password reset, RFI call-outs) can be
  reworded but not saved with an empty subject or empty text blocks (422); a mail-health banner
  on the screen says when sending is not possible (mail not configured, sender not verified,
  bounce wiring missing), from the same checks as the health probes, never showing credentials.
- **Broadcast e-mail** (T-0228): a System admin targets all active users, one or more projects,
  an audience category (`internal` = `is_internal`, `external` = not internal, `client` = PER-15
  holders) or personas (PER-01–PER-16); a PER-09 only users with an active membership on its own
  projects, by persona or category (TD-399). The audience is resolved on the server; the sender
  sees a count before sending, never a list of addresses. Body is plain text. Dispatch on the
  `batch` queue, one `send_mail` per recipient (send-log rows carry the `broadcast_id`),
  suppression respected, deactivated users excluded, rate-limited, audited. Broadcast mails
  carry `List-Unsubscribe` (one-click) and an opt-out that applies to broadcasts only;
  transactional mail is never opted out.

**Runtime settings registry** (T-0230–T-0232) extends §4.4 (T-0044). A code-declared registry
(key, category, type `int | float | bool | string | enum | int_list`, bounds, default,
client-visible flag) with one stored value per tenant and key; System admins edit; values out
of bounds get **422** (never clamped); a deployment-config value can cap an entry (the entry can
never exceed it); every change is cached in Redis with pub/sub invalidation and audited as
`{key: [old, new]}`. **No secret is ever a registry entry** (keys, passwords, API secrets,
tokens): registration refuses them. The §4.4 settings become registry entries;
`GET/PATCH /api/v1/settings/deployment` keeps its contract.

| Entry | Default | Bounds (planner) | Read by |
|---|---|---|---|
| `offline_window_days`, `offline_grace_hours`, `max_checkout_clock_skew_s`, new-project defaults | §4.4 | §4.4 | T-0044 consumers |
| `default_backdating_threshold_days` | 7 | 0–365 | new projects (T-0140 project setting) |
| `max_upload_size_mb` | 100 | 1 – `KERP_UPLOAD_MAX_BYTES` | uploads (T-0016) |
| `presigned_url_ttl_minutes` | 15 | 1 – 60 (capped by `KERP_UPLOAD_URL_SECONDS`) | uploads |
| `max_photo_annotations` | 50 | 1–50 | forms (T-0156) |
| `tablet_proxy_max_px`, `tablet_proxy_quality` (category `uploads_media`; user, 2026-11-21) | 1280, 72 | 640–2560, 40–95 | image derivatives (T-0247) |
| `report_image_max_px`, `report_image_quality` (category `uploads_media`; user, 2026-11-21) | 1024, 68 | 480–2048, 40–95 | image derivatives (T-0247) |
| `max_video_duration_s`, `max_video_size_mb` | 60, 100 | 5–600, 1–500 | forms, media probe (T-0167, T-0169) |
| `max_audio_duration_s`, `max_audio_size_mb` | 300, 10 | 5–1800, 1–100 | forms, media probe |
| `cosign_pin_token_ttl_minutes` | 5 | 1–15 | signing (T-0162) |
| `provisional_cosign_client_reminder_hours`, `provisional_cosign_escalation_hours` | 24, 72 | 1–720 (client < escalation) | signing (T-0174) |
| `impersonation_max_minutes` (not client-visible; pending TD-426) | 30 | 5–60 | support impersonation (§4.2, T-0277) |
| `signing_reauth_ttl_minutes` (not client-visible; enterprise roadmap 2026-11-21) | 5 | 1–15 | signing attestation (T-0289) |
| `api_key_rate_per_minute` (not client-visible) | 600 | 60–6000 | API keys (§4.9, T-0308) |

Read-only **deployment configuration** (shown, never edited at runtime; TD-046, TD-398):
refresh-token lifetime (`KERP_REFRESH_TOKEN_HOURS`, 12), PIN idle (`KERP_PIN_IDLE_MINUTES`, 30),
the PIN lockout schedule (T-0009), the deployment region (§4.4, default `ap-southeast-2`), the
mail sender and reply-to (§4.6), the malware scanner, and the AI provider (`none` until the
assistant, Phase 5). **Client bootstrap** (API T-0231, phase 0; client consumption T-0248,
phase 1): `GET /api/v1/settings/client` (the paste's
`/api/v1/config/client`, under the settings module's mount) for any signed-in user returns the
client-visible entries only; web and Android read it at sign-in and at every sync.

**Feature flags** (user, 2026-11-21, enterprise roadmap; T-0306; TD-439). Category
`feature_flags` of the registry, type `bool` only, declared in code by the owning module. A flag
may be overridden per project (`oe_settings_project_value`, project RLS; only entries marked
`project_overridable`); effective value = project override → tenant value → code default. Only a
`system_admin` sets flags; PER-09 reads its projects' effective flags. **Staged rollout:** a flag
is switched on for named pilot projects first, then tenant-wide (an explicit project `false`
still wins). A flag gates availability only (UI entry points; the feature's endpoints answer 404
`FEATURE_DISABLED` while off); it never relaxes authorization, `run_checks` or RLS, and turning
it off deletes nothing. The client bootstrap returns the effective flags of the active project.

### 4.8 User directory, account state and presence (user, 2026-11-21; planner defaults)

User addendum of 2026-11-21 to the admin module (T-0014 is done, so new tasks T-0243–T-0246).
The user table is the core `auth_user` (the addendum's `oe_identity_user`); new columns on it are
additive and need an ADR (CLAUDE.md §6).

- **Account state** (T-0243; TD-406). New nullable columns `first_login_at` (set once, by the
  first successful password, SSO or magic-link sign-in), `last_login_at` (every successful
  sign-in) and `last_active_at` (token refresh and authenticated requests, written at most once
  per 5 minutes per user through a Redis claim, never on the request's critical path). The
  status is **derived**, not stored: `deactivated` when `is_active` is false (deactivation
  already blocks sign-in and every token at once, and the last active System admin cannot be
  deactivated); `invited` when `first_login_at` is null; otherwise `active`. A PIN unlock
  continues a session and is activity, not a sign-in.
- **Sessions and presence** (T-0243). Core auth keeps a per-user index of live sessions in
  Redis (session id, expiry = sign-in + 12 h, §4.2), maintained at sign-in, logout and revoke.
  `active_session_count` = live, non-revoked sessions. `presence` is computed per request:
  `online` when `active_session_count > 0` and `last_active_at` is within 15 minutes; `idle` when
  sessions are live but the last activity is older; `offline` with no live session or a status
  other than `active`. Presence is an approximation (5-minute write throttle) and is shown only
  in the admin user directory, never to other users.
- **User directory** (`GET /api/v1/admin/users`, `system_admin`): name, e-mail, status,
  presence, session count, first/last login, last active, project count (live memberships of
  live projects, any source), tenant grants; filters status, presence, grant, project; search by
  name or e-mail.
- **Force sign-out** (T-0244): `POST /api/v1/admin/users/{id}/revoke-sessions` (`system_admin`,
  reason required) revokes every live session of the user in the revocation store at once; the
  next request or refresh is `401` and the user signs in again. It is **not** a device revoke:
  no `DEVICE_REVOKED`, no wipe (§4.3); an offline device keeps its outbox, which drains after the
  next sign-in. Audited with `event: user_sessions_revoked`, actor and reason. Force sign-out,
  deactivation, reactivation and every sign-in are also persisted as durable standing events in
  the identity service (T-0310), because the session index expires; offline formal sign-offs
  are checked against them (quality-and-inspection.md §Signing attestation).
- **Resend invite** (T-0244; TD-407): only for `invited` accounts. Re-sends the user's pending
  project invites through the invite re-send of §4.1 (rotation, audit, rate limits unchanged),
  else sends the set-password (reset) e-mail of §4.2 to an account without a password. Never a
  sign-in magic link (Field Workers only, §4.2); e-mail goes through core mail (§4.6).
- **Memberships across projects** (T-0245): `GET/POST/PUT/DELETE
  /api/v1/admin/users/{id}/memberships` lets a System admin manage the user's individual
  memberships on any project, and a PER-09 only on its own PER-09 projects (it never sees the
  user's other projects: 404 for them). Same rules as the per-project members API: personas and
  PER-07 site scope, team-derived rows shown read-only with their team, the PER-09 + hold-persona
  SoD warning returned before commit and audited `sod_warning: true` (§4.1, TD-151), PER-15 and
  PER-09 individual only.

### 4.9 Enterprise identity and access (user, 2026-11-21, enterprise roadmap; planner defaults)

The user's enterprise roadmap of 2026-11-21, applied through the coordinator's assessment that
the user approved. E-signature and delegation are in `modules/quality-and-inspection.md`
(§Signing attestation, §Delegation of authority); metering is §4.10; feature flags are in §4.7; AI data use, assurance and the project assistant are §4.11.

- **SCIM provisioning (after Pilot-0; T-0298–T-0300; ADR 0017; TD-437).** Entra ID provisions
  and deprovisions KERP accounts through SCIM 2.0 at `/scim/v2/` (off unless
  `KERP_SCIM_ENABLED`). This is the "provisioning" source of accounts named in §4.5; Entra
  groups still never map to personas, grants or teams, so `/Groups` is not supported.
  - **Users:** create (no credentials, memberships or grants; `is_internal` derived as in §4.1),
    filter by `userName` (= e-mail) or `externalId` (= Entra object id, linked like an SSO link:
    an account linked to a different oid → 409), PATCH/PUT, DELETE. SCIM acts only on
    `is_internal = true` accounts and never on service accounts: a match on an external or
    service account → 409, account untouched (TD-437).
  - **Deprovisioning** (`active = false` or DELETE) sets `auth_user.is_active = false` (never a
    hard delete) and revokes every live session at once through force sign-out's revoke (§4.8):
    the next request is 401, not `DEVICE_REVOKED`, and no device is wiped. Reactivation restores
    sign-in only; offline formal sign-offs whose signer-standing window contains the
    deactivation are kept but not effective (`signer_inactive`, quality-and-inspection.md
    §Signing attestation). The last active System admin cannot be deactivated (409, audited,
    System admins notified).
  - **Token:** a bearer token per Entra application, created by a `system_admin`, shown once,
    stored only as a SHA-256 hash, with expiry (default 365 days, max 730), rotation (the old
    token stays valid 24 h) and revocation; the tenant is bound from deployment configuration and
    the hash looked up under tenant RLS (ADR 0017); SCIM tokens work only on `/scim/v2/*`;
    uniform 401; per-token rate limit; `Authorization` scrubbed from logs; every change audited.
- **External parties (guest / subcontractor witness access; T-0295–T-0297; TD-435).** There is
  no cross-tenant guest model: a deployment has one tenant (§4.1), and clients, subcontractors,
  third-party inspectors and labs are ordinary accounts with `is_internal = false`, invited by
  e-mail with a password (§4.1, §4.2, never SSO) and given individual memberships. PER-15 covers
  client witness and co-sign. Gaps the roadmap closes:
  - **Internal-commercial boundary (T-0295; restrictive planner default):** routes and response
    fields classed `internal_commercial` (rates, cost, CBS burn, estimates, internal commercial
    registers) answer 404 or are omitted for every `is_internal = false` caller, whatever persona
    a PER-09 assigned; a contract test requires every route to declare its class. Client-facing
    claim verification for PER-15 is declared `project` explicitly.
  - **External inspection party (T-0296, T-0297; blocked by TD-435):** a third-party inspector
    or lab/NDT subcontractor attends witness points that name them and submits certificates and
    reports that land unverified in the QA verification queue. A new persona (planner
    recommendation PER-17, individual assignment only, no `A`) is a core matrix change and waits
    for TD-435.
- **API keys and service accounts (Phase 5, alongside E-05-07; T-0307–T-0309; ADR 0017;
  TD-438).** A service account is an `auth_user` with `account_type = service` (never signs in
  interactively; excluded from seats, presence and broadcasts) holding project memberships. Its
  API keys are tenant- or project-scoped, with `read`/`write` scopes and a required expiry
  (≤ 365 days), stored hashed, shown once at creation and at rotation (rotation issues a new
  secret and keeps the old one valid for an overlap of at most 24 h, as for SCIM tokens),
  revocable at once, audited, and rate limited per key (`api_key_rate_per_minute`, §4.7). The
  tenant comes from the key (ADR 0017). Capabilities are scopes ∩ memberships, and **`A` and
  `S` are always removed**: no sign-off, approval, hold release, co-sign, attestation or
  confirmation of an AI proposal is ever made with a key. A service account can **never hold
  an admin grant** (the admin-grant API refuses it with 422), and key-authenticated requests
  ignore admin grants and `system_admin` visibility (ADR 0016), so a key reaches only the
  projects its account is a member of. Service accounts are `is_internal = false`, so the
  internal-commercial boundary (T-0295) hides internal commercial data from keys; this is
  intended. Interactive-only routes (`/auth/*`, `/signing/reauth`, devices, PIN, invites, admin
  grants, `/admin/*` mutations, SCIM) refuse keys. Machine writes are identified by `created_by`
  being an `account_type = service` user (or, for SCIM, the SCIM token id) plus the key or token
  id in the audit payload; `source="api"` alone does not identify them, because it is also the
  default for human JWT writes. Outbound webhooks (HMAC) stay in E-05-07.
- **Network restrictions (IP allowlisting).** Not built in KERP: source-IP restrictions are
  infrastructure configuration (AWS WAF IP sets in front of the API and web app, Entra
  Conditional Access named locations for SSO users), documented in
  `docs/deploy/network-access.md`. KERP trusts forwarded client IPs only from
  `FORWARDED_ALLOW_IPS`. Per-project IP rules: TD-440 (later).

### 4.10 Usage metering (user, 2026-11-21, enterprise roadmap; planner defaults TD-436)

Step 1 is cheap data capture now (Phase 1, T-0301, T-0302); advisories, statements and screens
come later (Phase 6, T-0303–T-0305, epic E-06-05). Rules:

- **Counts and bytes only.** No currency amount is stored, computed or sent by KERP; money stays
  a read-only mirror (§1). A project's cost centre is a reference (`external_source` +
  `external_id` + `synced_at` when it comes from the ERP) in a metering table, not a column of
  `oe_projects_project`.
- **Storage ledger** `oe_metering_storage_event` (module `metering`, `BaseRecordMixin` + RLS):
  written through `metering/service.py` by the uploads file lifecycle (`available` adds a
  reference, `expired`/swept/deleted removes it; rejected files never count), the image
  derivatives (T-0247), the project archive registry (E-01-02: `ready`, tier changes, restore
  copies). Cold archives count as `DEEP_ARCHIVE` (§4.7 storage classes). Idempotent through an
  `idempotency_key` supplied by the hook (hook name + the source row's transition or version
  id) with a partial unique index on `(tenant_id, idempotency_key)` over live rows, so a retried
  hook writes nothing twice while every real transition (e.g. an archive moved to
  `DEEP_ARCHIVE`, back and again) is kept; soft delete only; nothing cascades into it.
- **Content-addressed bytes (TD-408, TD-436).** Physical bytes are counted once per object
  (tenant-wide and for its project); logical bytes per referencing file row. An object
  referenced from several projects is attributed in full to each project's figures and marked
  shared, while the tenant total counts it once. Derivatives go with their original; archives
  with the archived project.
- **Daily snapshot** `oe_metering_daily_snapshot`: one row per tenant (`project_id` null) or
  project, date and metric, `UNIQUE NULLS NOT DISTINCT (tenant_id, project_id, snapshot_date,
  metric_key)` as a partial index over live rows (PostgreSQL 16; no expressions in keys), soft
  delete only, retained. Built by an SAQ cron on the `batch` worker after midnight in the
  deployment's default site timezone; re-runs overwrite a date idempotently. Storage from the
  ledger; seats from `auth_user.last_active_at` (§4.8), never audit-log scans; counts through the
  stats-provider registry shared with deployment stats (§4.7, T-0219). Public capture
  submissions are counted only if TD-410 accepts ADR 0013.
- **Soft limits never block.** Quota policies (tenant default, per-project override) only raise
  advisories (in-app and e-mail) to PER-09 holders and System admins.
- **E-mail carries a link and the project code/name only** (§4.6); numbers stay in the app and
  in the statement CSV download.

---


### 4.11 AI assistance: data use, assurance record, project assistant (user decision and product intake, 2026-11-21)

- **Data use** (user decision 2026-11-21): client and site data of a project may be sent to an
  external AI provider for KERP's AI features (assistant, ITP drafting and watchdog, ingestion
  second pass, text refinement). The CLAUDE.md §6 rule is unchanged: every AI-initiated mutation
  carries `source="ai"` and needs human confirmation before it applies.
- **Provider, residency, credentials** (product intake 2026-11-21, planner default; user to
  confirm, TD-545): one provider per deployment (= the tenant, §4.1), chosen from OpenAI,
  Anthropic Claude or a self-hosted model, hosted in the deployment region (Australian region,
  TD-078). Credentials are the company's own (tenant-owned key) held in the deployment secret
  store, never a registry entry (§4.7, TD-398). **Fail closed:** with no provider or no valid
  credentials every AI feature is unavailable (entry points hidden; endpoints answer 503
  `AI_UNAVAILABLE`); nothing falls back to another provider or region.
- **AI assurance record** (product intake 2026-11-21, planner default; user to confirm). Every
  AI call writes one metadata row (BaseRecordMixin, project RLS): feature, provider, model id,
  credentials owner (tenant key or platform key), prompt template id and version, input record
  references (ids only), output artifact reference, start and end time, token counts, outcome.
  **No prompt or completion text is stored** in this record. A result that becomes a proposal
  links to the human decision (`confirmed | edited_and_confirmed | rejected`, reviewer, time):
  the reviewer's control is a named "I reviewed this" per AI call. The assurance screen lists
  calls (not reviews) and highlights calls whose output nobody reviewed. Rows follow the
  retention schedule (TD-175).
- **Refine note** (product intake 2026-11-21, planner default; user to confirm): on issue and
  NCR descriptions and diary text, the user may ask for a reworded version; it is shown as a
  diff, applied only when the user accepts it, and the saved change carries `source="ai"` in its
  audit entry with the assurance record id.
- **Project-scoped assistant** (product intake 2026-11-21, planner default; user to confirm;
  E-05-08). Retrieval runs **as the asking user**: every lookup goes through the owning module's
  `service.py` under the user's session, RLS and project membership (site scope, granted subtree,
  `internal_commercial` class), never through a service role or a shared index that bypasses
  them. An embedding index, if used, is per project and filtered by the same visibility at query
  time. Structured queries are preferred over embeddings where the data is structured (for
  example "what blocks milestone X": P6 links, the asset subtree, open issues with
  `mdr_effect = blocks`, unsigned hold points). The assistant reads and drafts only; any change
  it proposes is a draft with `source="ai"`, confirmed by a person in the normal screen.

## 5. Equipment calibration lifecycle & hard quality gate

- **`oe_equipment_asset` statuses:** `active`, `out_of_calibration`, `sent_for_calibration`,
  `quarantined`, `decommissioned`, `out_of_service`.
- **Immutable `oe_equipment_calibration_cert`** ledger, linked to presigned PDF certificate
  uploads in the VFS.
- Upload triggers a **SAQ OCR** job (cert number, lab accreditation, calibration date,
  expiry). **OCR-vs-user-entry discrepancy → QA review task.**
- A new verified cert sets `is_active = true` and **permanently supersedes** the prior cert
  (locked against edits).
- **Hard inspection gate:** instruments requiring calibration (hydro gauges, DFT meters,
  surface-profile gauges) that are expired **cannot be selected**. If forced offline, the CEL
  rule fails on sync:
  `equipment.calibration_due_date >= context.inspection_date && equipment.status == 'active'`
- Active cert PDFs + calibration details **auto-compile into the project MDR**.

**Equipment additions (user, 2026-11-21; planner reconciliation):**
- **Equipment classes** (T-0249; TD-412 resolved 2026-11-21: in Phase 1): `equipment_class` (`instrument | vehicle |
  plant`), `requires_calibration` (true for instruments), `asset_number` (fleet number),
  `custodian_user_id`. The calibration gate and the activation cert rule apply only when
  `requires_calibration` is true. Pulls this slice of E-03-07 into Phase 1 (product owner,
  2026-11-21): some vehicles and plant need calibration or monthly compliance inspections, so they
  are selectable inspection targets (through equipment links) like instruments.
- **Activation gate** (T-0260): new equipment starts `out_of_service`; moving to `active` calls
  `run_checks(context, "equipment.activate")`, whose locked system-default rule is
  `!equipment.requires_calibration || (has(equipment.active_cert) &&
  equipment.active_cert.verification_status == 'verified' && equipment.active_cert.document_id !=
  null && equipment.active_cert.expiry_date >= context.today)`. A cert uploaded without QA
  authority is `pending_qa_verification` and goes to the QA verification queue
  (`quality-and-inspection.md` §QA verification queue); the OCR-discrepancy review of this section
  is routed into the same queue (TD-416).
- **Out of service and tagout** (T-0257; equipment part T-0288): a failed pre-start can set `out_of_service` through a
  post-submit rule; a blocking tagout follow-up keeps it there until confirmed and closed
  (`equipment.activate` fails with 422 while it is open). Out-of-service, quarantined and
  decommissioned equipment is never selectable in inspections or allocation.
- **Standalone inspections on equipment** (pre-starts, fault reports): equipment is an
  informational **link** of a project-level inspection, never an anchor (user, 2026-11-21;
  ADR 0003 amendment 2026-11-21, ADR 0014 superseded, TD-411 resolved; T-0250). Fleet vehicles
  and plant enter the register in Phase 1 (TD-412 resolved 2026-11-21; T-0249). Expiry lead-time alerts for calibration: T-0183.

---

## 6. Front-end architecture & edge screens

- **Global IA:** an **8-group** navigation on the **4 industrial pillars** — **Logistics,
  Execution, Defense & Claims, Controls & Insights** — plus **Backbone, Overview, Documents,
  Admin**. Top-bar **Project Switcher** including an **All-Projects** portfolio scope for
  corporate QA/HSE/Operations.
- **Reusable domain shells (build first):** `RegisterPage` (filter, column visibility,
  CSV/PDF export, saved views), `DetailPage` (gate-status banner, tab routing, audit log),
  and the **Dynamic Form Renderer** — before any individual module page.
- **Three Tier-1 edge screens** (wireframed 360–412px mobile + tablet): **Gate Delivery &
  OS&D Capture**, **Look-Ahead & Constraint Board**, **Daily Workface Log** (see
  `KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md` §1).
- **Offline outbox:** **Dexie.js (IndexedDB)** transactional outbox keyed on unique
  `client_op_id`; optimistic UI, idempotent drain/replay on reconnect. (TanStack Query alone
  is no longer the write path.)

### 6.1 Navigation mapping and project picker (user, 2026-11-13; TD-148, TD-120)

Module screens register their nav items in these groups. Individual items may move later.

| Group | Items |
|---|---|
| **Overview** | dashboards, my work |
| **Backbone** | projects, asset hierarchy, items, teams |
| **Execution** | inspections / QMS, ITPs, **Templates** (every inspection template in one filterable list: area, issue category, formal, audit, status, kind; presets; user, 2026-11-21), **Issues** (punch/NCR register and, for the QA authority / PER-09 / PER-13, the issue templates — issue types per category; user, 2026-11-21), field time, progress, look-ahead |
| **Logistics** | materials, equipment, storeman |
| **Documents** | documents, transmittals, CDRL, drawings |
| **Defense & Claims** | TQs, variations, NOD / EOT, claims |
| **Controls & Insights** | P6 schedule, reporting, money mirror |
| **Admin** | users, personas, access requests, communications (broadcast e-mail, delivery stats, automated e-mail templates), project archives (export, storage tiering, download, restore), system (deployment stats, queue monitor, upload quarantine, runtime settings, admin grants, devices, audit log) (user, 2026-11-21; §4.7, §6.5) |

**Templates and Issues (user, 2026-11-21):** two menu items, one shared template designer
(E-01-03). Templates lists inspection templates (QA/ITP forms, H&S checks, pre-starts, equipment
checks, repair/fault, audits); Issues holds the issue register (T-0318) and the issue templates
(T-0320). "New template" starts from a preset (`modules/quality-and-inspection.md` §Template
settings; `modules/issues.md` §2). Both are project screens; a PER-13 caller also sees a
"Tenant library" scope (planner default). Template settings are not Admin screens (§6.5).

The top-bar **Project Switcher** is the project picker of §4.1: it lists the user's projects
(from memberships), remembers the last project per user on this client, offers All-Projects to portfolio
personas, and links to the lobby ("Browse / join another project"; product owner, 2026-11-14).
For a `system_admin` it lists every project of the tenant, marking those reached without a
membership ("Admin access"), and All-Projects covers all of them (ADR 0016; T-0276).

### 6.2 Field client storage (user, 2026-11-13; TD-161, decision 16a + d)

- **Per build.** The **Capacitor Android build** stores records and the outbox in
  **SQLCipher-encrypted SQLite**, and media on the device file system (app-private storage,
  encrypted with a key held in the Android Keystore). **Web-only PWA apps keep Dexie**
  (IndexedDB) as above. Sized for 500 inspections + 10 GB media per device on Android.
- **Android 11+** (API level 30) minimum; distributed only through **managed Google Play / MDM**.
- **One client storage contract**, implemented once per build: `RecordStore` (project-scoped
  records by entity and id, query by anchor), `OutboxStore` (append a mutation envelope keyed on
  `client_op_id` atomically with the record write, list pending, mark acknowledged),
  `MediaStore` (put a blob → local reference + sha256, read, delete, usage), and `wipe()`
  (records, outbox, media and keys; used by remote revoke, §4.3). App code depends only on the
  contract, never on Dexie or SQLite directly.
- **Client-agnostic sync contract.** The HTTP endpoints (master spec §4.3 `push-mutations` /
  `pull-deltas`, presigned media uploads, device enrolment) are the only client–server contract.
  There are no endpoints specific to one client, so a separate native Android app (TD-044) could
  implement the same storage and sync contract later.

### 6.3 Non-functional budgets (user, 2026-11-13; TD-174, decision 18a)

| NFR | Budget | Measured |
|---|---|---|
| Form open | A 300-question form opens (first interactive render) in **< 1.5 s** | CI: headless Chromium, 4× CPU throttle |
| Delta sync | **1,000 records** pulled and applied in **< 30 s on 4G** | CI: simulated 4G (9 Mbit/s down, 3 Mbit/s up, 85 ms RTT) |
| Glove use | Touch targets **≥ 48 dp** (48 CSS px) on field screens | CI: component tests + axe |
| Outdoor | A **high-contrast** theme (design tokens) for field screens; WCAG 2.1 AA everywhere | CI: axe contrast checks |

Automated performance tests **warn in CI now** and become a **hard gate before Pilot-0** (§7).
The reference device and network profile are confirmed before the gate flips (TD-205).

### 6.4 Sync scope and download for offline (Pilot-0; TD-162, planner spec 2026-11-13, Q5a)

The user chose to cut the Pilot-0 tasks (Q5a). The rules below are planner defaults unless marked
otherwise; they refine master spec §4.3 and ADR 0005 and keep the sync contract client-agnostic
(§6.2).

- **Download selection = checkout.** A field user picks what to take offline per device and
  project. Each pick is a checkout of ADR 0005 (`oe_field_checkout`, master spec §1.3 rule 4) with
  scope `inspection` (one inspection record) or `node` (an anchor subtree: its Items, their scan
  identifiers and their open inspections, for ad-hoc work). Removing a pick releases the checkout.
  At most **500 inspections** per device and project (planner default; NFR sizing in §6.2).
  `node` is a new checkout scope next to ADR 0005's inspection job / RSW batch (ADR 0005
  Addendum A).
- **One checkout per record (ADR 0005 §2).** Picking an inspection that another user or device
  already holds fails with **409 naming the holder** (user and device name) (planner default). A
  `node` pick is **partial** (planner default): it checks out every inspection under the node
  that nobody else holds, and the response lists the held ones with their holders; the Items,
  scan identifiers and reference data of the subtree are still downloaded read-only. A record
  held by someone else is never sent to another device as editable.
- **Lease length.** A pick's lease lasts the deployment's offline window (§4.2/§4.4: default 7
  days, at most 14) and is renewed on every successful sync of that device (planner default).
  Lease expiry does **not** produce a `remove` delta and does not purge the device: the pick stays
  local, and data synced after expiry is accepted as in p6 spec §8.5 (accepted if nobody else
  checked the record out, otherwise conflict log + review, never dropped). Once another user
  picks the expired record, the first device sees it as "checked out by <holder>" on its next
  pull (planner default).
- **Candidates with state and size.** `GET /api/v1/sync/download-candidates?project_id=` lists the
  inspections assigned to the user first, then unassigned ones (planner default), each with status,
  anchor display path, due date, whether it is already downloaded to this device,
  `checked_out_by` (user and device of another holder, else null; planner default) and an estimated
  size in bytes: the record JSON plus the stored sizes of its attachments (T-0016). Nothing is
  downloaded automatically (planner default).
- **Pull scope.** `pull-deltas` is scoped per **device and project**:
  `GET /api/v1/sync/pull-deltas?project_id=&since_version=&limit=`. In scope are the device's
  picks (records, answers, answer comments, attachment metadata, linked open defects) and the
  project reference data the client needs offline: level definitions, terminology, item types and
  field definitions, the template revisions (with their reference images) pinned by the picks or
  allowed on picked anchors, the **public** signing keys of enrolled client devices (quality-and-inspection.md §Client
  co-sign; never PIN hashes or other secrets), the
  equipment register with calibration state, the project's rule sets and member display names.
  Records outside the scope are never sent. A newly added pick is back-filled in full on the next
  pull, whatever `since_version` is.
- **Paging.** The response is `{deltas, new_version, has_more}`, ordered by the server commit
  sequence; `limit` defaults to 500 and is capped at 1,000 (planner default). The client pulls
  again with `since_version = new_version` until `has_more` is false. Each delta is `upsert` or
  `remove`; `remove` means the record left the device's scope (pick removed, record soft-deleted,
  or the inspection was approved, see purge).
- **Local purge.** On `remove` the client deletes the local records and media of that pick, but
  only once its outbox holds no pending mutation for them; until then the pick stays local and is
  shown as "waiting to sync". The user may remove a pick by hand on the same terms. The server
  drops an inspection from every device's picks once it is `INSPECTED_PASSED`,
  `INSPECTED_FAILED` or `LOCKED_FOR_TURNOVER` (planner default). Purge never deletes unsynced
  work; only a remote wipe (§4.3) does.
- **Client-generated ids.** Records created offline (ad-hoc inspections, answers, answer comments,
  attachment metadata, NCR and punch items raised in the field) carry a client-generated
  **UUIDv7** `id` (planner default). The server keeps that id. A replay of the same `client_op_id`
  is a no-op; a create whose id already belongs to a different record fails that mutation with
  `ERR_ID_CONFLICT`; an id that is not a UUIDv7 fails with `ERR_ID_INVALID`. Other mutations in
  the batch still apply.
- **Stale-base flag on answers.** When the per-field apply keeps the server value (ADR 0005 §4,
  p6 spec §8.3 rule 4), the answer carries a `conflict` flag that points to its
  `sync_conflict_log` entry. Inspector and reviewer see the flag on the answer; choosing a value
  is a normal edit and clears it. Submit is blocked while an answer has an open conflict flag
  (`ANSWER_CONFLICT_UNRESOLVED`, planner default).
- **Submit waits for attachments.** The `inspection.submit` rule set contains a built-in check
  that every attachment referenced by the record's answers is `complete` (uploaded and
  sha256-verified, T-0016); otherwise the submit fails with `ATTACHMENTS_INCOMPLETE`. The client
  orders a queued submit after the uploads it depends on and keeps the submit button disabled,
  with the pending count, until they finish. A failed submit is a per-mutation result, never a
  failed batch.

#### 6.4.1 Time of offline actions (user, 2026-11-13)

The user's design. It applies to **every offline-capable timed action** (answers, sign-offs,
submit, client co-sign, comments, NCR/punch raised in the field), not only submit. Items marked
"(planner default)" are planner choices; everything else is the user's decision.

- **Capture on the device, per action:** `device_wall_time` (UTC ISO 8601), `monotonic_ms` and
  `clock_session_id` (a UUID minted when the monotonic origin starts). Each push batch also
  carries `device_sync_wall_time`, `device_sync_monotonic_ms` and the current `clock_session_id`;
  the server stamps `server_sync_time` on receipt.
- **Reconstruction on the server:**
  - **Same clock session** as the push: `occurred_at = server_sync_time − (device_sync_monotonic_ms
    − action monotonic_ms)`, `time_confidence = MONOTONIC_RECONSTRUCTED`.
  - **Same clock session as a stored time anchor** (below), but not the push:
    `occurred_at = anchor.server_time + (action monotonic_ms − anchor.monotonic_ms)`,
    `MONOTONIC_RECONSTRUCTED`. The action carries its anchor; the server accepts only an anchor it
    issued (the `/api/v1/time` response carries a server-signed token, planner default).
  - **Different session:** `clock_skew_ms = server_sync_time − device_sync_wall_time`,
    `occurred_at = device_wall_time + clock_skew_ms`, `SKEW_ADJUSTED`; when |`clock_skew_ms`| is
    above **2 minutes** across a reboot (threshold: planner default) it is `UNVERIFIED_REBOOT`
    instead.
  - **Online actions:** `occurred_at` = server time, `VERIFIED_ONLINE`.
  - `occurred_at` drives sorting, SLAs, ITP sign-offs, overdue, dashboard tiles and the PDF. The
    offline-window check (§4.2) uses `occurred_at`.
- **Stored evidence, never overwritten.** Each record with an offline-capable timed action stores
  `occurred_at` (timestamptz), `recorded_device_at` (timestamptz, the raw device wall time),
  `synced_at` (timestamptz, `server_sync_time`), `clock_skew_ms` (bigint) and `time_confidence`
  (`VERIFIED_ONLINE | MONOTONIC_RECONSTRUCTED | SKEW_ADJUSTED | UNVERIFIED_REBOOT`). Column
  placement (planner default): the five columns on every sign-off ledger entry (T-0119 table:
  inspector submit signature, reviewer approval, client co-sign), on the inspection record for its
  submit (prefixed `submit_`), and on answer and answer-comment rows. Additive migrations. The
  T-0006 audit ledger gets **no new columns** (planner default): audit entries for offline actions
  carry the same five values inside their hashed payload, so no core-table ADR is needed; the
  sign-off ledger of T-0119 is a module table that is not built yet.
- **Time anchor.** `GET /api/v1/time?clock_session_id=&monotonic_ms=` (lightweight; needs a
  signed-in session, any persona — planner default) returns server time and an anchor token: an
  HMAC over server time, the client's `clock_session_id` **and** the `monotonic_ms` it sent, so a
  carried anchor cannot be paired with another monotonic origin (planner default). Whenever it has
  connectivity (login, pre-shift, each sync) the client stores an anchor
  `(server_time, monotonic_ms, clock_session_id, token)` and uses it offline for actions in the
  same clock session.
- **Skew check before going offline.** When the user takes a download/checkout (§6.4 picks), the
  client compares the device wall time with `/api/v1/time` (round-trip midpoint, planner default).
  If |skew| is above the deployment setting `max_checkout_clock_skew_s` (§4.4; default **5 s**),
  the checkout is blocked and the user is asked to turn on automatic date and time (NTP). The pick
  request carries the measured skew and the server refuses it too (`CLOCK_SKEW_TOO_LARGE`, 409;
  planner default).
- **Warning badge.** Records with `UNVERIFIED_REBOOT` show a high-contrast warning badge in the
  web UI (review screen, inspection detail) so a QA/QC reviewer can check them against shift logs.
  The PDF prints `time_confidence` next to each time.
- **Platform notes (planner).**
  - **Web/PWA:** `performance.now()` restarts on every page load or app restart, not only on
    device boot, so `clock_session_id` is per JavaScript realm and a reload starts a new session
    (skew path). The PWA persists its anchor and re-anchors on reload whenever it is online.
  - **Android (Capacitor):** monotonic time comes from `SystemClock.elapsedRealtime()` through a
    small native plugin (planner default), because it keeps counting through deep sleep while
    `performance.now()` may pause in some WebViews. A new `clock_session_id` is minted after a
    device boot (detected when `elapsedRealtime` is below the last stored value or the boot count
    changed).
  - **iOS:** out of scope (Android only).
- **Optional chain-of-custody hash** of offline records (SHA-256 of previous hash + payload +
  `monotonic_ms`): the user called it optional; tracked as a `later` TODO (TD-215), no task.

### 6.5 Admin console screens (user, 2026-11-21; planner defaults)

The Admin group of §6.1 gains three areas. Every screen uses the reusable shells (`RegisterPage`,
`DetailPage`), every string goes through `t()`, and a screen a caller may not use is hidden from
the navigation **and** refused by the API (403 for admin surfaces).

| Area | Screens | Who | Tasks |
|---|---|---|---|
| **Users** | user directory (status, presence, sessions, logins, grants, filters, search; resend invite, force sign-out, activate/deactivate); user detail with memberships across projects (§4.8); "Login as user (support)" with reason, and the impersonation banner with Exit on every screen (§4.2 *Support impersonation*; pending TD-426) | `system_admin`; PER-09 memberships tab for its own projects | T-0026, T-0246, T-0280 |
| **System** | deployment stats + health; queue monitor (retry / abort); upload quarantine (rows of every project; re-check, sweep now); runtime settings (editable registry + read-only deployment configuration); admin grants (T-0026); devices (T-0048); audit log (filters, verify chain); legal hold on the project admin page | `system_admin` | T-0224, T-0225, T-0232 |
| **Communications** | broadcast composer (audience, count, send); delivery stats (cards) and history; automated e-mail templates (edit, preview, test send, reset) | `system_admin`; PER-09 composer and stats for its own projects | T-0229 |
| **Project archives** | archive list; export; storage tier toggle; download; start a deep-archive restore; restore wizard (deleted project or new project) | `system_admin` (any project of the tenant, live or deleted; ADR 0016) | T-0241 (epic E-01-02) |
| **Identity and access** | SCIM provisioning (base URL, tokens: create once, rotate, revoke; §4.9); service accounts and API keys (Phase 5, §4.9); feature flags with project overrides (§4.7) | `system_admin` | T-0300, T-0309, T-0306 |
| **Usage** | usage trends, storage by class, seats, quota policies and advisories, cost-centre references; project usage report with monthly CSV for PER-09/PER-02 (§4.10, Phase 6) | `system_admin`; PER-09/PER-02 for their projects | T-0305 |

A screen never shows a project record the caller could not open in the project itself. Since
ADR 0016 a `system_admin` opens every project of the tenant, so the upload quarantine shows
rows of every project, the audit explorer every project chain, and the project archives allow
export, download and restore of any project (restore as new still makes the restoring admin
PER-09 of the new project, audited; `modules/documents-and-mdr.md` §Access; TD-396 resolved).

---

## 7. Task tracker & phasing

- **Milestones:**
  - **M1 — Phase 1 Freeze Gate (`T-0131`):** contract test freezing `oe_asset_node`,
    `oe_asset_item`, the mixins, and `run_checks` signatures. Post-freeze changes require a
    formal ADR.
  - **M2 — MVP cut line:** Phase 0 + 1 + 2 + **Documents-Lite** (VFS tree, presigned uploads,
    batch inspection PDF export) + **P6 schedule round-trip** (~85–95 tasks).
  - **Pilot-0 (user, 2026-11-13; TD-173)** — the **first pilot milestone**, ahead of M2: 8
    inspectors, one project, Android only. Core inspection, offline, signing, review/return and
    the inspection PDF; no P6 import/export, no Phase 2. M2 stays the next milestone after it.
    Scope and gate: `docs/DEVELOPMENT-PLAN.md` §Milestones.
- **Re-ordering:** `resource_summary` → **Phase 4** (depends on look-ahead + weekly
  commitments); **`procurement` pulled to the front of Phase 3** (before `site_inventory` and
  `site_logistics`); overloaded `T-0124` **split into 2–3** tasks (completion gates vs P6
  sync).

---

## 8. Preserved legacy detail (do NOT discard)

Granular, KAEFER-domain field-level definitions are **retained** and folded into
`docs/spec/modules/` with the overhauls above applied:

- **Welding (`wps`)** and **NDE** — procedures (WPS), welder qualification/continuity, weld
  state machine, RT/UT/MT/PT results, heat/MTR traceability.
- **Coatings / blasting** — ambient (temp, RH, dew point), surface profile, WFT/DFT, holiday
  testing; the dew-point CEL guard.
- **Scaffolding** — erect/inspect/green-tag lifecycle as an AWP constraint.

For exhaustive original text, the archive remains available; it is reference only and
superseded wherever this document or the two new specs speak.
