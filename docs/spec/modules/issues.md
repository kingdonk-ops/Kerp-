# Module detail — Issues: punch and NCR, one template-driven engine (`issues`)

Covers MOD-09 (master spec). Decisions are the **product owner's (user, 2026-11-21)** unless
marked **(planner default)**; planner defaults can be changed through a `decision` TODO.
This replaces the separate `ncr` and `punchlist` modules of the earlier plan: tasks T-0116 and
T-0117 are dropped and re-planned as T-0311–T-0323 and epic E-01-03 (§12). Pilot-0 gate
(user, 2026-11-21): T-0311–T-0314, T-0318, T-0319, T-0322.

Anchors follow [ADR 0003](../../decisions/0003-configurable-breakdown-and-terminology.md) §2
and its amendment 2026-11-21 (project-level issues, location and links, §3.1),
templates and layers follow ADR 0003 §4/§6 and
[ADR 0004](../../decisions/0004-project-rule-sets-with-defaults.md) §2–§5, fields come from the
shared registry ([quality-and-inspection.md](quality-and-inspection.md) §One field-type registry).

## 1. Two issue types, one engine (user, 2026-11-21)

| Type | Meaning | Workflow (fixed by the engine) |
|---|---|---|
| `punch` | minor remediation | raise → rectify → verify → close (§4.1) |
| `ncr` | non-conformance, full analysis | containment → disposition → root cause → corrective/preventive actions → effectiveness verification → closed (§4.2) |

- **Workflow comes from the type, fields come from the template.** The two types and their steps
  are code, so gates, dashboards and the MDR can rely on them. Everything else a project wants to
  capture is template fields. A new kind of issue (internal audit finding, external audit
  finding, client walkdown snag, …) is **a new template of type `punch` or `ncr`**, never code.
- **Origin** (`origin`, why the issue was raised): `client` (client requested), `qa` (our QA
  requested), `internal_audit`, `external_audit` (user), plus `construction` (our own site
  supervision outside QA) (planner default). The origin is a fixed field chosen at raise; an
  issue raised by an account with `is_internal = false` is always `origin = client` or
  `external_audit` (planner default: `client` unless the template's default origin is
  `external_audit`).
- **Upgrade.** A punch can be **upgraded** to an NCR (§7). There is **no downgrade** from NCR to
  punch; an issue raised by mistake is **voided** with a reason (§4.3).

## 2. Issue templates (user, 2026-11-21)

Designed the same way inspection templates are, only simpler.

- **Where they are managed (user, 2026-11-21).** Issue templates (issue types: punch or NCR per
  category) are created and managed under the **Issues** menu item, separate from the
  **Templates** menu item that lists every inspection template (quality-and-inspection.md
  §Template settings; diff §6.1). "New issue template" asks one question — **Punch** or **NCR**
  — and the preset copies the type and flag defaults of the project's `general` template of that
  type (planner default); every setting stays editable before the first publish (T-0320).
- **Authoring.** Templates are authored in the **shared template designer** used by inspection
  and issue templates alike (epic E-01-03, after Pilot-0; one engine, no per-module designer).
  For Pilot-0 the starting templates — `painting`, `blasting`, `civil`, `mechanical`, `welding`
  × punch / NCR, plus internal audit finding templates (punch and NCR, in their own category
  `audit`, `internal` + `excluded`, the defaults for `audit` × type so they never collide with
  the `general` defaults) — are **authored by us and seeded as tenant defaults** through the
  normal tenant provisioning path (user, 2026-11-21; T-0322).
- **One engine (planner default).** An issue template is a one-engine form template of kind
  **`issue`** in the forms template store (T-0110), like ITP templates are kind `itp`
  (quality-and-inspection.md §ITP authoring): same field-type registry, sections, CEL
  `visible_if` / `required_if` / `calculated` / range rules, localised strings (edge §2.2),
  reference images, canonical hash and freeze. There is no separate issue field designer.
