# Module detail — Planner: work plans, resource pool and allocation

Module `planner` (epic **E-04-12**). Sits on top of the P6-synced field programme
(`schedule`, E-04-01) and feeds `tasks` (E-04-07), `resource_summary` (E-04-08), the
look-ahead board (E-04-02) and the client plan review in the portal
([`portal-plan-review.md`](portal-plan-review.md), E-07-04).

Written against [ADR 0003](../../decisions/0003-configurable-breakdown-and-terminology.md)
("RSW" = the project's work-anchor level), [ADR 0005](../../decisions/0005-field-checkout-and-p6-reimport-merge.md)
(P6 owns P6 fields only) and [ADR 0006](../../decisions/0006-work-plans-layered-on-the-programme.md)
(how an approved plan joins the programme). All examples are synthetic.

Precedence: module detail (3). Disagreements → `spec-gap` TODO.

---

## 1. Reference examples (what the field teams run today)

| Example | What it shows | Where it lands |
|---|---|---|
| P6 construction schedule (XER, ~3,000 activities) | WBS Project → Site → Online/Shutdown circuit → Asset; activity names `Asset / Activity / RSW` (e.g. `CV101 / CP / 101`); activity code "Status" = Shutdown / Online Works / On-Hold P1 / P2; one calendar per shut window (`… CD3P`, `… PLT01 Preps`); labour resources per trade per region (Scaffolder, Painter/Blaster, Mechanical, Civil) with hours per activity; scaffold activities `SCA Erect` / `SCA Dism` | baseline programme (§3), resource groups (§4), calendars (§3.2) |
| Basis of schedule | WBS levels, online works 12 h × 7 d, shutdown works 12 h per shift (DS + NS), schedule depends on client shut dates and isolations | calendars and plan kinds (§3, §5) |
| Front-end planning workbook | per Asset / RSW / Activity: budget qty + UOM, budget hrs, scaffold hrs, Shutdown flag, grouping / workpack, sequence priority, offsite fab, outsource; summary of budget hours Online vs Shutdown per asset, with and without scaffolding | plan lines and stats (§6, §9); register columns ([`register-views.md`](register-views.md)) |
| Shut resource allocation & forecast (one week shut) | rows = "Scope Area n – description" (Asset, RSW #, client work-order #, hours by trade B&P / Mech / SR / DW); second sheet = crew blocks (Painting crew 13 DS + 6 NS, Mechanical 7 DS + 3 NS, Scaffolding 9 DS) × day × AM/PM with heads per scope area and totals per shift; milestones on the timeline (JSE start, junction start, loop start) | allocation grid (§7) |

Per ADR 0003 these are **examples, not formats**: trades, shifts, scope-area naming, plan
kinds and grid layout are configuration that a project can change.

## 2. Concepts

| Concept | Meaning |
|---|---|
| **Programme** | The P6-synced activities (E-04-01) plus everything KERP adds on top (committed plans). P6-owned fields stay read-only (ADR 0005). |
| **Work plan** | A planned job or package of work prepared in KERP — a shutdown, break-in, campaign, recovery or any project-defined kind — with its own window, lines, resources and approval. |
| **Plan line** | One piece of work in a plan, anchored to the work anchor (RSW) or any node / item, optionally to a P6 activity. |
| **Resource group** | A kind of resource: trade / discipline / role (Scaffolder, Painter/Blaster, Mechanical, Civil, Rigger, Supervisor, QA inspector…). Configurable per project. |
| **Resource** | A named person (KERP user or a worker record without login), a crew (named set of people), or plant. Belongs to one or more groups. |
| **Allocation** | Demand placed on a group or a named resource for a plan line, per day and shift, in heads or hours. |
| **Scenario / revision** | A plan is revised (A, B, C… or 0, 1, 2 per the project numbering pattern); each issued revision is frozen. |

## 3. Programme, calendars and shifts

### 3.1 Programme (read side)
The planner reads the programme from `schedule` through its `service.py` (no cross-module table
access). Each activity exposes: dates (early/late, baseline, forecast), status code
(online / shutdown / on-hold, from the P6 activity-code type the import profile maps), link to
the work anchor (`schedule_link` role), budgeted labour units per resource group, calendar.

### 3.2 Calendars and shifts
- P6 calendars are imported as read-only `external_source="p6"` calendars (work days, hours,
  exceptions). A shut window calendar (e.g. "Shut PLT01") is shown as a shaded band.
- KERP calendars (`oe_planner_calendar`) are created for plans that have no P6 calendar.
- **Shift pattern** (`oe_planner_shift_pattern`): named shifts with start/end and paid hours,
  e.g. `DS 06:00–18:00 11.5 h`, `NS 18:00–06:00 11.5 h`, or `AM` / `PM` halves. The allocation
  grid uses the plan's shift pattern as its column unit.
- All times stored UTC, displayed in the project's IANA timezone (master spec §1.2).

## 4. Resource pool

Not HR (consolidation diff §1 culls HR/payroll). The pool is the minimum needed to plan and
check eligibility.

| Table | Key columns |
|---|---|
| `oe_planner_resource_group` | `code`, `label_key` (terminology pack), `kind` (`labour`, `plant`, `supervision`, `inspection`), `default_rate_hours_per_shift`, `color`, `external_source/external_id` (P6 RSRC) |
| `oe_planner_resource` | `kind` (`person`, `crew`, `plant`), `display_name`, `user_id` (nullable — field workers may have no login), `contact_id` (contacts E-03-01, nullable), `group_ids`, `home_roster_id`, `employer` (own / subcontractor), `active_from/to` |
| `oe_planner_crew_member` | crew ↔ person, `role_in_crew` (lead hand, member) |
| `oe_planner_roster` | rotation (`days_on`, `days_off`, `anchor_date`), shift (DS/NS), e.g. 14/14 DS |
| `oe_planner_availability` | exceptions: leave, R&R, training, demob, restricted duties (date range, reason code) |
| `oe_planner_capacity` (derived, SAQ rollup) | group × date × shift → rostered heads and hours |

- Groups, rosters and people can be imported from P6 RSRC, from a roster spreadsheet (mapping
  profile, ADR 0003 §5) or entered by hand.
- **Competency link:** a person's credentials come from `credentials` (T-0118). A plan line can
  require competencies (e.g. "licensed scaffolder", "coating inspector", "WPQ for process X");
  assigning a person without a valid credential on the line's dates raises a finding through
  `run_checks(context, "planner.assignment")` (warn or block per the project rule set, ADR 0004).

## 5. Work plans

`oe_planner_plan` (`BaseRecordMixin`, project RLS):

| Column | Notes |
|---|---|
| `code`, `title` | numbering pattern per project (e.g. `{project}-WP-{seq:3}`); free `client_ref` for the client's shut code (e.g. a shut number like `SHUT-0001`) |
| `kind` | configurable list; defaults: `shutdown`, `break_in`, `campaign`, `look_ahead`, `recovery`, `other` |
| `mode` | `overlay` or `interrupt` (§5.1) |
| `interrupt_scope` | for `interrupt`: `project`, `nodes` (subtree list), `resource_groups` (list) or both |
| `window_start`, `window_end` | planned window; `calendar_id`, `shift_pattern_id` |
| `scope_nodes` | site / area / asset subtrees the plan covers (for filtering and stats) |
| `status` | §8 |
| `revision`, `revision_hash` | current revision label and SHA-256 of the frozen content when issued |
| `owner_user_id`, `approver_rule_id` | owner = planner; approver rule from `approval_routes` (E-06-07) |
| `milestones` | JSON list of named markers on the timeline (e.g. "isolation start", "junction start", "loop start", "handback") |
| `custom_fields` | JSONSchema per plan kind (field registry, ADR 0003 §6) |

### 5.1 Plan modes

- **`overlay`** — the plan runs **at the same time** as the programme. It consumes the same
  resource pool, so the capacity check (§7.3) shows combined demand. Nothing in the programme
  moves.
- **`interrupt`** — from `window_start`, programme work inside `interrupt_scope` **stops**,
  the plan runs, and the stopped programme work **resumes after `window_end`**. KERP computes a
  *displaced forecast* for the affected programme activities: remaining duration is pushed
  past the window on the activity's calendar, successors follow by their logic (FS/SS/FF/SF and
  lags from P6 TASKPRED). The displaced forecast:
  - is a KERP forecast layer only — it **never** writes P6-owned fields (ADR 0005);
  - is shown as ghost bars on the overlay Gantt (§9);
  - is exported as a forecast update / fragnet for the scheduler to bring into P6 (ADR 0006).
- `interrupt_scope = resource_groups` stops only the work of those groups (e.g. all
  Painter/Blaster crews are pulled onto the shut, mechanical online work continues).

Both modes can be mixed across plans; a later plan is evaluated against the programme **plus**
already-approved plans.

## 6. Plan lines

`oe_planner_plan_line`:

| Column | Notes |
|---|---|
| `plan_id`, `seq`, `group_label` | `group_label` = scope area heading ("Scope Area 1 – Prim. Crusher Maintenance Deck"); grouping is free text or a node |
| `anchor_node_id` / `anchor_item_id` | any node or item; `anchor_role` recorded (ADR 0003 §2). Usually the work anchor. Multiple anchors via `oe_planner_line_anchor` (one scope area covering RSWs `01-100.1-2` and `02-101.1-2`). |
| `activity_code` | the FEP / P6 activity (B, P, CP, W, SR, BN, G, HDNR…, SCA Erect, SCA Dism) — a value list from the project's pack |
| `p6_activity_external_id` | optional link to an existing P6 activity |
| `client_work_order` | text (client maintenance order no.); multi-value allowed |
| `planned_start`, `planned_end`, `duration_shifts` | shift granularity |
| `hours_by_group` | JSON `{group_code: hours}` (B&P 120, Mech 40…) — totals feed stats |
| `qty`, `uom` | optional earned-quantity basis (m², kg, Lm, m³, ea) |
| `depends_on` | links to other lines (FS/SS + lag) and to programme activities |
| `required_competencies` | credential types (§4) |
| `needs_access` | none / scaffold / EWP / rope access — `scaffold` creates or links a scaffold requirement (scaffolding module, [`scaffold-management.md`](scaffold-management.md) §5) |
| `status` | `planned`, `ready`, `in_progress`, `done`, `deferred` (after commit, from `tasks` progress) |
| `notes`, attachments | free text, RSW pdf / drawing links via `documents` |

Lines can be created by: picking nodes in the asset register (saved views, filters such as
"Shutdown = yes and Site = X"); copying programme activities; importing a spreadsheet (mapping
profile); or AI proposal (`source="ai"`, human confirmation before applying — CLAUDE.md §6).

## 7. Resource allocation grid

### 7.1 Data
`oe_planner_allocation`: `line_id`, `resource_group_id` **or** `resource_id`, `date`,
`shift_code`, `heads` (decimal), `hours` (derived: heads × shift paid hours, overridable).
Group allocations can later be **resolved** to named people (`oe_planner_assignment`:
allocation ↔ person/crew) without changing the totals.

### 7.2 Screen
Desktop edge screen, reproduces the allocation sheet:
- columns: days of the window × shifts (AM/PM or DS/NS); milestone markers above the dates;
- row blocks per resource group (or crew), header shows roster capacity ("13 DS + 6 NS");
- rows within a block = plan lines (scope areas); cells = heads; drag across cells to fill;
- footer per block = total heads per shift, coloured against capacity (§7.3);
- a "summary" tab = the first sheet: scope areas with Asset, RSW #, client WO #, hours per group;
- toggles: heads ↔ hours, group ↔ named people, show programme demand.

### 7.3 Capacity check
For each group × date × shift: `capacity` (rosters − availability) vs `demand` (programme
demand from P6 resource loading for activities not interrupted + other approved plans + this
plan). Over-allocation is a finding of `run_checks(context, "planner.capacity")`; severity per
rule set (default: warn on draft, block issue above a project threshold unless a planner records
an acknowledgement with reason).

## 8. Lifecycle

```
draft ──issue──▶ issued ──client_review_started──▶ in_review ──approved──▶ approved ──commit──▶ committed ──▶ in_execution ──▶ closed
  ▲                │                                   │
  └── revise ──────┴────────── changes_requested ◀─────┘          (any non-closed) ──cancel──▶ cancelled
```

| Transition | Who | `run_checks` rule set | Effect |
|---|---|---|---|
| `issue` | planner (PER-05) + internal approval (PER-03/PER-02 per `approval_routes`) | `planner.plan.issue` — every line anchored, window set, capacity findings acknowledged, required approvers defined | freezes revision, computes `revision_hash`, renders PDF pack (§10), unlocks distribution (portal §4) |
| `revise` | planner | — | new revision copied from the last; previous stays read-only; approvals reset (portal §7.4) |
| `approved` | set by the portal approval engine when the approver rule is satisfied | `planner.plan.approve` | records approvals + overrides in the signing ledger (T-0119) |
| `commit` | planner, or automatic on approval (project setting) | `planner.plan.commit` | §8.1 |
| `cancel` | planner / PM | — | releases allocations; keeps history |

### 8.1 Commit — "added to the schedule"
On commit, in one transaction through the owning modules' services:
1. plan lines become **programme items** in the KERP layer of `schedule` (ADR 0006), visible on
   every programme view with source = plan;
2. one `tasks` task per line (E-04-07), anchored like the line, with allocations as the task's
   resource demand;
3. for `interrupt` plans the displaced forecast is stored as the programme forecast layer;
4. allocations become bookings that `resource_summary` (E-04-08) and the look-ahead (E-04-02)
   read;
5. a P6 fragnet export (new activities keyed by KERP ids, plus forecast updates) is queued for
   the scheduler (SAQ job; nothing is pushed to P6 automatically);
6. event `planner.plan.committed` is published.

## 9. Views

Every view filters by plan, kind, status, window, node subtree, resource group and saved view.

- **Overlay Gantt** — programme bars (by P6 status colour: online / shutdown / on-hold) with plan
  bars drawn over them in the plan colour; shut-window calendars shaded; `interrupt` plans show
  the displaced programme as ghost bars and the pause as a hatched band; critical path optional.
- **Resource histogram** — stacked demand (programme, approved plans, this plan) per group vs
  capacity line, by day/shift/week.
- **Calendar** — month / week with plans and lines as events, coloured by group or kind.
- **Kanban** — lines by status (planned → ready → in progress → done), or by scope area.
- **Allocation grid** — §7.2.
- **Register** — plan lines as a register view (column chooser, filters, saved views).

### 9.1 Plan statistics (shared with the portal)
Computed by a SAQ rollup on every revision and on commit, stored in a summary table:
- total hours, hours by resource group, by RSW / work anchor, by asset / scope node, by day and
  shift; peak heads per group;
- start / end per plan, per group, per scope area;
- count of work packs / RSWs / lines, by status;
- issues per RSW or asset: open NCRs, punch items, scope changes, inspection holds, scaffolds not
  tagged or overdue — read from those modules' services;
- online vs shutdown hours, with and without scaffolding (the FEP summary).

## 10. Exports

SAQ jobs (heavy libraries lazily imported, CLAUDE.md §6):
- **Plan PDF pack** — cover (plan, revision, window, mode), stats page (§9.1), Gantt, allocation
  grid per group, line list by scope area, issues per RSW; optional appendix of RSW PDFs /
  drawings. Labels via the terminology pack. Uses the project's report header/footer config.
- **XLSX** — allocation grid and line list in the reference layout.
- **P6 fragnet** — XER/XML of new activities and forecast updates (§8.1 step 5).

## 11. Events

`planner.plan.issued`, `planner.plan.revised`, `planner.plan.approved`,
`planner.plan.committed`, `planner.plan.cancelled`, `planner.capacity.exceeded` — payloads carry
`plan_id`, `revision`, `revision_hash`, `project_id`, actor.

## 12. Permissions (persona matrix, edge spec §5)

| Action | Personas |
|---|---|
| create / edit draft, allocate | PER-05 Area Planner, PER-03 Superintendent |
| internal approval to issue | per `approval_routes`; default PER-03 + PER-02 |
| read | project team; PER-04 Trade Supervisor sees own groups' allocations |
| client review | PER-15 through the portal (portal spec) |
| manage resource pool | PER-05, PER-09; PER-12 for people records |

## 13. Offline

Planning is an office task (online). Tablets get a read-only copy of committed plan lines and
allocations for their area (Dexie cache) so supervisors see who is booked where.

## 14. Suggested breakdown (planner agent refines)

1. Resource pool: groups, resources, crews, rosters, availability; P6 RSRC import (M).
2. Shift patterns + calendars (import P6 calendars, KERP calendars) (S).
3. Plan + lines + multi-anchor + lifecycle with `run_checks` hooks (M).
4. Allocation model + capacity rollup (SAQ) + `planner.capacity` rule set (M).
5. Interrupt mode: displaced forecast calculation over programme logic (M).
6. Commit: programme layer items, tasks, bookings, fragnet export job (M).
7. Frontend allocation grid edge screen (M).
8. Frontend overlay Gantt + histogram + calendar + kanban (M, maybe split).
9. Stats rollup + PDF/XLSX pack (M).
10. E2E: create shutdown plan in interrupt mode → allocate → issue → approve (portal) → commit →
    programme shows plan and displaced work (S).
