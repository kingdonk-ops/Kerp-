# Module detail — P6 import, RSW register, matching & field-checkout merge

Covers the Primavera P6 import wizard (Phase 1: T-0104, T-0105, T-0106, T-0125, T-0129), the
expected-RSW register built from it, the RSW document ↔ P6 matching and reconciliation engine
(Phase 5 epic), and the **merge rule between a P6 re-import and work checked out to a tablet**
(Phase 1 sync core: T-0126, T-0133; decision in ADR 0005).

Written against [ADR 0003](../../decisions/0003-configurable-breakdown-and-terminology.md):
"RSW" below means **the project's work-anchor level** (RSW in the `remediation-*` packs, Line in
`cui-line`). P6 activities link to the level that carries the `schedule_link` role. Nothing in
code may assume the level is called RSW.

Precedence: module detail (3). Where this file and the consolidation diff disagree, raise a
`spec-gap` TODO.

---

## 1. Principles (binding)

1. **P6 is a system of record for the schedule only.** KERP mirrors P6 data by
   `external_source="p6"` + `external_id` + `synced_at`; no FK to P6. P6 never owns field data.
2. **Nothing a P6 import does may overwrite work captured in the field.** A re-import writes only
   P6-owned fields (§7). Field-captured and office-captured values are untouchable by import.
3. **Only what changed on the tablet is synced back** (§8). A tablet never sends, and the server
   never applies, a whole record. Fields the tablet did not touch keep whatever the server holds,
   including P6 changes that landed while the tablet was offline.
4. **Never delete on import.** An activity missing from a new XER is marked
   `removed_from_schedule`; its links and all field work stay, flagged for planner review.
5. **Preview before apply.** Every import produces a change-impact report; a planner confirms it
   before one atomic commit.
6. **Deterministic first, AI second, human last.** Name parsing, eligibility and matching are
   rules; AI may only propose (`source="ai"`, human confirmation required).
7. **Originals are immutable.** The uploaded XER (and later RSW PDFs) are stored as-is with a
   SHA-256; every derived row traces back to file + line (XER) or file + page (PDF).

---

## 2. What a real schedule looks like (evidence)

A client construction schedule (XER, P6 export version 24.12, ~5.5 MB) was profiled on
2026-11-10. **The file is real client data and must never be committed; fixtures are synthetic
copies of its shape.** Findings that drive this spec:

| Observation | Count / shape | Consequence |
|---|---|---|
| Tables present | `PROJECT, PROJWBS, TASK, TASKPRED, TASKRSRC, TASKACTV, ACTVTYPE, ACTVCODE, UDFTYPE, UDFVALUE, CALENDAR, RSRC, RSRCRATE, CURRTYPE, OBS, SCHEDOPTIONS, WBSMEMO, MEMOTYPE, FINTMPL` | Parser handles every `%T/%F/%R` table generically; only mapped tables are interpreted. |
| Activities | 2,987 (2,964 task, 22 milestones, 1 resource-dependent) | Milestones never generate RSW placeholders (eligibility, §5). |
| WBS nodes | 135, depth up to 7 (project → phase → region → site → shutdown/online window) | WBS kept as its own tree; not forced onto the asset tree. |
| Relationships | 3,809 (FS 3,456 · FF 212 · SS 130 · SF 11) | Stored for schedule reconciliation; not used to build the hierarchy. |
| Activity name grammar | 2,960 of 2,987 follow `<asset> / <activity type> / <RSW code>` | **The RSW and asset are encoded in the activity name**, not in codes/UDFs. Name parsing is the primary mapping (§5). |
| Distinct assets / asset+RSW pairs | 66 / 620 | One RSW has **many** activities (one per activity type) → RSW↔activity is many-to-many. |
| Activity-type tokens | `WFE, SCA Erect, SCA Dism, B, P, W, CP, SR, BN, G, GR, C, HDNR1, HDNR2, HDBN, Custom, Prov Sums` | Map to the `activity` level / item type via the client pack. |
| Token spelling variants | `SCA Dism` / `SCA-Dism`, `Prov Sums` / `Prov.Sums` | Project-scoped **alias dictionary** (§5.2). |
| RSW code variants | `701`, `900.2`, `918 Online` / `918 Shut`, `918-21`, `918.1-16`, `920.1-21` | Grammar must capture base code, sub-code, work window and a suffix whose meaning is **unknown** → spec-gap TODO. |
| Activity codes | 2 types (`<site> Status`) with values Shutdown, Online Works, On-Hold Priority 1/2 | Mapped to a node attribute (`work_window`, `hold_priority`). |
| UDFs | `Comment`, `Notes` (values `New`, `Secondary`, `Changed from "C" to "G"`) | Planners record revision deltas in UDFs; surface them on the change report. |
| Status | all `TK_NotStart` (tender schedule) | Status mirror must cope with an un-progressed baseline. |
| Activity ID uniqueness | `task_code` unique within the project; `guid` present | Identity = `task_code`, with `guid` to detect renumbering (§6). |
| Encoding | tab-delimited, cp1252, calendar blobs with `0x7f` separators | Parser decodes cp1252 with replacement and never chokes on calendar data. |

