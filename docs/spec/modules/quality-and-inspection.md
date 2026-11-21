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
- **Image derivatives (user, 2026-11-21; planner reconciliation TD-408; T-0247).** Every image
  upload (JPEG, PNG, WebP: photo answers and reference images) keeps its original, untouched and
  verified against the client SHA-256 at its content-addressed key (T-0016). After the scan
  passes, a `batch` SAQ job (Pillow imported lazily, T-0022) writes two derivatives, both
  auto-oriented from EXIF, EXIF stripped, sRGB JPEG:
  - **tablet proxy**: longest edge ≤ `tablet_proxy_max_px` (default 1280), progressive, quality
    `tablet_proxy_quality` (default 72);
  - **report rendition**: longest edge ≤ `report_image_max_px` (default 1024), quality
    `report_image_quality` (default 68).
  The file becomes `available` only when the scan passed **and** both derivatives exist (an image
  that cannot be decoded or exceeds the pixel cap is `rejected`). Width, height and each
  derivative's key and size are stored with the file. Tablets download and show the proxy
  (download size estimates use the proxy size); web lists and thumbnails use the proxy, the
  lightbox and downloads the original; the PDF report embeds the report rendition; the project
  archive exports the original.
- **Annotation frame.** "Normalised against the original image" means the original **after its
  EXIF orientation is applied**, which is the frame of both derivatives, so one annotation JSON
  fits all three. Editing on a tablet over the proxy sends only the annotation path of that
  photo (ADR 0005 per-field mutation, path keyed by `attachment_id`), never a new
  `attachment_id` or image; overlays and burn-in are drawn at render time on whichever tier is
  shown (TD-408).
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

## Standalone inspections (user, 2026-11-21; planner reconciliation)

The user's 2026-11-21 paste adds inspections that are not part of the ITP chain: daily
pre-starts, safety checks, equipment checks and repair/fault reports. Tasks: T-0179 (categories),
T-0251 (registers and tile filter), T-0249/T-0250 (equipment, blocked by TD-412/TD-411).

- **Category.** Templates (revisions) and `oe_inspections_inspection` carry `category`
  (`qa_itp | safety_prestart | equipment_check | repair_fault | general`, default `qa_itp`) and
  `is_standalone` (default false). A record takes both from its pinned revision; the category is
  part of the revision hash. Rule sets may branch on `record.category`.
- **No parent ITP.** A standalone template is `kind = inspection` only and is never attached to an
  ITP line. Its lifecycle, review (§Review and return) and signing are the same as any inspection.
- **Anchors.** ADR 0003 applies unchanged: an `inspection_anchor` node or an Item. Anchoring to an
  equipment register entry (fleet vehicle, plant) is **proposed** in ADR 0014 (TD-411): a third
  exclusive anchor `equipment_id`, allowed only for standalone `safety_prestart`,
  `equipment_check` and `repair_fault` records, plus an informational `location_node_id` that
  never counts for roll-ups or completion. Until TD-411 and TD-412 are decided, standalone
  inspections anchor to nodes and Items only.
- **Where they show.** Dedicated registers (Inspections → Pre-starts, Safety checks, Fault
  reports). The Pilot-0 tiles count `qa_itp` only by default, with a toggle for all categories
  (T-0251; T-0160 itself is unchanged).

## Public capture links (user, 2026-11-21; **proposed**, ADR 0013, decision TD-410)

Nothing in this section may be built until the user accepts ADR 0013 (T-0252–T-0255 are
blocked by TD-410). It conflicts with "tenant from the JWT only" (CLAUDE.md §6) and with magic
links for Field Workers only (diff §4.2), so it needs an explicit, documented exception.

- **Link** `oe_inspections_public_link` (`BaseRecordMixin` + standard project RLS; the paste's
  `oe_inspection_public_link`): one pinned standalone template revision, `category` (default
  `safety_prestart`), at most one pre-bound anchor (`anchor_node_id`, `anchor_item_id`,
  `equipment_id`), `token_hash` (SHA-256, unique), `label` (e.g. "LV-042 Daily Pre-Start QR"),
  `allowed_lookup_scopes` (default `{"equipment": false, "staff": false}`), `expires_at` (null
  only for an explicitly permanent link), `max_submissions`, `submission_count`, `is_active`,
  `created_by_user_id`; index `(project_id, is_active)`. Created, revoked and rotated by the
  project's PER-06 and PER-09; the token is shown once.
- **Token and scope.** The token is opaque: project id + 32 random bytes. The public router binds
  the deployment tenant from configuration and the token's project as the session scope, then
  looks the hash up under the normal project policies. No row-returning RLS bypass. Every failure
  (unknown, tampered, expired, revoked, inactive, exhausted, other project, public capture
  disabled on the project) is the same 404 and leaks nothing.
- **Endpoints** `/api/v1/public/inspections/{token}`: `GET /` (template, localised strings,
  anchor header, allowed lookups), `POST /uploads/presign` (T-0016, project-scoped, size ≤
  `max_upload_size_mb`, real ClamAV scan T-0218 required), `POST /submit` (server-side CEL
  validation, T-0110; `source = "public_link"`, `public_link_id`, `submitted_by_display_name`,
  optional `submitter_contact`, IP, user agent; ledger entry; post-submit automation).
