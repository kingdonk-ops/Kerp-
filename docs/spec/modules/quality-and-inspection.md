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
`inspection_request` / `INSPECTION_REQUESTED`. The RFI record, numbering scheme and
per-discipline layout are specified in §`inspection_requests` (planner spec 2026-11-21 on the
TD-045 decisions; the product owner checks it against a client sample before T-0115 ships).

## One field-type registry (`core/fields`)
Shared palette of field types used by every form, ITP, issue template ([issues.md](issues.md)) and
asset-schema field:
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
call-out state is `INSPECTION_REQUESTED`. A `qa_itp` record anchors to **any node whose level has the `inspection_anchor` role, or an Item**
(ADR 0003 §6; e.g. Line in `cui-line`, RSW and Item in `remediation-*`), including a node with no
work under it yet (ADR 0003 Amendment 5). Records of every other area are project-level with an
optional location and links (§Project-level inspections; ADR 0003 amendment 2026-11-21). Holds responses authored against a template revision
(pins `template_revision_id`), `status` lifecycle, `reading_*`, `signed_off_*` (user, signature
hash, UTC timestamp, geolocation). Lifecycle (master spec §4.2):
`PENDING → INSPECTION_REQUESTED → INSPECTED_PASSED → LOCKED_FOR_TURNOVER`, with
`INSPECTED_FAILED → (Cat A punch | NCR) → RE_INSPECT` (state name confirmed on TD-045); a re-inspection call-out moves `RE_INSPECT → INSPECTION_REQUESTED` (§`inspection_requests` 5). Every transition calls `run_checks`.
The punch or NCR is an **issue** of the `issues` module, raised through the post-submit action
`create_issue` or by the reviewer ([issues.md](issues.md) §8; user, 2026-11-21).
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
  compare view, not an inheritance overlay (TD-107). Templates are authored in the **shared
  template designer** for inspection and issue templates (epic E-01-03, after Pilot-0; user,
  2026-11-21); until then they come from client packs, seeds and the API.
- **Close-out bulk signing (TD-095).** An ITP close-out may sign all `R`/`S`/`M` rows of one party
  with one date in one action (one sign-off ledger entry per row, same signer and date); `H` and
  `W` points are always signed individually — a bulk request that includes any `H` or `W` row is
  rejected as a whole (422) and nothing is signed.

### Request anchors
A Request for Inspection may cover several anchors (e.g. one RFI slot for several RSWs; NDT
requests to an NDT provider are a separate record, TD-098): `oe_inspections_request_anchor(request_id, node_id | item_id)` — exactly one of
`node_id`/`item_id` set — replaces a single anchor FK (ADR 0003 §2). For the Request for
Inspection the rows also carry the `slot_id` they belong to (§`inspection_requests` 2).

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
  raises the Cat A punch or NCR as before, through `create_issue`, [issues.md](issues.md) §8). The reviewer cannot change the result; to disagree they
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
three ways to co-sign (user decision), plus a fourth, a scanned paper signature confirmed by the
client in the same way as mode iii (user, 2026-11-22, TD-592); details marked (planner default)
are planner choices.

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
- **(iv) Scanned paper signature** (user, 2026-11-22, TD-592). A page the client signed on paper
  may be scanned and used as the client co-sign, on exactly the terms of mode iii: the uploader
  attaches the scan to the specific record version and picks the client's PER-15 account from the
  project list; it is stored as **provisional** (`verification_mode = provisional_scan`) with the
  "provisional client signature" badge; the named PER-15 confirms (`client_confirmed`, linked to
  the provisional one) or rejects with a reason (`RETURNED`) in their own account; while it is
  provisional, approval is blocked (`CLIENT_SIGNATURE_PROVISIONAL`); the same reminders apply.
  The scan stays as evidence and is never deleted.
- **Ledger entry.** Every co-signature is its own sign-off ledger entry (T-0119): signer user id
  and name, persona PER-15, `verification_mode` (`online_pin | client_device_signed |
  provisional_wet | provisional_scan | client_confirmed`), the time evidence of diff §6.4.1, geolocation, the
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

## Signing attestation (e-signature) (user, 2026-11-21, enterprise roadmap; planner defaults TD-433)

Formal sign-offs are attributable electronic signatures: the signer proves presence again,
states what the signature means, and the sign-off ledger (T-0119) keeps the evidence. Tasks
T-0289 (backend), T-0290 (ceremony UI), T-0291 (consumers and PDF); later consumers adopt it in
their own tasks (T-0184, E-05-04, E-05-05). Not in the Pilot-0 gate (T-0146): sign-offs made
before it ships carry no attestation and stay valid ("no attestation recorded" on the PDF).

- **Formal kinds** (code-declared registry in `signing`, each with an intent-statement i18n key
  and an integer `intent_version`): `itp_hold_release`, `itp_witness` (single-row `H`/`W`
  sign-off), `inspection_approve`, `substitution_approve` (internal_qa route,
  `materials-and-traceability.md` §Product substitutions), `shelf_life_extension_approve`
  (§Shelf-life extension, Phase 3), `tq_approve` (TQ register, Phase 5), `ncr_close_verify`
  (NCR effectiveness verification, [issues.md](issues.md) §4.2; user, 2026-11-21; T-0313, wired
  in T-0291). Not attested
  (TD-433 default): the inspector's submit signature, the bulk close-out of `R`/`S`/`M` rows
  (TD-095) and client co-sign, which has its own verification modes (§Client co-sign).
- **Re-authentication.** Online the signer re-authenticates for each formal sign-off with the
  **signing PIN** (the T-0162 PIN, open to every user; same per-signer lockout) or the account
  **password** (T-0068 lockout); SSO-only users use the signing PIN, which can be set or reset
  only within 15 minutes of a fresh sign-in. `POST /api/v1/signing/reauth` returns a
  **single-use signing grant** bound to user, session, kind and record id + version, valid
  `signing_reauth_ttl_minutes` (registry, default 5, 1–15). Offline on the Android tablet (field
  H/W sign-off) the app asks for the **device PIN again at the moment of signing**, even while
  unlocked (local keystore check, local lockout of diff §4.2); the queued mutation carries
  `reauth_method = device_pin_offline` and its time evidence. Its sync acceptance is the
  **offline branch** below.
- **Offline branch at sync (T-0310).** A queued formal sign-off with
  `reauth_method = device_pin_offline` needs **no server signing grant** (the device-PIN
  re-entry is the re-authentication). The server decides on the reconstructed `occurred_at`
  (diff §6.4.1):
  - **Device:** accepted only if the device is active, enrolled to the signer and not revoked
    before `occurred_at`; a mutation from a revoked or foreign device is refused like any other
    mutation from it (T-0143). Past this check the sign-off is never dropped and never answered
    with 409.
  - **Intent version:** accepted if the version the device showed was the current one at
    `occurred_at` (the registry keeps every version of a kind with its `effective_from` and its
    i18n key); otherwise kept, effective, flagged `intent_outdated` and listed for QA review. The
    device sends only the `intent_version` it showed; the server renders that version's i18n
    text in the signer's locale and never accepts client-supplied intent text.
  - **Signer standing:** `signer_standing(user_id, from_ts, to_ts)` in the identity service
    answers from durable standing events persisted in the identity table
    `oe_identity_account_standing_event` (`deactivated | reactivated | sessions_revoked |
    signed_in`, with `occurred_at` and, for `signed_in`, the `device_id`), written by admin
    deactivation and reactivation (T-0014 path), force sign-out (T-0244), SCIM (T-0299) and every
    successful sign-in; it never reads the expiring Redis session index. The window starts at the
    signer's last successful sign-in on the mutation's `device_id` at or before `occurred_at` (no
    such record: at the signer's earliest recorded event, fail-safe) and ends at `occurred_at`;
    when `time_confidence` is neither `MONOTONIC_RECONSTRUCTED` nor `VERIFIED_ONLINE`, it ends at
    the sync time instead (fail-safe against a backdated clock). If a deactivation or a session
    revocation of the signer falls inside the window, the sign-off is **kept as evidence but not
    effective**: it does not count as signed, releases no hold and closes no row, and it is
    flagged `signer_inactive`. A deactivation and reactivation both before the window start do not
    flag it; a later reactivation never makes a flagged sign-off effective.
  - **QA routing:** a `signer_inactive` sign-off creates a QA verification item of target kind
    `offline_signoff_signer_inactive`, whose provider is registered in `signing/service.py`.
    Neither approve nor reject ever makes the original sign-off effective: the provider's
    `apply_approved` records the flag as reviewed and `apply_rejected` records the entry void,
    each only as a ledger append; the flagged entry is never altered and nothing is released or
    closed. The point is resolved only by a fresh attested online sign-off by a qualified signer
    through the normal consumer path, or by recording the flagged entry void.
  - **Grace:** outside the offline grace/window of diff §4.2 it is flagged `grace_exceeded`
    (kept, effective, as any offline mutation).
  Flags are recorded in the attestation (`flags: [...]`) and the audit payload; `ip_source = sync`.
- **Intent statement.** Before signing the signer sees and explicitly accepts a statement such as
  "I, <name>, as <persona>, release hold point <code> on <anchor> and confirm the work meets the
  stated acceptance criteria" (wording per kind in the i18n catalogue). The server renders the
  text in the signer's locale. Online, an outdated `intent_version` → 409 (the client re-renders
  and asks again) and not accepted → 422; offline sign-offs follow the offline branch above.
