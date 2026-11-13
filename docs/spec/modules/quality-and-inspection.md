# Module detail — Quality: one-engine inspection, eligibility, qms, construction_control, coatings

Merged from legacy `forms`/`inspections`/`qms`/`construction_control` with TD-002 (one-engine)
applied. Covers MOD-08 (QC/ITP) and the coatings/environmental criteria.

Anchors, header binding and template/register versioning follow
[ADR 0003](../../decisions/0003-configurable-breakdown-and-terminology.md); rule sets, overrides
and validity dates follow [ADR 0004](../../decisions/0004-project-rule-sets-with-defaults.md)
(both accepted with amendments, 2026-11-10). "RSW" below means the project's work-anchor level.

## ITP, Request for Inspection and ITR — purpose of each (user clarification, 2026-11-13)

| Document | Role | Answers | How often |
|---|---|---|---|
| **ITP** — Inspection & Test Plan | **The plan.** Lists every inspection/test activity, its reference specification, acceptance criteria, the records required, and who is involved at each step through **Hold, Witness, Review or Surveillance** points (`H`/`W`/`R`/`S`). | *What* must be checked | Approved **once**, before work starts |
| **RFI** — Request for Inspection | **The trigger.** Notifies the client or third-party inspector that a specific ITP activity is ready, so they can attend `H`/`W` points. | *When* it is ready | Raised **many times**, once per inspection event |
| **ITR** — Inspection & Test Record | **The evidence.** Records what was actually inspected, results and measurements, accept/reject, and signatures. Feeds the final handover dossier (MDR). | *Proof* it was done | One per inspected activity per anchor |

In short: the ITP says what must be checked, the Request for Inspection says when it is ready, and
the ITR proves it was done.

**All three functions are required, but not as three separate paper documents.** KERP builds them
as one linked chain:

1. **ITP = master template.** Each ITP line item defines the activity, criteria, intervention
   points (`H`/`W`/`R`/`S`/`M`) and the ITR form (template revision) to use. Authored in `qms`;
   instantiated per anchor (node or Item) as `kind = itp` records.
2. **Request for Inspection = a workflow step on an ITP line item** for a specific location or
   component. Raising it moves the line to `INSPECTION_REQUESTED`, auto-notifies the right
   parties, logs date and time, and enforces the contractual notice period. Per TD-045 it is
   backed by its own `inspection_request` record (booking, calendar, reschedule history, client
   delay attribution — §`inspection_requests`); one request may cover several anchors.
3. **ITR = the form filled in on site against that same request** (`kind = inspection`), with
   photos, readings and e-signatures. When it is completed and accepted, the ITP line closes
   automatically (`INSPECTED_PASSED`).

What the chain must deliver:
- **Traceability** — every ITR links back to its ITP line and its Request for Inspection.
- **No duplicate data entry** — the ITR header is bound from the ITP line, request and anchor
  context (header binding by role, below), never retyped.
- **Hold points block progress until signed** — enforced via `run_checks` (§`construction_control`).
- **Live progress** — dashboards show the percentage of ITP line items closed, per anchor/system.

**Field-user view.** Field users see a single inspection with one status lifecycle
(Requested → Scheduled → Inspected → Accepted/Rejected), even though the request and the ITR are
linked records underneath. Mapping: Requested/Scheduled = `inspection_request` `requested` /
`confirmed` with the ITP line in `INSPECTION_REQUESTED`; Inspected = ITR submitted (`SUBMITTED`);
Returned = `RETURNED`; Accepted = `INSPECTED_PASSED`; Rejected = `INSPECTED_FAILED`.