---

## 3. Pipeline

```
Upload .xer ─► store original (sha256, immutable) ─► SAQ batch: parse ─► staging snapshot
   ─► normalise (aliases, units, codes) ─► map (mapping profile: name grammar, eligibility)
   ─► diff vs current mirror ─► change-impact report ─► planner preview/confirm
   ─► atomic commit ─► events: p6.import.committed ─► matching/reconciliation (Phase 5)
```

- Parse runs on the SAQ **batch** queue (consolidation diff §3); parser is stdlib, streamed
  line by line; any heavy dependency added later goes on the TID253 ban list (TD-026).
- Re-uploading a file with the same SHA-256 for the same project is a no-op that returns the
  earlier import.
- A failed parse leaves no partial mirror rows: staging is written, commit is a separate
  transaction.
- Primavera XML (T-0105) produces the same staging shape.

---

## 4. Data model

All tables use `BaseRecordMixin`, project-scoped RLS, tenant from JWT; cross-tenant/project → 404.
Module: `asset_management` owns the mirror and links (other modules call its `service.py`).

| Table | Purpose | Key columns |
|---|---|---|
| `oe_p6_import` | One uploaded file and its lifecycle | `file_id`, `sha256`, `xer_version`, `source_project_short_name`, `data_date`, `status` (`uploaded → parsed → previewed → committed` / `failed` / `discarded`), `mapping_profile_revision_id`, `summary jsonb`, `committed_by/at` |
| `oe_p6_wbs` | WBS mirror (ltree) | `external_id` (wbs guid), `short_name`, `name`, `parent_id`, `tree_path ltree`, `removed_from_schedule` |
| `oe_p6_activity` | **Persistent identity** of an activity | unique `(project_id, external_activity_id)` = `task_code`; `p6_guid`; `current_version_id`; `schedule_state` (`active` / `removed_from_schedule` / `cancelled`) |
| `oe_p6_activity_version` | Immutable snapshot per import | `activity_id`, `import_id`, `wbs_id`, `name`, `task_type`, `status_code`, planned/target/early/late/actual dates, `codes jsonb`, `udfs jsonb`, `content_hash`, `source_line` |
| `oe_p6_relationship` | Predecessor/successor per import | `import_id`, `pred_activity_id`, `succ_activity_id`, `type` (FS/FF/SS/SF), `lag_hours` |
| `oe_p6_anchor_link` | **Many-to-many** activity ↔ work-anchor node (and optional asset node) | `activity_id`, `node_id`, `relationship_type` (`primary`, `supporting`, `shared`, `parent`, `schedule`, `area`), `allocation_pct` (nullable), `match_method` (`name_grammar`, `manual`, `matching_engine`), `match_score`, `status` (`proposed → confirmed → approved` / `rejected`), `verified_by/at`; unique `(activity_id, node_id, relationship_type)` |
| `oe_p6_change` | One row per detected change in an import | `import_id`, `activity_id`, `change_type` (§6), `field`, `old jsonb`, `new jsonb`, `impact jsonb` (affected nodes, checked-out jobs), `decision` |

Resource rates and costs in `RSRCRATE`/`TASKRSRC` are **not** imported (money is a read-only
mirror from the ERP, not from P6). Resource quantities may be kept in the snapshot for later
reconciliation.

---

## 5. Mapping profile (per project, from the client pack — ADR 0003 §5)