- **Evidence in the ledger.** The sign-off ledger entry's payload carries
  `attestation: {v, kind, intent_key, intent_version, locale, intent_text, intent_sha256,
  reauth_method, reauth_at, signed_at, occurred_at + time evidence (diff §6.4.1) when offline,
  device_id, ip, ip_source (request | sync), user_agent_family, on_behalf_of_user_id,
  delegation_id, flags}`. It is inside the hashed payload, so the ledger format (`v`) is
  unchanged. The IP is the one uvicorn resolves behind trusted proxies only
  (`FORWARDED_ALLOW_IPS`). Privacy: the IP and the user-agent family sit in the immutable,
  hash-chained ledger and cannot be erased later; this is accepted for evidentiary sign-offs,
  and only these minimum fields are kept (no full user-agent string, no geolocation; TD-433).
- **Authority is unchanged.** Attestation proves who signed and that they meant it; it never
  grants anything. Persona, site scope, credentials (TD-099/TD-100), SoD and `run_checks` are
  checked by the consumer as before; `system_admin` has no `A`/`S` (ADR 0016).
- **Never under impersonation.** `/signing/reauth` and every attested sign-off refuse a request
  context with an impersonator (`403 IMPERSONATION_ACTION_FORBIDDEN`, ADR 0015) on top of the
  central guardrail (T-0278); the admin also never holds the target's PIN or password.
- **On paper.** The inspection PDF (T-0158) and the MDR dossier (E-05-05) print an attestation
  block per formal sign-off: signer and persona, "on behalf of <name>" when signed by a delegate,
  intent text and version, method, signed time (occurred time and confidence offline), device
  id and IP, through one shared renderer in `signing` (T-0291).
- **Snapshot of what was signed** (product intake 2026-11-21, planner default; user to
  confirm). The attestation also records, at the moment of signing and never backfilled later:
  `signer_snapshot` (display name, organisation, persona and site scope used) and
  `credential_snapshot` (each credential or qualification the consumer checked for this
  sign-off: credential id, type, level, number, valid-until and verification state at that
  instant; empty when the kind needs none). For sign-offs against a template or contract text,
  the consumer passes the template revision id and its content hash. A later change of name,
  credential expiry or template revision never alters the stored snapshot. Records signed before
  this lands show "not recorded", never a value derived after the fact.
- **Competency at signing** (product intake 2026-11-22 (3), planner default; user to confirm;
  TD-591). The `credentials` service (T-0118) exposes a read-only fact to sign-off rule sets:
  the signer holds a valid credential of qualification type X (and level, where the type has
  levels) **at the signing time** (`as_of` = the sign-off's `occurred_at`, the same as-of rule
  as §eligibility service). A rule set can require it (for example a torque or bolting `H`/`W`
  line needs a current bolting certificate); it is evaluated by `run_checks` like any other
  fact, never as a hard-wired check. Every credential the rule set read is written to
  `credential_snapshot` above, so the evidence shows what was valid when the person signed.
- **Paper-signed pages** (product intake 2026-11-22 (3), planner default; user to confirm;
  TD-592). A scan of a page signed on paper can be attached to a **specific ITR revision**
  (inspection record and version) as a file of kind `wet_signed_scan`, with uploader, upload
  time, the signer's name and party as stated by the uploader, and an optional note (audited).
  On its own it is **evidence only**: it carries no attestation and counts as no internal
  sign-off; internal formal sign-offs (the kinds above) still need their own attestation. It
  counts as the **client co-sign only through §Client co-sign mode iv** (user, 2026-11-22,
  TD-592): the uploader names the client's PER-15 account and the co-sign stays provisional until
  that client confirms in their own account (TD-219 unchanged: the client always confirms with
  their own account). The
  inspection PDF and the dossier (E-05-05) list it as "Wet-signed on paper; scan uploaded by
  <name> on <date>", next to and never in place of an attestation or co-sign block; a scan used
  as a mode iv co-sign shows in the co-sign block with its provisional or confirmed state. The client's wet signature drawn on the tablet (§Client co-sign, mode iii) is a
  different thing and is unchanged.
- **Keys and fingerprints** (product intake 2026-11-21, planner default; user to confirm). Every
  record sealed with a key names the fingerprint (SHA-256 of the public key or certificate) of
  that key: client device co-sign assertions (T-0173), PAdES signatures of dossiers and Delay
  Notices (documents-and-mdr.md). Rotated or retired keys and certificates are kept (public part
  only, with their validity window and retirement time) so older records stay verifiable; a key
  is never deleted while a record names it. The hash-chained ledger itself is not key-signed and
  is unaffected.
- **Retention vs erasure** (product intake 2026-11-21, planner default; user to confirm). A
  signature image (provisional wet signature, T-0174; drawn signatures) is a file with its own
  retention schedule (TD-175); when it is erased, the ledger entry, attestation and image hash
  remain, so the fact of signing survives and the PDF prints "signature image removed under
  retention policy, <date>". A privacy erasure request does not erase a signature on a formal or
  co-signed record: it is retained as evidence (legal-obligation basis), and the refusal is
  recorded in the erasure log.

## Delegation of authority (narrow; user, 2026-11-21, enterprise roadmap; planner defaults TD-434)

A user who will be away names an **acting delegate** for a time window, so items addressed to
them personally do not stall. This is the narrow slice of E-06-07 (approval_routes), pulled
forward to Phase 1 (T-0292 model and API, T-0293 routing and on-behalf actions, T-0294 screens).

