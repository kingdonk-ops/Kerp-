# Module detail — Handover (shift / swing handover register)

Module `handover` (epic **E-04-11**). Lets people who work back-to-back or FIFO —
supervisors, QA/QC, inspectors, coordinators, project managers — write a handover for the
person who takes over, delivers it to that person on their first shift, and keeps every
handover in a searchable register.

Reference example: a coordinator's swing handover (header with name, position, date of leave,
date of return, contact; then one block per area/asset/work-pack with a heading, sub-headings
and bullet points; then "Concerns & Focus"; then an appendix). Per ADR 0003's principle
("examples, not formats") that layout is the **default template**, not a fixed format.
All examples in this document are synthetic.

## 1. Placement

`handover` is its **own module**, shown in the **Execution** nav group (8-group IA,
`KERP-CONSOLIDATION-AND-DIFF.md` §6) next to the Daily Workface Log (`field_execution`, E-04-04), not a section inside meetings or the diary:

- its lifecycle is person-to-person (author → named recipients → acknowledgement), unlike a
  diary (one shift, one crew) or minutes (one meeting, many attendees);
- it has its own project settings (who must hand over), register and reminders.

Links in both directions instead of nesting: a handover section can reference a meeting or a
diary entry (§5), and meeting / diary / record detail pages show "Mentioned in handovers"
(§9).

## 2. Who must hand over — project settings

Project Settings → **Handover** (Project Administrator, PER-09). Table
`oe_handover_assignment` (`BaseRecordMixin`, project RLS):

| Column | Purpose |
|---|---|
| `subject_kind` | `persona` or `user` |
| `persona_code` / `user_id` | who must hand over (e.g. PER-03, PER-06, PER-07, PER-02, or a named user) |
| `position_label` | free text shown on the handover header ("Shut Coordinator") |
| `recipient_rule` | `partner` (named back-to-back users), `same_persona` (everyone else on the project with the same persona), `named` (fixed list) |
| `partner_user_ids` | back-to-back partner(s) for `partner` / `named` |
| `rotation` | optional: `days_on`, `days_off`, `anchor_date` (e.g. 14/14 from 2026-08-20) — drives due dates and reminders |
| `reminder_lead_hours` | reminder before the end of the swing (default 24) |
| `ack_deadline_hours` | escalate if a recipient has not acknowledged within this time after their first shift (default 12) |
| `escalate_to_persona` | default PER-02 Project Manager |
| `active` | on/off |

Project-level options on the same page:

- `visibility`: `recipients_and_managers` (default) or `project` (whole project team can read).
- **Default template** (section headings, see §3) — editable, versioned.
- **Auto-link rules** and **activity rules** (rule sets, §6 and §7) — default sets ship with
  every project and can be edited, disabled, deleted or added to per project (ADR 0004).

Anyone may write a handover even when not assigned; assignments only add the obligation,
reminders and overdue tracking.

## 3. The handover record

`oe_handover_handover`:

| Field | Notes |
|---|---|
| `author_id`, `position_label` | position defaults from the assignment |
| `period_start`, `period_end` | swing covered; `period_end` = "date of leave". Default `period_start` = end of the author's last handover received, else their last handover written, else the rotation start |
| `expected_return` | "date of return" |
| `contact` | phone / email, prefilled from the author's profile (not retyped), editable |
| `recipients` | resolved from the assignment's `recipient_rule`, editable before submit |
| `status` | `draft → submitted → closed`; plus `overdue` flag (computed) |
| `template_revision_id` | pinned |
| `submitted_at`, `content_hash` | set on submit; submitted content is immutable and its hash goes into the audit ledger (T-0006). Corrections are **addenda** (new section appended, timestamped) |

`oe_handover_section` (ordered): `heading` (free text; may itself contain a record link, e.g.
"Wk47 Shut — TLO — RSW 301"), `kind` (`free`, `activity_summary`, `concerns`, `appendix`),
`body` (rich text, §4), `source` (`user` or `system`, see §7).

**Default template** (mirrors the reference example): *General*; one repeatable *Area / Asset /
Work pack* section; *Concerns & Focus*; *Appendix* (attachments). Users add, rename, reorder
and delete sections freely; "+ Add section" adds a blank one matching the current layout
(heading column + description column).

`oe_handover_ack`: per recipient — `first_seen_at`, `acknowledged_at`, optional `reply`
(questions back to the author, which notify the author).

## 4. Free-form text

Body is a rich-text document stored as **ProseMirror JSON** (edited with TipTap, MIT).
Allowed nodes/marks only: paragraph, sub-heading (h3/h4), bullet and numbered lists (nested),
bold, italic, underline, **record link** (§5), web link, `@user` mention, attachment / photo.
The server validates against this schema and rejects anything else (no raw HTML).
A plain-text projection is stored for search (§9). Attachments use presigned uploads.