### 5.1 Activity-name grammar
A versioned profile holds one or more patterns with named groups, tried in order:

```
^(?P<asset>.+?) / (?P<activity_type>[^/]+?) / (?P<anchor_code>\d+(\.\d+)?)(?:-(?P<suffix>\d+))?(?: (?P<window>Online|Shut))?$
```

- `asset` → node on the level with role `physical` (created if missing, as `proposed`).
- `anchor_code` → node on the **work-anchor** level under that asset (the RSW).
- `activity_type` → node on the `activity` level (remediation-fep pack) or item type, via §5.2.
- `window`/`suffix` → node attributes; meaning of `suffix` is an open question (TD raised).
- No pattern matches → activity goes to the **Unmatched-Row Linking Screen** (T-0105), never
  dropped, never guessed.
- Other sources the profile may use instead of the name: an activity-code type, a UDF, or the WBS
  path (some clients put the RSW in a code). Order of precedence is profile configuration.

### 5.2 Normalisation and aliases
- Codes are text; comparison uses a normalised form (upper case, collapse whitespace, `-`/space
  equivalence) while the original is stored alongside.
- Project-scoped alias dictionary: `SCA-Dism → SCA Dism`, `Prov.Sums → Prov Sums`,
  `TK1002 / TK 1002 → TK-1002`. Aliases never resolve across projects.

### 5.3 Eligibility (which activities expect an RSW)
CEL rules in the profile, e.g. `task_type == "TT_Mile" → no_placeholder`,
`activity_type in ["Prov Sums","Custom"] → no_placeholder`, everything that parsed →
placeholder. Ineligible activities are still mirrored and visible; they just do not create
expected RSWs.

### 5.4 Placeholders
On commit, each eligible parsed activity ensures (idempotently) its asset node, work-anchor node
and `oe_p6_anchor_link`. New work-anchor nodes start in state **`awaiting_document`**
("Draft — awaiting RSW upload"). This is what lets the missing-RSW dashboard know what should
exist before any PDF arrives.

---

## 6. Re-import and change-impact report

Identity: `task_code` first; if a `task_code` is new but its `p6_guid` matches an existing
activity, it is a **renumber** (proposed, planner confirms).

| Change type | Detected by | Action on commit |
|---|---|---|
| `new` | unknown `task_code` and `guid` | mirror + placeholder if eligible |
| `name_changed` | name differs | new version; if the parsed asset/RSW differs → link becomes `proposed`, flagged for review; approved links are never moved automatically |
| `dates_changed` | any planned/target date differs | new version; schedule association updated; inspection due dates **not** changed |
| `wbs_moved` | `wbs_id` differs | hierarchy reconciliation finding |
| `status_changed` | P6 status differs | mirror only — P6 status never drives inspection or RSW state |
| `codes_or_udfs_changed` | code/UDF values differ | mirror; UDF "Changed from …" notes shown on the report |
| `removed` | activity absent from the file | `schedule_state = removed_from_schedule`; linked anchors flagged *orphaned*; **no delete** |
| `cancelled` | status/code mapped to cancelled | links flagged for planner review |
| `renumbered` | new `task_code`, known `guid` | identity carried over after confirmation |
| `unchanged` | `content_hash` equal | nothing written |

The report also lists **which affected records are currently checked out to a device** (§8) and
states that their field data will not be touched. A planner may accept the whole report or
exclude individual changes; excluded changes are recorded with the decision.

---

## 7. Field ownership

Every column/JSON path that the tablet or the import can write belongs to exactly one owner. The
ownership map is declared in each module's schema registry (one-engine field registry) and is
part of the M1 freeze.

| Owner | Examples | Written by |
|---|---|---|
| `p6` | activity name, dates, P6 status, WBS, activity codes, UDFs, float | P6 import only |
| `office` | planner description override, assignment, priority, approvals, link decisions | desktop users |
| `field` | inspection answers, measurements, readings, photos & markups, notes, voice transcripts, access/constraint flags, scoped quantities, checklist ticks | tablet (or desktop on a record not checked out) |

