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
| **1** Asset backbone, one-engine & quality core | **ltree** hierarchy, **JSONB** field registry (EAV deleted), Items + CML stubs, P6 import/export (idempotent + unmatched-row linking), **one-engine** forms+CEL, **unified inspection record** + **eligibility**, **equipment calibration** register + hard gate, qms/construction_control/inspection_requests, NCR/punch (one template-driven issues engine, 2026-11-21), signing, completion-gate + P6 rule sets, offline sync core (Dexie), UI, **M1 freeze gate (T-0131)**; Capacitor Android build + SQLCipher store, FCM push, Pilot-0 gate (2026-11-13); Pilot-0 slice: sync scope + download, review/return, client co-sign, capture screen, scan-to-locate, photo annotation, video/audio/barcode fields + reference images, inspection PDF, dashboard tiles | **75 tasks** | P6→hierarchy; inspections gated by CEL; expired tool blocked; completion blocked by open NCR; backbone contract frozen |
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
    inbox), T-0052/T-0053 (project invites; in the gate since 2026-11-14), T-0054/T-0055 (Entra ID SSO, identity only; 2026-11-14), T-0057 (magic links for Field Workers only; product owner confirmed in the gate, 2026-11-14), T-0058 (outbound e-mail, SES), T-0059 (invite e-mail + first-password set), T-0077 (password policy + self-service reset), T-0078 (password screens) (all 2026-11-14), T-0043 + T-0048 (device registry, revoke/wipe; T-0048 absorbed into T-0051, 2026-11-20), T-0044 (deployment settings,
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
    (field registry + CEL), T-0111 (inspection record), T-0311–T-0314 (issues engine: templates, punch, NCR,
    flags; replace the dropped T-0116/T-0117, user 2026-11-21, issues.md), T-0322 (seeded
    starting issue templates), T-0318/T-0319 (issues register, raise, punch and NCR detail
    screens; product owner 2026-11-21, TD-444 resolved), T-0119
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
    2026-11-13, R5a). T-0104 is no longer in the gate. T-0247 (image derivatives, via T-0147,
    T-0157, T-0158 and T-0168; user addendum 2026-11-21).
  - **Not in the pilot:** inspection calendar, general recurrence and work orders (TD-217); the P6
    half of the checkout contract (T-0166).
  - **Deferred for the pilot:** P6 import/export (T-0104–T-0106, T-0125, T-0129, T-0166), Phase 2,
    Phases 3–7. The client signs on the inspector's device with their own PER-15 account and PIN
    instead of the portal (user, 2026-11-13, R1a).
- **M2 — MVP cut line (next milestone after Pilot-0):** Phase 0 + 1 + 2 + **Documents-Lite**
  (VFS tree, presigned uploads, batch inspection PDF export) + **P6 schedule round-trip**
  (~85–95 tasks).
  Since 2026-11-21 also the admin operations of diff §4.7 (system console T-0218–T-0225,
  communications T-0226–T-0229, runtime settings T-0230–T-0232), the user directory of
  diff §4.8 (T-0243–T-0246) and the project archive epic E-01-02 (T-0233–T-0242). None of
  them is in the Pilot-0 gate (T-0146; TD-404 asks about the ClamAV scanner).
  The phase-1 enterprise governance tasks of 2026-11-21 (attestation T-0289–T-0291, delegation
  T-0292–T-0294, external boundary T-0295, SCIM T-0298–T-0300, metering capture T-0301/T-0302,
  feature flags T-0306) are post-Pilot-0 as well; T-0146 is unchanged.

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
  audited and rate-limited (1 per 60 s, 5 per 24 h per project + invited e-mail, creations included; diff §4.1). A typeable invite
  code was deferred (TD-264; since resolved: no code). (4) Hardening the permission files
  against agent edits through Bash is a later item (TD-263). Updated: T-0052, T-0053, T-0059,
  T-0077, TD-262. Raised: TD-263, TD-264.

- **2026-11-14 — invite confirmations (product owner, binding):** no invite code, invites are
  links only (TD-264 resolved); re-send limits 1 per 60 s and 5 per 24 h per project + invited
  e-mail, re-send of expired never-accepted invites, and re-send by any PER-09 of the project or
  a System admin are confirmed (diff §4.1 Re-send). Updated: T-0052 (also: POST /invites returns
  429 at the cap), T-0053, T-0059, TD-264.