## 5. Linking text to records

### 5.1 Reference registry (`core/refs`)
Platform kernel, no tables of its own. Each module's `manifest.py` registers the record kinds
it can be linked to, with a resolver in its own `service.py`:

```
RefKind(kind="node", label_key="ref.node", search(q, ctx) -> [RefHit], preview(id, ctx) -> RefPreview | None, url(id))
```

Kinds at first: work-anchor / any level node and Item (`asset_management`), inspection,
NCR / punch, inspection request, document, meeting, diary entry, user. Later modules add
theirs by registering — no change in `handover`. Resolvers enforce the **viewer's** RLS:
if the viewer cannot see the record, `preview` returns `None` and the link renders as a
"restricted" chip. No cross-module table access.

API: `GET /refs/search?q=&kinds=`, `GET /refs/{kind}/{id}/preview`.

A record link mark stores `{kind, id, label_snapshot}` so the text still reads correctly if
the record is renamed or deleted (shown struck-through with "no longer exists").

### 5.2 Link from the toolbar
Select text → **link icon** in the toolbar (or Ctrl+K) → picker dialog: search box across all
kinds, kind filter chips, recent records, results showing code + title + status → choose →
the selected text becomes a record link.

### 5.3 Auto-link while typing
Tokens that match a project **auto-link rule** (rule set `handover.autolink`, kind `autolink`,
engine `regex`, ADR 0004) are resolved as the user types (debounced):

- exactly one match → linked automatically, shown with a dotted underline; Ctrl+Z or the
  "unlink" action undoes it;
- several matches → suggestion list under the token;
- none → left as plain text.

Default rules are generated from the project's breakdown template and numbering patterns
(ADR 0003 `code_pattern`, terminology pack), e.g. with the remediation pack:

| Rule | Pattern (illustrative) | Resolves to |
|---|---|---|
| work anchor code | `(?i)\bRSW[\s:\-]*(?P<code>\d+(?:[.\-]\d+)?)\b` (RE2 syntax) — matches `rsw103`, `RSW: 401.1`, `RSW 16-900` | node of the `work_anchor` level with that code |
| NCR number | the project's NCR numbering pattern | NCR |
| inspection request number | project numbering pattern | inspection request |
| asset code | `asset` level `code_pattern` | node |

A CUI project's defaults use "Line" instead of "RSW". Projects edit, delete or add rules.

### 5.4 Hover preview
Hovering a record link (tap on touch) opens a preview card from the resolver's `preview()`:
kind, code, title, status, 3–6 key fields chosen by the resolver, last updated by/when, and
"Open". Loaded lazily and cached for the session.

## 6. Delivery — the incoming person's first shift

On **submit**:
1. Each recipient gets an in-app notification with `priority = high`, label "Handover",
   **pinned** at the top of their notification list until acknowledged (T-0019).
2. **First shift:** the first time a recipient opens the project on or after `period_end`
   (or straight away if already on shift) they get a "Handover from {author}" screen before
   their home page. They can read now or "Read later" — it stays pinned. `first_seen_at` is
   recorded. **Decided (TD-073, 2026-11-10): the first shift comes from the roster** kept under
   staff/project settings — start dates, two or more teams (day/night, back-to-back), people
   assigned to teams, extra teams with from/to dates (e.g. a shutdown day/night crew); rosters
   also feed task scheduling. The recipient's first rostered shift on or after `period_end`
   triggers the screen; until the roster exists (spec in TD-083, owned by the planner resource
   pool) the first project sign-in on or after `period_end` is the fallback.
3. **Acknowledge:** "I have read this handover", with an optional reply/questions box.
   Acknowledgement is recorded per recipient (audit ledger) and notifies the author.
4. **Escalation:** not acknowledged within `ack_deadline_hours` of `first_seen_at` →
   notification to `escalate_to_persona`.

**Obligation tracking** (assignments with a rotation): reminder to the author
`reminder_lead_hours` before the swing ends; no submitted handover by the end of the swing →
`overdue` in the register and a notification to `escalate_to_persona`.
Reminders, escalations and overdue checks run as **SAQ cron** jobs.

## 7. Activity suggestions ("what happened on my swing")

A side panel in the editor lists what has happened since `period_start`, for **guidance only**.

- **Source:** the platform activity feed built on the audit ledger / domain events (T-0006,
  E-05-07 event bus when available) — never other modules' tables. Event labels and links
  come from `core/refs`.
