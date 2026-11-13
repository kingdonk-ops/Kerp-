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
  User**), plus an **Asset-Site scoping** filter for Site Inspectors (visibility limited to
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
    row and every membership (the "tenant administrator" of edge spec §6) but read no
    project-scoped records without a membership like any other user.
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
  such a persona acts under that persona.
- **PER-09 combined with a hold-authority persona** (PER-02, PER-06, PER-07, PER-11) on the same
  project is allowed. The assignment API returns a warning the UI must show before saving, and
  the assignment is written to the audit ledger with `sod_warning: true` (TD-151).
- **One URL, no subdomains.** Every project is reached under the same origin; the active project
  is chosen in the app, never by host name.
- **Landing after sign-in.** One project → straight into it. Several → the project last used
  per user on this client (falls back to the first by name if it is no longer allowed), with a
  **project picker in the top bar** to switch; portfolio personas keep the All-Projects scope
  (§6). No project → the **lobby**. Users with projects reach the lobby from the picker
  ("Find a project").
- **Lobby and access requests.** The lobby lists only projects whose PER-09 holders set
  *visible in lobby* (default off): code and name only. A user requests access with an optional
  message; the request goes to that project's PER-09 holders (in-app notification), who approve
  it — choosing the persona(s) and site scope — or reject it. One open request per user per
  project; the requester may withdraw it. Approval creates the membership; request, decision and
  decider go to the audit ledger. Projects not visible in the lobby are reached only by an
  **invite** (a PER-09 assigns the user) or by **project code**: entering an exact project code
  finds that one project and lets the user file an access request; a code never grants access
  by itself; users who already have projects can open the lobby from the picker (user,
  2026-11-13; TD-202). The lobby and code lookup read through a narrow path (code, name, id of
  matching projects only); the membership-based `projects` policy is not widened.

### 4.2 Sessions (user, 2026-11-13; TD-121, TD-135, TD-170)

- **Access token** 15 minutes (unchanged). **Refresh token** lifetime **12 hours**
  (`KERP_REFRESH_TOKEN_HOURS=12`, user decision). Counted from sign-in: rotation issues a new
  single-use token but never extends the session past sign-in + 12 h (user, 2026-11-13). Users sign in again
  daily; for field workers that needs Entra ID SSO (TD-043) or magic-link e-mail delivery
  (TD-084) in place before Pilot-0.
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
- **Daily re-login vs offline.** The 12-hour session limit applies only to server calls. Offline
  work continues on local unlock for the whole offline window; on reconnect the user signs in
  (password, SSO, or magic link) and the outbox drains under the new session. Queued mutations
  carry their author; the server accepts them only from a session of the same user and device.

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
- **Audit.** Ledger entries made through a device carry its `device_id`.
- **After the pilot:** biometric unlock, certificate pinning and per-device sync-health views
  (TD-204).

### 4.4 Deployment settings and region (user, 2026-11-13; TD-170, TD-171, TD-185)

- **Tenant (deployment) settings**, edited by System admins: offline window (days, default 7,
  1–14), and the defaults for new projects: status `ACTIVE`, site timezone `Australia/Perth`,
  currency `AUD`, locale `en-AU`. Also `max_checkout_clock_skew_s` (default 5; user, 2026-11-13):
  the largest device clock skew allowed when taking work offline (§6.4.1). Project status values are `ACTIVE`, `ON_HOLD`, `CLOSED`.
- **Region is a per-deployment setting**, not code: AWS `ap-southeast-2` (Sydney) for Australian
  deployments; `eu-central-1` (Frankfurt) only if an EU deployment is ever needed. S3, SES and
  any other regional AWS client read the deployment's region; nothing hard-codes a region.

---

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
| **Execution** | inspections / QMS, ITPs, field time, progress, look-ahead |
| **Logistics** | materials, equipment, storeman |
| **Documents** | documents, transmittals, CDRL, drawings |
| **Defense & Claims** | TQs, variations, NOD / EOT, claims |
| **Controls & Insights** | P6 schedule, reporting, money mirror |
| **Admin** | users, personas, system (deployment settings, admin grants, devices), access requests |

The top-bar **Project Switcher** is the project picker of §4.1: it lists the user's projects
(from memberships), remembers the last project per user on this client, offers All-Projects to portfolio
personas, and links to the lobby ("Find a project").

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