- **Delegation record** `oe_delegations_delegation` (project-scoped, `BaseRecordMixin` + RLS):
  delegator, delegate, kinds (`hold_point`, `verification`, `sla_escalation`, `tq_approval` from
  Phase 5), start, end (at most 90 days), reason, revoked at/by. Created by the delegator, or for
  an absent member by a PER-09 of the project or a `system_admin` (audited, delegator notified).
  Never to oneself, never chained (a delegate's own delegations are ignored), at most one active
  delegation per delegator, project and kind; revocable at any time. Every change audited.
- **Routing.** Hold/witness call-outs to a named person (`inspection_requests`, T-0115),
  verification items assigned to a user (§QA verification queue) and SLA reminders and
  escalations addressed to a user (§Verification SLA) also go to that user's active delegate for
  the kind. Items already assigned to the delegator when the delegation starts are included
  (TD-434 default); the delegate gets one summary notification at start. Authority that comes
  from a persona (any PER-06/PER-07 may approve an inspection) gains nothing from delegation.
- **Delegation never confers authority.** To act, the delegate must themself hold the qualified
  persona on the project, the site scope and valid credentials (TD-099/TD-100), and the action
  runs the same `run_checks`. SoD and self-approval apply to the delegate (no approving work they
  performed or submitted; `allow_self_approval` evaluated for them). No admin grant adds anything
  (§4.1, ADR 0016). The delegator keeps their own authority; the first action wins.
- **Recorded on behalf.** Every action a delegate takes on behalf of the delegator records
  `on_behalf_of_user_id` + `delegation_id`, with `created_by` / the actor = the delegate: for
  formal kinds in the ledger attestation (`attestation.on_behalf_of_user_id`, `delegation_id`);
  for actions that are not attested (e.g. verification approve/reject in the QA verification
  queue, reassignments) on the decision record itself and in its audit entry. The PDF and the
  MDR print "Signed by X on behalf of Y". Roster awareness (who is on shift) belongs to E-04-12.

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
- **Open defects:** open NCRs and open punch items by category A/B/C, through the `issues`
  service (NCR and punch are the two issue types, [issues.md](issues.md); user, 2026-11-21).
Each tile opens the matching filtered list. Soft-deleted records are not counted.

## Standalone inspections (user, 2026-11-21; planner reconciliation)

The user's 2026-11-21 paste adds inspections that are not part of the ITP chain: daily
pre-starts, safety checks, equipment checks and repair/fault reports. Tasks: T-0179 (categories),
T-0324 (template settings), T-0325 (project-level records, location, links), T-0251 (registers
and tile filter), T-0250 (equipment links), T-0249 (fleet classes; TD-412 resolved 2026-11-21: fleet vehicles and plant join the Phase-1
register and can be inspection targets through equipment links).

- **Category = Area.** Templates (revisions) and `oe_inspections_inspection` carry `category`
  (`qa_itp | safety_prestart | equipment_check | repair_fault | health_safety | general`, default
  `qa_itp`) and `is_standalone` (default false). The screens call it **Area** and show plain
  labels through `t()`: Quality (QA/ITP), Pre-start, Equipment check, Repair / fault,
  Health & Safety, General (user, 2026-11-21; `health_safety` added as the area for H&S checks
  and H&S audits; user, 2026-11-21, TD-456). A record takes both from its pinned revision; the category
  is part of the revision hash. Rule sets may branch on `record.category`.
- **No parent ITP.** A standalone template is `kind = inspection` only and is never attached to an
  ITP line. Its lifecycle and signing are the same as any inspection; review depends on the
  formal tick (§Template settings).
- **Belongs to vs connected to** (user, 2026-11-21; ADR 0003 amendment 2026-11-21): derived from
  the area, not a separate setting. A `qa_itp` record **belongs to an asset**: it anchors to an
  `inspection_anchor` node or an Item exactly as ADR 0003 §6 says (roll-ups and completion gates
  unchanged). A record of any other area has **no anchor**: it belongs to the **project**, with an
  optional informational **location** and optional **links** (§Project-level inspections).
  Equipment is a link, never an anchor (ADR 0014 superseded; TD-411 resolved).
- **Where they show.** Dedicated registers (Inspections → Pre-starts, Safety checks, Fault
  reports). The Pilot-0 tiles count `qa_itp` only by default, with a toggle for all categories
  (T-0251; T-0160 itself is unchanged); informal records are never counted (§Template settings).

## Template settings, presets and the two template menus (user, 2026-11-21)

Tasks: T-0324 (settings, presets, lock rules), T-0327 (linked issue types), T-0328 (Templates
menu), T-0320 (Issues menu → issue templates), epic E-01-03 (the shared designer both open).

- **Two menu items, one designer.** **Templates** (Execution group, diff §6.1) lists **all**
  inspection templates of the project in one filterable list — QA/ITP forms, H&S checks,
  pre-starts, equipment checks, repair/fault forms, audits — with filters area, issue category,
  formal, audit, status (`draft | published | retired`) and kind (`inspection | itp`).
  **Issues** is a separate menu item where issue templates (issue types: punch or NCR per
  category) are created and managed ([issues.md](issues.md) §2). Both open the same shared
  template designer (E-01-03) for sections and fields. Inspections are filled full-page in the
  field; issue forms are simpler.
- **Presets.** "New template" asks one question. Inspection side: QA inspection, H&S check,
  Pre-start, Equipment check, Repair/fault, Internal audit, External audit; Issues side: Punch,
  NCR. The preset only pre-sets the settings below; the user can change every one of them before
  the first publish. Inspection presets (user, 2026-11-21; TD-456):

  | Preset | Area | Formal | Audit |
  |---|---|---|---|
  | QA inspection | `qa_itp` | on | off |
  | H&S check | `health_safety` | off | off |
  | Pre-start | `safety_prestart` | off | off |
  | Equipment check | `equipment_check` | off | off |
  | Repair/fault | `repair_fault` | off | off |
  | Internal audit | `general` | on | `internal` |
  | External audit | `general` | on | `external` |

  The issue presets copy the type and flag defaults of the project's `general` punch or NCR
  template (issues.md §2; user, 2026-11-21, TD-456).
- **Settings** (on the template revision, part of the revision hash):
  - **Area** = `category` above.
  - **Issue category** (`issue_category`, optional; user, 2026-11-21): a key from the issue
    category list `oe_issues_category` (issues.md §2), read through the issues service. The list
    holds disciplines (painting, blasting, …) **and** non-discipline categories (vehicle,
    equipment, pre-start, general, …), because e.g. a vehicle pre-start has no discipline. It is
    the category of the issue-type fallback (issues.md §8) and a filter of the Templates list.
    There is no separate `discipline` field (the earlier "discipline" filter is served by the
    issue category; planner default).
  - **Formal inspection** (`is_formal`, default on): ticked = the record counts toward QA
    progress, ITP close-out and KPIs, is eligible for the MDR under the collection rule of
    documents-and-mdr.md §Gating invariant (formal `qa_itp` records of the scope; formal
    project-level records only when their location is inside the scope; internal-audit records
    never; user, 2026-11-21, TD-456) and goes through review and approval
    (§Review and return). Unticked = **informal**, record-only: no review step — submit is final
    and moves the record straight to `INSPECTED_PASSED` / `INSPECTED_FAILED` from the proposed
    result (user, 2026-11-21, TD-456), it never enters the QA verification queue, and it is excluded from
    the MDR, the progress tiles and KPIs; it is still kept, signed, ledgered and auditable.
    `kind = itp` templates and every template attached to an ITP line are always formal (the
    tick is locked on; attaching an informal template to an ITP line → 422).
  - **Audit template** (`is_audit`, with `audit_scope` `internal | external`): an audit can sit
    under any area (e.g. Health & Safety). Its findings raise issues through `audit` category
    issue templates: `internal` → origin `internal_audit`, `internal` + `excluded` defaults;
    `external` → origin `external_audit`, `client` + `blocks` defaults (issues.md §1, §5, §8).
    An external-scope audit always raises `client` issues (`excluded` lifted to
    `register_only` at raise, issues.md §5); the settings panel warns when it links an internal
    issue template (user, 2026-11-21, TD-456).
  - **Linked issue types** (`linked_issue_templates`, 0..many issue template keys: one → used, several → the user picks,
    none → category × type default; issues.md §8).
  - **Locked settings.** Changing area, formal, audit (or its scope) — and with the area the
    anchor rule — on a template that has a published revision needs the project's QA authority
    (`qa_reviewer_persona`) or PER-09 (PER-13 for a tenant-library template) and is published as a
    **new revision**; existing records keep their pinned revision (403 for anyone else).

## Project-level inspections, location and links (user, 2026-11-21; ADR 0003 amendment 2026-11-21)

Task: T-0325 (inspections), T-0326 (the same for issues), T-0250 (equipment specifics),
T-0329 (screens).

- **Project-level record.** An inspection whose area is not `qa_itp` has `node_id` and `item_id`
  NULL (giving one → 422) and belongs to the project. It is still project-scoped by RLS like
  every record; cross-project → 404.
- **Location** (`location_node_id`, optional): any live node of the project, where the check was
  done. Informational only.
- **Links** (`oe_inspections_link`: `inspection_id`, `target_kind` `node | item | material |
  equipment`, `target_id`; `BaseRecordMixin` + project RLS): 0..many links to an asset (node or
  Item), a material (materials module) or an equipment register entry. Each target is checked
  through its owning module's `service.py` (no FKs across modules, no cross-module table
  access); unknown or other project → 404. At most one `equipment` link per record (user,
  2026-11-21, TD-456), so post-submit automations have one unambiguous `equipment` context (T-0250, T-0288).
  `material` links are accepted once the materials module exposes a link target (E-03-03; until
  then 422 `LINK_KIND_UNAVAILABLE`; user, 2026-11-21, TD-456).
- **Informational only.** Location and links never roll up (node inspection roll-up of T-0111),
  never feed progress, never block or satisfy completion and never make a record count for an
  asset. They are searchable (`GET /api/v1/inspections?location=…&linked=<kind>:<id>`) and shown
  on the linked record ("Linked inspections" on the node, Item, equipment or material detail,
  through `inspections.service.linked_records`).
## Public capture links (user, 2026-11-21; **proposed**, ADR 0013, decision TD-410)

Nothing in this section may be built until the user accepts ADR 0013 (T-0252–T-0255 are
blocked by TD-410). It conflicts with "tenant from the JWT only" (CLAUDE.md §6) and with magic
links for Field Workers only (diff §4.2), so it needs an explicit, documented exception.

- **Link** `oe_inspections_public_link` (`BaseRecordMixin` + standard project RLS; the paste's
  `oe_inspection_public_link`): one pinned standalone template revision, `category` (default
  `safety_prestart`), pre-bound context following §Project-level inspections (user,
  2026-11-21): for a `qa_itp` template at most one anchor (`anchor_node_id`, `anchor_item_id`);
  for any other area an optional `location_node_id` and an optional `equipment_id` that becomes
  the record's equipment **link**, never an anchor (TD-411 resolved), `token_hash` (SHA-256, unique), `label` (e.g. "LV-042 Daily Pre-Start QR"),
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
(`notify_users`), T-0257 (`create_action_task`, tagout gate for node/Item), T-0288 (follow-ups on the linked equipment and
the `equipment.activate` tagout provider; equipment is a link, TD-411 resolved), T-0258/T-0259 (screens), T-0316
(`create_issue`).

- **Rule** `oe_inspections_post_action` (paste: `oe_inspection_post_action`): `sort_order`,
  `name`, `enabled`, `trigger_cel` (CEL bool over `answers`, `result`, `category`, `inspection`,
  `equipment`, `node`, `item`; default `true`; compiled on save; sandboxed, fails closed),
  `action_type`, `action_config`. `equipment` in the context is the record's single equipment
  link (§Project-level inspections; T-0250). Part of the frozen template revision and copied with client
  packs; edited by PER-06 and PER-09 on draft revisions.
- **`update_target_fields`:** only fields the owning module whitelists in its `service.py`
  (equipment: `last_inspected_at`, `last_inspection_id`, status `out_of_service` /
  `quarantined`; node/Item: `last_inspected_at`, `last_inspection_id`), applied through that
  service's own transition (with its `run_checks`). **Automations only restrict, never release**
  (planner rule): no automatic transition to `active`.