- **Revisions.** A template has `draft` and frozen (`published`) revisions. An issue pins the
  `template_revision_id` it was raised on and keeps it for life; new issues use the latest
  published revision. Adding, removing or changing a field, a step assignment or a flag default
  is a new revision. Publishing needs the project's QA authority (`qa_reviewer_persona`, default
  PER-06) or PER-09 for a project template, PER-13 for a tenant-library template (planner
  default, as for post-submit rules).
- **Revision metadata** (part of the frozen revision and its hash):
  `name` (localised), `issue_type` (`punch | ncr`), `category` (key from the category list
  below), `default_origin`, `default_visibility` (`internal | client`), `default_mdr_effect`
  (`blocks | register_only | excluded`), `completion_effect` (`blocks | none`, §6; planner
  default name for "unless the template says otherwise"), `default_severity`, and per section a
  **`step`**: punch `raise | rectify | verify`; NCR `raise | containment | disposition |
  root_cause | corrective_action | effectiveness`. Sections without a step belong to `raise`.
  A section's fields are filled during its step and read-only after the step is completed.
  An optional **5-whys** section in `root_cause` is an ordinary template section (system default
  NCR templates ship one, disabled by `visible_if` until the user ticks "use 5-whys"; planner
  default).
- **Layers, copied not overlaid (ADR 0004 §2 model).** (1) **System defaults** shipped with the
  module seed and with client packs (ADR 0003 §4, T-0108): at least one punch and one NCR template
  for `general` plus the pack's discipline templates; system-default punch templates (which use
  Cat A) default to `mdr_effect = blocks` (§6, TD-445); (2) **tenant library / client pack**,
  edited by PER-13; (3) **project copy**, made when the project is provisioned or when a library
  template is first used, with a `derived_from` link (TD-107 style), edited by the project.
  Library updates are offered as a new revision to adopt, never forced (ADR 0004 §4 analogy;
  planner default).
- **Categories.** A pack- and project-editable value list `oe_issues_category`
  (`key`, localised `label`, `is_active`, `sort_order`; same three layers). Seed (planner
  default): `painting`, `blasting`, `insulation`, `cladding`, `civil`, `mechanical`, `piping`,
  `welding`, `scaffolding`, `electrical`, `audit`, `general`; T-0326 adds `health_safety`
  (planner default). Keys are never re-keyed or deleted once used, only deactivated. The same
  list is the **discipline** list of inspection templates (quality-and-inspection.md §Template
  settings). Each category carries `allows_project_level` (bool, default false; seeded true for
  `audit` and `health_safety`, planner default): only issues of such a category may be raised
  manually without an anchor (§3.1).
- **Default template per category × type.** `oe_issues_default_template` (project,
  `category`, `issue_type`, template key). Raising an issue of category `painting` and type
  `punch` pre-selects the project's painting punch template; with no mapping the user picks from
  the templates of that type and category, then from `general` (planner default).

## 3. The issue record and its fixed system fields

`oe_issues_issue` (`BaseRecordMixin` + project RLS). Fixed fields exist on every issue whatever
the template; template answers live in `answers` JSONB, validated against the pinned revision.