**RFI number and terminology (user decision, 2026-11-13, amends TD-045).** In KERP, **RFI
means Request for Inspection**, as clients and site use it. A Request for Information is called a
**TQ (Technical Query)** and lives in the TQ module (`docs/spec/modules/documents-and-mdr.md`).
Every `inspection_request` carries an **RFI no.** (TD-045 field list), printed on the notification
and on the ITR, because many clients require a separate RFI number for contractual tracking even
where no separate request form is issued. The record and state keep their confirmed code names
`inspection_request` / `INSPECTION_REQUESTED`. The RFI numbering scheme and per-discipline layout
remain open on TD-045.

## One field-type registry (`core/fields`)
Shared palette of field types used by every form, ITP and asset-schema field:
`numeric`, `text`, unit-bound **`measurement`** (value + unit), `photo`, `signature`,
**`instrument reference`** (binds a calibrated tool, gated by calibration), `calculated`
(CEL expression). No per-module field designers.

**Photo answers and annotation (Pilot-0, planner spec 2026-11-13; TD-166, Q5a).** Rules below
are planner defaults unless marked otherwise. A `photo` answer is a list of captured photos, each
`{attachment_id, sha256, captured_at, geo, annotation}`:
- `captured_at` is the device UTC time of capture; the server also stores when it received the
  photo. `geo` is `{lat, lon, accuracy_m}` from the device location when the user allowed it,
  else `null`; a missing location never blocks capture.
- `annotation` is `null` or `{schema_version: 1, shapes: [...]}`. Shapes: `arrow` (from, to),
  `ellipse` and `rect` (x, y, w, h), `freehand` (points) and `text` (x, y, text up to 200
  characters), each with a `color` from a fixed palette of four high-contrast colours and a
  `stroke` of `thin` or `thick`. Coordinates are normalised 0–1 against the original image, origin
  top-left. At most 50 shapes per photo (planner defaults).
- The original image is never changed. The annotation is stored with the answer and drawn as an
  overlay on screen; it is **burned in only on export** (inspection PDF, §Inspection report).
- Annotations can be edited until the inspection is submitted and are read-only after that, like
  the other answers. The server validates the annotation against its schema (422 for that path).
- Video and audio notes, barcode/QR answer fields and reference images on questions are in
  Pilot-0 too (user, 2026-11-13, R4b): §Media, barcode fields and reference images below.

**Media, barcode fields and reference images (Pilot-0; user, 2026-11-13, R4b; TD-218).** The user
brought these into Pilot-0; limits, formats and the processing split are planner defaults.
- **`video` field.** Value: a list of `{attachment_id, sha256, captured_at, geo, duration_s,
  poster_attachment_id}`. Recorded on the device as MP4 (H.264 video, AAC audio), at most 720p,
  at most 60 s and 100 MB per clip, at most 3 clips per answer. The client makes the poster frame
  (JPEG) and uploads it with the clip.
- **`audio` field.** Value: a list of `{attachment_id, sha256, captured_at, duration_s}`. AAC in
  M4A, at most 5 minutes and 10 MB per note, at most 3 notes per answer. No transcription.
- **`barcode` field.** Value: `{value, kind (barcode | qr), source (scanned | manual)}`. Scanned
  with the same scanner adapter as scan-to-locate (`asset-and-tagging.md`); manual entry is always
  allowed and recorded as `manual`. An optional CEL rule can check the value like any other field.
- **Reference images on questions.** A template question may carry up to 3 reference images (JPEG
  or PNG, at most 5 MB each) with an optional caption (localised like other template strings).
  They are uploaded through T-0016 presigned uploads when the template is authored, their sha256
  is part of the frozen template revision, and they are downloaded with the template revision
  (`KERP-CONSOLIDATION-AND-DIFF.md` §6.4 reference data). Changing an image makes a new revision.
- **Storage and processing.** All media go through T-0016 presigned uploads with sha256
  verification and count against the device media budget (diff §6.2). After upload, a SAQ `batch`
  job probes each video and audio file (container, codec, duration, size) with a permissively
  licensed library imported lazily (no GPL); a file outside the limits is marked `rejected` with a
  reason, and until the probe passes the attachment is not `complete`, so submit waits for it
  (`ATTACHMENTS_INCOMPLETE`). No server transcoding.