- **Rules:** rule set `handover.activity` (kind `digest`, CEL, ADR 0004). Each rule: `key`,
  `group` (panel heading), CEL filter over the event, `scope` (`mine` — I did it; `my_area` —
  in nodes I am assigned to; `project`), bullet template, `enabled`, `order`. Rules whose
  source module is not enabled on the project are hidden.
- **Default rules** (every project; editable, deletable, extendable per project):

| Group | Default rule |
|---|---|
| Started | work-anchor status → in progress; inspection started |
| Completed | work anchor completed; inspection passed; punch closed; NCR closed |
| Sent to others / status changes | work-anchor (scope) status changed to a configured "sent" state — e.g. sent to engineering, planning, costing, client — or returned from them |
| Scope changes | work-anchor scope, budget quantity or description changed |
| Quality | inspection failed; NCR raised; Cat A punch raised; inspection request booked / no-show |
| Logistics | delivery received with OS&D (when Phase 3 modules are on) |
| Commercial | variation raised / approved (when Phase 6 modules are on) |

  Status values such as "sent to engineering" are workflow data, so the rule matches on
  configured values, not code.

- **Use:** the user ticks items (or "select all in group") → **Insert** → an
  `activity_summary` section is created or updated with one linked bullet per item, grouped
  by rule group. The section has `source = "system"` and is fully editable after insert.
  Nothing is inserted or submitted without the user choosing it.
- Computed in the SAQ **interactive** queue, debounced, cached per draft.
- Any future AI wording ("summarise these updates") is `source="ai"` and needs the user's
  confirmation before it enters the document (CLAUDE.md §6) — out of scope for v1.

## 8. Permissions

| Action | Who |
|---|---|
| Write / edit own draft, submit, add addendum | author |
| Read | author, recipients, PER-02 / PER-03 on the project; whole project team if `visibility = project` |
| Acknowledge / reply | recipients |
| Configure assignments, template, `digest` / `autolink` rule sets | PER-09 Project Administrator |
| Read all handovers in register (full content) | PER-01, PER-02 |
| See register metadata only (author, period, recipients, ack / overdue status — no body) | PER-09 |

Tenant from the JWT; cross-tenant and cross-project → 404 (project RLS, T-0007).

## 9. Register, search and reverse links

- **Register** (RegisterPage shell): author, position, period, recipients, ack status
  (n of m), status / overdue, linked-record count. Filters: person, persona, date range,
  status, linked record.
- **Search:** full-text over headings and body (`tsvector` on the plain-text projection),
  plus "handovers linking to {record}".
- `oe_handover_ref(handover_id, section_id, kind, target_id)` is written on save from the
  record-link marks, so any record's detail page can show **"Mentioned in handovers"**.
- **PDF export** (SAQ batch queue): header table + two-column section layout like the
  reference example; links printed as `code — title`.
- Submitted handovers are never deleted. Drafts can be deleted by their author.

## 10. Events

`handover.submitted`, `handover.first_seen`, `handover.acknowledged`, `handover.overdue`,
`handover.escalated` — onto the event bus for notifications and later integrations.

## 11. Tables (all `BaseRecordMixin` + project RLS)

`oe_handover_assignment`, `oe_handover_template` (+ revisions), `oe_handover_handover`,
`oe_handover_section`, `oe_handover_ack`, `oe_handover_ref`. Rule sets live in the shared rule
store (ADR 0004), not in this module.

## 12. Suggested breakdown (for the planner, when Phase 4 is broken down)

| # | Task | Size |
|---|---|---|
| 1 | `core/refs` registry + search / preview API + first resolvers (node, item, inspection, NCR) | M |
| 2 | Assignments + project settings page + default template | M |
| 3 | Handover record, sections, submit / immutability / addendum, register API + page | M |
| 4 | Rich-text editor (TipTap) with sections, toolbar link picker, hover preview cards | M |
| 5 | Auto-link rule kind (`regex`) + default rules from level defs + inline linking | S |
| 6 | Activity feed service + default `handover.activity` rule set + suggestions panel + insert | M |
| 7 | Delivery: pinned high-priority notification, first-shift screen, acknowledge / reply, escalation + reminders + overdue (SAQ cron) | M |
| 8 | Full-text search, `oe_handover_ref` reverse links, PDF export | M |
| 9 | E2E: assign → write with links + activity insert → submit → recipient first shift → acknowledge | S |

Dependencies: T-0019 (notification priority + pin), T-0018 and ADR 0004 (rule sets with
project defaults), T-0006 (audit ledger), T-0015 (SAQ), T-0101 (nodes), T-0025 (shells).
Offline drafting (Dexie outbox) is deferred: TD-074.
