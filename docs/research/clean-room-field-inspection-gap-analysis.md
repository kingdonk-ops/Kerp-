# Field Inspection clean-room spec → KERP gap analysis

**Date:** 2026-11-11, re-verified against `dev` on 2026-11-13 (after T-0010, #39).
**Input:** `SOURCE-clean-room-spec.md`, the "Field Inspection App — Clean-Room Requirements &
Technical Spec" (2026-11-11), including its answered open questions. The source is **not** in
this repository.
**Compared against:** the authoritative spec set in [`docs/spec/`](../spec/README.md)
(consolidation diff → master + edge specs → `modules/`), `docs/decisions/` (ADR 0001–0007),
and the tracker on `dev` (T-0001…T-0209, TD-001…TD-160).

> **Clean-room note.** This analysis uses only the clean-room specification and KERP's own
> documents. No material from the incumbent product (binaries, decompiled code, assets, API
> contracts or screenshots) was used, and none may be added to this repository. Requirement
> sources for anything raised here are the clean-room spec and our own procedures.

> **Status.** This is research input, not a build target. Agents build to `docs/spec/` only.
> Anything here that should change the spec goes through the TODOs in §7.

## 0. What changed from the 2026-11-11 draft

The draft was written against the pre-consolidation documents and an early tracker snapshot. On
re-verification:

- **TODO ids were wrong.** TD-011…TD-022 already existed for other topics. The real ids are
  in §7.
- **TD-003, TD-004, TD-007 and TD-009 were already resolved** (Entra ID before pilot via TD-043,
  AWS production, one tenant per deploy, M2 cut line). None of them can be "closed by" this
  analysis. The pilot slice in §6 is now a proposal to amend TD-009, not an answer to it.
- **Task numbers were stale.** Audit is T-0006 (not T-0013), uploads are T-0016 (not T-0015),
  security headers are T-0021 (not T-0022), signing is T-0119 (not T-0121), and offline sync is
  T-0126/T-0127 (T-0125 is P6 rule sets). T-0112 is the calibration register, not PDF reports.
- **Superseded engine references were removed.** Conditions use **CEL**, not JSONLogic. Jobs run
  on **SAQ**, not ARQ or Procrastinate. MODULE-SPEC-11, ADR-001 and the E-xx review items are
  archived or not in the repo.
- **Several verdicts changed.** The current spec already covers a sync pull endpoint and conflict
  rules (master §4.3), DataMatrix/NFC scan-to-locate as a user story, configurable defect and
  observation issue types (TD-066), a findings register (TD-106), email over SES (TD-084), and
  per-locale terminology (T-0136). The offline window in the spec is **7 days** (master §6),
  not 72 h. The field-type palette does **not** include video or audio.

## 1. Summary

| Verdict | Count | Meaning |
|---|---|---|
| ✅ Covered | 9 | The KERP spec or tracker already covers it, possibly in a later phase |
| 🟡 Partial | 26 | The building block exists, but a behaviour, a field or a task is missing |
| 🔴 Gap | 7 | Not in the KERP spec |
| ⚠️ Conflict | 3 | The KERP spec or a resolved decision says something different |

Counts are over 30 FRs and 15 NFRs. NFR-09 is n/a.

**The five things that matter most**

1. **The client is decided, but its local store is not.** TD-044 (decided 2026-11-10) settles
   on one React codebase shipped as a PWA and as a Capacitor-wrapped Android build. The
   clean-room choice of React Native is therefore out. The spec still contradicts itself on the
   local store: master §1 says embedded SQLite (WASM/OPFS on web, native via Capacitor), while
   the edge spec and T-0127 say a Dexie/IndexedDB outbox. IndexedDB will not reliably hold 500
   inspections plus 10 GB of media (NFR-02), and nothing specifies encryption at rest (NFR-06).
   → TD-161.
2. **Sync pull exists as a protocol but not as a scope.** Master §4.3 defines `push-mutations`,
   `pull-deltas?since_version` and the conflict rules, and T-0126 builds the push side. Still
   missing: per-device and per-project pull scope, has-more paging, choosing what to download
   (with size), local purge, flagging a stale base, and blocking submit until attachments are
   complete. ADR 0005's field checkout lease is the nearest thing to download selection.
   → TD-162.
3. **Defects are mostly configured, but the close-out loop is not.** TD-066 (issue types
   DEFECT/OBSERVATION as versioned configuration) and TD-106 (separate findings register) cover
   the configurable scheme. Still missing: the clean-room lifecycle open → action assigned →
   rectified → verified closed by a re-inspection. → TD-167.
4. **Three settings conflict:**
   - Offline window: 14 days in the clean-room spec vs 7 days in master §6.
   - Data residency: EU in the clean-room spec (NFR-10) vs KERP's Australian context. The spec
     names no hosting region, only an Australian region for the AI provider and an
     `Australia/Perth` default timezone. TD-004 settles AWS but not the region.
   - Per-BU theming vs TD-007 (one tenant per deploy).

   → TD-170, TD-171, TD-172.
5. **The clean-room pilot is much smaller than M2.** The pilot is 8 inspectors, one project,
   Android only. M2 (TD-009) is Phase 0+1+2 + Documents-Lite + P6 round-trip, about 85–95 tasks.
   §6 proposes a narrower "pilot-0". It amends a resolved decision, so it needs the user.
   → TD-173.

## 2. Functional requirements → KERP

| ID | Requirement (short) | KERP home | Verdict | Notes / action |
|---|---|---|---|---|
| FR-01 | SSO via Entra ID; local accounts for contractors | T-0008 JWT/JWKS, T-0009 magic-link + PIN, TD-003 → TD-043 (Entra before first pilot) | ✅ | Already decided. TD-043 must land before the pilot. |
| FR-02 | Offline session continues 14 days | Master §6: "up to 7 consecutive days offline" | ⚠️ | Conflict → TD-170 |
| FR-03 | PIN / biometric unlock on resume | T-0009 6-digit PIN local unlock; master MOD: biometric only for sign-off | 🟡 | Biometric unlock not specified → TD-164 |
| FR-04 | Multiple tenants / BUs, each themed | TD-007: one tenant per deploy, tenant_id + RLS kept | ⚠️ | Conflict → TD-172 |
| FR-05 | Projects; users see only assigned projects | T-0010 tiers × 15 personas × project/site scope, T-0014 membership | ✅ | KERP is richer |
| FR-06 | Hierarchical equipment register, unique tag per project | T-0101 ltree hierarchy + code uniqueness, T-0103 Items | ✅ | Mapping in §4 |
| FR-07 | Bulk CSV/XLSX import with validation report | T-0107 ingestion wizard (dry run, dedup), T-0138 mapping profiles | ✅ | |
| FR-08 | Locate by barcode / QR / NFC scan | Master user story (DataMatrix/NFC → hierarchy), edge spec WebRTC barcode | 🟡 | No identifier columns on Items, no lookup endpoint, no NFC path for PWA vs Capacitor → TD-163 |
| FR-09 | Visual template builder, all listed field types | `core/fields` palette (numeric, text, measurement, photo, signature, instrument ref, calculated), T-0110 | 🟡 | Palette is narrower than the clean-room list → TD-166 |
| FR-10 | Conditional visibility, required, ranges, cross-field | CEL evaluator (T-0110), `run_checks` (T-0018) | ✅ | Clean-room `visible_if` maps to CEL, not JSONLogic |
| FR-11 | Versioned templates; inspection locked to its version | quality-and-inspection: records pin `template_revision_id` (ADR 0003 §6) | ✅ | |
| FR-12 | Reference images / diagrams on questions | — | 🔴 | → TD-166 |
| FR-13 | Schedule per item: planned start, duration, assignee; calendar | Planner work plans (E-04-12) have calendars; inspections have no planned start/duration | 🟡 | → TD-165 |
| FR-14 | Recurring inspections + work orders | Recurrence only for scaffolds (`next_due_at`); workpacks E-06b-03 (Phase 6b) | 🟡 | No general recurrence; workpacks heavy and late → TD-165 |
| FR-15 | Pick inspections to download; show state and size | ADR 0005 / T-0133 field checkout lease | 🟡 | No selection UI or size → TD-162 |
| FR-16 | Ad-hoc inspections offline against downloaded items | T-0126 `client_op_id`; BaseRecordMixin uuid id (T-0005) | 🟡 | Client-generated record ids on create not stated → TD-162 |
| FR-17 | Autosave every field; resume after crash | T-0127 Dexie outbox | 🟡 | Device durability depends on TD-161 |
| FR-18 | Photo annotation, auto GPS + timestamp | `photo` field; PostGIS spatial anchoring; markups E-05-02 (drawings only) | 🟡 | Annotation on a captured photo (stored JSON, burned in on export) → TD-166 |
| FR-19 | Video and audio notes | — | 🔴 | Not in palette → TD-166 |
| FR-20 | Defects: type, severity, location, photos; scheme per project | TD-066 issue types (open), T-0117 punch Cat A/B/C, T-0116 NCR, TD-106 findings register | 🟡 | Scheme covered by TD-066 once specced |
| FR-21 | Defect lifecycle → verified closed by re-inspection | T-0117 punch, T-0116 NCR dispositions | 🟡 | No close-by-inspection link, action owner or due → TD-167 |
| FR-22 | Signatures with name/role/time; witness/hold points | T-0119 hash-chained signing, T-0115 inspection requests (witness), TD-063 | ✅ | Client co-sign on own device = portal v2 (E-07-02, Phase 7). Pilot uses inspector's device. |
| FR-23 | Submit → review → approve/return; comments on answers | T-0111 lifecycle via `run_checks` | 🟡 | No explicit `returned` state; no addressable answer for comments → TD-165 |
| FR-24 | Server PDF report per inspection; bulk export | Consolidation §7 M2 Documents-Lite "batch inspection PDF export" | 🟡 | In M2 scope but **no task** → TD-169 |
| FR-25 | Dashboards: progress, overdue, open defects | E-06-05 reporting/dashboards (Phase 6) | 🟡 | Pilot needs three tiles → TD-173 |
| FR-26 | Search by tag, status, date, answer value | Register views, E-05-06 search (Phase 5) | 🟡 | Answer-value search needs a JSONB/GIN index; note for T-0111 |
| FR-27 | Push and email notifications | T-0019 in-app; TD-084 email via AWS SES (decided) | 🟡 | Push (FCM) not specified → TD-168 |
| FR-28 | Audit: user, device, time, before/after | T-0006 hash-chain audit ledger | 🟡 | No `device_id`; diff not specified → TD-164 |
| FR-29 | Public REST API + webhooks | OpenAPI + generated client (T-0024), E-05-07 webhooks (Phase 5) | ✅ | |
| FR-30 | Retention rules + legal hold per project | — | 🔴 | Priority C → TD-175 |

## 3. Non-functional requirements → KERP

| ID | Requirement | KERP | Verdict | Notes |
|---|---|---|---|---|
| NFR-01 | All inspection features offline | Master §4.3, T-0126/T-0127, edge spec tier-1 screens | 🟡 | Edge spec covers three tier-1 screens; full inspection capture offline depends on TD-161/TD-162 |
| NFR-02 | 500 inspections + 10 GB media on device | — | 🔴 | Rules out IndexedDB as the media store → TD-161 |
| NFR-03 | No data loss; idempotent uploads | T-0126 `client_op_id` idempotency, T-0016 sha256 verify | ✅ | Suggest kill-mid-sync chaos tests in T-0126 |
| NFR-04 | 1,000-record delta sync < 30 s on 4G | Master §4.3 `pull-deltas` | 🟡 | Protocol exists, no budget → TD-174 |
| NFR-05 | 300-question form opens < 1.5 s | — | 🔴 | No performance budget → TD-174 |
| NFR-06 | SQLCipher, key in Keystore | — | 🔴 | → TD-161 |
| NFR-07 | TLS 1.2+, certificate pinning | T-0021 HSTS/CSP | 🟡 | Pinning only possible in the Capacitor build → TD-164 |
| NFR-08 | Remote wipe (MDM) + device revocation | T-0008 jti revocation | 🟡 | No device entity → TD-164 |
| NFR-09 | Least-privilege permissions | — | n/a | Mobile client concern |
| NFR-10 | GDPR, EU region | Spec silent on hosting region; Australian context | ⚠️ | → TD-171 |
| NFR-11 | Android 11+ (iOS dropped per answers) | TD-044: Capacitor Android build | 🟡 | Minimum Android version not set → TD-161 |
| NFR-12 | Glove targets ≥ 48 dp, outdoor high contrast, WCAG 2.1 AA | T-0024 design tokens | 🟡 | → TD-174 |
| NFR-13 | API 99.5 %; mobile unaffected by outages | TD-004: production on AWS | 🔴 | No availability target or HA design → TD-176 |
| NFR-14 | Crash reporting, logs, per-device sync health | T-0021 observability, structured logs | 🟡 | Per-device sync health missing → TD-164 |
| NFR-15 | MDM / managed Google Play only | TD-044 Capacitor build | 🟡 | Distribution not specified → TD-161 |
| NFR-16 | English + German at launch; metric/imperial per project | T-0002 i18n, T-0136 per-locale terminology packs, ADR 0002 SI units converted for display | 🟡 | Infrastructure exists; German at launch is scope → TD-172 |

## 4. Data model mapping

| Clean-room entity | KERP equivalent | Fit |
|---|---|---|
| tenant (+ theme, sso_config) | tenant (`tenant_id` + RLS), one tenant per deploy (TD-007) | Theme per BU missing (TD-172) |
| user (+ SCIM) | users (T-0008) | SCIM not specified |
| project_membership(role) | T-0014 membership + T-0010 personas/scope | ✅ richer |
| project(units) | projects (T-0012: timezone, locale, currency) | ✅ |
| equipment(parent_id, tag, barcode, nfc_uid, attributes) | `oe_asset_node` (ltree) + `oe_asset_item` (JSONB attributes) | 🟡 add scan identifiers (TD-163) |
| template / template_version | template + pinned `template_revision_id` | ✅ |
| defect_scheme | issue types (TD-066) | 🟡 once specced |
| work_order | workpacks (E-06b-03, heavy) | 🟡 TD-165 |
| inspection(status, scheduled_start/duration, gps) | `oe_inspections_inspection` (T-0111) | 🟡 add schedule fields + `returned` (TD-165) |
| answer(question_key, repeat_index, value, flagged) | inspection responses | 🟡 repeat index + conflict flag (TD-162) |
| defect | punch (T-0117) / NCR (T-0116) / findings (TD-106) | 🟡 TD-167 |
| attachment(sha256, annotation, upload_state) | storage + presigned uploads (T-0016) | 🟡 annotation JSON (TD-166) |
| signature | hash-chained sign-off (T-0119) | ✅ stronger |
| comment (threaded) | — (anchored comments only in portal v4, E-07-04) | 🟡 TD-165 |
| audit_event | T-0006 hash-chain ledger | 🟡 `device_id` (TD-164) |
| change_log(seq) | master §4.3 server commit stream / `since_version` | 🟡 scope (TD-162) |
| device(push_token, revoked_at) | — | 🔴 TD-164 |

**Common columns.** Clean-room: client-generated UUIDv7 `id`, `version`, `deleted_at`,
`origin_device_id`. KERP `BaseRecordMixin` (T-0005): uuid `id`, `tenant_id`, `project_id`,
timestamps, `deleted_at` soft delete, `custom_fields` JSONB. Soft delete exists, so deletes can
sync. Still open: whether ids may be generated on the client (UUIDv7) and whether a record
revision doubles as the sync base. ADR 0005 keeps `base_revision` on the envelope for
idempotency and diagnostics only, and uses per-field three-way apply.

**Anchor model.** No conflict with ADR 0003. A clean-room "inspection belongs to one equipment
item" is KERP's "inspections anchor to an Item or any `inspection_anchor` node". Equipment that
is not an Item in KERP terms must be modelled as an Item to be inspectable.

## 5. Sync & stack differences

| Topic | Clean-room | KERP today | Recommendation |
|---|---|---|---|
| Client | React Native + op-sqlite/SQLCipher + Drizzle | One React codebase: PWA + Capacitor Android (TD-044) | Follow TD-044. RN is out. |
| Local store | SQLite/SQLCipher | Master §1: SQLite; edge spec + T-0127: Dexie | Resolve in TD-161: native SQLite (SQLCipher) + files on disk in the Capacitor build; Dexie for the web PWA only |
| Push | Ordered outbox, batches of 200, Idempotency-Key, per-mutation status | Master §4.3 `{applied, conflicts}`, T-0126 | Same model. Keep per-mutation results; never fail a whole batch. |
| Pull | `change_log.seq` cursor scoped to projects + downloaded inspections, has-more paging | `pull-deltas?since_version` | Add scope + paging (TD-162) |
| Answer conflicts | Field-level keep-both + flag | Per-field three-way apply, server value kept, tablet value to `sync_conflict_log` (ADR 0005) | ✅ KERP equivalent; surface the flag on the answer |
| Status conflicts | Server state machine wins | Immutable quality records (`ERR_CONFLICT_IMMUTABLE_QUALITY_RECORD`), `run_checks` on transitions | ✅ |
| Media | Files outside DB, pre-signed multipart, `/complete` with sha256; submit blocked until all complete | T-0016 presign/complete/sha256 | Add "attachments complete" to the submit rule set (TD-162) |
| Jobs | Celery or ARQ | **SAQ** (consolidation diff, T-0015) | Follow SAQ |
| PDF | WeasyPrint / headless Chromium | PyMuPDF (`fitz`, lazily imported per T-0022), pyHanko PAdES at turnover | Keep KERP's stack unless report layouts need HTML templates |
| Hosting | AWS | Dev/test Coolify + MinIO, production AWS (TD-004) | Region still open (TD-171) |

## 6. Proposed pilot slice ("pilot-0", amends TD-009)

**Goal:** 8 inspectors, one project, Android only. The pilot is usable when an inspector can
download work, inspect offline, sign and sync, and a reviewer can approve or return the
inspection and get a PDF.

- **Phase 0 as planned**, plus TD-043 (Entra ID) and an AWS production deploy target (TD-004).
- **Phase 1 subset:** T-0101 and T-0103 (hierarchy + Items), T-0107 (equipment import), T-0110
  (field registry + CEL), T-0111 (inspection record), T-0116/T-0117 (per TD-167), T-0119
  (signing), T-0126/T-0127 (offline sync), T-0133 (field checkout).
- **New work from §7:** Android Capacitor build with native store (TD-161), sync scope and
  download selection (TD-162), scan-to-locate (TD-163), device registry and revoke (TD-164),
  review/return (TD-165), photo annotation (TD-166), push notifications (TD-168), inspection PDF
  export (TD-169), three dashboard tiles.
- **Defer for the pilot:** P6 import/export (T-0104–T-0106, T-0125, T-0129), Phase 2 (WPS/NDE),
  Phases 3–6, and the portal. The client signs on the inspector's device with a PIN instead.

The M2 cut line keeps the P6 round-trip and Phase 2. Pilot-0 drops both, so it needs a user
decision (TD-173).

## 7. TODOs raised

All raised with `--source clean-room-gap-analysis`; see [`docs/TODO.md`](../TODO.md).

| ID | Kind | Topic |
|---|---|---|
| TD-161 | spec-gap | Field client local store: Dexie vs native SQLite/SQLCipher, 10 GB media, encryption at rest, minimum Android version, MDM distribution |
| TD-162 | spec-gap | Sync pull scope: per-device/project scope, paging, download selection + size, purge, client-generated ids, stale-base flag, attachments-complete on submit |
| TD-163 | spec-gap | Barcode/QR/NFC identifiers on Items + scan-to-locate lookup |
| TD-164 | spec-gap | Device registry: revoke/remote wipe, `device_id` in audit, biometric unlock, cert pinning, per-device sync health |
| TD-165 | spec-gap | Inspection scheduling (planned start/duration, calendar, recurrence), work-order grouping, `returned` state, comments on answers |
| TD-166 | spec-gap | Field-type palette: video, audio, barcode/QR, photo annotation, reference images on questions |
| TD-167 | spec-gap | Defect close-out by re-inspection, action owner/due (extends TD-066) |
| TD-168 | spec-gap | Push notification channel (FCM) for assignment/return |
| TD-169 | missed | M2 Documents-Lite "batch inspection PDF export" has no task |
| TD-170 | decision | Offline window: 14 days (clean-room) vs 7 days (master §6) |
| TD-171 | decision | Production AWS region / data residency: EU (clean-room NFR-10) vs Australia |
| TD-172 | decision | Per-BU theming vs one tenant per deploy; German at launch |
| TD-173 | decision | Pilot-0 slice (§6) vs the M2 cut line (TD-009) |
| TD-174 | spec-gap | NFR budgets: form-open, delta-sync, glove/outdoor UI tokens |
| TD-175 | later | Record retention rules + legal hold per project |
| TD-176 | risk | No availability target or HA design for the production API (clean-room 99.5 %) |

**Related existing items (not duplicated):** TD-043 (Entra ID before pilot), TD-044 (PWA +
Capacitor client strategy), TD-063 (hold/witness as signed ITP steps), TD-066 (issue types),
TD-084 (email via SES), TD-106/TD-117 (findings register), TD-019 (PWA manifest + service
worker), TD-021 (outbox envelope field names).