- **On the PDF.** Video and audio answers print the poster or an audio icon, duration, capture
  time and sha256 (not embedded); barcode answers print value, kind and source; reference images
  are not printed.

## Unified inspection record — `oe_inspections_inspection`
Single store across all kinds: **`kind IN (inspection, itp)`**. TD-045 (decided 2026-11-10)
renames the old `rfi` kind and `RFI_ISSUED` state; the Request for Inspection (RFI) becomes its own `inspection_request` record (§`inspection_requests`).
The names used here are **confirmed by the user (TD-045, 2026-11-10)**: no `rfi` kind, and the
call-out state is `INSPECTION_REQUESTED`. Anchors to **any node whose level has the `inspection_anchor` role, or an Item**
(ADR 0003 §6; e.g. Line in `cui-line`, RSW and Item in `remediation-*`), including a node with no
work under it yet (ADR 0003 Amendment 5). Holds responses authored against a template revision
(pins `template_revision_id`), `status` lifecycle, `reading_*`, `signed_off_*` (user, signature
hash, UTC timestamp, geolocation). Lifecycle (master spec §4.2):
`PENDING → INSPECTION_REQUESTED → INSPECTED_PASSED → LOCKED_FOR_TURNOVER`, with
`INSPECTED_FAILED → (Cat A punch | NCR) → RE_INSPECT` (state name confirmed on TD-045). Every transition calls `run_checks`.
**Review (Pilot-0, TD-165):** the result is reached through `SUBMITTED` and, when returned,
`RETURNED`: `PENDING | INSPECTION_REQUESTED → SUBMITTED → INSPECTED_PASSED | INSPECTED_FAILED`,
`SUBMITTED → RETURNED → SUBMITTED` (§Review and return).

- **Dates (ADR 0004 Amendments C–D).** Each record stores `inspection_date` (when the work was
  inspected, entered by the user) and `entered_at` (server time of entry). Validity checks
  (calibration expiry, credential expiry, superseded specification revisions) evaluate **as of
  `inspection_date`**, not today; a record that was valid then never blocks later, and reports
  render from the completion snapshot (TD-064). Checks rerun live in the form whenever the date,
  equipment or specification changes, and again on the server at submit. An `inspection_date`
  more than *N* days before `entered_at` (project setting `backdating_threshold_days`,
  default 7) requires a typed `backdating_reason` and is flagged on the report.
- **Segregation of duties (TD-070).** Project setting `allow_self_approval` (default **false**):
  the user who performed an inspection cannot approve or supervisor-sign it. Inspection records
  are **soft-deleted only** (`deleted_at`, audit-ledgered); no hard delete.
- **Header binding by role (ADR 0003 §6).** Template header fields bind to context by role or
  level key (`context.level('asset').name`, `context.work_anchor.code`). A template declares the
  roles/level keys it requires and cannot be attached to an anchor that lacks them.
- **Templates are versioned** (ADR 0003 §6). Records pin `template_revision_id`; new records use
  the latest revision. A client-variant template is a copy with a `derived_from` link and a
  compare view, not an inheritance overlay (TD-107).
- **Close-out bulk signing (TD-095).** An ITP close-out may sign all `R`/`S`/`M` rows of one party
  with one date in one action (one sign-off ledger entry per row, same signer and date); `H` and
  `W` points are always signed individually — a bulk request that includes any `H` or `W` row is
  rejected as a whole (422) and nothing is signed.

### Request anchors
A Request for Inspection or NDT request may cover several anchors (e.g. one NDT request for
several RSWs): `oe_inspections_request_anchor(request_id, node_id | item_id)` — exactly one of
`node_id`/`item_id` set — replaces a single anchor FK (ADR 0003 §2).

