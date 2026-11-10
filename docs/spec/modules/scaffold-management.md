# Module detail — Scaffold management

Module `scaffolding` (epic **E-04-13**). Registers every scaffold on a project, tracks it from
request through erection, tagging, periodic re-inspection, modification and dismantling, and
links it to the work that needs it. Today scaffolds appear only as an AWP constraint
("scaffolding erected and green-tagged", consolidation diff §8; master spec MOD-05) and as
`SCA Erect` / `SCA Dism` activities and "Scaff Hrs" in the P6 schedule and front-end planning
workbook. This module makes them first-class records. All examples are synthetic.

Precedence: module detail (3). Written against ADR 0003 (configurable register types,
terminology) and ADR 0004 (rule sets with project defaults).

---

## 1. Anchoring

- A scaffold is a temporary works asset. Each scaffold record owns one `oe_asset_item` of item
  type `scaffold` (created through `asset_management` service), so **inspections anchor to an
  Item** as everywhere else (CLAUDE.md §6). The item's parent node is the scaffold's location.
- A scaffold links to the work it serves through `oe_scaffold_link` (many-to-many):
  `target_kind` = `node` (any level, e.g. asset or RSW), `item`, `plan_line`, `task`,
  `permit`, `inspection`; `purpose` text. One scaffold can serve several RSWs; one RSW can need
  several scaffolds.
- A scaffold built for another contractor or managed in an external scaffold system keeps
  `external_source` + `external_id` (+ `synced_at`) — never an FK.

## 2. Scaffold record

`oe_scaffold_scaffold` (`BaseRecordMixin`, project RLS). Core columns are fixed; everything
else is a versioned **register type** (JSONSchema, ADR 0003 §6) so a project can add columns.

| Field | Core / default config | Notes |
|---|---|---|
| `scaffold_no` | core | numbering pattern per project, e.g. `{site}-SC-{seq:4}`; a client tag number can be stored separately |
| `status` | core | §3 |
| `location_node_id` | core | site / area / asset node |
| `location_text` | core | free description ("north side of tail pulley, level 2") |
| `elevation`, `grid_ref`, `gps` | config | optional; GPS captured from the device photo if allowed |
| `scaffold_type` | config | value list: independent, tower / mobile, birdcage, hanging, cantilever, system, tube & fitting… |
| `duty_class`, `max_load` | config | e.g. light / medium / heavy duty, kPa |
| `dimensions` | config | length × width × height, lifts, bays, platform count |
| `access_type`, `encapsulation` | config | ladder, stair; sheeted / encapsulated (blast containment) |
| `design_required`, `design_ref` | config | engineered design reference + document link |
| `requested_by`, `requested_at`, `required_by` | core | from the request (§5) |
| `erected_by` | core | crew / persons (planner resources); licence checked (§7) |
| `erect_start`, `erect_complete` | core | |
| `handover_by`, `handover_at` | core | competent person who hands it over to users |
| `approved_by`, `approved_at` | core | approval to use (project-defined persona) |
| `custodian_user_id` | core | supervisor responsible; receives notifications |
| `inspection_interval_days` | core | default from project setting (§4.2), editable per scaffold |
| `current_tag_*` | denormalised | status, tag no., tagged at/by, next due (from the latest tag, §4) |
| `dismantle_*` | core | requested, started, completed, by whom |
| `notes` | core | rich text |
| attachments | core | design drawings, files from other scaffold programs, PDF, Word, photos (uploads T-0016) |

## 3. Lifecycle

```
requested → approved_to_build → erecting → erected (awaiting inspection)
   → tagged_safe ⇄ tagged_restricted ⇄ untagged_unsafe
   → (modification_requested → modifying → erected)
   → dismantle_requested → dismantling → dismantled → archived
any active state → inspection_overdue (automatic, §4.3)
any pre-erection state → cancelled
```

Transitions call `run_checks(context, rule_set)` with project-editable rule sets (ADR 0004):

| Transition | Rule set | Default rules |
|---|---|---|
| request approve | `scaffold.request.approve` | location + required-by set; linked work exists |
| → `erected` | `scaffold.erect.complete` | erector crew includes a credentialed scaffolder (§7); ≥1 photo |
| → `tagged_*` | `scaffold.tag` | inspector holds a valid credential; checklist complete; ≥1 photo; tag no. unique |
| → `dismantle_requested` | `scaffold.dismantle.request` | no open plan line / task / permit still linked, or override with reason |
| → `dismantled` | `scaffold.dismantle.complete` | photo of cleared area |

## 4. Tagging and inspections

### 4.1 Tag record
Every inspection is a one-engine inspection (`oe_inspections_inspection`, kind `inspection`,
T-0111) on the scaffold's Item, using the project's **scaffold inspection template**
(default template shipped in the pack; editable: checklist rows, photos, restrictions text,
signature). On completion the scaffold module writes `oe_scaffold_tag` from the `inspections` completion event / service call (never by reading inspections tables): `inspection_id`,
`tag_status` (`safe` / green, `restricted` / yellow, `unsafe` / red — colours and names come
from the terminology pack), `tag_no`, `tagged_at`, `tagged_by`, `restrictions`, `load_class`,
`next_due_at` = `tagged_at` + `inspection_interval_days`.