- **Lookups** list only selectable fleet asset numbers + descriptions and/or display names of
  active members: never e-mails, phones, user ids or internal ids.
- **Controls:** per-client-IP and per-link rate limits (429), token hashing and log scrubbing,
  no PII in lookups, project-scoped short-lived presigned uploads, ClamAV, abuse controls
  (caps, idempotency, honeypot, size limits, project kill switch), audit with
  `source = "public_link"`. IP and user agent are kept 90 days (planner default, TD-410).
- **Runner** `/public/inspect/:token`: mobile-first, Dynamic Form Renderer, 48 dp targets, photo
  capture + annotation, receipt.

## Post-submit automation (user, 2026-11-21; planner defaults)

Template-level rules that run after an inspection reaches `SUBMITTED` (authenticated and, once
accepted, public). Tasks: T-0180 (store, engine, `update_target_fields`), T-0256
(`notify_users`), T-0257 (`create_action_task`, tagout gate), T-0258/T-0259 (screens).

- **Rule** `oe_inspections_post_action` (paste: `oe_inspection_post_action`): `sort_order`,
  `name`, `enabled`, `trigger_cel` (CEL bool over `answers`, `result`, `category`, `inspection`,
  `equipment`, `node`, `item`; default `true`; compiled on save; sandboxed, fails closed),
  `action_type`, `action_config`. Part of the frozen template revision and copied with client
  packs; edited by PER-06 and PER-09 on draft revisions.
- **`update_target_fields`:** only fields the owning module whitelists in its `service.py`
  (equipment: `last_inspected_at`, `last_inspection_id`, status `out_of_service` /
  `quarantined`; node/Item: `last_inspected_at`, `last_inspection_id`), applied through that
  service's own transition (with its `run_checks`). **Automations only restrict, never release**
  (planner rule): no automatic transition to `active`.
- **`notify_users`:** personas (resolved to project members at send time), the equipment
  custodian, explicit users; in-app high priority (T-0019), push, and an e-mail with project
  code/name and a deep link only (diff §4.6). No SMS (TD-413). Manual API notifications stay
  PER-09-only (diff §4.1); automations notify through the service.
- **`create_action_task`:** `oe_inspections_follow_up` (paste: `oe_inspection_follow_up`) with
  `kind` (`tagout_confirmation | repair_request | supervisor_review`), assignee persona/user,
  `due_at` from SLA hours, `blocks_return_to_service`, status `open | tagout_confirmed |
  resolved | cancelled`. May move to the tasks module later (E-04-07, TD-415).
- **Tagout gate:** a blocking `tagout_confirmation` sets the target `out_of_service` in the submit
  transaction (blocked from selection in inspections and allocation, T-0112 gate); the supervisor
  (PER-04/PER-03) gets a pinned high-priority "Confirm physical tagout applied" (timestamp, user,
  optional tag photo, notes); returning to `active` fails `run_checks(context,
  "equipment.activate")` with 422 while a blocking follow-up is open.
- **Execution and audit:** database actions run in the submit transaction; remote notification
  channels after commit on the SAQ `interactive` queue. Each action writes
  `oe_inspections_action_log` and a T-0006 ledger entry with `source = "automation"` (new
  `RECORD_SOURCES` value, TD-417). A template without rules has no side effects.

## QA verification queue and two-lane workbench (user, 2026-11-21; planner defaults)

One queue for documents QA must verify before a record becomes active. Tasks: T-0181 (queue),
T-0260 (equipment), T-0261 (credentials), T-0265 (heat/MTR, Phase 2), T-0184 (shelf-life
extensions, Phase 3; materials-and-traceability.md §Shelf-life extension), T-0262 (window).

- **QA authority.** Project setting `qa_reviewer_persona` (default PER-06; QA-capable personas
  only). Holding it on the project is QA authority; admin grants never confer it (diff §4.1).
- **Item** `oe_qms_verification_item` (`BaseRecordMixin` + project RLS): `target_kind`
  (`equipment_calibration | staff_credential | material_mtr | material_shelf_life_extension`),
  `target_id`, `parent_entity_id`,
  `document_id`, `status` (`pending | approved | rejected | reassigned | escalated |
  superseded`), submitter, assigned persona/user, decision by/at, `rejection_reason`,
  `follow_up_task_id`; indexes `(project_id, status, target_kind)`, `(project_id,
  parent_entity_id)`. Owning modules register a provider (details, OCR values, history,
  apply-approved, apply-rejected); qms never reads their tables.
- **Gates** (`run_checks`, locked system-default `check` rules, ADR 0004 §5):
  `equipment.activate` (diff §5), `credential.activate` (eligibility fails unverified
  credentials, T-0139), `material_cert.verify` (materials-and-traceability.md).
- **Missing-document alert:** creating equipment that requires calibration, a credential whose
  type needs a document, or a heat batch without a certificate sends a high-priority notification
  to the QA reviewer persona.