### Per-record override of a blocked check (ADR 0004 Amendment A)
When a `check` rule blocks a transition on one record, the gate states why and the user may
**Request override** with a typed reason (mandatory). The approver is the QA/QC Lead (PER-06);
if the requester is QA/QC, the Project Manager (PER-02); if the requester is the PM, QA/QC.
**Nobody approves their own request.** The approver is notified of the request and the requester
of the decision. Request, reason, approver, decision and timestamps go to the record's activity
history and the audit ledger. The approval is bound to a hash of the exact values checked
(equipment, specification revision, date…); changing any of them voids it and the block returns.
This is separate from unlocking a rule (ADR 0004 §5 and Amendment B).

`oe_validation_override` (module `validation`, `BaseRecordMixin` + project RLS; shape frozen at M1,
T-0131):

| Column | Purpose |
|---|---|
| `record_type`, `record_id` | the blocked record (e.g. `inspection`, its id) |
| `rule_set_key`, `rule_key`, `rule_version` | the blocking rule as evaluated |
| `finding_id` | the blocking finding the request answers |
| `requested_by`, `requested_at`, `reason` | requester and mandatory typed reason (non-blank) |
| `approver_persona`, `approver_user_id` | routed approver (PER-06, or PER-02 when the requester is PER-06); never the requester |
| `decision` | `pending` → `approved` \| `rejected` \| `void` |
| `decided_by`, `decided_at`, `decision_comment` | the decision |
| `checked_values_hash` | SHA-256 of the canonical JSON of the values the rule checked (equipment id, specification revision, inspection date…); a later evaluation with a different hash sets `decision = void` and the block returns |
| `audit_chain_key` | ledger chain the request and decision were appended to (`<record_type>:<record_id>`) |

## Review and return (Pilot-0, planner spec 2026-11-13; TD-165, Q5a)

The Pilot-0 goal is that a reviewer can approve or return an inspection (TD-173). Rules below are
planner defaults unless marked otherwise.

- **Submit.** The inspector submits from `PENDING`, `INSPECTION_REQUESTED` or `RETURNED` with a
  proposed result `pass` or `fail`. Submitting signs the record (T-0119 ledger entry: signer,
  signature hash, UTC timestamp, geolocation, `device_id`) and runs
  `run_checks(context, "inspection.submit")`: required answers, validity as of `inspection_date`
  (eligibility, calibration), whether a Request for Inspection had to come first (hold/witness
  points, `construction_control`), every referenced attachment `complete`
  (`ATTACHMENTS_INCOMPLETE`) and no open answer conflict flag (`ANSWER_CONFLICT_UNRESOLVED`)
  (`KERP-CONSOLIDATION-AND-DIFF.md` §6.4). A submit made offline is a queued mutation; if a check
  fails at sync, that mutation fails with the findings, the record keeps its earlier state and the
  inspector is notified. In `SUBMITTED` the answers are read-only.
- **Time and authority of an offline submit.** The submit signature and the inspection's submit
  carry the time evidence of `KERP-CONSOLIDATION-AND-DIFF.md` §6.4.1 (user, 2026-11-13):
  `occurred_at`, `recorded_device_at`, `synced_at`, `clock_skew_ms` and `time_confidence`.
  `occurred_at` is the sign-off time shown on the record and the PDF; raw evidence is never
  overwritten; `UNVERIFIED_REBOOT` shows a warning badge for the reviewer. The submit's geolocation is
  the signature's geolocation that master spec §4.2 requires for `INSPECTED_PASSED`. An offline
  submit is authorised by the local PIN unlock of the hybrid model
  (`KERP-CONSOLIDATION-AND-DIFF.md` §4.2), not by a live session: the mutation carries the user
  and `device_id`, and the server accepts it only if it was signed (by `occurred_at`) inside the offline window and
  the user and device match the pushing session (T-0126 note) and the device is not revoked.