| Field | Notes |
|---|---|
| `number` | per-type numbering pattern of the project (ADR 0003 §4 item 5); defaults `{project}-NCR-{seq:4}` and `{project}-PL-{seq:4}` (planner default). Assigned by the server, never reused; a voided issue keeps its number |
| `issue_type` | `punch | ncr`, from the template |
| `template_key`, `template_revision_id` | pinned at raise |
| `anchor_node_id` / `anchor_item_id`, `anchor_role` | exactly one of the two, or neither for a **project-level** issue (§3.1); `anchor_role` = role of the anchor's level (`work_anchor`, `inspection_anchor`, `physical`) or `item` (ADR 0003 §2), NULL when project-level. Never a BOQ anchor |
| `work_anchor_node_id` | resolved at raise by `asset_management.service.work_anchor_for` (T-0101); a path without a work-anchor node → 422 until one is picked or created (asset-and-tagging.md §Anchor invariants, TD-113). Retired nodes → 422. NULL for a project-level issue |
| `location_node_id`, links | project-level issues only: optional informational location (any live node) and 0..many links (`oe_issues_link`: node, Item, material, equipment) (§3.1) |
| `category` | from the template (may be changed to another active category only by re-templating before the first step completes; planner default: not editable) |
| `origin` | §1 |
| `severity` | `minor | major | critical` (planner default list); required for NCR, optional for punch |
| `punch_category` | `A | B | C`, punch only, required (master spec MOD-09: A hold, B post-completion, C information). Fixed system field because gates use it |
| `status` | §4 |
| `raised_by`, `raised_at` (`occurred_at` + time evidence offline, diff §6.4.1) | the raising user; never rewritten |
| `assigned_to_user_id` / `assigned_to_persona`, `due_date` | owner of the current step |
| `source_inspection_id` | the inspection it was raised from, if any (§8) |
| `upgraded_from_issue_id`, `escalated_from_issue_id` | links of §7 (and the reverse `successor_issue_id` on the source) |
| `visibility`, `mdr_effect`, `completion_effect` | effective flags (§5), copied from the revision at raise |
| `closed_by_inspection_id` | the re-inspection that verified the fix, when one was used (TD-167) |
| `client_seen_at`, `client_seen_channel` | nullable; set once by `issues.service.mark_client_seen(issue_ids, channel)` (idempotent, first value wins), never cleared (§5) |
| `void_reason`, `voided_by`, `voided_at` | §4.3 |

### 3.1 Project-level issues (user, 2026-11-21; resolves TD-443)

Audit and H&S findings may be **project-level**: no anchor and no work anchor, belonging to the
project (ADR 0003 amendment 2026-11-21; T-0326).

- **When.** An issue raised from a project-level inspection (any area other than `qa_itp`,
  quality-and-inspection.md §Project-level inspections) is project-level and inherits the
  inspection's location and links (§8). A manual raise may be project-level only when the issue
  template's category has `allows_project_level` (§2); every other manual raise still needs an
  anchor (422).
- **Location and links** follow the inspection rules: `location_node_id` (any live node),
  `oe_issues_link` (`issue_id`, `target_kind` `node | item | material | equipment`,
  `target_id`; `BaseRecordMixin` + project RLS), every target checked through its owning
  module's service; informational only (never roll up, never block or satisfy completion),
  searchable (`GET /api/v1/issues?location=…&linked=<kind>:<id>`) and shown on the linked record
  through `issues.service.linked_issues`; internal issues stay hidden from external callers
  there too (§5).
- **Gates.** A project-level issue never blocks work completion (it has no work anchor to
  block). MDR: see §6.
- RLS stays project-scoped; cross-project → 404.

Photos and other media are template answers (photo field with capture time and annotation,
quality-and-inspection.md §Photo answers). Every create, transition, assignment, flag override
and void is written to the T-0006 audit ledger (chain `issue:<id>`).

## 4. Workflow (fixed by type)

Every transition calls `run_checks(context, "issue.<type>.<transition>")` (ADR 0004 rule store,
system-default rule sets shipped by the `issues` module; project-editable unless locked) and
writes the ledger. Step fields required by the template are checked by the same call.

### 4.1 Punch
`open → rectified → verified → closed` (planner default state names).
- **Raise** → `open`: raise-step fields, Cat A/B/C, anchor, owner and due date.
- **Rectify** (the owner or PER-03/PER-04 of the project) → `rectified`: rectify-step fields
  (what was done, photos).
- **Verify** (PER-06, PER-07 within their site scope, or the client PER-15 for a client-origin
  punch; planner default) → accepts or rejects. Reject needs a reason and returns the punch to
  `open`. Accept may link the re-inspection that proved the fix (`closed_by_inspection_id`,
  TD-167); a template may require it (`required_if` on the verify step via a rule in
  `issue.punch.verify`).
- **Close.** Verification closes the punch (`verified` → `closed` in the same action; planner
  default, `verified` is kept as a state for templates whose rule set requires a separate close).
- **Verifier ≠ resolver:** the user who marked it rectified can never verify it, whatever
  `allow_self_approval` says (enforced in `service.py`, not the router).

### 4.2 NCR
`containment → disposition → root_cause → corrective_action → effectiveness → closed`.
- **Containment** (at raise): immediate action to contain the non-conformance (template fields:
  what was quarantined, tagged, stopped).
