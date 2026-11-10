# Module detail — Client portal: role layouts, plan distribution, review sessions and approval

Module `portal` (epic **E-07-04**, after E-07-01 read-only portal). Lets a planner send an
issued work plan ([`planner-and-work-plans.md`](planner-and-work-plans.md)) to the client and
other stakeholders, collect comments and approvals over one or more review sessions, and on
approval add the plan to the schedule (ADR 0006).

The same mechanisms — role layouts, distribution lists, threaded anchored comments, revision-bound
approvals — are **generic**: they are written for work plans first and are meant to be reused by
other reviewable records (change-of-scope submissions, ITP co-sign E-07-02, visit schedules
E-07-03). All examples are synthetic.

Precedence: module detail (3).

---

## 1. Audiences

| Audience | How they get in | What they can do |
|---|---|---|
| Internal KERP users | normal login, link opens the plan in KERP | full per their persona |
| Client / third-party users **with** a KERP account (PER-15) | link to the client portal, normal login (JWT, T-0008) | view per portal layout, comment, approve if they are a required approver |
| People **without** an account | **magic link** by email (T-0009 mechanism, portal-scoped) | view the one plan revision (and its comments) per the layout of their participant role; comment; approve **only if** the project allows external approvers (§7.2) |

### 1.1 Magic-link rules
- Token bound to: participant record, plan id, email address, scope (`view`, `comment`,
  `approve`), expiry (project setting, default 14 days), single revision or "latest revision".
- Exchange: link → one-time code check (6-digit code sent in the same email or a second email,
  project setting) → short session cookie (portal origin, `SameSite=Strict`), never a full JWT.
- Revocable per participant; rate-limited; every view/comment/approval audited with IP and
  user agent (hash-chain audit T-0006).
- Cross-tenant/cross-plan access → 404 (CLAUDE.md §6).
- A magic-link participant can be upgraded to a full account later; history stays linked.

## 2. Portal layouts by client role

The client portal looks different per client role. A **layout** is configuration, shipped as a
default in the client pack (ADR 0003 §4) and editable by the Project Administrator (PER-09).

`oe_portal_layout`: `key`, `audience_role` (client role code, e.g. `client_project_lead`,
`client_superintendent`, `client_engineer`, `client_discipline_advisor`, `client_planner`,
`client_document_controller`, `external_viewer`), `record_kind` (`work_plan`, …), `widgets`
(ordered JSON list), `version`.

Widget catalogue (each widget reads the plan statistics or a register view, never raw tables):

| Widget | Shows |
|---|---|
| `summary_header` | plan, revision, kind, mode (overlay/interrupt), window, status, approval progress |
| `kpi_cards` | total hours, peak heads, # RSWs / work packs, # open issues |
| `hours_by_group` | bar/stacked chart by resource group (Scaffolders, Mechanical, Civil, Painter/Blaster…) |
| `hours_by_anchor` | table: hours per RSW / work anchor, with start/end and status |
| `hours_by_node` | per asset / area |
| `timeline` | overlay Gantt (planner §9) — read-only |
| `calendar` | month/week calendar of lines |
| `kanban` | lines by status or by scope area |
| `allocation_grid` | read-only grid (planner §7.2), heads per day/shift |
| `issues` | open NCRs, punch, scope changes, holds, untagged scaffolds per RSW/asset |
| `documents` | attached PDF pack, RSW PDFs, drawings |
| `comments` | comment feed with filters (§5) |
| `approvals` | required approvers, votes, attendance, overrides (§7) |
| `saved_view` | any register view shared to the portal ([`register-views.md`](register-views.md)) |

Default layouts (examples): a project lead sees summary, KPIs, hours by group, timeline,
approvals; a discipline advisor (coatings / welding / H&S) sees hours by anchor filtered to their
discipline's activity codes, issues and comments; a document controller sees documents and
approvals only. Field-level visibility still obeys RLS and the participant's scope.

## 3. Participants

`oe_portal_participant` per reviewable record (plan): `user_id` **or** `email` + `display_name`
+ `organisation`, `client_role` (→ layout), `rights` (`view`, `comment`, `approve`),
`required_approver` (bool), `notify` (bool), `source` (distribution list, manual, mention).
Anyone with access may comment (`comment` right is the default for every participant); only
participants with `approve` count towards approval; **only KERP users with edit permission on
the plan can change it.**

## 4. Distribution

### 4.1 Distribution matrix (project configuration)
Reference: the client's communication matrix — rows = people / group mailboxes with company,
department, position; columns = communication formats (daily report, daily report (shutdown),
weekly progress, monthly progress, shutdown planning — per shut, technical query, document
deliverables, MDR, transmittal, incident, escalation…) each with a frequency and a To / Cc mark.

KERP models it once per project and reuses it in every module that sends something:
- `oe_portal_comm_format`: `key` (`shutdown_planning`, `weekly_progress`, …), `label`,
  `frequency` (text + optional cron for scheduled ones), `due_rule` (e.g. "by 12:00 Monday"),
  `default_record_kind`.
- `oe_portal_distribution`: `comm_format_id` × `contact_id` (contacts E-03-01) → `mode`
  (`to`, `cc`, `none`), plus the participant role and default rights for that format.
- Importable from the matrix spreadsheet via a mapping profile; editable as a register view.

### 4.2 Sending a plan for review
When a plan revision is issued the planner opens **Send for review**:
1. recipients pre-filled from the distribution matrix for the plan kind's comm format
   (`shutdown_planning` for a shutdown), editable; external recipients get magic links,
   KERP users get portal links;