- **Non-QA upload routing:** an upload by a user without QA authority is `pending_qa_verification`
  (equipment) / `pending_verification` (credential, MTR) even when SAQ OCR matches, and creates an
  item with a deep-link notification; a QA reviewer's own upload may be verified at once (still an
  item, audited). The T-0112 OCR-discrepancy review is routed into this queue (TD-416).
- **Decisions:** Accept/Verify (target verified and active, prior certificate superseded and
  locked, ledger, uploader notified); Reject (mandatory non-blank reason, target stays inactive,
  optional rectification follow-up); Reassign/Forward (user or persona + note). Nobody decides an
  item they submitted unless the project allows self-approval (T-0140).
- **Window:** full-height drawer from the inbox or a notification. Left lane: record values next to
  OCR values with discrepancies highlighted and a history selector of superseded documents. Right
  lane: PDF/image viewer over presigned URLs with zoom and pages.
- **Existing records** when the gates land: TD-414 (planner default: documents already attached
  count as verified with `verification_source = 'pre_gate'`).

## Verification SLA and pending-approval banners (user, 2026-11-21; planner defaults)

Tasks: T-0182 (SLA rule set, cron, APIs), T-0263 (banner), T-0264 (overrides and follow-ups).

- **Rule set `verification.sla`** (ADR 0004; project-editable, not locked): rules of the existing
  `notify` kind; CEL bool over the item (`event.target_kind`, `event.record_type`,
  `event.criticality`, `event.category`, `event.p6_start_in_hours` when a P6 link exists via
  `oe_p6_anchor_link`) with `params` `{remind_after_hours, escalate_after_hours,
  escalate_to_persona}`; first matching rule wins; default 24 h / 48 h / PER-02.
- **Columns** on `oe_qms_verification_item`: `due_remind_at`, `due_escalate_at`,
  `reminder_sent_at`, `escalated_at`, `escalated_to_user_id`, `escalated_to_persona`. Override
  requests (`oe_validation_override`, frozen at M1) and follow-ups keep their shape; their SLA
  state is a qms-owned `oe_qms_sla_state` row (TD-420).
- **Cron** (`batch` SAQ, every 15 min per active project): reminder (pinned high-priority in-app +
  e-mail `verification_reminder`) once; escalation once (status `escalated`, target notified,
  original reviewer notified, ledger `verification.escalated`). Idempotent.
- **Escalation does not widen approval** (planner default, TD-418): approve/reject still needs QA
  authority; override approvals keep ADR 0004 Amendment A routing.
- **Banner** on the DetailPage of any record with an active pending or escalated item:
  status, assignee, submitted at, countdown or overdue; Review & Verify for QA authority; Send
  Reminder for the submitter and PER-02/03/09 (one per item per 4 h, 429 beyond).

## Expiry lead-time alerts (user, 2026-11-21; planner defaults)

Task T-0183 (module `compliance`). Warns ahead of expiry; validity itself stays the as-of-date
check of the owning modules (§eligibility service).

- **Rule set `compliance.expiry_alerts`** (ADR 0004 layers, shipped in client packs, project copy
  edited by PER-06/PER-09): `notify`-kind rules selecting resources by kind and subtype
  (equipment type or criticality, qualification type, material class) with `params`
  `{lead_days, notify_holder, notify_personas, notify_user_ids}`. Defaults: calibration
  [30, 14, 3], credentials [60, 30, 7], material use-by [14, 7, 2] (when materials register).
- **Providers:** `equipment` (calibration due, custodian), `credentials` (expiry, holder), later
  welder continuity (T-0207) and material use-by (E-03-03/E-03-04), through `service.py`.
- **Daily cron** sends one warning per resource, expiry date and threshold (dedup log
  `oe_compliance_expiry_alert_log`, unique `(project_id, resource_kind, resource_id,
  expiry_date, lead_days)`); in-app + e-mail `expiry_lead_warning` (code/name + link).

## Schedule readiness conflicts (user, 2026-11-21; planner defaults)

Tasks T-0266 (engine, inspections), T-0267 (P6). Rendering belongs to the calendar (E-04-15) and
the Look-Ahead board (E-04-02).

- `oe_schedule_readiness_conflict` (`BaseRecordMixin` + project RLS): resource (`resource_kind`,
  id, label, `expiry_date`), target (`target_kind` `p6_activity | work_plan_line |
  scheduled_inspection`, id, label, planned start/finish), `anchor_node_id`, `status` (`open |
  acknowledged | resolved`), `last_notified_at`, `resolved_at`; unique `(project_id,
  resource_kind, resource_id, target_kind, target_id)`; index `(project_id, status,
  expiry_date)`.
- A conflict exists when a required resource (assigned or anchor-linked equipment, an assignee's
  required credential, a staged material lot) expires before the target's planned finish (or the
  inspection date). Targets come from providers (inspections T-0152, P6 through
  `oe_p6_anchor_link` T-0104, work-plan lines E-04-12).
- Evaluated daily and reactively (inspection date/assignee change, P6 import commit, expiry
  change, verification approved). A conflict that no longer holds resolves itself. Conflict alerts
  (in-app + e-mail `schedule_readiness_conflict`) go to the matching rule's recipients, again only
  when the target dates change.

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
