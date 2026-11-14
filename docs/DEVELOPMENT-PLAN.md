# KERP — Development plan (merged)

**What we're building:** KERP, an industrial Field Operations & Commercial Execution
platform for heavy construction / fabrication / remediation / turnaround (KAEFER-style).
It owns field ground truth — asset/scope hierarchy, earned progress, plant & labour
presence, diaries, inspections/ITP/NCR, welds & NDE, materials & heat traceability,
documents/MDR — and exports verified actuals to P6 and the corporate ERP. It is **not**
back-office ERP and **not** ongoing plant maintenance.

**Single source of truth:** [`docs/spec/`](spec/README.md) —
[`KERP-MASTER-SPEC.md`](spec/KERP-MASTER-SPEC.md) (12-domain architecture, core DDL,
events) · [`KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md`](spec/KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md)
(edge screens, offline outbox, CEL, calibration) · [`KERP-CONSOLIDATION-AND-DIFF.md`](spec/KERP-CONSOLIDATION-AND-DIFF.md)
(**binding** scope culls + overhauls) · [`spec/modules/`](spec/modules/) (preserved
KAEFER domain detail). Legacy analysis is archived under
[`archive-legacy-specs/`](archive-legacy-specs/README.md) — reference only.

**Live progress:** [`TASKS.md`](TASKS.md) · **Open questions / decisions:** [`TODO.md`](TODO.md) · **Pipeline:** [`WORKFLOW.md`](WORKFLOW.md)

## Scope (from the consolidation diff)

**In:** 12 domains (MOD-01…12) — identity/RBAC, project master & engineering tagging,
estimating/WBS-CBS, commercial/SoR/claims, AWP, field execution, materials/heat trace,
QC/ITP, NCR/punch, asset integrity/preservation/hydro, documents/MDR, dynamic
fields/integrations — plus the edge/quality layer (CEL, calibration gates, offline).

**Out (culled):** General Ledger / double-entry / bank rec / depreciation / tax / direct
invoicing / AR-AP · HR / payroll / time-billing · e-commerce / POS / CRM pipelines ·
`enterprise_workflows` (→ `approval_routes`) · `module_builder` (runtime no-code) ·
interactive 3D CAD/BIM viewers & native `.dwg` parsers (→ PDF viewer + markups +
pluggable parser adapter). Money is a **read-only external mirror** only.

## Phases