Rules: the import writes `p6` fields only. A tablet may write `field` fields only (sending any
other owner's field → `ERR_FIELD_NOT_OWNED`, logged, rest of the batch still applied). Office
users may not edit `field` fields of a record while it is checked out (409, "checked out to
<user>/<device>") unless a supervisor force-releases the checkout.

---

## 8. Field checkout and sync-back (the tablet rule)

**Requirement (user, 2026-11-10):** *if an inspection job is checked out on a tablet and a P6
import updates the records in the meantime, only the changes made on the tablet are synced
back.*

### 8.1 Checkout
`oe_field_checkout` (module `sync`): `scope` (inspection job / RSW batch), `user_id`,
`device_id`, `checked_out_at`, `lease_expires_at`, and per record `base_revision` plus the
`template_revision_id` pinned at checkout. One active checkout per record. The tablet downloads
the records, their read-only P6 context and the pinned template for offline use.

### 8.2 What the tablet sends
Each outbox mutation (T-0126/T-0127, `client_op_id`) carries **only the fields the user
changed**, each with the value it was based on:

```json
{ "client_op_id": "…", "entity": "inspection", "entity_id": "…",
  "base_revision": 7,
  "changes": [
    { "path": "answers.wall_thickness_mm", "base_hash": "sha256:…", "value": 11.8 },
    { "path": "notes", "base_hash": "sha256:…", "value": "Pitting at 6 o'clock" }
  ],
  "collection_ops": [ { "path": "photos", "op": "add", "id": "…" } ] }
```

### 8.3 How the server applies it (per field, three-way)
For each changed path:

1. Owner is not `field` → reject that path (`ERR_FIELD_NOT_OWNED`).
2. Record is locked (signed off / `LOCKED_FOR_TURNOVER`) → reject (existing immutable-quality
   rule, master spec §4.3 rule 1).
3. Current server value hash == `base_hash` → **apply**.
4. Server value changed since checkout (only possible after a force-release or an expired lease
   taken by someone else) → keep the server value, store the tablet value in `sync_conflict_log`
   and raise a review item. Nothing is silently lost either way.

Paths the tablet did not send are never written. Therefore every P6 change committed during the
checkout (dates, names, status, codes, re-linking proposals) survives the sync untouched, and
every field value the tablet captured lands. Child collections (photos, measurements) are
additive operations, never array overwrites (master spec §4.3 rule 3).

### 8.4 P6 changes during a checkout
- The tablet receives them on its next pull as **read-only context** and shows "schedule changed
  since checkout" on the job; the inspection form is not altered.
- The pinned template revision stays; a P6 import never changes a template.
- If the activity was removed or cancelled during the checkout, the tablet's work is still
  accepted and attached to the work-anchor node; the node is flagged for planner review. Field
  work is never discarded because the schedule changed.
- If a re-import re-parents the RSW (asset changed), the change is held as `proposed` while the
  record is checked out and surfaced to the planner after the tablet syncs.

### 8.5 Lease expiry
Data from an expired lease is still accepted if nobody else checked the record out; if someone
did, the batch goes through rule 4 (conflict log + review), never dropped.

---

## 9. RSW document ingestion, matching and reconciliation (Phase 5 epics)

Summarised here so the Phase 1 tables leave room for it; detailed breakdown happens when Phase 5
is planned (E-05-09, E-05-10).

**Ingestion (E-05-09).** Individual PDFs, multi-file and ZIP uploads; originals immutable;
splitting by text/metadata, page OCR, template recognition (versioned legacy and current RSW
layouts), RSW-number detection, header/footer and page-sequence analysis, AI only where rules
are unsure. Stores the original file, page numbers, the split PDFs and the page→RSW map. OCR via
the pluggable parser adapter (Docling / PaddleOCR candidates, engine swappable), running in the
SAQ worker. Two passes: local extraction for everything, AI/vision only for low-confidence
fields. Every extracted value keeps original text, numeric value, unit, source page/field and
confidence. One failed document never fails the batch.

**Matching (E-05-10).** Three separate stages, each with its own score and audit:
*identification* (which RSW record a document is), *matching* (which activities/assets it
links to), *reconciliation* (do the linked records agree). Six steps: normalise → candidate
generation (exact IDs, asset tag, WBS, area, drawing, `pg_trgm`/full-text, later pgvector) →
weighted score from a **versioned scoring profile** (initial weights: RSW no. 20, asset 20,
activity ID 20, WBS 10, location 10, description 10, drawing 5, discipline 5) → hard/soft rule
validation (CEL, controlled operators, no arbitrary code) → decision (auto ≥ 90 **and** all hard
rules pass **and** margin to runner-up ≥ 10; 75–89 human verification; < 75 manual assignment;
any conflict → conflict queue) → relationship creation. Scores are evidence scores, not
probabilities; calibrate on a labelled set of verified matches. Re-matching never replaces an
approved link; it creates a new proposal and flags the old one. AI may explain and propose,
never approve.

**Reconciliation.** Identity, scope, quantity (only mapped like-for-like measures, unit
conversion, % or absolute tolerance), schedule (e.g. activity starts next week, RSW still
awaiting upload → planning risk), document and technical checks. Two directions: P6→RSW
(expected work with no document) and RSW→P6 (documents with no activity). Output is structured
findings with source, values, variance, recommendation, status — never silent edits.

**Quantity ledger (with tablet scoping, Phase 4/6b).** Original estimate, field-measured,
calculated, engineer-approved, final execution and actual installed quantities are separate
values; nothing overwrites the original.

---

## 10. API (Phase 1 part)

| Endpoint | Purpose |
|---|---|
| `POST /projects/{id}/p6/imports` | upload XER/XML → `oe_p6_import` (`uploaded`), enqueue parse |
| `GET /projects/{id}/p6/imports/{import_id}` | status, summary, parse errors |
| `GET /projects/{id}/p6/imports/{import_id}/changes` | change-impact report incl. checked-out records |
| `GET /projects/{id}/p6/imports/{import_id}/unmatched` | rows the grammar could not map |
| `POST /projects/{id}/p6/imports/{import_id}/unmatched/{row}/link` | manual link (preserves existing ties) |
| `POST /projects/{id}/p6/imports/{import_id}/commit` | atomic apply of accepted changes |
| `POST /projects/{id}/p6/imports/{import_id}/discard` | discard staging |
| `GET /projects/{id}/p6/activities?node_id=` | activities linked to a node |
| `GET /projects/{id}/p6/export` | actuals keyed on Activity ID (T-0106) |

Sync endpoints are the existing `push-mutations` / `pull-deltas` (master spec §4.3) plus
`POST /sync/checkouts` and `DELETE /sync/checkouts/{id}` (release / supervisor force-release).

---

## 11. Acceptance tests (must exist as automated tests)

P6 import:
1. Synthetic XER with the §2 shape imports; counts of activities, WBS, relationships match.
2. Same file twice → second is a no-op (sha256) and no duplicate rows.
3. Revised XER: changed dates, renamed activity, moved WBS, removed activity, new activity,
   renumbered activity (same guid) → each produces the right `oe_p6_change` and action.
4. Removed activity: node and inspections kept, flagged orphaned, nothing deleted.
5. Milestone and `Prov Sums` activities create no placeholders.
6. Unparseable names land on the unmatched screen; manual link preserves existing ties.
7. Aliases (`SCA-Dism`) resolve; an alias from project A never resolves in project B.
8. Approved link is not moved by a re-import that changes the parsed RSW; a proposal is raised.
9. cp1252 text and calendar blobs parse without error.

Checkout merge (ADR 0005):
10. Tablet checks out job; P6 import changes activity dates and name; tablet edits two field
    answers and adds a photo; sync → server has the new P6 dates/name **and** the two answers and
    the photo; every other field is unchanged.
11. Tablet payload containing a `p6`-owned field → that path rejected with
    `ERR_FIELD_NOT_OWNED`, the rest applied.
12. Activity removed while checked out → tablet work accepted, node flagged for review.
13. Force-released record edited in the office, then stale tablet sync → server value kept,
    tablet value in `sync_conflict_log`, review item raised.
14. Signed-off inspection cannot be changed by a later tablet sync.
15. Replay of the same `client_op_id` is a no-op.

---

## 12. Open questions (raised as TODOs)

- Meaning of the RSW code suffix (`-16`, `-21`) and of `Online`/`Shut` variants: same RSW in two
  windows, or two RSWs?
- P6 supply format beyond XER: Primavera XML (T-0105), Excel exports, direct P6 API later?
- Where RSW PDF processing runs: self-hosted OCR only, hybrid (local OCR + external AI), or cloud.
- Whether office edits to field-owned values on a checked-out record should be blocked (this
  spec) or queued.