The scaffold card always shows: **tagged? last tagged when, by whom, next due**.

### 4.2 Re-inspection interval
Project setting `scaffolding.default_inspection_interval_days` (set to the site's rule; the pack
ships a default the project must confirm) plus event triggers that make an inspection due
immediately: modification completed, adverse weather event recorded in the daily log, incident
linked to the scaffold, tag status change to restricted.

### 4.3 Automation (SAQ scheduled job, daily + on events)
1. `lead_days` before `next_due_at` (project setting, default 2): create a **scaffold
   inspection task** (`tasks` E-04-07, type `scaffold_inspection`, anchored to the scaffold
   Item, due = `next_due_at`).
2. Assignment per project rule (`scaffolding.assign_rule`):
   `supervisor` — notify the custodian, who assigns an inspector (default);
   `auto` — assign automatically from the inspector pool (resource group "scaffold inspector",
   with a valid credential and available on that shift; tie-break least-loaded, then last
   inspector of this scaffold).
3. Notifications (T-0019) to custodian and assignee; reminder on the due day.
4. At `next_due_at` without a new tag: status → `inspection_overdue`, tag treated as
   **invalid**; this fails the AWP scaffolding gate (E-06b-02), the `planner` readiness of
   linked plan lines, and any permit rule that requires a valid scaffold tag. Escalation
   notification to the custodian's manager.

### 4.4 Request to tag / inspect
Any user can press **Request inspection** on a scaffold (or scan its QR, §8): creates the same
task type with reason (`periodic`, `after_modification`, `after_weather`, `user_request`) and
notifies the custodian (or auto-assigns per §4.3).

## 5. Build, modify and dismantle work

- **Requests**: `oe_scaffold_request` (`build`, `modify`, `dismantle`), requested by any user
  from a scaffold, an RSW, a plan line or a task; fields: location, purpose, required-by,
  access height, duty, linked work, sketch/photos. Approval by the scaffold coordinator
  persona (project setting).
- Approved requests create **tasks** (`scaffold_erect`, `scaffold_modify`,
  `scaffold_dismantle`) with estimated hours by resource group (Scaffolder), so they appear in
  the planner and in allocations. P6 activities `SCA Erect` / `SCA Dism` and the FEP
  "Scaff Hrs" / "Scaff" / "Scaff Shut" columns map to these through the import profile.
- **Planner integration**: a plan line with `needs_access = scaffold` links or requests a
  scaffold; the plan's readiness and the portal "issues" widget show missing or untagged
  scaffolds per RSW.

## 6. Photos and files

- Photos and files can be added at any time against the scaffold, a request, a task or a tag
  (uploads T-0016, presigned, offline capture in the Dexie outbox for tablets).
- Photo metadata: taken at, by, optional GPS, caption snippets (pack value list: "before",
  "after", "tag", "base plates", "access").
- Scaffold timeline tab: requests, status changes, tags, photos, notes, linked tasks (audit
  ledger T-0006).

## 7. Competency checks

Credentials (T-0118) types used: licensed scaffolder (by class), scaffold inspector / competent
person. `run_checks` verifies the people recorded as erector / inspector hold the credential
on the date of the act. Missing credential = finding with rule-set severity (default block for
tagging, warn for erection crews where only the lead must be licensed).

## 8. Field use (mobile)

- Each scaffold gets a QR code (printable tag label). Scanning opens the **scaffold card**:
  status colour, last tag (date, by, restrictions), next due, linked work, buttons *Request
  inspection*, *Add photo*, *Report issue*.
- Inspection and tagging run offline (Tier-1 outbox); the tag is provisional until synced.

## 9. Register and reports

- Register view ([`register-views.md`](register-views.md)) with default saved views:
  "Due in 7 days", "Overdue", "Untagged", "By area", "Linked to shut X".
- Exports: scaffold register (XLSX), tag certificate PDF per tag, inspection history per
  scaffold, scaffold count / days-standing by area (for hire reconciliation; cost stays in the
  external system).

## 10. Events

`scaffold.requested`, `scaffold.erected`, `scaffold.tagged`, `scaffold.inspection_due`,
`scaffold.inspection_overdue`, `scaffold.dismantled`.

## 11. Permissions

| Action | Default personas |
|---|---|
| request build / modify / dismantle / inspection | any project member |
| approve requests, set custodian, edit register | scaffold coordinator (project role), PER-03, PER-04 |
| tag | users holding the inspector credential (PER-07 or a scaffold inspector role) |
| configure register type, intervals, rules | PER-09 |

## 12. Suggested breakdown (planner agent refines)

1. Scaffold register + item creation + links + register type (M).
2. Lifecycle + rule sets + requests (M).
3. Tagging on the one-engine inspection + default scaffold template + tag record (M).
4. Scheduler: due tasks, auto-assignment, overdue gate, notifications (M).
5. Mobile scaffold card + QR + offline photo/tag (M).
6. Planner / AWP / permit integration (S).
7. Exports (S).
8. E2E: request → erect → tag → interval passes → task auto-created → overdue blocks a plan
   line → re-tag clears it (S).