| Phase | Scope | Depth | Exit criterion |
|---|---|---|---|
| **0** Platform substrate & security core | scaffold, DB + BaseRecordMixin, **project-scoped RLS** (+4 bypass tests), **RS256/EdDSA JWT + JWKS**, field-worker magic-link+PIN, 15-persona RBAC, module loader, **SAQ** jobs, presigned uploads, **CEL runtime substrate**, `run_checks` shell, hash-chain audit, frontend shell (8-group IA) + reusable shells; access model (System admin → Project Admins, lobby, project picker), 12 h sessions, hybrid PIN, device registry, deployment settings (2026-11-13); login & lobby, invites, Entra SSO, outbound e-mail, password reset (2026-11-14) | **78 tasks** | tenant/project created, user authed & scoped, module toggled, rule set run — CI green |
| **1** Asset backbone, one-engine & quality core | **ltree** hierarchy, **JSONB** field registry (EAV deleted), Items + CML stubs, P6 import/export (idempotent + unmatched-row linking), **one-engine** forms+CEL, **unified inspection record** + **eligibility**, **equipment calibration** register + hard gate, qms/construction_control/inspection_requests, NCR/punch, signing, completion-gate + P6 rule sets, offline sync core (Dexie), UI, **M1 freeze gate (T-0131)**; Capacitor Android build + SQLCipher store, FCM push, Pilot-0 gate (2026-11-13); Pilot-0 slice: sync scope + download, review/return, client co-sign, capture screen, scan-to-locate, photo annotation, video/audio/barcode fields + reference images, inspection PDF, dashboard tiles | **75 tasks** | P6→hierarchy; inspections gated by CEL; expired tool blocked; completion blocked by open NCR; backbone contract frozen |
| **2** Welding quality & traceability | WPS, welder quals/continuity, welds as Items, NDE, heat/MTR trace, R-WPS rules | **9 tasks** | a weld can't be accepted outside qualification/NDE rules |
| **3** Logistics & supply chain | **contacts**, **procurement (front)**, materials/MRIR+heat, inventory, **cutting (FFD + rectpack)**, Gate Delivery edge screen, equipment/fleet | 7 epics | delivery → gate receipt + OS&D; cutting plan → inventory |
| **4** Execution & LPS | schedule, Look-Ahead board, earned-qty progress, Daily Workface Log + CBS burn, field time, estimating/EVM, tasks, **resource rollup (moved here)**, safety/PTW, supervision/closeout, **shift/swing handover register** | 11 epics | progress as earned qty (gated), actuals → P6, PPC computed |
| **5** Documents, CDE & MDR | VFS/CDE + parser adapter, markups/plan room, transmittals, TQ/submittals, **MDR collation engine**, search/OCR, integrations/event bus, assistant | 8 epics | docket → OCR → searchable; MDR dossier compiled; assistant proposes only |
| **6** Defense, variations & claims | commercial/SoR, progress claims (G702/G703) + `claim.approved`, variations + delay attribution + TIA, physical EVM, reporting/BI, risk, cases + `approval_routes`, ESG | 8 epics | measured variation → priced mirror back; physical EVM + claims binder |
| **6b** Asset integrity & work packaging | preservation + hydrotest packs, AWP constraint engine (7 gates), field work packs + MDR compile | 3 epics | CWP→IWPs, offline stage sign-off, hydro gating, MDR filed |
| **7** Client portal | v1 read-only → v2 co-sign (pyHanko) → v3 visit scheduler + client NCR | 3 epics | per sub-phase |

**Rolling-wave planning.** Phases 0–2 are broken to S/M tasks now; Phases 3–7 are epics
(`needs-breakdown`) the planner splits once the **M1 freeze gate (T-0131)** is green —
breaking them down earlier would bake in guesses Phases 0–2 will invalidate.

## Milestones

- **M1 — Phase 1 Freeze Gate (`T-0131`):** contract test freezing `oe_asset_node`,
  `oe_asset_item`, the mixins and `run_checks` signatures; CEL JS/Python parity; seam
  hashes. Post-freeze changes require a formal ADR in `docs/decisions/`.