- **2026-11-15 — product-owner decisions (binding):** magic-link request lockout stays keyed
  per mailbox (tenant + e-mail), targeted-delay risk accepted (TD-224; diff §4.2); object keys
  stay `{tenant}/{project}/{module}/{sha256}` without extension, plus transient `staging/` and
  `quarantine/` prefixes (TD-213; master spec §1.1); tenants directory table + `tenant_id` FK
  split into its own task (TD-271); PER-15 Client Portal Users sign in by password/SSO, no
  sign-in magic links (TD-261; diff §4.2; E-07-01 reworded); the owner is the sole
  collaborator (agents act as the owner login); fork PR workflows from external contributors
  need approval (setting applied, owner-reported) and unexpected fork PRs are closed; no
  human-approval requirement until a second collaborator (TD-223; WORKFLOW.md); coating/Grating ITR interim confirmed, still open with
  the client (TD-096). New: T-0079 (platform, M). Updated: T-0023 (depends on T-0079), T-0077,
  E-07-01, TD-032. Raised: TD-276 (risk), TD-277 (later).

- **2026-11-15 — product-owner decisions, second batch (binding):** one account per `device_id`
  for Pilot-0 (T-0043 behaviour, another account's enrolment is 409); the client keys its local
  store per account so a revoke wipes only that account's data; whether pilot crews share
  tablets is confirmed with the pilot team, and if so a shared-device task is added before the
  pilot (TD-283; diff §4.3; TD-293). Rule-set upgrade offers, "Try it" and the drift report
  follow the T-0036 reading (offers read by set readers, accepted and tried only by the set's
  editor so PER-09 never accepts a `check` update; drift report PER-13/PER-01 within their
  portfolio; `removed_by_<layer>` = deleted copy whose default changed since the copy, on offer
  until reset; first-time system sets auto-copied to the tenant layer; routes by set id) (TD-281;
  ADR 0004 Amendment G). Native body transport: `KERP_NATIVE_APP_ORIGINS` requires
  `KERP_WEB_ORIGIN`, the API's own origin never gets it, and it also covers magic-link redeem and
  PIN unlock (TD-285; diff §4.2). Unauthenticated `wipe-confirmed` and the revoked-device status
  oracle are accepted for development and fixed before pilot go-live (TD-286, TD-291; diff §4.3).
  New: T-0080 (identity, S, phase 0, order 10). Updated: T-0143 (per-account store, depends on
  T-0080). Raised: TD-293 (decision, ask the pilot team).

- **2026-11-20 — Phase 0 bundling (user request):** small ready Phase 0 tasks ship together, one
  PR per bundle; the absorbed task is `dropped` (body kept) and its acceptance criteria live on in
  the survivor. T-0051 absorbs T-0048 (devices screen; both Admin-group screens on merged
  backends; size M). T-0146 now depends on T-0051 for the devices screen.

- **2026-11-20 — Phase 0 bundling (user request), bundle 3:** T-0063 absorbs T-0056 (projects
  follow-ups + access-request decision e-mails; both small backend follow-ups on merged modules).
  T-0056 is `dropped` (body kept); its acceptance criteria live on in T-0063. The decision e-mails
  stay outside the Pilot-0 gate.

- **2026-11-20 — Phase 0 bundling (user request), bundle 2:** T-0033 absorbs T-0032 (frozen
  migration DDL + CI hardening 2; both platform hardening on disjoint files; size M). T-0032 is
  `dropped` (body kept); its acceptance criteria live on in T-0033.

- **2026-11-21 — admin operations, communications, runtime settings, project archive and the
  user directory (user-supplied spec + addendum; planner pass):** spec added as diff §4.7
  (system console, legal hold, communications, runtime settings registry), §4.8 (account state,
  presence, force sign-out, resend invite, memberships across projects), §6.5 (admin screens),
  the §6.1 Admin row, and `modules/documents-and-mdr.md` §Project Export & Retention Archive.
  The paste's ids T-0073/T-0074/T-0075 were already used by done tasks and T-0178 was size L,
  so the work got new ids above T-0217: pasted T-0073 → T-0218–T-0225 (phase 0), T-0074 →
  T-0226–T-0229 (phase 0), T-0075 → T-0230–T-0232 (phase 0, extends T-0044), T-0178 → epic
  E-01-02 with T-0233–T-0242 (phase 1); the addendum to T-0014 (done) and T-0026 → T-0243–T-0246
  (phase 1, user 2026-11-21: Pilot-0 and T-0026 do not need them; T-0026 unchanged). Where the
  paste disagreed with the spec, the spec won: send log, not a second e-mail ledger (§4.6);
  hashed recipients; text-only template overrides; count-only `SECURITY DEFINER` stats with an
  ADR; System admins read no project record without a membership; auth/security values stay
  deployment config; no magic links outside PER-14; derived user status over `is_active`; real
  table names (`auth_user`, `oe_projects_project`, `mail_send_log`); ADRs for every core-table
  change. Raised: TD-396–TD-407. Updated: T-0026, T-0146 (notes only), T-0140, T-0156, T-0162,
  T-0167, T-0169, T-0174 (registry entries), TD-046, TD-175, TD-211, TD-297 (resolved by T-0232).
  Second addendum the same day (image derivative tiers): `quality-and-inspection.md` §Image
  derivatives and §Annotation frame, four `uploads_media` registry entries (diff §4.7). New:
  T-0247 (uploads, M, phase 1; in the Pilot-0 gate through T-0147/T-0157/T-0158/T-0168).
  Updated: T-0147, T-0156, T-0157, T-0158, T-0159 (note), T-0168, T-0230, T-0235, T-0239.
  Raised: TD-408 (reconciliation with the upload model, ADR 0005 path, EXIF frame).

- **2026-11-21 — standalone inspections, public capture, post-submit automation, QA
  verification workbench, verification SLA, expiry alerts, readiness conflicts, shelf-life
  extensions and the calendar (user-supplied tasks T-0179–T-0184 + E-04-15; planner pass):**
  spec in `quality-and-inspection.md` (§Standalone inspections, §Public capture links,
  §Post-submit automation, §QA verification queue and two-lane workbench, §Verification SLA and
  pending-approval banners, §Expiry lead-time alerts, §Schedule readiness conflicts),
  `materials-and-traceability.md` (§MTR verification gate, §Shelf-life extension), diff §5
  (equipment additions), `documents-and-mdr.md` (§Material traceability in the MDR),
  `planner-and-work-plans.md` §15 (calendar). The user's ids were free on every branch and are
  kept: T-0179 (categories), T-0180 (post-submit rules), T-0181 (verification queue), T-0182
  (verification SLA), T-0183 (expiry alerts), T-0184 (shelf-life extension, **Phase 3**: no
  material batch exists before E-03-03; TD-421), E-04-15 (calendar epic, Phase 4,
  needs-breakdown). Splits to stay S/M got T-0249–T-0268: T-0249 fleet classes (blocked TD-412),
  T-0250 equipment anchor (blocked TD-411, ADR 0014 proposed; re-scoped 2026-11-21 to equipment links, see below), T-0251 registers + tile toggle,
  T-0252–T-0255 public capture (blocked TD-410, ADR 0013 proposed), T-0256 notify action,
  T-0257 follow-ups + tagout gate (node/Item; equipment part T-0288, waits on TD-411), T-0258/T-0259 screens, T-0260 equipment.activate, T-0261
  credential.activate, T-0262 two-lane window, T-0263 banner, T-0264 SLA for overrides and
  follow-ups, T-0265 material_cert.verify (Phase 2), T-0266/T-0267 readiness conflicts,
  T-0268 offline batch bundle (Phase 3). Where the paste disagreed with the spec, the spec won:
  tenant from configuration only under a proposed ADR (no row-returning RLS bypass); equipment
  anchoring only under a proposed ADR; module-prefixed table names
  (`oe_inspections_public_link`, `oe_inspections_post_action`, `oe_inspections_follow_up`);
  automations only restrict; e-mail carries code/name + link only and no SMS; escalation does not
  widen approval; `verification.sla` and `compliance.expiry_alerts` use ADR 0004's `notify`
  kind (no new kind); the M1-frozen override table is not altered. **Pilot-0 gate unchanged**:
  T-0112, T-0118, T-0139, T-0147 and T-0160 got notes only; none of the new tasks is in T-0146.
  Updated: T-0111, T-0112, T-0118, T-0122, T-0147, T-0160, T-0205, T-0207 (notes), E-03-03,
  E-03-04, E-03-07, E-04-02 (now depends on T-0183/T-0266), E-04-07, E-04-12, E-05-05 (now
  depends on T-0184/T-0265). Raised: TD-410–TD-422.
  Fifth addendum the same day: **product substitutions** (user's T-0185, id kept):
  `materials-and-traceability.md` §Product substitutions, MDR rule in `documents-and-mdr.md`.
  T-0185 (register + internal_qa route, M, phase 1), T-0269 (baseline approved product list, S;
  TD-424), T-0270 (pure CEL helper `is_product_approved` with parity fixtures over a resolved
  approvals map, S; TD-425), T-0271 (offline bundle, `substitution_id` binding, inspection/PDF
  note, M), T-0272 (tablet selector badge, S), T-0273 (client_tq route, S, **phase 5** with the
  TQ register E-05-04). Self-approval of internal_qa substitutions is TD-423 (default: four
  eyes); PER-13 approval follows TD-422. T-0139, T-0147, T-0158, T-0165 notes only (Pilot-0
  unchanged); E-05-05 now also depends on T-0185/T-0271; T-0181 gains target kinds
  `material_shelf_life_extension` and `material_substitution`.
  PR #137 review round 3 suggestions folded in here (PR #137 itself unchanged): T-0237/T-0239 and
  `documents-and-mdr.md` §Access revive only `direct` membership rows soft-deleted by the
  deletion itself (tests for an earlier team-link row and a PER-07 grant on another site), and an
  uploaded-zip restore needs the caller's own upload (`file_id` readable under upload RLS, else
  404).

- **2026-11-21 — system_admin global visibility (user decision) and support impersonation
  (user-supplied task; planner pass):** the user amended diff §4.1: a `system_admin` lists,
  opens, reads and administers every project of the tenant without a membership, never with `A`
  or `S` (ADR 0016, **accepted**; edge spec §5/§6; core RLS change). New: T-0274 (tenancy, M,
  phase 0: `project_isolation_policy` + frozen migration on top of in-flight T-0033, ORM scope,
  the four RLS-bypass contract tests updated, not weakened), T-0275 (identity, S, phase 0:
  capabilities, project lists, SoD warning), T-0276 (frontend, S, phase 0: picker and
  All-Projects). T-0014, T-0042 and T-0033 untouched. TD-396 resolved; PR #137 content
  simplified (membership gates and count-only `SECURITY DEFINER` functions for admins dropped):
  diff §4.7, §6.1, §6.5, `documents-and-mdr.md` §Access/§Package/§Registry/§Restore, T-0219,
  T-0221 (no longer depends on T-0219), T-0222, T-0224, T-0233, T-0237, T-0239, T-0240, T-0241;
  ADR 0013 context sentence. Raised: TD-427 (access log of non-member admin reads; default no).
  The user's support impersonation task "T-0076" (id taken: PER-16 Project Viewer, done) became
  T-0277 (token + target RLS, M, phase 0), T-0278 (central guardrail + route contract test, M,
  phase 0), T-0279 (dual-identity ledger hash v3, S, phase 0), T-0280 (Admin → Users action and
  banner, M, phase 1, after T-0246), all **blocked by TD-426** (ADR 0015 proposed; planner
  default: read-only "view as"). Spec: diff §4.2 *Support impersonation*, §4.7 registry entry
  `impersonation_max_minutes`, §6.5, edge spec §5. **Pilot-0 gate unchanged.**

- **2026-11-21 — AI ITP authoring, rule-based ITP instantiation and propagation, scope-drift
  watchdog (user-supplied T-0187, T-0188, T-0189; planner pass):** spec in
  `quality-and-inspection.md` §ITP authoring, assignment and propagation, `change-of-scope.md`
  §5 (outbox consumer), ADR 0004 Amendment H (rule kind `itp_assignment`, in force as a planner default; TD-430 may revise it before the M1 freeze). The
  user's ids were free on every branch and are kept. Phase 1: T-0188 (rules, manual
  instantiation, auto-generate proposals, Approve & Activate; M), T-0281 (instance edits +
  propagation API; M), T-0282 (activation matrix UI; M), T-0283 (propagation wizard UI; S).
  Phase 5 (E-05-08): T-0187 (AI draft + rewrite + human publish; M), T-0284 (two-pane screen;
  M), T-0285 (outbox events from scope changes, P6 import, assets, tasks; S), T-0189 (drift
  detection + notification; M), T-0286 (review actions; S), T-0287 (review drawer; M). Where the
  paste disagreed with the spec, the spec won: no `oe_qms_itp_template` table (one-engine
  template, approved = published), `pending_qa_review` is a qms instance status, not a record
  state, propagation never touches started or signed lines (T-0119), the AI provider stays
  deployment configuration (TD-398, TD-078 Australian region), AI output is a `source="ai"`
  proposal applied only by a human, `system_admin` never approves (ADR 0016), e-mail templates
  via T-0227, events through the ADR 0007 outbox (E-05-07). **Pilot-0 gate unchanged.**
  Raised: TD-428 (template structure/approval/instance), TD-429 (`pending_qa_review`,
  propagation), TD-430 (rule kind). Updated: TD-398, TD-422 (task refs), E-05-08, E-04-14 (notes).