- **Disposition:** `scrap | rework | repair | use_as_is` (master spec MOD-09) with
  `concession_ref` (the client's concession / deviation number). Default rule (not locked,
  planner default): `use_as_is` and `repair` need a `concession_ref`. Recorded by QA authority.
- **Root cause:** `root_cause_category` (pack/project value list; seed `workmanship`,
  `material`, `design`, `procedure`, `equipment`, `training`, `subcontractor`, `other`; planner
  default) and a `root_cause_statement`; 5-whys is an optional template section (§2).
- **Corrective / preventive actions:** child records `oe_issues_action` (`BaseRecordMixin` +
  RLS): `kind` (`corrective | preventive`), `description`, `owner_user_id`, `due_date`,
  `status` (`open | done | cancelled`), `completed_by`, `completed_at`, `evidence` (attachment
  ids, T-0016), `cancel_reason`. The step completes when at least one corrective action exists
  and every action is `done` or `cancelled` with a reason (planner default). For a `rework` or
  `repair` disposition the default rule set also requires a linked passed re-inspection
  (`closed_by_inspection_id`) before effectiveness verification (T-0116 "re-inspection
  close-out"; not locked).
- **Effectiveness verification:** an **attested formal sign-off** of kind **`ncr_close_verify`**
  (quality-and-inspection.md §Signing attestation): the verifier re-authenticates, accepts the
  intent statement ("I verify that the corrective actions of NCR <number> are implemented and
  effective…") and the sign-off ledger (T-0119) keeps the attestation. Verifier: the project's
  QA authority. **Verifier ≠ resolver:** nobody who recorded the disposition or completed a
  corrective action may verify (enforced in `service.py`). Until the attestation backend ships
  (T-0289/T-0291) the sign-off is a plain sign-off ledger entry, like the other formal kinds.
  Ineffective → back to `corrective_action` with a reason.
- **Client engineering review** of the master spec state machine is the disposition's
  `concession_ref` plus, for client-visible NCRs, client sign-off through the portal (E-07-03).

### 4.3 Terminal states for both types
`closed`, `void` (any non-terminal state; mandatory reason; QA authority or PER-09; the record
and number stay), `upgraded` (punch only, §7) and `escalated` (internal issue, §7). "Open" in
gates, tiles and registers means any non-terminal state.

## 5. Flags: visibility and MDR effect (user, 2026-11-21)

- **Defaults on the template**, copied onto the issue at raise:
  - `visibility`: `internal` (our people only) | `client` (client-side accounts and the client
    portal can see it).
  - `mdr_effect`: `blocks` (an open issue blocks the MDR compile) | `register_only` (listed in
    the MDR NCR/concession register, does not block) | `excluded` (not in the MDR). The dossier
    goes to the client, so `internal` issues are never printed in it; an internal issue with
    `blocks` still blocks the compile while open (planner default; documents-and-mdr.md §NCR /
    concession register). The MDR gate rule is stated once in §6 (TD-445).
  - `completion_effect` (planner default name): `blocks | none`; default `blocks` for NCR
    templates. For punch the completion gate is Cat A (§6) and `completion_effect` is ignored.
- **Per-issue override** only by QA authority or the project's PER-09, with a mandatory reason,
  audited (ledger entry with old value, new value, reason, actor, direction); shown in the issue
  history. This is an explicit user exception to PER-09's SoD block on loosening `check` rules
  (ADR 0004 §5): PER-09 changes an issue's flag, never a rule, and only within these guardrails
  (user, 2026-11-21):
  - **Locked origins.** An issue with `origin = client` or `external_audit` can never be set
    `visibility = internal` or `mdr_effect = excluded` (422).
  - **Once client-seen, never internal again.** `client_seen_at` is set by
    `issues.service.mark_client_seen(issue_ids, channel)` (idempotent, first value wins) the
    first time an `is_internal = false` account reads the issue through the issues API or
    receives it in the offline pull (T-0314), or a client-facing output includes it: portal
    (E-07-xx, channel `portal`), an inspection PDF exported by an external account
    (T-0158/T-0159, `pdf_export`), shared saved views reached by an external account outside the
    portal (E-01-01, channel `saved_view`), and the MDR (`mdr`), where `mdr_register` marks its rows itself (planner default for
    "has been client-visible"). Every such consumer reads issues through the service and calls
    it. After that `client → internal` is refused (422).
  - **No widening by override.** `internal → client` is not an override; it goes through "Raise
    external NCR from this" (§7) (422).
  - **Loosening** — `mdr_effect` towards `excluded` (`blocks → register_only | excluded`,
    `register_only → excluded`), `completion_effect` `blocks → none`, or `client → internal`
    before `client_seen_at` — needs the reason, **notifies the project's QA authority**
    (`qa_reviewer_persona`, high priority), is marked `loosening` in the ledger entry, filterable
    in the audit trail and **counted in QMS KPIs** (loosening overrides per project and period).
  - **Tightening** (towards `blocks`) needs only the reason.
- **Client-raised issues are always `client`** (`origin = client` or raised by an
  `is_internal = false` account): visibility cannot be overridden to `internal` (422).
- **Clients never see internal issues.** For every `is_internal = false` caller (client PER-15,
  subcontractors, third-party inspectors; diff §4.9 external parties) internal issues are absent
  from lists, counts, search, refs and exports and answer **404** by id, like the
  `internal_commercial` boundary (T-0295). Links that point to or from an internal issue
  (`escalated_from_issue_id`, `upgraded_from_issue_id`, `successor_issue_id`) are omitted for
  them. Planner default: an internal issue cannot be assigned
  to an external account (422) (TD-442).
- **Examples:** internal audit (we audit ourselves) → `internal` + `excluded`; external or
  client audit, and client-raised NCRs → `client` + `blocks`.
- **Guardrails (accepted by the user):** the MDR gate and the work-completion gate are separate —
  an internal NCR still blocks `work_anchor.complete` by default unless its template sets
  `completion_effect = none`; internal issues still count in QMS KPIs and internal dashboards;
  gates are CEL rule sets over these flags (§6), not hard-coded.

## 6. Gates (CEL rule sets, ADR 0004 locked defaults)

The issues service exposes the open issues of a scope (a work-anchor node with its descendants,
its Items and its lineage predecessors, asset-and-tagging.md §Anchor invariants) with their
type, `punch_category`, `completion_effect`, `mdr_effect` and status as rule context. Rules:

| Rule set (owner) | Locked default rule |
|---|---|
| `work_anchor.complete` (T-0124) | no open punch with `punch_category = A`; no open NCR with `completion_effect = blocks` |
| `mdr.compile` (E-05-05) | no open issue with `mdr_effect = blocks` in the dossier scope (MDR gate rule below; documents-and-mdr.md §Gating invariant) |
| hydrotest pack release (E-06b-01) | no open Cat A punch on the test boundary (master spec MOD-10) |
| `weld.accept` (welding, Phase 2) | no open NCR anchored to the weld's Item (R-WPS-4) |
| `closeout.final_handover` (E-04-10, planner default name) | no open punch with `punch_category = B` (or A) on the handed-over scope: master spec MOD-09 "Cat B must be rectified prior to final contractual handover" |

**Project-level issues (user, 2026-11-21; §3.1):** never in `work_anchor.complete`, hydrotest
pack release, `weld.accept` or `closeout.final_handover` facts (nothing to block). With
`mdr_effect = blocks`, an open project-level issue **without a location** blocks every MDR of the
project; one **with a location** blocks only the MDRs whose dossier scope covers that location —
the location node is the scope node, inside it, or an ancestor of it (scopes overlap; lineage
through the `asset_management` service; planner default, conservative). `mdr_register` lists
project-level issues on the same rule.

**MDR gate rule (planner default, TD-445):** `mdr_effect` alone governs the MDR gate: `mdr.compile` blocks while any issue in the dossier scope with `mdr_effect = blocks` is open, and nothing else about an issue (type, `punch_category`, visibility) enters it; this replaces the earlier "open Cat A punch" clause. Punch templates that use Cat A (all system-default punch templates) default to `mdr_effect = blocks`. An open Cat A punch always blocks work completion (`work_anchor.complete`) regardless of `mdr_effect`.

Projects may add stricter rules to any of these sets. Upgraded and escalated sources are not
open; their successors count instead.

## 7. Upgrade punch → NCR and "Raise external NCR from this" (user, 2026-11-21)

Both create a **new record**; nothing is converted, re-dated or rewritten.

- **Upgrade punch → NCR** (QA authority): creates an NCR from the project's NCR template for the
  punch's category (§2 default mapping), dated now and raised by the clicking user; answers are
  copied where field keys match; photos carry over **with their real capture timestamps and
  hashes** (same attachment ids); the punch closes as `upgraded`; both are linked
  (`upgraded_from_issue_id` / `successor_issue_id`). **An upgrade never widens visibility:** the
  NCR keeps the punch's visibility (internal punch → internal NCR, client punch → client NCR),
  whatever the NCR template's `default_visibility` says; widening to `client` happens only through
  "Raise external NCR from this" (planner default, PR #145 review). Upgrade links that touch an
  internal issue are hidden from `is_internal = false` callers (§5).
- **Raise external NCR from this** (button + confirm dialog; on an `internal` issue of either
  type; QA authority or PER-09): creates a **new client-visible NCR** dated now, raised by the
  clicking user, from the NCR template of the category (the user may pick another NCR template).
  The dialog lists the description, the template answers with matching keys and the photos; the
  **user picks which carry over**. Internal discussion (comments), RCA notes, actions and history
  **never** carry over. The internal issue closes as `escalated` with an **internal-only** link
  to the new NCR (`escalated_from_issue_id` on the new NCR is internal-only too: external callers
  never see it). The hash-chain ledger is only appended to.
- No downgrade NCR → punch. Mistakes are voided with a reason (§4.3).

## 8. Raising from inspections and audits (user, 2026-11-21)

- **Linked issue types (user, 2026-11-21).** An inspection template's settings link **1..many
  issue templates** (`linked_issue_templates`, part of the inspection revision hash;
  quality-and-inspection.md §Template settings; T-0327). When an issue is raised from an
  inspection: **exactly one** linked → it is used; **several** → the user picks one of them;
  **none** → the category × type default of §2 for the template's discipline (no discipline →
  `general`). For an **audit** template with no links the fallback is the `audit` category
  templates of the type whose `default_origin` matches the audit scope (`internal_audit` /
  `external_audit`); one → used, several → the user picks (planner default).
- **Post-submit action `create_issue`** (quality-and-inspection.md §Post-submit automation):
  `action_config` names **one of the template's linked issue templates** (422 otherwise; a
  template without linked issue types cannot use `create_issue`), plus optional
  `punch_category`, `severity` and an answer mapping (inspection field key → issue field key).
  The type comes from the named template. It runs when the inspection reaches its **result**
  (`INSPECTED_PASSED` / `INSPECTED_FAILED` after review, T-0150; for an informal record, at
  submit, which is final), not at `SUBMITTED`, so a returned inspection never raises issues
  (planner default). The new issue anchors to the inspection's anchor (or is project-level with
  the inspection's location and links, §3.1), links `source_inspection_id`, carries the
  inspection's photos selected by the mapping, and has `raised_by` = the approving reviewer (the
  submitter for an informal record) (planner default) with `source = "automation"` in the
  ledger.