- **Pilot-0 — first pilot (user, 2026-11-13; TD-173):** 8 inspectors, one project, **Android
  only**. Usable when an inspector can download work, inspect offline, sign and sync, and a
  reviewer can approve or return the inspection and get a PDF. No P6 import/export and no
  Phase 2. Gate: **T-0146** (NFR budgets become hard CI gates, checklist below complete).
  - **Phase 0** as planned, including the 2026-11-13 additions: T-0037 (12 h sessions, hidden
    tenant), T-0038 (hybrid PIN), T-0039–T-0042 (Project Admins, lobby, access requests,
    picker), T-0049 (internal staff flag), T-0050/T-0051 (access-request lifecycle and admin
    inbox), T-0052/T-0053 (project invites; in the gate since 2026-11-14), T-0054/T-0055 (Entra ID SSO, identity only; 2026-11-14), T-0057 (magic links for Field Workers only; product owner confirmed in the gate, 2026-11-14), T-0058 (outbound e-mail, SES), T-0059 (invite e-mail + first-password set), T-0077 (password policy + self-service reset), T-0078 (password screens) (all 2026-11-14), T-0043 + T-0048 (device registry, revoke/wipe), T-0044 (deployment settings,
    offline window), T-0045–T-0047 (NFR harness, glove/high-contrast tokens, localised template
    strings); plus the AWS production deploy in `ap-southeast-2` (TD-004, TD-171).
  - **Daily re-login dependency:** sessions end 12 h after sign-in, so the pilot inspectors
    sign in every day. **Entra ID SSO gates Pilot-0** (product owner, 2026-11-14; diff §4.5;
    T-0054, T-0055, Android sign-in in T-0143; checked by T-0146) for company staff. Internal
    staff are steered to SSO (primary option on the sign-in screen) but password sign-in stays
    allowed (product owner, 2026-11-14; diff §4.5). **External users (clients, subcontractors)
    never use SSO:** they sign in with e-mail + password, set their first password at invite
    acceptance and reset it by e-mail (product owner, 2026-11-14; diff §4.1, §4.2). Field
    Workers may also use e-mailed magic links. **Outbound e-mail is a Pilot-0 dependency**
    (diff §4.6, T-0058; TD-084 resolved).
    Offline, the device PIN keeps working for the offline grace (default 24 h after the session
    ended; the grace binds, the 7-day window is only an upper bound; diff §4.2; TD-257 resolved).
  - **Invites are in the gate (product owner, 2026-11-14; TD-262 resolved):** the invite flow
    (T-0052, T-0053), the invite e-mail and first-password set at acceptance (T-0059, T-0078)
    and self-service password reset (T-0077, T-0078) are exercised in the pilot (T-0146
    runbook). The copyable invite link stays as a fallback but can never set a password.
  - **Not in the gate (2026-11-14):** the access-request decision e-mails (T-0056; the lobby
    panel is the authoritative channel).
  - **Phase 1 subset:** T-0101, T-0103 (hierarchy + Items), T-0107 (equipment import; in T-0146's gate since PR #51 round 3), T-0110
    (field registry + CEL), T-0111 (inspection record), T-0116/T-0117 (defects, TD-167), T-0119
    (signing), T-0126/T-0127 (offline sync), T-0133 (field checkout; its P6 tests split to
    T-0166, outside the pilot — user, 2026-11-13, R2a), T-0130 (inspections register +
    calibration-gated tool pick; user, 2026-11-13, R2a), T-0141–T-0145 (Android build, SQLCipher
    store, device session + offline unlock, FCM push, sync budget).
  - **Pilot-0 slice** (cut 2026-11-13, user Q5a; spec with planner defaults in diff §6.4,
    `quality-and-inspection.md` and `asset-and-tagging.md`):
    - sync scope and download for offline (TD-162): T-0147 (picks, candidates with size, paged
      scoped pull), T-0148 (client UUIDv7 ids, answer conflict flag), T-0149 (download screen,
      local purge);
    - review/return (TD-165): T-0150 (`SUBMITTED` / `RETURNED`, approve/return, submit waits for
      attachments), T-0151 (comments on single answers), T-0152 (assignee, planned start,
      duration, overdue), T-0153 (review and returned-work screens);
    - scan-to-locate (TD-163): T-0154 (Item scan identifiers + lookup), T-0155 (camera/NFC scan,
      offline lookup);
    - photo annotation (TD-166): T-0156 (capture time, location, annotation JSON), T-0157
      (capture + annotation editor);
    - inspection PDF (TD-169): T-0158 (per inspection, annotations burned in), T-0159 (batch
      export);
    - dashboard tiles: T-0160 (progress, overdue, open defects);
    - client co-sign with the client's own PER-15 account (TD-219; user, 2026-11-13, R1a and the
      co-sign redesign; no client secret on inspector devices): (i) online PIN — T-0162; (ii) QR
      assertion signed on the client's own device — T-0173 (device signing keys), T-0175 (client
      app); (iii) provisional wet signature confirmed by the client — T-0174, T-0176; ledger
      entries T-0163; inspector-side screens and hand-back T-0164;
    - inspector capture screen and ad-hoc start (TD-214; user, 2026-11-13, R2a): T-0165;
    - video and audio notes, barcode/QR answer fields, reference images (TD-218; user,
      2026-11-13, R4b): T-0167 (field types), T-0168 (reference images), T-0169 (SAQ media
      probe), T-0170 (video/audio capture), T-0171 (barcode field + reference image display);
    - time of offline actions (user, 2026-11-13; diff §6.4.1): T-0172 (client clock stamps,
      anchors, Android `elapsedRealtime` plugin); server reconstruction T-0177, evidence
      columns in T-0177/T-0150/T-0163; `/api/v1/time` and the checkout skew block in T-0147/T-0149 with the
      setting in T-0044; badge in T-0153; `time_confidence` on the PDF in T-0158;
    - end-to-end: T-0161 (Pilot-0 journey).
  - **Also in the gate through dependencies** (T-0146 depends on them transitively): T-0102,
    T-0112, T-0113, T-0114, T-0118, T-0124, T-0128, T-0134, T-0138 (via T-0107), T-0139, T-0140
    (inspection date and self-approval, via T-0150) and T-0136 (terminology layer, via the PDF T-0158; user,
    2026-11-13, R5a). T-0104 is no longer in the gate.
  - **Not in the pilot:** inspection calendar, general recurrence and work orders (TD-217); the P6
    half of the checkout contract (T-0166).
  - **Deferred for the pilot:** P6 import/export (T-0104–T-0106, T-0125, T-0129, T-0166), Phase 2,
    Phases 3–7. The client signs on the inspector's device with their own PER-15 account and PIN
    instead of the portal (user, 2026-11-13, R1a).