- **`notify_users`:** personas (resolved to project members at send time), the equipment
  custodian, explicit users; in-app high priority (T-0019), push, and an e-mail with project
  code/name and a deep link only (diff §4.6). No SMS (TD-413). No Teams/WhatsApp messaging
  bridge (user decision 2026-11-21). Manual API notifications stay
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
- **`create_issue` (user, 2026-11-21; [issues.md](issues.md) §8):** names one of the
  template's **linked issue types** (§Template settings), the type, optional `punch_category`,
  `severity` and an answer mapping. Runs when the inspection reaches its result
  (`INSPECTED_PASSED` / `INSPECTED_FAILED`), not at `SUBMITTED` (planner default), so a failed
  painting inspection raises the painting punch or NCR. The `INSPECTED_FAILED` auto-raise of the
  lifecycle goes through it. A site audit is a standalone inspection template whose findings
  raise issues through audit issue templates. Manual raise from an inspection uses the linked
  issue types (one → used, several → the user picks, none → the category × type default for
  the template's `issue_category`; issues.md §8).
- **Execution and audit:** database actions run in the submit transaction; remote notification
  channels after commit on the SAQ `interactive` queue. Each action writes
  `oe_inspections_action_log` and a T-0006 ledger entry with `source = "automation"` (new
  `RECORD_SOURCES` value, TD-417). A template without rules has no side effects.

## QA verification queue and two-lane workbench (user, 2026-11-21; planner defaults)

One queue for documents QA must verify before a record becomes active. Tasks: T-0181 (queue),
T-0260 (equipment), T-0261 (credentials), T-0265 (heat/MTR, Phase 2), T-0184 (shelf-life
extensions, Phase 3; materials-and-traceability.md §Shelf-life extension), T-0185 (product
substitution drafts; §Product substitutions there), T-0262 (window).

- **QA authority.** Project setting `qa_reviewer_persona` (default PER-06; QA-capable personas
  only). Holding it on the project is QA authority; admin grants never confer it (diff §4.1).
- **Item** `oe_qms_verification_item` (`BaseRecordMixin` + project RLS): `target_kind`
  (`equipment_calibration | staff_credential | material_mtr | material_shelf_life_extension |
  material_substitution`),
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
  item with a deep-link notification. A QA reviewer's own upload creates a pending item like any
  other, decided by a different QA-authority holder; it is auto-verified only when the project's
  effective `allow_self_approval` (T-0140) is true, and is then flagged `self_approved` in the
  ledger (TD-431). The T-0112 OCR-discrepancy review is routed into this queue (TD-416).
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

- `oe_compliance_readiness_conflict` (`BaseRecordMixin` + project RLS): resource (`resource_kind`,
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
- **Client-supplied material late** (product intake 2026-11-22 (3), planner default; user to
  confirm; TD-593). A second conflict kind, from the materials provider: a material line or lot
  with `supplied_by = client` (the flag of welding-and-nde.md, TD-091, reused on materials
  records) whose `expected_delivery` is later than the planned start of the work pack, work-plan
  line or P6 activity that installs it (or has no expected delivery date while that start is
  inside the look-ahead). Resource kind `client_supplied_material`, the date compared is the
  target's planned **start** (not finish). It is a **warning** only, never a block; the
  Look-Ahead board (E-04-02) and the AWP constraint view (E-06b-02) show it as a material
  constraint warning. It resolves itself when the delivery date moves or the material is
  received.

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

## ITP authoring, assignment and propagation (user, 2026-11-21; planner defaults)

User-supplied tasks T-0187, T-0188 and T-0189 (ids kept). Where they disagreed with the spec,
the spec won; open points: TD-428 (template structure, approval, instance row), TD-429
(`pending_qa_review` and propagation semantics), TD-430 (rule kind), TD-398 (AI provider
configuration), TD-422 (PER-13 as an approver).

**ITP template and instance (TD-428 default).** An ITP template is a one-engine template of kind
`itp` (no separate `oe_qms_itp_template` table). A revision holds ordered **stages** of **lines**:
activity, acceptance criteria, specification/code references, the ITR form template (key and
revision), and per party (Contractor, Principal, Client PER-15) one intervention point of `H`,
`W`, `R`, `S`, `M`. A revision may carry an optional `client_document_number` (the client's ITP
number, printed as "ITP No." on RFIs, §`inspection_requests` 10.3). "Approved" means **published** (frozen). Publishing an ITP revision needs
PER-06 for a project template or PER-13 for a tenant master; never PER-09 and never
`system_admin` alone (diff §4.1, ADR 0016). Applying a template to an anchor creates an
**ITP instance** (`oe_qms_itp_instance`: anchor, template key, pinned revision, `is_customized`,
status `pending_qa_review | active | superseded`) and, when active, one `kind = itp` record per
line in `PENDING` (linked by `itp_instance_id`).

**Assignment and activation (T-0188, phase 1).** Rule kind `itp_assignment` (ADR 0004
Amendment H, a planner default TD-430 may revise) maps anchor, Item and activity attributes to a template key, for
example "coatings ITP for Blast & Paint, piping welding ITP for Weld". Manual instantiation
applies a published revision to selected anchors. **Auto-generate** evaluates the rules over a
subtree in a `batch` job and writes **proposals only**: instances in `pending_qa_review` without
line records, invisible to the field (TD-429; `pending_qa_review` is not a record lifecycle
state). The **Activation Review Matrix** lets PER-06 swap or drop proposals and
**Approve & Activate**, which creates the line records in `PENDING` and writes one ledger entry
per batch. An unpublished revision is never instantiated (`run_checks` rule
`itp.template_published`).

**Instance edits and propagation (T-0281, phase 1).** A line is **started** once it has left
`PENDING`, has an `inspection_request`, answers, or any sign-off ledger entry (T-0119). PER-06
may add or retire **unstarted** lines on one instance (`is_customized = true`, audited).
Publishing a new revision changes no instance by itself (records pin `template_revision_id`).
The **Propagation Wizard** (dry run, then confirm by PER-06 / PER-13 for masters) re-pins, adds
and retires unstarted lines on non-customized instances, and asks per customized instance:
**Keep instance customizations** or **Apply master update**. Started and signed lines are
never re-pinned, edited or retired, whatever the choice: T-0119 immutability is absolute.

**AI-assisted authoring (T-0187, phase 5, E-05-08).** An `interactive` SAQ job drafts an ITP
revision from a master template, the scope subtree and level definitions, the project
requirements and specifications (T-0120) and the published ITR form templates. The provider is
the deployment's configured AI provider in an **Australian region** (OpenAI, Anthropic Claude or
a self-hosted Hugging Face model; TD-078), read-only deployment configuration (diff §4.7,
TD-398). Output is schema-validated and saved as a **draft** revision with `source="ai"`; AI
rewrite of selected stages or lines returns a diff that the user accepts in whole or in part.
Only the human publish (above) makes it usable. Screen: two panes, source scope and requirements
beside the editable ITP table (T-0284).

**Alignment watchdog (T-0189, phase 5).** Outbox events (T-0285, ADR 0007, E-05-07):
`scope_change.agreed`, `scope_change.implemented` (`change-of-scope.md` §5),
`p6.import.committed`, `asset.node.updated`, `task.created`. Per affected anchor a `batch` job
applies the `itp_assignment` rules, then asks the AI provider to compare the change with the
active instance and the published templates. The result is an `oe_qms_itp_drift_review`
(`aligned | misaligned | missing_itp | obsolete_steps`, AI summary, proposed patch with
`source="ai"`, status `pending_review | dismissed | manual_updated | ai_patch_approved`). A patch
may add lines from published templates, retire lines, or change intervention points of
unstarted lines only. Anything but `aligned` notifies the reviewers (project setting, default
PER-06 and PER-02) in-app at high priority and by e-mail (core-mail template
`qms.itp_alignment_review`, overridable through T-0227). **Nothing changes until a human acts**
(T-0286): dismiss, edit manually, or **Apply AI proposal & approve** (PER-06), which re-checks
started lines at apply time, skips them, and ledgers the human confirmation of the AI proposal.
Drawer and register: T-0287.

## `construction_control` — conformance + hold/witness evaluation
Conformance criteria and hold / witness / surveillance / review point evaluation **via
`run_checks`**. The legacy `construction_control._inspection` table is **dropped**.

## `inspection_requests` — Request for Inspection (RFI)

User decisions on TD-045 (2026-11-09, 2026-11-10, 2026-11-13) and the product owner's
decision of 2026-11-16 (numbering and layout); everything else in this section is a **planner
default** (planner spec 2026-11-21), written from the client sample analysis in
`docs/research/coastal-inputs-qa-documents.md` §2. The product owner checks it against a client
RFI sample before T-0115 ships (TD-045). Tasks: T-0115 (records, draft, issue, numbering,
cancel; discipline defaults), T-0340 (discipline configuration editing), T-0333 (lifecycle after issue, outcomes, witness check), T-0334 (client delay
attribution), T-0335 (notifications and e-mail), T-0336 (PDF), T-0337 (calendar API and tablet
pull scope), T-0338 (register and detail screens), T-0339 (calendar screens). Delegation routing
is T-0293; an external inspection party as witness is T-0296 (TD-435).

**In one paragraph.** An RFI is one **request** (one RFI no., one discipline: mechanical, coating
or civil) that lists **1..N slots**; each slot is one inspection event at one scheduled time,
covering one or more anchors and linked to the ITP line(s) and/or ITR(s) it calls out. The state
machine runs **per slot**; the request's status is derived. Issuing a request allocates the RFI
no., moves the linked inspection records to `INSPECTION_REQUESTED`, notifies the witnessing
parties and renders the notification PDF. A reschedule returns a slot to `requested` and keeps
its history. A no-show or a failed call-out opens a delay record attributed to a cause party.
Slots show on the RFI calendar in the main web app, in the client's (PER-15) view of the web app
and on the tablet.

### 1. Terms

| Term | Meaning |
|---|---|
| **Request** (RFI) | One notification: RFI no., discipline, requester, 1..N slots. One per discipline per day is the usual practice; nothing enforces it. |
| **Slot** | One inspection event on a request: inspection type, scheduled start, anchors, linked inspection records, intervention point per party, its own state. |
| **Party** | `contractor` (our side), `principal`, `client`, and `third_party` once TD-435 is decided (T-0296). Each has one intervention point on a slot. |
| **External H/W point** | A slot party other than `contractor` whose point is `H` or `W`. Only these need notice and a witness outcome; `S`, `R`, `M` and the contractor's own points need no RFI (TD-086 may later make this a per-project code catalogue with `requires_notice`). |
| **Call-out recipients** | The people a slot's external H/W points notify (§9.1). |

### 2. Records

All tables use `BaseRecordMixin` and a project-scoped RLS policy (cross-project → 404). The
module is `inspection_requests` (`backend/app/modules/inspection_requests/`); it reads
inspections, qms, asset_management and identity only through their `service.py`.

**`oe_inspection_requests_request`** — the RFI.

| Column | Rule |
|---|---|
| `rfi_no` | NULL in draft; allocated at first issue (§8); unique per project where not NULL; never changes |
| `discipline` | a code of the project's discipline configuration (`M`, `C`, `CV` by default); editable in draft, fixed at issue |
| `status` | derived and stored: `draft` (never issued), `open` (≥ 1 slot `requested` or `confirmed`), `closed` (issued, every slot final: `witnessed`, `waived`, `no_show`, `failed` or `cancelled`, not all `cancelled`), `cancelled` (every slot `cancelled`). A reschedule of a `no_show`/`failed` slot reopens a closed request |
| `shift` | optional `DS` \| `NS` (printed) |
| `contract_no` | snapshot at issue of the project setting `contract_number` (PER-09; blank when unset) |
| `requested_by_user_id`, `requested_by_name`, `requested_by_email` | the user who created the draft; name and e-mail snapshot at issue |
| `issued_at` | first issue (server time) |
| `pdf_revision`, `pdf_object_key`, `pdf_sha256` | latest rendered notification (§10) |
| `results_pdf_object_key`, `results_pdf_sha256` | the rendition made when the request closed, kept for the dossier (E-05-05) |
| `version` | optimistic concurrency; a stale write → 409 |

**`oe_inspection_requests_slot`** — one inspection event; carries the state machine (§4).

| Column | Rule |
|---|---|
| `request_id`, `position` | position 1..`max_slots` of the discipline (default 4), unique per request |
| `inspection_type_key` | one key of the discipline's inspection-type list (§10.2); required to issue |
| `scheduled_start` | `timestamptz`; required to issue; must be in the future when issued or rescheduled (422) |
| `duration_minutes` | 15–720, default project setting `inspection_request.default_duration_minutes` (60); used by the calendar only |
| `location_ref` | free text ≤ 200 characters: job location (mechanical, civil: grid, level) or batch number (coating work lot, a sub-location of the work anchor, not a material batch); label per discipline |
| `status` | `draft \| requested \| confirmed \| witnessed \| waived \| no_show \| failed \| cancelled` |
| `required_notice_hours`, `notice_given_at`, `notice_met` | snapshot of the notice rule at the last issue or reschedule (§6) |
| `reschedule_count` | number of reschedules |
| `confirmed_by_user_id`, `confirmed_at`, `confirmed_via` (`witness_account \| recorded_on_behalf`), `confirmation_ref` | last confirmation; cleared by a reschedule |
| `outcome_by_user_id`, `outcome_occurred_at`, `outcome_reason_code`, `outcome_note` | the outcome (§4); a waiver's reference is per party (`slot_party.waiver_ref`) |
| `reminder_sent_at` | set by the reminder job (§9); cleared by a reschedule |
| `version` | optimistic concurrency |

**`oe_inspections_request_anchor`** (name fixed by ADR 0003 §2; owned by `inspection_requests`):
`request_id`, `slot_id`, exactly one of `node_id` / `item_id`. A slot's anchors are the anchors
of its linked inspection records, plus optional extra nodes or Items **inside the subtree of one
of those anchors** (for example the cathodic-protection Items or welds inspected under an RSW);
anything else → 422. Kept in step by the service; used for scope checks, the calendar and the
PDF. Only `qa_itp` records are linkable (project-level inspections of other areas have no
anchor and no RFI; ADR 0003 amendment 2026-11-21).

**`oe_inspection_requests_slot_inspection`**: `slot_id`, `inspection_id`, `prior_status`
(the record's status when the slot was issued: `PENDING` or `RE_INSPECT`). A slot links ≥ 1
inspection record (`kind = itp` line or `kind = inspection` ITR, category `qa_itp`; checked
through `inspections.service`; other project → 404, other category → 422). An inspection record
is on **at most one live slot** (`draft`, `requested`, `confirmed`) at a time (409
`INSPECTION_ALREADY_REQUESTED`); earlier slots stay linked as history. An ITR and the ITP line it
evidences may sit on the same slot.

**`oe_inspection_requests_slot_party`**: `slot_id`, `party`, `point` (`H \| W \| S \| R \| M \|
none`), `point_source` (`itp \| manual`), `witness_user_id` (optional named witness; a project
member who may witness for that party: PER-15 for `client`, the TD-435 persona for
`third_party`, an internal member for `contractor`, any project member for `principal`), `attendance` (`pending \|
attended \| absent \| waived`), `attendance_by_user_id`, `attendance_at`, `waiver_ref` (the
party's written waiver, set by the waive-a-party action; also in its event), `notice_delivery`
(`pending \| delivered \| failed`, default `pending`) and `notice_delivery_reason` (nullable:
`bounced \| suppressed \| no_channel \| provider_error \| skipped`; `skipped` = an attempt not
made for a reason other than opt-out, for example a channel excluded by a notification rule
condition, TD-587; never a recipient's opt-out, §9), set from the out-of-app
delivery results of the party's call-out recipients (§9 "Delivery failure per party"; product
intake 2026-11-21, planner default; user to confirm), and `notice_valid_at_outcome` (boolean,
null until an outcome is recorded; frozen validity of the party's notice, §6). One row per party.
Points default from the linked ITP line(s) through `qms.service` (per-party points, §ITP
authoring): with several lines, the strongest point per party (`H` > `W` > `R` > `S` > `M` >
`none`). A changed point is `manual`, needs a reason (event payload) and, when it differs from the
ITP, returns the non-blocking finding `POINT_DIFFERS_FROM_ITP`. **The slot's points are what the
notice says; the gate is always evaluated from the ITP line** (§5), so lowering a point on a slot
never lifts a hold.

**`oe_inspection_requests_comment`** — comments from both sides: `request_id`, `slot_id`
(optional), `party` (`contractor \| principal \| client \| third_party`, from the author's
persona), `author_user_id`, `body` (non-blank, ≤ 2,000 characters), `created_at`. Append-only
(no edit, no delete). Printed per slot on the PDF.

**`oe_inspection_requests_event`** — the history (§13): append-only, one row per action.

**`oe_inspection_requests_discipline`** — per-project discipline configuration (§10.2):
`code` (`^[A-Z]{1,4}$`, unique per project), `title` (localised strings), `inspection_types`
(ordered list of `{key, label (localised strings)}`), `location_label` (`job_location \| batch_no
\| location_grid_level`), `max_slots` (1–10, default 4), `active`. Code-declared defaults (§10.2)
are copied to a project on first use (T-0115); a client pack may later seed them (T-0108);
PER-06 or PER-09 edit them through T-0340. A code that has been used on an issued request
cannot be changed or deleted (only deactivated).

**`oe_inspection_requests_counter`**: `project_id`, `key` (`rfi \| rfi:<discipline>`, by
`counter_scope`, §8), `last_value`; unique
`(project_id, key)` (§8).

**`oe_inspection_requests_delay`** and **`oe_inspection_requests_reason_code`**: §7.

### 3. Relation to the inspection and the hold/witness point

- The ITP line (`kind = itp`) holds the per-party intervention points (§ITP authoring). The
  ITR (`kind = inspection`) is the evidence. The slot is the call-out between them: it links
  both, carries the notice and records whether the witness requirement was met.
- Traceability both ways: the ITR header and PDF print the RFI no.(s) of the slots that link it
  (`inspection_requests.service.rfi_refs_for_inspection(inspection_id)`, used by T-0158); the
  RFI PDF prints the ITP no.(s) of its linked lines (§10).
- **Who needs an RFI.** A `qa_itp` record whose ITP line has an external H/W point must reach a
  satisfied witness requirement (§5) before its ITR can be submitted. A record without external
  H/W points may still be put on a slot (for example to give the client notice of a surveillance
  visit); it is then never blocked by the slot.
- **Hold release is unchanged.** A satisfied witness requirement lets the ITR be submitted; it
  does not release a hold. Hold release and witness signatures stay the single-row H/W sign-off of
  the ITP line (T-0291 attested `itp_hold_release` / `itp_witness`) and client co-sign
  (§Client co-sign).
- NDT requests to an NDT provider are not RFIs (TD-098); TQ links come later (TD-485).

### 4. State machine (per slot)

```
draft ──issue──▶ requested ──confirm──▶ confirmed
                    ▲  │                    │
                    │  ├── witnessed ◀──────┤
       reschedule   │  ├── waived    ◀──────┤
  (keeps history)   │  ├── no_show   ◀──────┤
                    │  └── failed    ◀──────┘
                    └──── from requested, confirmed, no_show, failed
draft | requested | confirmed ──cancel──▶ cancelled
```

This reconciles T-0115's `draft → requested → confirmed → witnessed | waived | no_show | failed`
with TD-045: **rescheduled is not a state** but the transition back to `requested`; the history
table keeps every earlier time.

| Transition | Who (§12) | Conditions | Effects |
|---|---|---|---|
| add / edit / remove slot (`draft`) | creators | request is `draft`; at most `max_slots` | — |
| `draft → requested` (**issue**, request-level: all draft slots at once) | creators | every slot has type, future `scheduled_start`, ≥ 1 linked record in `PENDING` or `RE_INSPECT` (else 409 `INSPECTION_NOT_REQUESTABLE`), party rows; `run_checks(context, "inspection_request.issue")` passes | RFI no. allocated on first issue (§8); notice computed (§6); linked records → `INSPECTION_REQUESTED` (§5); call-out notifications (§9); PDF rendered (§10) |
| `requested → confirmed` | named witness or PER-15 of the client party; PER-06/PER-07 recording on behalf (`confirmation_ref` required) | before an outcome | requester notified |
| `requested \| confirmed \| no_show \| failed → requested` (**reschedule**) | creators; PER-15 of the client party (cause `client` set automatically) | new `scheduled_start` in the future; `reason` (non-blank) and `cause_party` required | `reschedule_count` + 1; old and new times in the event; confirmation, attendance and `reminder_sent_at` cleared; every party's `notice_delivery` reset to `pending` and `notice_valid_at_outcome` cleared with the outcome (§6, §9); notice recomputed from now (§6); delay entry (§7); notifications; PDF re-rendered |
| **waive a party** (no slot transition) | PER-15 of that party (any point); PER-06 for a `W` point only, with `waiver_ref` (the party's written waiver) | slot `requested`/`confirmed`; the party is an external H/W party | that party's `attendance = waived` (`attendance_by_user_id`; `waiver_ref` on the party row and in the event); when every external H/W party is now `waived` the slot moves to `waived` in the same action |
| `requested \| confirmed → witnessed` | PER-06, PER-07 (site scope) | **no external H/W party `pending`**; every one `attended` or `waived`, at least one `attended` | witness requirement satisfied (§5) |
| `requested \| confirmed → waived` | automatic, by the last **waive a party** action | every external H/W party `waived` | witness requirement satisfied |
| `requested \| confirmed → no_show` | PER-06, PER-07 | `outcome_occurred_at` ≥ `scheduled_start` + `inspection_request.no_show_grace_minutes` (default 30); **no external H/W party `pending`**; ≥ 1 `absent` | satisfied only if every absent party's point is `W` and that party had valid notice, frozen per party in `notice_valid_at_outcome` when this outcome is recorded (§6; §5); delay entry (§7) |
| `requested \| confirmed → failed` | PER-06, PER-07 | `outcome_reason_code` required (§7): the call-out could not be held for a reason other than the witness's absence (work not ready, access, weather, permit, instrument) | not satisfied; delay entry (§7) |
| `draft \| requested \| confirmed → cancelled` | requester, PER-06 | reason required unless `draft` | linked records revert (§5); notifications; PDF re-rendered; a draft request whose slots are all cancelled may be soft-deleted instead (no number was used) |

- **Attendance.** Per external H/W party, the named witness may record their own attendance as
  `attended`; PER-06/PER-07 record `attended` or `absent` for any party; `waived` is set only by
  the per-party **waive a party** action, so a mixed call-out is recordable (for example the
  client waives its `W` point and the principal attends its `H` point → `witnessed`).
  Attendance is recorded with the outcome in one action or before it; `witnessed` and `no_show`
  are refused (422 `ATTENDANCE_PENDING`) while any external H/W party is still `pending`.
- `witnessed`, `waived` and `cancelled` are final. `no_show` and `failed` are final unless the
  slot is rescheduled. Every transition is a conditional update on `version` and `status`: the
  first wins, a concurrent second gets 409 (this covers delegator and delegate acting together,
  T-0293).
- The **result of the inspection** (accepted or rejected) is not a slot state: it is the linked
  ITR's result and is printed from it (§10). No result is typed twice.
- Every transition except draft edits runs `run_checks(context, "inspection_request.<action>")`
  (`issue`, `confirm`, `reschedule`, `outcome`, `cancel`) with code-declared system-default rules
  (ADR 0004); the findings below are those defaults.

### 5. Effects on the linked inspection records

Called through `inspections.service` (the inspections module owns its lifecycle and runs its own
`run_checks`); never by writing its table.

| Slot state | Linked inspection record |
|---|---|
| `draft` | unchanged; the record is reserved for this slot (one live slot) |
| `requested` (issue) | `PENDING → INSPECTION_REQUESTED`, or `RE_INSPECT → INSPECTION_REQUESTED` (re-inspection call-out; this transition is added to the lifecycle by T-0115); `prior_status` saved; `planned_start` (§Planning fields) set to the slot's date in the project time zone |
| `confirmed` | unchanged (`INSPECTION_REQUESTED`; field view "Scheduled") |
| `requested` (reschedule) | stays `INSPECTION_REQUESTED`; `planned_start` moved to the new date |
| `witnessed`, `waived` | stays `INSPECTION_REQUESTED`; **witness requirement satisfied**: the ITR may be submitted |
| `no_show` | stays `INSPECTION_REQUESTED`; satisfied only when every absent party had a `W` point and valid notice (§6: `notice_met` and its `notice_delivery` not `failed`) (the CDRL rule: on W the client may stay away after valid notice and work proceeds; on H it may not). Otherwise not satisfied: reschedule or cancel |
| `failed` | stays `INSPECTION_REQUESTED`; not satisfied: reschedule or cancel |
| `cancelled` | back to `prior_status` if the record is still `INSPECTION_REQUESTED` and on no other live slot; a record that has moved on (submitted, returned, approved) is not touched |

**Witness check on submit (T-0333).** `inspection_requests.service.witness_status(inspection_id)`
returns `not_required | none | pending | satisfied | not_satisfied` overall **and per external
party**. It looks at the most recently issued slot that links **the record itself or, for an ITR
(`kind = inspection`), the ITP line it evidences** (`kind = itp` record, resolved through
`inspections.service`). The usual flow raises the request on the ITP line and fills the ITR in
later, so a slot that links only the line counts for the ITR, **provided the slot's anchors cover
the ITR's anchor** (the ITR's node or Item is a slot anchor or lies in the subtree of one);
otherwise that slot does not count for the ITR. Per party, the requirement is met when its
attendance is `attended` or `waived`, or `absent` on a `W` point with valid notice (§6; the party's frozen `notice_valid_at_outcome`); it is unmet
when no slot was issued, its attendance is still `pending`, the party was absent on an `H`
point or without valid notice (`notice_valid_at_outcome = false`: short notice or `notice_delivery = failed` at the outcome), or the slot `failed`. Precedence: `failed` makes every party unmet;
otherwise the party's attendance decides, and `pending` = unmet. `construction_control` adds two locked
system-default `check` rules (ADR 0004 §5) to `inspection.submit` (§Review and return),
evaluated from the ITP line's external points: `WITNESS_REQUIREMENT_HOLD` fails only for unmet
parties whose point is `H`, and `WITNESS_REQUIREMENT_WITNESS` only for unmet parties whose point
is `W`, so a line with a client `W` and a principal `H` yields separate findings and the `W`
finding stays overridable. Findings: `RFI_REQUIRED` (no slot ever issued), `WITNESS_PENDING`
(slot `requested`/`confirmed`), `WITNESS_NOT_SATISFIED` (absent on an H point or without valid
notice (§6), or `failed`), each naming the parties concerned. An offline submit is checked at
sync like every other submit check.

**Override (decision TD-486, planner default applied).** A per-record override (ADR 0004
Amendment A, T-0035) is possible for `WITNESS_REQUIREMENT_WITNESS` as for any check. For
`WITNESS_REQUIREMENT_HOLD` it is refused: the rule carries the param `overridable: false`, and
the override-request path of `validation.service` refuses a request naming a finding of such a
rule with 422 `OVERRIDE_NOT_ALLOWED` (an additive rule param read by the T-0035 request path; the
frozen `oe_validation_override` shape is unchanged; other modules call it through the validation
service, never its tables). An internal approver cannot step over a client's hold; this is an
exception to Amendment A pending TD-486.

### 6. Notice period

- Project setting `inspection_request.min_notice_hours` (0–336, default 24; master spec "e.g. 24
  hours"). At issue and at every reschedule the slot stores `required_notice_hours`,
  `notice_given_at` (server time of the action) and `notice_met = scheduled_start −
  notice_given_at ≥ required_notice_hours`.
- Short notice is allowed: the default rule `NOTICE_SHORT` in `inspection_request.issue` and
  `inspection_request.reschedule` is a **warning** (the response carries it, the requester
  sees it before confirming, it is in the event). A project may make it blocking through its rule
  copy (ADR 0004). A short-notice slot can never satisfy a witness requirement through
  `no_show`, and its no-show delay is attributed to `contractor` (§7).
- **Valid notice for a party** = `notice_met` on the slot **and** that party's
  `notice_delivery` is not `failed` (§2, §9). A party whose call-out was never delivered out of
  the app is treated exactly like short notice for that party: its absence never satisfies the
  witness requirement through `no_show`, it creates a delay entry, and the default cause is
  `contractor` (§7), which PER-02 and PER-06 can change with a reason (product intake
  2026-11-21, planner default; user to confirm). `notice_delivery = pending` at the outcome
  counts as delivered (no evidence of failure).
- **Validity is fixed at the outcome** (planner default; user to confirm). Valid notice is
  evaluated **once per party, when the outcome** (`witnessed`, `no_show`, `failed`, or the
  slot-level `waived`) **is recorded**, and stored as `slot_party.notice_valid_at_outcome` (§2).
  The witness check (§5, `witness_status`) and delay creation (§7) both use only this frozen
  value. A delivery result that arrives later (a late bounce, a failed push retry) still updates
  `notice_delivery`, is shown on the screen and is emitted as an event, but never changes the
  effect of the recorded outcome. A reschedule clears the frozen value together with the
  outcome.

### 7. Client delay attribution

**`oe_inspection_requests_reason_code`** (project-editable data, PER-06/PER-09; code-declared
defaults copied on first use): `code`, `label` (localised), `applies_to` (`no_show \| failed \|
reschedule`, one or more), `default_cause_party`, `active`. Defaults:

| Code | Applies to | Default cause party |
|---|---|---|
| `witness_absent` | no_show | the absent party |
| `client_requested_change` | reschedule | `client` |
| `access_not_available` | failed, reschedule | `client` |
| `work_not_ready` | failed, reschedule | `contractor` |
| `permit_not_issued` | failed, reschedule | `contractor` |
| `instrument_unavailable` | failed, reschedule | `contractor` |
| `weather` | failed, reschedule | `other` |
| `third_party_absent` | no_show | `third_party` |
| `other` | all | `other` (note required) |

**`oe_inspection_requests_delay`**: `request_id`, `slot_id`, `trigger` (`no_show \| failed \|
reschedule`), `cause_party` (`client \| principal \| third_party \| contractor \| other`),
`reason_code`, `notice_met`, `started_at`, `ended_at` (nullable), `hours` (wall-clock, 2
decimals, set when ended), `status` (`open \| closed`), `cause_changed_by`, `cause_changed_at`,
`cause_change_reason`. Anchors are the slot's (§2). For a `no_show` entry, `notice_met` records
whether every absent party had valid notice (§6), so it is false after a failed delivery.

- **`no_show`** with at least one absent party whose point is `H` or who lacked valid notice
  (§6: short notice, or that party's `notice_delivery = failed`; the value frozen at the outcome
  in `notice_valid_at_outcome`): one entry, `cause_party` = the
  absent party (several absent parties: `client` before `principal` before `third_party`) when
  every absent party had valid notice, else `contractor`; `started_at = scheduled_start`.
  A `no_show` where only `W` parties were absent, each with valid notice, creates no entry (work
  proceeds). PER-02/PER-06 may change the cause as below.
- **`failed`**: one entry, `cause_party` = the reason code's default, `started_at =
  scheduled_start`.
- An open entry closes when the slot (after its reschedules) reaches `witnessed` or `waived`
  (`ended_at` = that outcome's `outcome_occurred_at`) or is cancelled (`ended_at` = cancel time).
- **`reschedule`** from `requested` or `confirmed` to a later time: one closed entry from the old
  to the new `scheduled_start`, `cause_party` from the action (PER-15 → always `client`). A
  reschedule to an earlier time, or from `no_show`/`failed` (the open entry runs on), creates
  none.
- PER-02 or PER-06 may change `cause_party` with a mandatory reason; the change is an event and
  an audit entry; earlier values stay in the history.
- **Internal only.** Delay records and their API are classed `internal_commercial` (T-0295):
  404/omitted for every `is_internal = false` account, PER-15 included. PER-15 sees the slot's
  outcome and history, not the attribution: the history is redacted for them as §13 says.
- **Read API** `GET /api/v1/projects/{project_id}/inspection-requests/delays?cause_party=&status=&from=&to=&anchor=`
  and `inspection_requests.service.delays(...)` for the Look-Ahead blocked badge (E-04-02) and
  delay events / time-impact analysis (E-06-03). No money is computed here; hours are wall-clock
  (working-calendar hours belong to E-06-03).

### 8. Numbering

- **Pattern** (product owner, 2026-11-16): `<project>-RFI-<discipline>-<seq>`, e.g.
  `P1234-RFI-M-0001`, `P1234-RFI-C-0002`, `P1234-RFI-CV-0003`. The pattern is the project
  setting `inspection_request.number_pattern`, **owned by this module** (default
  `{project}-RFI-{discipline}-{seq:4}`; PER-09 edits it; T-0108 client packs may later seed it
  through the project-settings API, like any other setting), parsed with a whitelist of placeholders: `{project}`
  (project code), `{discipline}` (discipline code), `{seq:N}` (zero-padded to at least N digits,
  growing past 9999). Unknown placeholders are refused when the pattern is saved.
- **Pattern validity (no duplicate numbers):** a pattern must contain **exactly one**
  `{seq:N}`, and must contain `{discipline}` whenever `counter_scope = discipline` (otherwise two
  disciplines' counters would print the same string). Saving a pattern, or switching
  `counter_scope`, so that this no longer holds returns 422 `NUMBER_PATTERN_INVALID`; the unique
  index on `(project_id, rfi_no)` is the last line of defence.
- **Discipline code mapping:** mechanical `M`, coating `C`, civil `CV` (defaults, §10.2); a
  project may add disciplines with their own codes.
- **Counter scope:** project setting `inspection_request.counter_scope` = `project` (default:
  one counter per project shared by all disciplines, the 2026-11-16 wording) or `discipline` (one
  counter per discipline, as in the client samples). It can be changed only while the project has
  no issued request (409 otherwise). The counter row key is `rfi` or `rfi:<discipline>`.
- **One counter mechanism:** `oe_inspection_requests_counter` is this module's own counter;
  T-0108's numbering (when it lands) does not allocate RFI numbers.
- **Allocation** happens in the issue transaction: `UPDATE oe_inspection_requests_counter SET
  last_value = last_value + 1 … RETURNING last_value` (row lock; the row is created with
  `INSERT … (last_value = seq_start − 1) ON CONFLICT DO NOTHING` first). Concurrent issues get distinct consecutive numbers; a
  rolled-back issue uses no number, so committed numbers have no gaps. A unique index on
  `(project_id, rfi_no)` backs it.
- **Start value:** project setting `inspection_request.seq_start` (default 1) lets a project
  continue a client's existing paper sequence. A new counter row starts at `last_value =
  seq_start − 1`, so the first number is `seq_start`. PER-09 may change it only to a value above
  the last allocated number of every existing counter row of the project (422 otherwise); the
  change sets `last_value = seq_start − 1` on those rows under the same row lock, in the same
  transaction as the setting.
- **Drafts have no number** (shown as "Draft"). Deleting a draft uses no number.
- **Cancel** keeps the number; it is never reused; the request stays in the register and its PDF
  is re-rendered with a "Cancelled" mark.
- The number is a stored string: a later change of the project code, the pattern or the
  discipline configuration never renumbers.
- **Online only:** issue (and so allocation) needs the server.

### 9. Notifications and e-mail

**9.1 Recipients.** Per slot, for each external H/W party: the named `witness_user_id`; when the
client party has none, every active PER-15 of the project whose granted subtree contains **all**
the slot's anchors; `principal` and `third_party` parties notify only a named witness. Plus the
active delegate of each named witness for kind `hold_point` (T-0293). These are the slot's
**call-out recipients**. The requester and the project's PER-06 are added as informed. One
notification per request per recipient per event (not one per slot).

**9.2 Events** (in-app through T-0019 with `kind` + params, push on Android through T-0144):

| Event | To | Priority | E-mail template |
|---|---|---|---|
| issued | call-out recipients, PER-06 | `high` if any `H` point, else `normal` | `inspection_requests.issued` |
| confirmed | requester | `normal` | — |
| rescheduled | call-out recipients, requester, PER-06 | `high` | `inspection_requests.rescheduled` |
| cancelled | call-out recipients, requester | `normal` | `inspection_requests.cancelled` |
| reminder | call-out recipients, requester | `normal` | `inspection_requests.reminder` |
| no_show, failed | requester, PER-06, PER-02 | `high` | — |
| witnessed, waived | requester | `normal` | — |

- In-app params may carry the RFI no., discipline, scheduled date and time and anchor codes
  (inside the app, behind RLS). Push payloads carry ids and a title only (T-0144).
- **Reminder:** a SAQ `batch` cron every 15 minutes per active project sends one reminder per
  slot in `requested` or `confirmed` once `now ≥ scheduled_start −
  inspection_request.reminder_lead_minutes` (default 120); idempotent through
  `reminder_sent_at`; a slot issued inside the lead time gets no reminder.
  A reminder that cannot go out before `scheduled_start` (worker backlog, outage) is recorded
  as failed (`too_late`) and never sent late (product intake 2026-11-21, planner default; user
  to confirm; general rule in TD-587, carried over from TD-549).
- **Delivery failure per party** (product intake 2026-11-21, planner default; user to confirm;
  task T-0350). Every call-out recipient is a project account, so the in-app row always exists;
  delivery is therefore judged over the **out-of-app channels only** (push and e-mail). A
  party's `notice_delivery` (§2) is `failed` when **every** push and e-mail attempt for **all**
  of that party's call-out recipients for the latest issue or reschedule failed or was skipped
  (e-mail bounced, suppressed or failed in the core-mail log, T-0226; push failed or no device;
  no out-of-app channel at all = `no_channel`), with the reason in `notice_delivery_reason`; it
  is `delivered` once any such attempt succeeds, and `pending` until then. **Call-outs ignore
  opt-out** (user, 2026-11-22; TD-549 resolved): call-out and inspection-request notices are
  contractual, transactional messages, so a recipient's channel opt-out or unsubscribe never
  suppresses them and is never a `skipped` reason for them; opt-out applies to optional
  notifications only (as for broadcasts, diff §4.7). A missing address or a bounce is still
  recorded per party as above (`no_channel`, `bounced`, `suppressed`). A reschedule resets it
  to `pending`. On `failed` the requester and PER-06 get a `high` in-app notification and the
  slot detail shows it. A failed delivery is never attendance: the party stays `pending` until
  someone records `attended`, `absent` or a waiver. Effect on outcomes: no valid notice for
  that party (§6, §5, §7), fixed once when the outcome is recorded (§6). Per-channel results come from the persisted delivery log (TD-238,
  TD-587).
- **E-mail** through core mail (T-0058, diff §4.6), recipient's locale. Planner default, decision
  **TD-484**: only to project accounts above; content = project code and name, RFI no.,
  discipline title, the event, and a link to the request page behind sign-in; **no** scheduled
  date/time or location in the body, **no** PDF attachment, **no** `.ics` invite, **no**
  addresses without an account. The templates are overridable per locale through T-0227.

### 10. Notification PDF (one layout, title and field set per discipline)

**10.1 Rendering.** A SAQ `batch` job renders with PyMuPDF (imported lazily, T-0022; the same
stack as the inspection report, T-0158, so no reportlab), stores the file through the storage
service with its SHA-256 and returns a presigned link. A new revision (`pdf_revision` + 1) is
rendered on issue, reschedule, cancel and when the request closes; the closing rendition is
titled "<discipline title> – Results" and its key and SHA-256 are kept for the dossier. If a
reschedule reopens the request and it closes again, the new Results rendition replaces the
pointer on the request; every earlier rendition stays stored (each revision's key and SHA-256 are
in its `rendered` event) and is never deleted. `GET
…/{id}/pdf` returns the latest; a draft renders on demand with a "Draft" watermark and is not
stored. Fixed labels come from the backend message catalogue of T-0158 in the project's default
language (fallback `en`; only English ships, TD-172). Empty values print blank: never `0`,
`12:00 AM`, `#N/A` or a placeholder; unused slot rows print blank.

**10.2 Discipline configuration** (defaults; project-editable, §2):

| | Mechanical | Coating | Civil |
|---|---|---|---|
| Code | `M` | `C` | `CV` |
| Title | Mechanical Inspection Notification | Coating Inspection Notification | Civil Inspection Notification |
| Inspection types | Visual weld; Bolt tensioning; Grid mesh and clip check; Final inspection | Blast; 1st coat; 2nd coat; 3rd coat; Final | Visual after removing loose concrete/grout; Inspection prior to pour; Inspection after removal of formwork; Visual finishing; Review |
| Location column | Job location | Batch no. | Job location (grid / level) |
| Slots per request | 4 | 4 | 4 |

**10.3 Content** (every discipline; the TD-045 field list):

- **Header:** deployment logo (configuration; none when unset), title, Contract No.
  (`contract_no`), RFI No., Project (code and name), ITP No. (distinct references of the linked
  ITP lines from `qms.service`: the template's client document number when set, else template key
  and revision), Scheduled Inspection Date (the slots' date in the project time zone; when slots
  fall on several dates each row also prints its date), Shift (when set), revision and issue date.
- **One row per slot**, `max_slots` rows: inspection type; intervention-point grid with one line
  per party present (`contractor` labelled with the project terminology's contractor name,
  `principal`, `client`, `third_party`) and columns H / W / S / R (M printed in the legend only);
  asset details (code and name of the anchors' `asset`-role level, ADR 0003 §6, in the project's
  terminology, T-0136); RSW No. (the work-anchor codes of the anchors, label from the project
  terminology; several print as a list); job location or batch no. (`location_ref`); scheduled
  time (project time zone, 24 h); inspection result; comments (each comment prefixed with its
  party).
- **Inspection result** per slot: `requested` → "Requested" ("Rescheduled" when
  `reschedule_count` > 0); `confirmed` → "Confirmed"; `witnessed` / `waived` → from the linked
  ITRs: "Accepted" when all are `INSPECTED_PASSED` or `LOCKED_FOR_TURNOVER`, "Rejected" when any
  is `INSPECTED_FAILED` or `RE_INSPECT`, else "Witnessed" / "Waived"; `no_show` → "No show";
  `failed` → "Not inspected – <reason label>"; `cancelled` → "Cancelled". Each reschedule prints
  a note under its row: "Rescheduled from <date time>: <reason>".
- **Footer:** Requested by (name, e-mail), legend of the point codes (terminology pack), record
  id, generation time, page x of y.

### 11. Calendar

- **API** `GET /api/v1/projects/{project_id}/inspection-requests/calendar?from=&to=&discipline=&status=&mine=`
  (range ≤ 62 days, else 422): one event per slot of issued requests (draft slots only for
  internal members): slot and request ids, RFI no., discipline, inspection type, status, start, end
  (`start + duration_minutes`), anchor codes, `location_ref`, strongest external point, result
  label; times in UTC with the project's IANA time zone. `mine=true` limits to slots where the
  caller is requester, named witness or (PER-15) call-out recipient. Several slots on one day and
  overlapping slots are allowed and listed.
- **Why a slot is open** (product intake 2026-11-21, planner default; user to confirm). The
  detail screen (T-0338) and the calendar event popover (T-0339) show, beside the slot state,
  one line per external party: party, its point (H / W), attendance (`pending | attended |
  absent | waived`) and notice delivery (`pending | delivered | failed`, §9; T-0350), so the reason a slot is still
  open is visible without opening the history. PER-15 sees the same lines (no
  `internal_commercial` fields, §13).
- **Who sees what:** internal members with `V` (PER-07 only inside their site nodes); PER-15 only
  slots of issued requests whose every anchor is in their granted subtree; a named witness the
  slots naming them. The same rule applies to the register and detail (§12).
- **Three surfaces, one screen** (T-0339): the main web app (month, week, day and agenda views in
  the project time zone); the **client portal**, which until E-07-01 is the PER-15 view of the
  same web app/PWA (E-07-01 later embeds the same API); and the **tablet app**, which shows the
  same views from the local store, offline and read-only, with the time of the last sync.
- **Tablet pull scope** (T-0337, T-0147): requests and slots whose anchors intersect the device's
  downloaded anchors or where the user is requester or named witness, scheduled from 7 days ago
  to 28 days ahead (planner default), read-only except the outcome mutation (§14).
- **Later:** the unified calendar (E-04-15) registers an `inspections_rfis` provider over the
  same service function; the iCal feed waits for TD-419.

### 12. Who may do what

| Action | Personas (project membership; scope as noted) |
|---|---|
| View register, detail, calendar | internal members with `V` (PER-07 in site nodes); PER-15 per §11; named witnesses |
| Create and edit drafts, issue, add/remove draft slots | PER-03, PER-04 (assigned area), PER-06, PER-07 (site nodes): every anchor in the actor's scope |
| Reschedule | the creators above; PER-15 of the client party (cause `client`) |
| Confirm | named witness; PER-15 of the client party; PER-06/PER-07 on behalf with `confirmation_ref` |
| Record attendance | named witness (own `attended`); PER-06/PER-07 (any party) |
| Record `witnessed`, `no_show`, `failed` | PER-06, PER-07 (site nodes) |
| Waive a party (per party; the slot becomes `waived` when every party is waived) | PER-15 of that party (any point); PER-06 for that party's `W` point with `waiver_ref` |
| Cancel | requester, PER-06 |
| Comment | the creators, PER-06, PER-07, PER-15 and named witnesses on slots they can see |
| Discipline configuration, reason codes | PER-06, PER-09 |
| Settings (`number_pattern`, `counter_scope`, `seq_start`, notice, grace, reminder, duration) | PER-09 |
| Change a delay's cause party | PER-02, PER-06 |

- These are persona actions like inspection review: a `system_admin` without one of these
  personas on the project reads only (ADR 0016); impersonation follows the central guardrail
  (T-0278).
- A delegate acts on behalf only with the qualified persona themself (T-0293); the event records
  `on_behalf_of_user_id` and `delegation_id`.
- An external inspection party named as `third_party` witness (T-0296, after TD-435) sees the
  slots naming them, confirms, records their own attendance and signs the ITP witness line through
  T-0289 when the point requires it.

### 13. Audit and history

- `oe_inspection_requests_event` is append-only (no UPDATE/DELETE grant; a trigger refuses
  them): `request_id`, `slot_id`, `action` (`created \| edited \| issued \| confirmed \|
  attendance \| party_waived \| rescheduled \| witnessed \| waived \| no_show \| failed \|
  cancelled \| commented \| cause_changed \| point_changed \| rendered`), `from_status`, `to_status`, actor, `on_behalf_of_user_id`,
  `delegation_id`, `payload` (old/new `scheduled_start`, cause party, reason code, note,
  references, findings), `occurred_at` and, for offline actions, the time evidence of diff
  §6.4.1. The detail screen shows it as the request's history; nothing is ever overwritten.
- **Redaction for external readers.** `cause_party`, `reason_code` default cause, delay ids and
  delay hours in event payloads are fields of class `internal_commercial` (T-0295). For every
  `is_internal = false` reader (PER-15, external inspection parties) the history and detail
  responses **omit `cause_changed` events entirely and drop those fields from every other
  payload** (the reason code and its label stay, as they explain the outcome). The stored event is
  unchanged; the redaction is in the read schema.
- Every action except draft edits also writes a T-0006 audit-ledger entry (chain
  `inspection_request:<id>`).
- Issued requests are never deleted (cancel instead); drafts are soft-deleted.
- Nothing in this module is AI-initiated; a future AI-drafted request would carry
  `source = "ai"` and need human confirmation (CLAUDE.md §6).

### 14. Offline

- **Online only:** create, edit, issue, confirm, reschedule, waive, cancel and comments (number
  allocation, notice and notifications need the server).
- **Offline on the tablet:** reading (pull scope, §11) and **recording an outcome** —
  attendance plus `witnessed`, `no_show` or `failed` with reason and note — as a queued mutation
  with client op id and the time evidence of diff §6.4.1. At sync the server applies the §4 rules
  with `outcome_occurred_at` = the reconstructed `occurred_at` (the `no_show` grace check uses
  it). If the slot is no longer `requested`/`confirmed` (rescheduled, cancelled or another outcome
  already recorded) the mutation fails with `SLOT_STATE_CHANGED`, nothing is applied and the
  inspector is notified; there is no merge.

### 15. i18n and out of scope

- Every screen string through `t()` (namespace `inspection_requests.*`); discipline titles,
  inspection-type labels and reason-code labels are localised configuration strings with `en`
  fallback; PDF labels from the backend catalogue (§10.1); e-mails from the core-mail locales.
  Dates and times are stored UTC and shown in the project time zone.
- **Not in this module:** NDT requests to NDT providers (TD-098); TQ links (later, TD-485, E-05-04);
  the iCal feed (TD-419, E-04-15); money, EOT and working-calendar delay hours (E-06-03);
  client responses by magic link without an account (portal, E-07-04).

## Findings register (TD-106)
Client-asset findings (CUI, water ingress, cladding damage) go to a **separate findings
register** — not NCR, not punch — with an **assessment step**. Per project: the register's name,
its finding types, and whether follow-up work may be raised from an assessed finding. Detailed
spec and task: TD-117. It stays separate from the issues engine (another department, not
trialled yet; user, 2026-11-21) but could later be built as issue templates ([issues.md](issues.md) §11).

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
