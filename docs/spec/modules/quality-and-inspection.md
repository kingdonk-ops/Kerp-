# Module detail — Quality: one-engine inspection, eligibility, qms, construction_control, coatings

Merged from legacy `forms`/`inspections`/`qms`/`construction_control` with TD-002 (one-engine)
applied. Covers MOD-08 (QC/ITP) and the coatings/environmental criteria.

Anchors, header binding and template/register versioning follow
[ADR 0003](../../decisions/0003-configurable-breakdown-and-terminology.md); rule sets, overrides
and validity dates follow [ADR 0004](../../decisions/0004-project-rule-sets-with-defaults.md)
(both accepted with amendments, 2026-11-10). "RSW" below means the project's work-anchor level.

## One field-type registry (`core/fields`)
Shared palette of field types used by every form, ITP and asset-schema field:
`numeric`, `text`, unit-bound **`measurement`** (value + unit), `photo`, `signature`,
**`instrument reference`** (binds a calibrated tool, gated by calibration), `calculated`
(CEL expression). No per-module field designers.

## Unified inspection record — `oe_inspections_inspection`
Single store across all kinds: **`kind IN (inspection, itp)`**. TD-045 (decided 2026-11-10)
renames the old `rfi` kind and `RFI_ISSUED` state because RFI means Request for Information; the
Request for Inspection becomes its own `inspection_request` record (§`inspection_requests`).
The names used here are **confirmed by the user (TD-045, 2026-11-10)**: no `rfi` kind, and the
call-out state is `INSPECTION_REQUESTED`. Anchors to **any node whose level has the `inspection_anchor` role, or an Item**
(ADR 0003 §6; e.g. Line in `cui-line`, RSW and Item in `remediation-*`), including a node with no
work under it yet (ADR 0003 Amendment 5). Holds responses authored against a template revision
(pins `template_revision_id`), `status` lifecycle, `reading_*`, `signed_off_*` (user, signature
hash, UTC timestamp, geolocation). Lifecycle (master spec §4.2):
`PENDING → INSPECTION_REQUESTED → INSPECTED_PASSED → LOCKED_FOR_TURNOVER`, with
`INSPECTED_FAILED → (Cat A punch | NCR) → RE_INSPECT` (state name confirmed on TD-045). Every transition calls `run_checks`.

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
`inspection_request` record in scheduling, cross-linked to the Request for Information module;
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