- **M2 — MVP cut line (next milestone after Pilot-0):** Phase 0 + 1 + 2 + **Documents-Lite**
  (VFS tree, presigned uploads, batch inspection PDF export) + **P6 schedule round-trip**
  (~85–95 tasks).

## Plan changes

- **2026-11-13 — Phase 0 hardening merges (user request):** small hardening tasks from the
  `docs/pr-suggestions.md` triage are consolidated so they ship as fewer PRs. The absorbed tasks
  are `dropped` (bodies kept for history) and their acceptance criteria live on in the survivor:
  T-0030 (absorbed T-0064: logging/config safety), T-0060 (absorbed T-0065: real SAQ-worker RLS
  binding test), T-0031 (absorbed T-0062: UI shell fixes, after the ESLint pass), T-0029
  (absorbed T-0066: `tracker.py set --note` date fix), T-0037 (absorbed T-0061: same-second
  password-change token rejection). T-0032, T-0033, T-0063, T-0039 and T-0044 stay separate.

- **2026-11-13 — Teams re-plan (user decisions TD-228, TD-229, TD-230, TD-237):** teams are
  tenant-level and grant project access through team-project links (diff §4.1, ADR 0008).
  T-0013 re-scoped to tenant-level teams, members and links (now after T-0014 and T-0073).
  New: T-0073 (team-derived grants in the core membership store) and T-0074 (saved views shared
  with several teams; absorbs TD-242), T-0075 (teams reconcile, split from T-0013 in review), T-0076 (PER-16 Project Viewer persona). ADR 0008 accepted with grants keyed by source (`direct` | `team_link`); TD-251 resolved (links: PER-01–PER-14 except PER-09, plus PER-16).

- **2026-11-14 — Login and lobby (product-owner decisions):** access-request lifecycle with
  a stateful lobby, "Browse / join another project", token-based invites, offline grace after
  the session ends, Entra ID SSO identity only, internal staff flag (diff §4.1, §4.2, §4.4,
  §4.5). T-0040 split (now lobby discovery, S) into T-0040 + T-0050 (lifecycle API); T-0041
  split into T-0041 (lobby screens) + T-0051 (admin inbox); invites moved from T-0014 to
  T-0052/T-0053. New: T-0049 (internal flag), T-0054/T-0055 (SSO), T-0056 (e-mails, blocked by
  TD-084). Updated: T-0038 (`session_expires_at`), T-0042 (picker entry), T-0044
  (`offline_grace_hours`), T-0143 (offline grace, Android SSO), T-0146 (SSO in the gate).
  TD-043 resolved; TD-257 raised.