2. the email body is generated from a template (terminology pack, i18n) describing the planned
   work: plan, window, mode, scope areas, hours by group, start/end, RSW count, issues summary,
   review session dates;
3. attachment: the plan PDF pack of **that revision** (planner §10), filename carries the
   revision;
4. preview → planner confirms → send (SAQ job). Nothing is sent without that confirmation.
   AI-drafted email text is `source="ai"` and needs the same confirmation (CLAUDE.md §6).
5. send log per recipient (queued / sent / bounced), resend, revoke link.

Outbound email transport is an integration (E-05-07) — the provider is configuration.

## 5. Comments

`oe_portal_comment`: `record_kind/record_id`, `revision`, `parent_id` (threads),
`anchor_kind` (`whole`, `plan_line`, `node`, `resource_group`, `date_range`, `scope_area`,
`document_page`), `anchor_ref`, `body` (rich text, mentions), attachments (uploads T-0016),
`author_participant_id`, `status` (`open`, `resolved`, `actioned`), `resolved_by`,
`linked_change` (the revision / line edit that actioned it).

- Comment on the whole plan or on any individual area: a line, an RSW, a resource group,
  a day, a scope area, a page of the PDF.
- **Every linked participant is notified** of a new comment or reply (in-app via T-0019 for
  users; email digest for magic-link participants, with the link). Participants can mute a
  thread. `@mention` adds a person as a participant with `comment` rights.
- Planners action comments by editing the draft/next revision; the comment shows
  "actioned in rev C".
- Comments are carried forward to later revisions (shown with the revision they were made on).

## 6. Review sessions

A plan may be reviewed over several meetings.

`oe_portal_review_session`: `record_id`, `revision`, `scheduled_at`, `location / call link`,
`status` (`scheduled`, `held`, `cancelled`), `minutes` (rich text), `decisions` (list).
`oe_portal_session_attendance`: session × participant → `attended` (yes/no/apologies),
recorded by the session chair.

- Sessions show on the plan timeline and in invites (calendar file attached to the email).
- During a session the chair can record approvals on behalf of attendees who state them
  verbally only if the project allows proxy recording (default off); otherwise each approver
  clicks Approve themselves.

## 7. Approval

### 7.1 Votes
`oe_portal_approval_vote`: `record_id`, `revision`, `revision_hash`, `participant_id`,
`decision` (`approve`, `approve_with_comments`, `reject`), `comment`, `cast_at`, `channel`
(`app`, `portal`, `magic_link`, `proxy`), `session_id` (nullable), `override_of` (nullable).
Each vote is written to the signing ledger (T-0119) with the revision hash.

### 7.2 Rule
The plan's approver rule comes from `approval_routes` (E-06-07, quorum/delegation/SLA). Default
rule for work plans: **all required approvers approve** the same revision. Votes may be cast at
any time between issue and approval, in any session or asynchronously, so approval can build up
over several sessions.

External (magic-link) approvers count only if the project setting
`portal.allow_external_approval` is on; otherwise their decision is recorded as a comment-level
"endorsement".

### 7.3 Override for absent approvers
If a required approver has neither voted nor attended any session for the revision, a user with
the `override_approval` capability (default: PM PER-02 and the client project lead role) can
record an **override** for that approver with a mandatory reason (by default only for approvers of the overrider's own organisation; a project may widen this, TD-081). The override is a vote with
`decision=approve`, `override_of=<participant>`, it is shown distinctly everywhere, the absent
approver is notified, and the ledger records who overrode whom and why. A `reject` vote cannot
be overridden; it sends the plan to `changes_requested`.

### 7.4 Revisions reset approvals
Votes bind to `revision_hash`. Issuing a new revision resets all votes (they stay visible as
history on the old revision). Re-requesting review re-notifies participants with a change
summary (diff of lines, hours by group, dates).

### 7.5 Outcome
When the rule is satisfied: plan → `approved` (`run_checks(context, "planner.plan.approve")`),
participants notified, and — per project setting — the plan commits automatically or waits for
the planner (planner §8.1). An approval certificate page (approvers, votes, overrides, sessions,
hash) is appended to the PDF pack.

## 8. Notifications

All via `notifications` (T-0019): plan issued to you, new comment / reply / mention, session
scheduled / changed, approval requested, approval reminder (SLA from `approval_routes`),
override recorded on your behalf, plan approved / rejected / committed. Email delivery for
external participants and for users who opted in.

## 9. Events

`portal.review.sent`, `portal.comment.created`, `portal.session.held`,
`portal.approval.cast`, `portal.approval.overridden`, `portal.review.approved`,
`portal.review.rejected`.

## 10. Suggested breakdown (planner agent refines)

1. Participants + magic-link scoping for a single record (extends T-0009) (M).
2. Distribution matrix: comm formats, distribution, import profile, register (S).
3. Send-for-review: email template, PDF attachment, confirm-then-send SAQ job, send log (M).
4. Comments: anchored threads, notifications, carry-forward across revisions (M).
5. Review sessions + attendance (S).
6. Approval votes on `approval_routes`, override, revision-hash reset, ledger (M).
7. Portal layouts + widget catalogue + default layouts per client role (M).
8. Frontend portal pages for plan review (M).
9. E2E: issue → send (internal + magic link) → comment on RSW → second revision → two sessions →
   approvals + one override → approved → committed (S).