- **Auto-raise from `INSPECTED_FAILED`** (T-0111 lifecycle `INSPECTED_FAILED → (Cat A punch |
  NCR) → RE_INSPECT`) goes through `create_issue`: a failed painting inspection raises the
  painting punch or NCR. If no rule raised an issue, the approve-as-failed dialog requires the
  reviewer to raise one (template chosen by the linked-issue-type rule above);
  `RE_INSPECT` needs at least one linked issue (planner default). Informal records have no
  approve step, so nothing is forced (planner default).
- **Audits.** A site audit is an inspection template with the **audit** tick (any area, e.g.
  Health & Safety). Its findings raise issues through `audit` category issue templates:
  internal scope → `origin = internal_audit`, `internal` + `excluded` defaults; external scope →
  `origin = external_audit`, `client` + `blocks` defaults (§1, §5 locked origins). The
  internal audit finding templates are seeded for Pilot-0 (T-0322); the external ones with
  T-0327.
- **Manual raise** (not from an inspection) filters templates by type + category; the
  project's default template per category × type is pre-selected.
- **No work anchor on the path** (TD-443, resolved by the user 2026-11-21): findings of
  project-level inspections (audits, H&S checks, pre-starts) are project-level issues (§3.1), so
  they need no work anchor. What remains is a `qa_itp` inspection on an `inspection_anchor` node
  whose path has no work anchor (e.g. a condition survey, ADR 0003 Amendment 5): manual raise
  answers 422 until a work-anchor node is picked or created (§3), and a `create_issue` action
  does not roll the result back: it is logged `skipped` (`NO_WORK_ANCHOR`) and the reviewer and
  QA authority are notified to raise it manually (planner default kept).