- **2026-11-14 — Login decisions, second pass (product owner, binding):** offline grace
  confirmed (TD-257 resolved: the 24 h grace binds edits, the 7-day window is only an upper
  bound); invites in the Pilot-0 gate (T-0052, T-0053 added to T-0146); internal staff should
  use Entra SSO, promoted as the default on the sign-in screen, while password sign-in stays
  allowed (no SSO-only enforcement; diff §4.5; T-0054, T-0055, T-0143); sign-in magic links
  limited to Field Workers, PER-14 (TD-136 resolved; diff §4.2). New: T-0057 (identity, S,
  magic-link eligibility; in the gate). Updated: T-0052 (magic-link carry-through only for
  PER-14 invites; depends on T-0057), T-0053, T-0146. Raised: TD-261 (PER-15 portal users and
  magic links, planner reading applied, not blocking).

- **2026-11-14 — PR #79 review round 1 and product-owner decisions (binding):** external users
  never use Microsoft sign-in; they are invited by e-mail, create their own password at invite
  acceptance (the `setup_token` only the invite e-mail carries is the proof; owner-confirmed
  2026-11-14, see below) and use self-service password reset by e-mail;
  local passwords follow NIST SP 800-63B rev. 4 (minimum now 8, owner-confirmed 2026-11-14, see
  below; blocklist, no composition rules; diff §4.2); outbound e-mail
  (SES, diff §4.6) is a Pilot-0 dependency. T-0057 confirmed in the gate. New: T-0058 (outbound
  e-mail, M), T-0059 (invite e-mail + first-password set, S), T-0077 (password policy + reset,
  M), T-0078 (screens, S). T-0056 restructured to access-request decision e-mails only (outside
  the gate). Magic-link eligibility read in core (`load_grants`) with a core extension point for
  invites; magic-link and reset delivery off the request path (diff §4.2). Round 2: invite
  acceptance after sign-in through a core post-authentication hook (diff §4.1); SNS bounce
  endpoint pinned to our topic, certificate host checked, deployment tenant from settings
  (diff §4.6). TD-262 raised and resolved; TD-084 resolved.

- **2026-11-14 — login follow-ups (product owner, binding):** (1) local password minimum
  **8 characters** (`KERP_PASSWORD_MIN_LENGTH` default 8, never below 8), replacing the planner
  default of 15; the rest of the NIST-style policy stands (max length, no composition rules, no
  expiry, common/context blocklist, optional breached check; diff §4.2). (2) The `setup_token`
  rule is confirmed: only the e-mailed invite can set a first password, a copied link falls
  back to the e-mail reset flow (diff §4.1). (3) **Invite re-send:** the project's PER-09
  holders and a System admin re-send a pending (or expired, never accepted) invite; the old
  `invite_token` and `setup_token` are invalidated, fresh ones issued with a new expiry, the
  invitee e-mailed again, the re-sender shown the new copyable link (`invite_token` only);
  audited and rate-limited (1 per 60 s, 5 per 24 h per invite; diff §4.1). A typeable invite
  code is deferred (TD-264, planner default: link only). (4) Hardening the permission files
  against agent edits through Bash is a later item (TD-263). Updated: T-0052, T-0053, T-0059,
  T-0077, TD-262. Raised: TD-263, TD-264.

## Key engineering decisions (baked into the spec — see consolidation diff)