- **2026-11-21 — enterprise governance roadmap (user roadmap; coordinator assessment approved by
  the user; planner pass):** e-signature attestation, narrow delegation of authority, external
  party access, SCIM deprovisioning, usage metering, feature flags, API keys; IP allowlisting and
  the escalation matrix as notes. Spec: `quality-and-inspection.md` §Signing attestation and
  §Delegation of authority, diff §4.7 (*Feature flags*, two registry entries), §4.9 (SCIM,
  external parties, API keys and service accounts, network restrictions), §4.10 (usage metering),
  §6.5 (two admin rows), edge spec §5.2 (pending external party persona), master spec MOD-01
  upstream line, `materials-and-traceability.md` and `documents-and-mdr.md` (attested approvals,
  MDR signature evidence), `docs/deploy/network-access.md` (IP allowlisting as WAF / Conditional
  Access). ADR 0017 (machine credentials: tenant bound from the SCIM token / API key under the
  deployment tenant, accepted by the user's approval; service-account column a planner default).
  New, phase 1: T-0289 (attestation backend, M), T-0290 (ceremony UI, M), T-0291 (consumers +
  PDF block, M), T-0292 (delegations, M), T-0293 (delegate routing and on-behalf sign-off, M),
  T-0294 (delegation screens, S), T-0295 (internal-commercial boundary for external accounts, S),
  T-0296 (external inspection party persona, M, **blocked TD-435**), T-0298/T-0299 (SCIM tokens
  and /Users, M), T-0300 (provisioning screen, S), T-0301 (storage ledger, M), T-0302 (daily usage
  snapshot, M), T-0306 (feature flags, M). Phase 2: T-0297 (external certificate submissions, M,
  **blocked TD-435**). Phase 5 (E-05-07): T-0307 (service accounts + API keys, M), T-0308 (key
  authentication, A/S never, M), T-0309 (screen, S). Phase 6 (E-06-05): T-0303 (soft-quota
  advisories + cost-centre reference, M), T-0304 (monthly statements + CSV, M), T-0305 (screens,
  M). Where the roadmap disagreed with the spec, the spec won: attestation kept out of T-0119 so
  it stays M and **the Pilot-0 gate (T-0146) is unchanged** (no new task is in it); attestation
  lives inside the ledger payload (hash format unchanged); delegation never confers authority and
  keeps SoD; no cross-tenant guests; a new persona only after a decision; metering DDL corrected
  (no expressions in keys, `UNIQUE NULLS NOT DISTINCT` on PostgreSQL 16, partial indexes over
  live rows, no `ON DELETE CASCADE`, soft delete, real table names, `BaseRecordMixin` + RLS, SAQ
  cron on the batch worker, cold archives `DEEP_ARCHIVE`, seats from `last_active_at`, rollups
  shared with T-0219, no currency, cost centre as an external reference, e-mails link +
  code/name only); feature flags never relax authorization. Updated: T-0184 (attestation AC,
  depends on T-0289); notes on T-0115, T-0119, T-0150, T-0158, T-0162, T-0181, T-0182, T-0185,
  T-0219, T-0226, T-0230, T-0233, T-0237, T-0240, T-0243, T-0244, T-0247, T-0273, T-0278,
  E-04-12, E-05-04, E-05-05, E-05-07, E-06-05, E-06-07. Raised: TD-433 (attestation details),
  TD-434 (delegation), TD-435 (external party persona, blocking), TD-436 (metering attribution),
  TD-437 (SCIM), TD-438 (machine credentials), TD-439 (feature flags), TD-440 (later: in-app IP
  rules).

- **2026-11-21 — template menus, template settings, project-level inspections and links
  (product-owner decisions, binding; planner pass on PR #145):** two menu items, one shared
  designer: **Templates** (every inspection template, filterable by area, issue category, formal,
  audit, status, kind) and **Issues** (register + issue templates), diff §6.1. Presets for "New
  template"; settings on the revision: area (= `category`, plain labels, `health_safety` added),
  issue category (`issue_category`; the planner's `discipline` replacement was reverted by the
  product owner, see TD-451), formal tick (informal = record-only, submit final,
  outside MDR/progress/KPIs; ITP-chain always formal), audit tick with internal/external scope,
  linked issue types (one used, several picked, none → category × type default); changing area,
  formal or audit needs QA authority or PER-09 and makes a new revision. **ADR 0003 amendment
  2026-11-21:** `qa_itp` inspections keep the asset anchor; every other area is project-level with
  optional informational location and links (asset, material, equipment); equipment is a link
  (ADR 0014 superseded, TD-411 resolved; TD-412 stays open). Audit/H&S issues may be
  project-level: never block work completion; with `mdr_effect = blocks` they block every MDR of
  the project, or only MDRs covering their location (TD-443 resolved). Spec:
  `quality-and-inspection.md` §Standalone inspections, §Template settings, §Project-level
  inspections; `issues.md` §2, §3.1, §5 (`saved_view` channel), §6, §8; `documents-and-mdr.md`
  §Gating invariant. New, phase 1: T-0324 (template settings, M), T-0325 (project-level
  inspections, location, links, M), T-0326 (project-level issues, M), T-0327 (linked issue types,
  S), T-0328 (Templates menu, M), T-0329 (project-level screens, M), T-0330 (raise from an
  inspection, S). Re-shaped: T-0250 (equipment links, unblocked, S), T-0288, T-0316 (names a
  linked issue type; depends on T-0324/T-0326/T-0327), T-0320 (Issues menu → issue templates,
  S → M), E-01-03; T-0251 depends on T-0324/T-0325. Forward-compatible criteria only on the gate
  tasks T-0312 and T-0314 (plus round-4 review fixes on T-0314); notes on T-0111, T-0112,
  T-0179, T-0180, T-0249, T-0252, T-0257, T-0259, T-0311, T-0318, T-0322, E-03-03. **Pilot-0 gate
  unchanged** (none of the new or re-shaped tasks is in T-0146's closure). Raised: TD-451
  (planner defaults of this pass, not blocking).

- **2026-11-21 — product-owner approvals (binding):** TD-445 approved as-is (`mdr_effect` alone
  governs `mdr.compile`; Cat A punch templates default to `blocks`; Cat A always blocks work
  completion). TD-451 approved except "`discipline` replaces `issue_category`": inspection
  templates keep the optional `issue_category`, pointing at an issue category list that also
  holds non-discipline categories (vehicle, equipment, pre-start, general; a vehicle pre-start
  has no discipline); the Templates list filters by area and issue category; no `discipline`
  field. Updated: `quality-and-inspection.md`, `issues.md` §2/§8, T-0316, T-0324, T-0326,
  T-0327, T-0328, E-01-03, T-0311 (note). Pilot-0 gate unchanged.

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
| Admin visibility (2026-11-21) | `system_admin` sees and administers every project of its tenant without a membership (RLS `app.is_system_admin`, server-bound, never cross-tenant), never `A`/`S` (ADR 0016) |
| Teams (2026-11-13) | Tenant-level teams, managed by System admins; a team links to several projects with one role (persona) per project; effective access = individual memberships ∪ team-derived grants, stored as membership rows keyed by source type/id (diff §4.1, ADR 0008); new PER-16 Project Viewer; teams are not planner crews; saved views share to several linked teams |
| Sessions & devices (2026-11-13) | 15 min access, 12 h refresh from sign-in (daily re-login); hybrid device PIN, offline unlock independent of the server session; offline window 7 days default, 14 max; device registry + remote revoke/wipe (diff §4.2–§4.4) |
| Login & lobby (2026-11-14) | Access-request lifecycle (`pending/approved/rejected/withdrawn`, reason, 24 h re-request cooldown) shown in the lobby; lobby list for internal staff only (Entra sign-in or `KERP_INTERNAL_EMAIL_DOMAINS`); single-use invite tokens bound to the e-mail (invites in the Pilot-0 gate); offline grace 24 h after session end binds edits, 7-day window only an upper bound (TD-257); Entra ID SSO identity only (OIDC + PKCE, no group mapping, no auto-create, same 12 h session), promoted for internal staff with password sign-in kept; magic links for Field Workers (PER-14) only (TD-136); externals never SSO: password set at invite acceptance (e-mailed invite only) + self-service reset (NIST 800-63B style, min 8); invite re-send rotates tokens; outbound e-mail via SES in Pilot-0 (diff §4.1, §4.2, §4.5, §4.6) |
| Field client (2026-11-13) | Capacitor Android 11+, SQLCipher SQLite + file-system media, managed Play/MDM; one client storage contract and a client-agnostic sync contract (diff §6.2); NFR budgets warn now, hard gate before Pilot-0 (diff §6.3) |
| Region (2026-11-13) | Per-deployment setting: AWS `ap-southeast-2` for Australian deployments (diff §4.4) |
| Enterprise governance (2026-11-21) | Formal sign-offs attested (re-auth + intent + device/IP in the ledger); narrow delegation that never confers authority; SCIM deprovisioning; machine credentials bind the tenant from the deployment (ADR 0017), never `A`/`S`; metering counts only, soft limits never block; feature flags gate availability only; IP allowlisting in WAF / Conditional Access |

## Open decisions (see `TODO.md`)
TD-433 (signing attestation details), TD-434 (delegation open points), TD-435 (external inspection party persona, blocks T-0296/T-0297), TD-436 (metering attribution), TD-437 (SCIM), TD-438 (machine credentials, ADR 0017), TD-439 (feature flags), TD-410 (public capture links, ADR 0013 proposed), TD-412 (fleet in the Phase-1 equipment register), TD-418 (escalation and approval authority), TD-419 (calendar iCal feed), TD-422 (PER-13 shelf-life approval), TD-423 (self-approval of internal-QA substitutions), TD-426 (system admin impersonation, ADR 0015 proposed, blocks T-0277–T-0280), TD-427 (access logging of system_admin reads without membership, ADR 0016), TD-430 (ADR 0004 Amendment H rule kind `itp_assignment`, planner default in force until revised), TD-431 (QA reviewer self-verification of own uploads), TD-096 (coating/Grating ITR, with the client; interim confirmed 2026-11-15), TD-293 (shared tablets in the pilot, ask the pilot team before go-live), TD-004 (hosting target), TD-072 (ADR 0004 rule-set defaults per project), TD-073 (handover first-shift trigger),
TD-039 (dropped AIP-proposal extras). TD-013 (review-gate ignores tracker) is resolved by T-0029. TD-002/005/007/008/009/012 are resolved by this merge; TD-001 by ADR 0001 (`docs/decisions/`). TD-002 is carried into code by ADR 0002 (`docs/decisions/0002-port-one-engine.md`). TD-034 (remaining Inspection-ERP core packages) is resolved by ADR 0007. TD-003 (SSO vs local JWT) is resolved; the SSO spec is diff §4.5 (TD-043 resolved 2026-11-14). Resolved 2026-11-14 by the product owner: TD-257 (offline grace binds; window is an upper bound), TD-136 (magic links PER-14 only, T-0057) and TD-262 (externals create their own password, reset by e-mail); TD-084 (outbound e-mail) resolved by diff §4.6. Resolved 2026-11-15 by the product owner: TD-224 (per-mailbox magic-link cap), TD-213 (object key layout), TD-271 (T-0079), TD-261 (PER-15 password/SSO) and TD-223 (PR principals; residual risk TD-276). Resolved 2026-11-15 (second batch): TD-283 (one account per device id, per-account client store; follow-up TD-293), TD-281 (rule-set upgrade permissions, ADR 0004 Amendment G) and TD-285 (native-origin allow-list); TD-286 and TD-291 (device risks) accepted for development and covered by T-0080 before pilot go-live.