## 9. Offline (user, 2026-11-21)

Issues and issue templates follow the inspection sync model (diff §6.4): templates download with
the project reference data; issues are raised and stepped offline with client-generated UUIDv7
ids and the time evidence of diff §6.4.1; **the server assigns the number at sync** (the device
shows "number pending"). A transition that fails `run_checks` at sync fails that mutation, the
issue keeps its earlier state and the user is notified. Attested sign-offs follow §Signing
attestation's offline branch.

## 10. Spatial pins on drawings

`oe_issues_pin` (`BaseRecordMixin` + RLS): `issue_id`, drawing `file_id` (T-0016) and
`drawing_revision` label, `page`, normalised `x`, `y` (0–1, origin top-left, as photo
annotations), optional `label`. Several pins per issue. Pins reload on the drawing and in the
issue; the drawing viewer itself is documents (documents-and-mdr.md §Drawing viewer). A pin on a
superseded drawing revision stays on that revision (planner default).

## 11. Out of scope

- **Client-asset findings register** (TD-106, TD-117): stays a separate register owned by
  another department, not trialled yet. It could later be built as issue templates.
- MDR compile itself (E-05-05) and the client portal NCR input/sign-off (E-07-03) consume this
  module through its service.

## 12. Tasks

| Task | Scope |
|---|---|
| T-0311 | issue templates (kind `issue`), revision metadata, categories, default template per category × type, layers and project copy |
| T-0312 | issue record, fixed fields, numbering, anchors, punch workflow, void, offline create, RLS |
| T-0313 | NCR steps, corrective/preventive actions, effectiveness sign-off (`ncr_close_verify`) |
| T-0314 | visibility / MDR / completion flags, `client_seen_at` + `mark_client_seen`, external-caller filtering, gate context providers, `mdr_register` |
| T-0323 | per-issue flag override API with guardrails (after Pilot-0) |
| T-0315 | upgrade punch → NCR, raise external NCR from an internal issue |
| T-0316 | `create_issue` post-submit action (names a linked issue type), auto-raise from `INSPECTED_FAILED`, issues from project-level inspections |
| T-0317 | spatial pins on drawings |
| T-0318 | screens: issues register, raise (online/offline), punch detail (Pilot-0) |
| T-0319 | screens: NCR detail workflow up to the `ncr_close_verify` sign-off (Pilot-0) |
| T-0320 | screens: Issues menu → issue templates (Punch / NCR presets, list, categories, defaults, library copy) |
| T-0321 | screens: flag override, upgrade and raise-external dialogs; signing ceremony for `ncr_close_verify` (after Pilot-0) |
| T-0322 | seed the Pilot-0 starting issue templates as tenant defaults (Pilot-0) |
| T-0326 | project-level issues: no anchor, location, links, `allows_project_level` categories, gate and MDR scope rules (§3.1, §6) |
| T-0327 | linked issue types on inspection templates, raise-time resolution, audit fallback by scope, external audit seed templates (§8) |
| T-0330 | screen: raise an issue from an inspection with the linked-issue-type picker (§8) |
| E-01-03 | shared template designer for inspection and issue templates (after Pilot-0) |