| Area | Decision |
|---|---|
| Rule engine | **CEL** (celpy server / JS client), CI parity fixtures — replaces JSONLogic/ad-hoc JS |
| Queue | **SAQ** over Redis (interactive + batch) — replaces ARQ |
| Hierarchy | **ltree** `tree_path` (GiST) + `parent_id` source-of-truth + `display_path` |
| Attributes | **`custom_fields` JSONB** only (EAV `oe_asset_field_value` deleted); `oe_asset_field_def` = JSONSchema registry |
| Inspection | **one record** `oe_inspections_inspection` (kind inspection/itp/rfi) + stateless **eligibility** |
| Cutting | in-house **FFD** (1D) + **rectpack** (2D); `libnest2d`/`nest2D` banned (GPL) |
| Security | **project-scoped RLS** + 4 bypass tests; **RS256/EdDSA + JWKS**; hash-chain audit; pyHanko PAdES at turnover |
| Calibration | lifecycle + SAQ OCR + supersede-lock + hard CEL gate (expired tool blocked) |
| Frontend | 8-group IA on 4 pillars (nav mapping diff §6.1); RegisterPage/DetailPage/Dynamic Form Renderer first; **Dexie** offline outbox for web PWA apps |
| Access (2026-11-13) | One hidden tenant per deployment; System admins appoint Project Admins (`create_projects`); creator gets PER-09; lobby + access requests; project picker; one URL; admin roles never confer hold/sign-off (diff §4.1) |
| Teams (2026-11-13) | Tenant-level teams, managed by System admins; a team links to several projects with one role (persona) per project; effective access = individual memberships ∪ team-derived grants, stored as membership rows keyed by source type/id (diff §4.1, ADR 0008); new PER-16 Project Viewer; teams are not planner crews; saved views share to several linked teams |
| Sessions & devices (2026-11-13) | 15 min access, 12 h refresh from sign-in (daily re-login); hybrid device PIN, offline unlock independent of the server session; offline window 7 days default, 14 max; device registry + remote revoke/wipe (diff §4.2–§4.4) |
| Login & lobby (2026-11-14) | Access-request lifecycle (`pending/approved/rejected/withdrawn`, reason, 24 h re-request cooldown) shown in the lobby; lobby list for internal staff only (Entra sign-in or `KERP_INTERNAL_EMAIL_DOMAINS`); single-use invite tokens bound to the e-mail (invites in the Pilot-0 gate); offline grace 24 h after session end binds edits, 7-day window only an upper bound (TD-257); Entra ID SSO identity only (OIDC + PKCE, no group mapping, no auto-create, same 12 h session), promoted for internal staff with password sign-in kept; magic links for Field Workers (PER-14) only (TD-136); externals never SSO: password set at invite acceptance (e-mailed invite only) + self-service reset (NIST 800-63B style, min 8); invite re-send rotates tokens; outbound e-mail via SES in Pilot-0 (diff §4.1, §4.2, §4.5, §4.6) |
| Field client (2026-11-13) | Capacitor Android 11+, SQLCipher SQLite + file-system media, managed Play/MDM; one client storage contract and a client-agnostic sync contract (diff §6.2); NFR budgets warn now, hard gate before Pilot-0 (diff §6.3) |
| Region (2026-11-13) | Per-deployment setting: AWS `ap-southeast-2` for Australian deployments (diff §4.4) |

## Open decisions (see `TODO.md`)
TD-261 (PER-15 portal users and magic links; planner reading applied), TD-004 (hosting target), TD-072 (ADR 0004 rule-set defaults per project), TD-073 (handover first-shift trigger),
TD-039 (dropped AIP-proposal extras). TD-013 (review-gate ignores tracker) is resolved by T-0029. TD-002/005/007/008/009/012 are resolved by this merge; TD-001 by ADR 0001 (`docs/decisions/`). TD-002 is carried into code by ADR 0002 (`docs/decisions/0002-port-one-engine.md`). TD-034 (remaining Inspection-ERP core packages) is resolved by ADR 0007. TD-003 (SSO vs local JWT) is resolved; the SSO spec is diff §4.5 (TD-043 resolved 2026-11-14). Resolved 2026-11-14 by the product owner: TD-257 (offline grace binds; window is an upper bound), TD-136 (magic links PER-14 only, T-0057) and TD-262 (externals create their own password, reset by e-mail); TD-084 (outbound e-mail) resolved by diff §4.6.