- **Approve.** A reviewer approves with `run_checks(context, "inspection.approve")`. The record
  moves to `INSPECTED_PASSED` (proposed `pass`) or `INSPECTED_FAILED` (proposed `fail`, which then
  raises the Cat A punch or NCR as before). The reviewer cannot change the result; to disagree they
  return the inspection. The approval is a sign-off ledger entry (reviewer, persona, UTC time;
  geolocation optional for office users, because the geolocation master spec §4.2 requires on
  `INSPECTED_PASSED` is the one on the inspector's submit signature).
- **Return.** A reviewer returns with a mandatory, non-blank reason and optionally comments on
  single answers (§Comments on answers). The record moves to `RETURNED`, its answers become
  editable again and the inspector is notified (in-app, and push on Android, T-0144). The earlier
  submit signature stays in the ledger; the next submit starts a new review round
  (`review_round` 1, 2, …). The return reason and round are in the record history and on the PDF.
- **Who reviews (user, 2026-11-13, R3a).** PER-06 or PER-07 on the project may approve or
  return; a PER-07 only inspections anchored inside their site node(s) ("Site Node Only"), a PER-06
  the whole project. Approve and return are online actions in the web app (planner default).
- **Self-approval (user, 2026-11-13; replaces the R3a "never" wording).** By default nobody
  approves an inspection they performed. The existing project setting `allow_self_approval`
  (TD-070, T-0140; off by default) is extended with a per-template override (`inherit | allow |
  deny`, either way). When it is effectively on, a **PER-06** may approve their own inspection; a
  PER-07 never can. A self-approval is written to the audit ledger with the setting and override
  that allowed it, and shows a "self-approved" badge on the review screen and the PDF.
- **Notifications.** Submit notifies the project's reviewers; return and approve notify the
  inspector.

## Client co-sign (Pilot-0; user, 2026-11-13, R1a and co-sign redesign; TD-219)

The client representative co-signs with **their own KERP account (PER-15)** (user decision R1a).
After review round 3 of PR #51 the user replaced the offline PIN check (2026-11-13): **no client
PIN hash or other client secret is ever sent to or stored on an inspector's device.** There are
three ways to co-sign (user decision); details marked (planner default) are planner choices.

- **(i) Online, own account and PIN.** On the inspector's device ("Hand to client") the client
  signs in with username (or e-mail) and signing PIN; the server verifies it and returns a
  single-use co-sign token for that inspection, valid 5 minutes (planner default). The signing
  PIN is a 6-digit PIN the PER-15 sets online, stored only as an Argon2id hash in its own identity
  table (`oe_identity_signing_pin`, `BaseRecordMixin` + tenant RLS; not a column on the core user
  table — planner default). Rate limiting and lockout are **server-side per signer**: 5 wrong PINs
  lock co-sign for that signer for 15 minutes, 10 until the signer resets the PIN online (planner
  defaults). `verification_mode = online_pin`.
- **(ii) QR proof from the client's own app — the preferred offline path.**
  - **Key.** Each client device the PER-15 uses (KERP Android app or PWA) enrols a signing key
    pair to their account (`oe_identity_signing_key`: user, `device_id` from the device registry,
    public key, algorithm, status, enrolled/revoked times; `BaseRecordMixin` + tenant RLS). The
    private key never leaves that device: Android Keystore, or a non-extractable WebCrypto key in
    the PWA. Algorithm **ECDSA P-256 (ES256)** (planner default: supported by Android 11+ Keystore
    and WebCrypto). Revoking the device (T-0043) revokes its keys.
  - **Challenge.** The inspector's tablet shows a challenge QR: inspection id, record version,
    record hash (SHA-256 of the canonical record content at that version), ITP step or line id
    (if any), a summary (anchor code, template name, proposed result) and a fresh 128-bit nonce.
    The client app scans it. If the client device holds the record it checks the hash against its
    own copy and shows its own copy; otherwise it shows the challenge summary and the client
    reads the record on the tablet (two-way QR exchange).
  - **Assertion.** The client approves in their app, logged in as PER-15 (local unlock is enough;
    works offline). The app signs an assertion: `{v: 1, inspection_id, record_version,
    record_hash, step_id, signer_user_id, key_id, decision: "approve", nonce, time}` where `time`
    is the §6.4.1 evidence (`device_wall_time`, `monotonic_ms`, `clock_session_id`, anchor if any).
    Encoding (planner default): canonical JSON, ES256 signature, compressed and base45-encoded in
    one QR of at most 1,200 bytes (QR version ≤ 25, error correction M).
  - **Verify.** The tablet scans the assertion QR and verifies it offline: signature against the
    public keys of enrolled client devices delivered in the pull scope (public keys only), nonce
    equal to its challenge and unused, record hash equal to the local record, signer an active
    PER-15 of the project with the anchor in their granted subtree. The assertion is queued as a
    co-sign mutation. The server re-verifies everything at sync (key active at `occurred_at`, not
    revoked before it; nonce never used). `verification_mode = client_device_signed`: a full
    co-signature.
- **(iii) Wet signature on the inspector's tablet.** The client draws a signature and types their
  name; the inspector picks the client's PER-15 account from the project list (planner default).
  - It is stored as **provisional** (`verification_mode = provisional_wet`) and shows a
    "provisional client signature" badge on the review screen and the PDF.
  - The named PER-15 is notified (in-app now, push later) and confirms or rejects it in the
    client web app/PWA, signed in to their own account. Confirm writes a confirmed co-signature
    (`verification_mode = client_confirmed`) linked to the provisional one. Reject needs a reason
    and returns the inspection (`RETURNED`) with that reason. The wet image stays as evidence and
    is never deleted.
  - While a co-sign is provisional, an inspection whose template has `requires_client_signature`
    cannot be approved (`CLIENT_SIGNATURE_PROVISIONAL`).
  - Reminder to the client after 24 hours and to the project's PER-06 after 72 hours; a
    provisional signature never expires on its own (planner defaults).
- **Ledger entry.** Every co-signature is its own sign-off ledger entry (T-0119): signer user id
  and name, persona PER-15, `verification_mode` (`online_pin | client_device_signed |
  provisional_wet | client_confirmed`), the time evidence of diff §6.4.1, geolocation, the
  inspector's `device_id` (and the client `key_id` for mode ii), the record version and hash it
  covers, and the drawn image for mode iii. A co-signature covers one record version; editing the
  answers after it (for example after a return) needs a new co-signature. A template may set
  `requires_client_signature`; then submit checks `CLIENT_SIGNATURE_MISSING`.
- **Hand-over and hand-back.** "Hand to client" locks the inspector's work behind the inspector's
  PIN and shows only the inspection being signed, read-only. Afterwards the app clears everything
  the client entered except the co-sign mutation and returns to the inspector only after the
  inspector's PIN (T-0143 local unlock). The PDF prints each co-signature with its mode.
- **Residual risk** (TD-216): a provisional wet signature is only an
  inspector-witnessed claim until the client confirms it; a stolen client device key is stopped by
  revoking the device (T-0043), and assertions signed before the revocation stay valid.

## Planning fields (Pilot-0, planner spec 2026-11-13; TD-165)

Rules below are planner defaults unless marked otherwise.
An inspection carries `assignee_user_id` (nullable; a project member who may perform
inspections), `planned_start` (date, nullable) and `planned_duration_days` (integer ≥ 1, default 1).
`due_date` = `planned_start` + `planned_duration_days` − 1, stored and recomputed on change. An
inspection is **overdue** when `due_date` is before today in the project timezone and its status
is `PENDING`, `INSPECTION_REQUESTED` or `RETURNED` (planner defaults). PER-06 and PER-07 on the
project set these fields; assigning notifies the assignee (in-app, push on Android). A calendar
view, general recurrence and work-order grouping are not in Pilot-0 (deferred, see
`docs/TODO.md`).

## Comments on answers (Pilot-0, planner spec 2026-11-13; TD-165)

Rules below are planner defaults unless marked otherwise.
`oe_inspections_answer_comment` (`BaseRecordMixin` + project RLS): `inspection_id`,
`question_key`, `repeat_index` (null for a question outside a repeating group), `review_round`,
`body` (non-blank), author and time, `resolved_at` / `resolved_by`. A comment addresses one
answer by `(inspection_id, question_key, repeat_index)`; an address that is not in the pinned
template revision → 422. Comments are flat (no threads), append-only once synced (no edit, no
delete), and created offline with client-generated ids (`KERP-CONSOLIDATION-AND-DIFF.md` §6.4).
The inspector or a reviewer may mark a comment resolved. Comments sync to the devices that hold
the inspection (planner defaults).

## Inspection report (PDF) and batch export (Pilot-0, planner spec 2026-11-13; TD-169)

Rules below are planner defaults unless marked otherwise (TD-169 asked only for a server PDF per
inspection and a bulk export).

- **Per inspection.** `POST /api/v1/inspections/{id}/report` enqueues a SAQ `batch` job and
  returns its job id (T-0015 polling); the job renders the PDF with PyMuPDF (imported lazily,
  T-0022), stores it through the storage service with its sha256 and returns a presigned download
  link. A report exists for `SUBMITTED` (watermarked "Not approved") and for approved records; a
  report for an unchanged record version is reused, not rendered again. When an inspection is
  approved its report is rendered and its object key and sha256 are kept on the record for the
  dossier later.
- **Content.** Header: project, anchor display path in the project's terminology (T-0136), Item
  tag, template name and revision, RFI no. when a request is linked, `inspection_date`,
  `entered_at`, backdating flag and reason. Body: answers in template order with labels in the
  template's default language and units, conflict flags, photos with annotations burned in plus
  capture time and location, linked NCR and punch references. Signatures: inspector and reviewer
  with name, persona, UTC and site-local time, geolocation and the ledger content hash. Review
  history: rounds and return reasons. Footer: record id, page x of y, generation time.
- **Fixed labels.** Section headings, the "Not approved" watermark, "page x of y" and the other
  fixed labels come from a backend message catalogue keyed like the frontend `t()` keys and are
  rendered in the template's `default_language`, falling back to `en`; only English ships for now
  (TD-172). Template text uses the template's own localised strings (edge §2.2).
- **Batch export.** `POST /api/v1/inspections/reports/batch` with a list of ids or a filter
  (status, `inspection_date` range, anchor subtree) of at most 500 inspections enqueues one SAQ
  `batch` job. It produces a ZIP with one PDF per inspection, named
  `<anchor-code>_<template-key>_<inspection-date>_<short-id>.pdf`, and an `index.csv`; the link
  expires after 24 hours and the requester is notified when it is ready (planner defaults).

## Pilot-0 dashboard tiles (planner spec 2026-11-13; TD-173)

Rules below are planner defaults unless marked otherwise (TD-173 named only the three tiles).

Three tiles on Overview → dashboards (diff §6.1) for the current project, computed on request
from the owning modules' services (no rollup tables until E-06-05; planner default) and online
only, with the time they were computed:
- **Progress:** inspection counts in one bucket per status: not started (`PENDING`,
  `INSPECTION_REQUESTED`), returned (`RETURNED`), submitted (`SUBMITTED`), accepted
  (`INSPECTED_PASSED`), rejected (`INSPECTED_FAILED`), re-inspection due (`RE_INSPECT`) and locked
  (`LOCKED_FOR_TURNOVER`); every status falls in exactly one bucket. Share reviewed = (accepted +
  rejected + locked) / all. Filter "all" or "assigned to me".
- **Overdue:** count of overdue inspections (§Planning fields) and the ten most overdue with
  assignee and due date.
- **Open defects:** open NCRs and open punch items by category A/B/C, through the `ncr` and
  `punchlist` services.
Each tile opens the matching filtered list. Soft-deleted records are not counted.

## `eligibility` service (stateless, no tables)
Answers "is this person / instrument / material valid **on the inspection date**?" (ADR 0004
Amendment C; the `as_of` date defaults to today only for forward-looking assignment) across:
- **WPS/welder** qualifications ([`welding-and-nde.md`](welding-and-nde.md)),
- **credentials** register (tickets/accreditations/VOC + expiry),
- **equipment calibration** register (hard gate),
- **materials** (batch/heat certificates, [`materials-and-traceability.md`](materials-and-traceability.md)).
It owns no database tables; it reads the registers through their services and returns
pass/fail + reason; a missing expiry fails closed. It is a frozen seam at the M1 gate (T-0131).

**Registers — hybrid (TD-061).** Personnel competency (`credentials`), materials and test
equipment (`equipment` calibration) are **dedicated modules**. Everything else a template needs
to look up (WPS references, other client registers) is a **generic register** on the one-engine
item types: a register type is a versioned JSONSchema, so a project can add columns at any time
(ADR 0003 §6).

**Credentials are KERP records (TD-101).** KERP holds the credential records and the uploaded
certificate/VOC documents (not a read-only LMS mirror). A tenant-wide **qualification-type
catalogue** defines: name, whether it expires, expiry as a period from the issue date (or none),
and which inspection categories / work-task categories it applies to. Detailed catalogue and
competency-matrix spec: TD-099, TD-100; catalogue ownership decided in TD-118 (tenant administrator; PER-09 may add
project-level types; no super admin).

## `qms` — master ITP authoring + audit + calibration records
Strictly: ITP template authoring (on the one-engine), audit registers, and calibration
records. No parallel inspection store.

## `construction_control` — conformance + hold/witness evaluation
Conformance criteria and hold / witness / surveillance / review point evaluation **via
`run_checks`**. The legacy `construction_control._inspection` table is **dropped**.

## `inspection_requests` — RFI / call-outs
Workflow `draft → requested → confirmed → witnessed | waived | no_show | failed` with minimum
contractual notice, calendar bookings, broadcasts, and **automatic client delay attribution**
on `no_show`.
Decided (TD-045, 2026-11-10; full spec pending there): the Request for Inspection is its own
`inspection_request` record in scheduling, cross-linked to the TQ (Technical Query) module;
"rescheduled" is a transition back to `requested` that keeps history. Multi-anchor requests use
`oe_inspections_request_anchor` (above).

## Findings register (TD-106)
Client-asset findings (CUI, water ingress, cladding damage) go to a **separate findings
register** — not NCR, not punch — with an **assessment step**. Per project: the register's name,
its finding types, and whether follow-up work may be raised from an assessed finding. Detailed
spec and task: TD-117.

## Coatings & environmental criteria (preserved)
Recorded as measurement fields with CEL validation:
- ambient `temperature`, relative `humidity`, `dew_point`, substrate `steel_temp`;
- surface `profile` (microns), wet/dry film thickness `WFT`/`DFT`, `holiday_testing` result;
- **dew-point guard (CEL):** `fields.steel_temp >= fields.dew_point + 3.0` — block coating
  application when substrate is within 3 °C of dew point;
- **DFT average (CEL):** `(fields.dft_1 + fields.dft_2 + fields.dft_3) / 3.0`;
- **adhesion (CEL):** `fields.coating_adhesion_psi >= 1450.0` per SP-COAT-001.

## Welding/NDE inspection
Weld visual/NDT acceptance runs through this engine; weld-specific entities/rules live in
[`welding-and-nde.md`](welding-and-nde.md).
