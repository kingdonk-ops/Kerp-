# Feature Filler — review

**TL;DR.** The KERP spec is strong on *architecture* (anchor model, verification engine, module contracts) but thin on the *concrete surface a deployment needs on day one*: the 13 personas are named but never turned into capability tiers + module grants; `oe_users_user` and `oe_contacts_contact` are one-line field sketches; there is no persona×module access matrix; and the high-traffic registers (Inspections/ITP, NCR, Punch, Weld, Asset tree, Documents/VFS, Progress, Deliveries/Gate, Inspection Requests) have no list-view columns, filters, create-form fields or row actions. This document fills all of that in concretely so the planner can lift it into tasks T-0007/T-0008/T-0025 (RBAC + admin UI), T-0111/T-0112/T-0129 (inspections UX), E-01-01 (inspection_requests), E-03-02 (Gate Delivery), and the portal epics E-07-01/02/03. **My single most important recommendation: freeze the persona→capability-tier→module-grant matrix (§3 below) as a seeded config pack before any register UI is built — every list view, create form and gate banner reads from it, and retrofitting scope is the costliest rework on the board.**

Everything here stays inside the anchor model (work→RSW, inspection→Item), the single-company doctrine (scope = Project Settings, not i18n), the money-is-a-mirror boundary, and the dynamic-forms engine (`forms`/`item_types` one-engine, pending TD-002).

---

## 1. Account types / personas (expanded)

The spec (`DEVELOPMENT-PLAN.md` §4, `FINAL-PLAN.md` §2) lists 13 personas + scope but gives them no capability tier, no module grant, and omits the two edge personas that actually generate most writes (field worker) and most external interaction (client). Below: **purpose · default capability tier · scope · home modules**. Capability tiers are the spec's `viewer < editor < manager < admin` (`users` [F2]); a persona's tier is its *default* — per-user overrides and per-module grants still apply (`users` [F4]).

| # | Persona (business-role preset) | Purpose (one line) | Default tier | Scope | Primary modules it touches |
|---|---|---|---|---|---|
| 1 | **Operations Management** | Portfolio health; cross-project standards & escalations | manager (read-wide) | All projects + portfolio dashboard | `dashboard`, `bi_dashboards`, `reporting`, `project_controls`, `risk`, read-all registers |
| 2 | **Project Manager (PM)** | Owns delivery of the projects they manage | manager | All projects **they manage** | `schedule`, `progress`, `variations`, `changeorders`, `rfi`, `reporting`, `inspection_requests` (delay dashboard), full registers |
| 3 | **Site Superintendent** | Runs the whole site day-to-day | manager | Everything for **their project** | `schedule_advanced` (look-ahead), `field_time`, `daily_diary`, `site_logistics`, `progress`, `tasks`, `safety` |
| 4 | **Supervisor** | Leads a crew/discipline; completes workpack stages | editor | Their project (usually one discipline/area) | `workpacks` (stage sign-off), `tasks`, `progress`, `field_diary`, `fieldreports`, `punchlist` |
| 5 | **Planner** | Keeps the field programme & look-ahead honest | editor | Their project | `schedule`, `schedule_advanced`, `progress`, `resource_summary`, P6 import/export |
| 6 | **QA/QC Lead** | Owns the ITP regime; releases hold points | manager (quality) | Their project | `qms` (ITP), `construction_control` (hold gates), `inspections`, `ncr`, `wps`, `inspection_requests` |
| 7 | **Inspector** | Raises & closes inspections, witnesses holds | editor | **Their site only** (Asset `Site` node) | `inspections`, `qms` ITP items, `ncr`, `punchlist`, `inspection_requests`, `workpacks` (QC gate) |
| 8 | **Stores Person** | Books deliveries, gate-receives, meters stock, OS&D | editor | Their project | `site_logistics` (Gate), `site_inventory`, `procurement` (GR), `cutting` |
| 9 | **Admin** | User/role/scope admin, project settings, module enablement | admin (ops, not quality-gate) | Their project | `admin`, `users`, `teams`, `projects` (settings/module enablement), `saved_views` |
| 10 | **Purchasing & Procurement** | Vendor/PO oversight; PO-line mirror reconcile | editor (read-wide) | All projects (read); edit procurement | `procurement`, `supplier_catalogs`, `subcontractors`, `contacts`, `site_inventory` (read) |
| 11 | **H&S Manager** | Safety assurance across projects | manager (safety) | All projects (overview, read); edit HSE | `safety`, `hse_advanced`, `credentials`, incident/observation registers |
| 12 | **HR** | People/credential/competency master | editor (people) | All projects (read) | `contacts` (staff), `credentials`, `resources` (skills/certs), `field_time` (read) |
| 13 | **Quality Manager** | Quality governance, NCR trend, audit across projects | manager (quality, read-wide) | All projects (overview, read) | `qms` audits, `ncr`, `inspections` (read), `reporting`, `compliance` |
| 14 | **Field Worker (magic-link)** | Mobile/offline capture: diary, hours, photos, delivery, workpack stage | editor (grant-scoped) | Single project; **per-module grants** only | `field_diary`, `field_time`, `workpacks`, `site_logistics` Gate, `progress` — via `oe_field_module_grant` + `_magic_link` (`field_diary` [F4][F5]) |
| 15 | **Client Portal user (external)** | Read registers; co-sign; book witness visits; raise/sign-off client NCR | portal-scoped (not a staff tier) | Granted project(s) + **asset subtree** | `portal` v1/v2/v3 over `inspections`/`qms`/`ncr`/`signing`/`review_authority`/`meetings`/`inspection_requests` |

**Gaps this fills:** personas 14–15 are *the two highest-volume personas in the field* yet appear only inside `field_diary` and `portal` module specs, never in the RBAC matrix. They need first-class preset rows so `cases` can seed their playbooks (E-06-07) and so `verify_project_access` (`teams` [F3]) resolves them. Also: **"QA/QC Lead" and "Quality Manager" overlap** — I treat QA/QC Lead as *project-level hold-point authority* and Quality Manager as *portfolio read + audit*; the access matrix (§3) makes the split explicit so the planner doesn't ship two identical presets.

**Employer-kind dimension (orthogonal to persona).** Staff vs subcontractor is modelled on `oe_contacts_contact.contact_type` and `oe_welder.employer_kind`/`oe_field_time_line.employer_kind` but **not on the user preset**. A subcontractor supervisor is persona *Supervisor* with `employer_kind=subcontractor`; the access matrix should let a project toggle "subcontractor users cannot view other subcontractors' rates/ratings" — today nothing carries that flag on `oe_users_user`. Add `employer_kind` + `employer_contact_id` to the user (see §2).

---

## 2. User & contact profile fields (concrete column list)

The spec gives `oe_users_user` as *"(email, name, status, auth refs, business_role, capability_tier, project/site scopes, module_access)"* and `oe_contacts_contact` as a longer but still sketchy line. A real deployment needs the columns below. All tables carry `BaseRecordMixin` (entity URI, `tenant_id`, hash, provenance, revision) + RLS per hard-rule 2 — those are **not** repeated here.

### 2.1 `oe_users_user` — the platform-login identity

| Group | Columns |
|---|---|
| Identity | `email` (unique per tenant), `first_name`, `last_name`, `display_name`, `avatar_document_id`, `phone`, `locale` (resolves to Project Settings, not i18n), `timezone` |
| Auth | `auth_provider` [`local|entra|magic_link`] (TD-003 seam), `password_hash` (local only), `password_changed_at` (token-invalidation, mirrors as-built `User.password_changed_at`), `mfa_enabled`, `last_login_at`, `failed_login_count`, `locked_until` |
| Role & tier | `business_role` [the 15 presets], `capability_tier` [`viewer|editor|manager|admin`], `is_portfolio_scope` bool (persona 1/10–13) |
| Scope | `home_project_id`, `project_scope` (array of project_id, NULL = all for portfolio tiers), `site_scope` (array of `oe_asset_node` id, `level='site'` — Inspector), `all_projects_managed` bool (PM) |
| Module access | `module_access` jsonb (`{module_key: tier_override}`), `denied_modules` array — the per-user grant layer (`users` [F4]) |
| Employer | `employer_kind` [`staff|subcontractor|client|third_party`], `employer_contact_id → contacts` (NULL for direct staff), `trade_primary`, `disciplines` array |
| Competency link | `contact_id → contacts` (one directory row backs every user — D7 fold), `credential_ids` denormalised cache for the eligibility banner |
| Preferences | `default_saved_view_id`, `notification_prefs_id → notifications._preference`, `nav_pillar_order` jsonb, `density` [`comfortable|compact`], `theme` [`system|light|dark`] |
| Status | `status` [`invited|active|suspended|deactivated`], `invited_by`, `invited_at`, `accepted_at`, `deactivated_at` |

### 2.2 `oe_contacts_contact` — party master (people + companies, D7 fold)

The spec line is close; the concrete set a deployment needs, grouped:

| Group | Columns |
|---|---|
| Classification | `contact_type` [`client|contractor|staff|supplier|subcontractor|authority|third_party`], `party_kind` [`person|company`], `parent_company_id → contacts` (person→company grouping), `is_platform_user`, `user_id → users` |
| Person | `first_name`, `last_name`, `job_title`, `role_on_project`, `is_primary_contact` |
| Company | `company_name`, `legal_name`, `vat_number`, `registration_number`, `company_type` |
| Contact | `primary_email`, `secondary_email`, `primary_phone`, `mobile`, `website`, `address` (jsonb: line1/2, city, subdivision, country_code, postcode), `preferred_language` |
| Qualification | `certifications` jsonb (feeds `eligibility`), `insurance` jsonb (`compliance_docs` link), `prequalification_status` [`not_assessed|pending|approved|rejected`], `qualified_until`, `trade_categories` array |
| Commercial (mirror only) | `payment_terms_days`, `currency_code` (Project Settings default), `external_source`+`external_id` (ERP vendor master — hard-rule 4), **no priced balances** |
| Organising | `tags` array, `module_tags` array, `custom_properties` jsonb (dynamic schema), `is_active`, `blocked` + `blocked_reason` |
| Trail | `_note`(author_id, body, at), `_file`(document_id, version) — the interaction log |

**Design note:** keep `oe_users_user` and `oe_contacts_contact` as **two rows linked 1:1** (`user.contact_id`), not merged. A user is a login; a contact is a directory party. Every staff user has a contact row (so HR/credentials/competency attach to the contact and the eligibility banner reads one place), but suppliers/authorities have contact rows with **no** user. This matches `wps.oe_welder.contact_id` and `teams._roster_member.contact_id` already pointing at `contacts`, not `users`.

---

## 3. Access matrix (persona × module-group × capability)

Capabilities: **V** view · **C** create · **E** edit · **A** approve/release-gate · **S** sign/co-sign. Blank = no access by default (grantable per-user). "**site**" = limited to the user's Asset `Site` subtree; "**proj**" = their project(s); "**mgd**" = projects they manage; "**R**" = read-only across all projects (portfolio).

| Persona | Asset backbone | Inspections/ITP | NCR | Punch | Weld (wps) | Documents/VFS | Progress/Schedule | Logistics (Gate/Stock/PO) | Portal admin | RBAC/Admin |
|---|---|---|---|---|---|---|---|---|---|---|
| Operations Mgmt | V(R) | V(R) | V(R) | V(R) | V(R) | V(R) | V(R) | V(R) | V | — |
| PM | VCE(mgd) | VCEA | VCEA | VCE | VE·A(accept) | VCES | VCEA | VCE | VCE | — |
| Site Superintendent | VCE(proj) | VCEA | VCEA | VCE | VE | VCE | VCEA | VCE | V | — |
| Supervisor | VE(proj) | VCE | VC | VCE | V | VC | VCE(entry) | VC(request) | — | — |
| Planner | VE(proj) | V | V | V | V | VC | **VCEA**(schedule) | V | — | — |
| QA/QC Lead | VCE(proj) | **VCEA**(release hold) | VCEA | VCEA | VCEA(accept weld) | VCES | V | — | VE | — |
| Inspector | V(site) | **VCE·A**(site holds) | VC(site) | VCE(site) | VC·E(nde)(site) | VC(site) | — | — | — | — |
| Stores Person | V(proj) | — | VC(material NCR) | V | — | VC(dockets) | — | **VCEA**(Gate/OS&D/stock) | — | — |
| Admin | V(proj) | V | V | V | V | VE(folders) | V | V | VCE | **VCEA**(users/scope) |
| Purchasing | V(R) | — | V | — | — | V | — | **VCE**(PO/vendor) | — | — |
| H&S Manager | V(R) | V(R) | V(R) | V | V(certs) | V(R) | V(R) | V | — | — |
| HR | V(R) | — | — | — | V(quals) | V(R) | V(R)(hours) | — | — | VE(people only) |
| Quality Manager | V(R) | V(R)·A(audit) | VEA(R) | V(R) | V(R) | V(R) | V(R) | — | V | — |
| Field Worker | V(grant) | C·E(grant) | C(grant) | CE(grant) | — | C(photos) | C(entry) | C(Gate, grant) | — | — |
| Client Portal | V(subtree) | V·**S**(cosign v2) | V·C·**S**(v3) | V | V | V(current rev) | V | — | — | — |

**Where scope bites (the rows that matter most):**
- **Inspector is site-scoped, not project-scoped** — the one persona whose writes must be filtered to an Asset `Site` node, not a project. The `scope.py`-style loader criteria (as-built ADR 0014) injects `project_id IN scope`; KERP additionally needs `site_node_id IN site_scope` on inspection/NCR/punch reads for Inspectors. **This is new plumbing** — flag it (§Conflicts).
- **Hold-point release (A on construction_control) is QA/QC Lead + Inspector(site) only** — Supervisor can *complete a stage* but cannot *release the gate* (`workpacks` supervisor→QC gating). The matrix must prevent a Supervisor preset from ever getting `A` on `inspection.close`/`rsw.complete`.
- **Weld accept (A on wps)** is QA/QC Lead + PM only; Inspector can record NDE (`C/E`) but R-WPS-4 acceptance is a gated manager action.
- **Subcontractor visibility**: a subcontractor-employer user with persona Supervisor should **not** see `subcontractors._rating` or other subbies' agreements. Add a project toggle `restrict_cross_subcontractor` read filter.
- **Admin ≠ quality authority**: project Admin can manage users/scope/folders but has **no** `A` on quality gates — separation of duties. Spec's single "admin" tier conflates these; the matrix splits "admin (ops)" from "manager (quality)".

This grid should ship as a **seeded `oe_asset_config_pack`-style role preset pack** consumed by `users._role_preset`, editable in the Admin → Users & Access screen (T-0025), so a deployment tunes it without code.

---

## 4. Per-screen UX detail (high-traffic registers)

Format per register: **list columns · filters · saved-view facets · search · sort · row actions · create/edit form**. All strings via `t()` (hard-rule 9); all lists are `saved_views`-backed (`saved_views` [F1]); all create forms that are *inspections/ITP/permit/test* render through the `forms`/`item_types` one-engine (TD-002) with `template_revision_id` pinned.

### 4.1 Inspections register (`inspections` / global `/inspections`, T-0112/T-0129)
- **Columns:** Inspection # · Type · Title · Anchor (Asset/sub-Asset/RSW/Item breadcrumb) · Discipline · Inspector · Date · Status · Result (pass/fail/pending) · Hold/Witness badge · Open-NCR count · geo pin.
- **Filters:** project · site/area/asset subtree (tree picker) · discipline · type · status · result · inspector · date range · "has open NCR" · "overdue witness" · party role.
- **Saved-view facets:** *My inspections today · Open holds on my site · Failed this week · Awaiting client witness · By discipline.* Inspector's default view is **site-scoped** automatically.
- **Search:** inspection #, title, asset tag/code, checklist value (via `file_search` tsvector over responses).
- **Sort:** date desc (default), status, discipline, anchor path.
- **Row actions:** Open · Raise NCR · Request inspection (→ `inspection_requests`) · Release hold (gated, QA/Inspector) · Export PDF · Clone from template.
- **Create/edit form (one-engine):** Type (→ pins `forms` template kind=inspection) → Anchor picker (**required**, RSW *or* Item, enforces anchor model) → Discipline (default from anchor) → Inspector (default self; competency-checked, `inspections._assignment.competency_ok`) → Date → **dynamic checklist** (template fields: measurement+unit, photo, signature, instrument-ref → eligibility gate) → Result → Hold/witness point flags (*conditional*: show required-party-role when hold). **Eligibility banner** renders inline before sign-off (`inspections` [F3]).

### 4.2 ITP builder & register (`qms` [F1], T-0113)
- **Columns:** ITP # · Title · Discipline · Asset/RSW scope · # control points · # hold/witness · % released · Linked template · Status · Co-sign status (portal v2).
- **Filters:** discipline · asset subtree · status · "has unreleased hold" · template.
- **Saved views:** *Unreleased hold points · ITPs pending client co-sign · By discipline.*
- **Row actions:** Open · Add control point · Release hold point (gated) · Request co-sign (portal) · Clone template · Export.
- **ITP builder form:** header (title, discipline, acceptance standard) → ordered **control points** list, each: control-point description · acceptance criteria (template-linked) · intervention point type [`H(old)|W(itness)|S(urveillance)|R(eview)`] · required party role · signatory roles · verification method. Hold points render the "blocks progress" toggle. Builder is the `qms._itp_template` → instance clone used by `workpacks` stage generation.

### 4.3 NCR register (`ncr`, T-0115)
- **Columns:** NCR # · Type · Severity · Anchor (Asset/RSW/Item) · Source [internal/client] · Root-cause category · Status · Schedule-impact days · Raised-by · Linked inspection · Days open.
- **Filters:** severity · type · status · source · discipline · asset subtree · "client-raised" · overdue · schedule-impacting.
- **Saved views:** *Open critical · Client-raised awaiting response · Overdue corrective actions · By root cause.*
- **Search:** NCR #, description, asset tag, corrective-action text.
- **Row actions:** Open · Assign corrective action · Link to change order · Request client sign-off (portal v3) · Close (gated `ncr.signoff`) · Reopen (logs finding).
- **Create/edit form:** Type · Severity (*conditional*: critical shows schedule-impact required) · Anchor (required) · Source (internal default; client via portal) · Description · Root cause + category (*required to close*) · Corrective action + Preventive action · Verification method · Linked inspection/weld/change-order · photos/geo.

### 4.4 Punch / snag (`punchlist`, T-0116)
- **Columns:** # · Title · Priority · Status · Anchor + drawing pin (document/page/x,y) · Trade · Assigned-to · Due · Reopen count · photo thumb · geo.
- **Filters:** priority · status · trade · assignee · overdue · "on this drawing" · asset subtree.
- **Saved views:** *My open punch · Overdue · Reopened · By trade/area.*
- **Row actions:** Open · Assign · Resolve · Verify (separate person) · Reopen · Drop pin on drawing · Export snag list.
- **Create form:** Title · Anchor *or* drawing-pin (document_id+page+x/y) · Priority · Trade · Assignee · Due · photos · location (geo). Resolve→Verify is a two-person flow (verifier ≠ resolver, mirrors `inspection.signoff`).

### 4.5 Weld register (`wps`, T-0207)
- **Columns:** Weld # · Item/Asset anchor · WPS (code+rev) · Welder (stamp) · Qualification status badge · Joint/size/thickness · NDE required/types · NDE result · Status [`fit_up→welded→nde_pending→accepted|rejected|cut_out`] · Welded-at · Continuity flag.
- **Filters:** status · WPS · welder · discipline · NDE outcome · "continuity at risk" · "awaiting NDE" · asset subtree.
- **Saved views:** *Awaiting NDE · Rejected/cut-out · Continuity watch (expiring) · By welder · By WPS.*
- **Row actions:** Fit-up → Weld (checks R-WPS-1/2/3 at assign) · Record NDE · Accept (gated R-WPS-4) · Reject · Cut-out · Export weld register · Open continuity-watch.
- **Create/edit form:** Anchor Item (type=weld, required) · Weld # · **WPS (required, must be `approved`)** · **Welder qualification (required, `valid`, range covers weld — enforced, blocked with finding)** · joint/size/thickness/diameter/material · NDE required + types (*conditional*). Assignment is **blocked** with a logged finding if the welder's qualification is expired/out-of-range/continuity-lapsed (`welder.assign` rule set).

### 4.6 Asset tree & Asset-Details (`asset_management`, T-0127)
- **Tree view:** recursive Site→Area→Asset→sub-Asset→RSW with level icons, status chip, %-complete bar, discipline band on RSWs, external-key badge (P6/Excel/manual). Drag-to-move calls `POST /assets/nodes/{id}/move` (path/depth maintained).
- **Tree filters:** level · discipline · status · external_source · "has open NCR/hold" · "unmatched P6 rows".
- **Search:** name, code, asset_tag, WBS code, Activity ID.
- **Asset-Details tabs** (designer-driven, spec'd): Overview · RSWs · Items · **Inspections & ITPs** (aggregates descendants) · Files (VFS folder) · Tasks · MOC. Each tab is a filtered register scoped to the node subtree.
- **Create/edit node form:** Level (constrains parent/children per anchor rules) · Name · Code · Category/Type (dynamic schema → renders type's field layout) · Discipline (RSW only) · external keys (WBS/Activity — *required when source=p6*, `asset.p6_fields`) · planned dates · budgeted units · primary resource · subcontractor contact. **Items** sub-form uses `item_types` schema with inheritance.

### 4.7 Documents / VFS (`documents`, E-05-01)
- **Columns:** Name · Type/category · CDE state · Suitability code · Revision · Drawing # · Discipline · Folder path · Linked entities count · Orphan badge · Version · Uploaded-by/at · size.
- **Filters:** folder (tree) · CDE state · suitability · discipline · category · "current revision only" · "orphaned" · linked-to-this-asset · security classification.
- **Saved views (Smart Folders, `file_saved_views`):** *Current drawings by discipline · Awaiting approval · Orphans to reassign · My shares.*
- **Search:** name, drawing #, OCR content (`file_search`), tags.
- **Row actions:** Open/markup · New revision · Route & file (resolver) · Link to entity · Share (role/external token) · Transmit · Move folder (rename-safe) · Toggle folder versioning · Approve (file_approvals).
- **Upload/route form:** Drag-drop → presigned direct-to-bucket (ADR-001) → category/discipline → **routing resolver** suggests `/Site/Asset/RSW` folder + standard name (editable) → entity links → CDE state. Orphan engine flags no-link/target-gone.

### 4.8 Progress (`progress`, E-04-03)
- **Columns:** Anchor (RSW/Item) · Period · Method [units/milestone/%] · Earned qty + unit · Planned qty · % complete · Crew hours · Status [draft/submitted/verified] · Recorded-by · geo/photo · cap-exceeded flag.
- **Filters:** anchor subtree · period · method · status · "awaiting verification" · "over planned (capped)" · discipline.
- **Saved views:** *My submitted this period · Awaiting QC verification · Capped/over-claim · By RSW.*
- **Row actions:** Submit · Verify (quantity claim vs inspection acceptance) · Edit · View evidence · Export to P6 (progress actuals).
- **Create/edit form:** Anchor (RSW or Item, required) · Period · Method (*conditional*: units→qty+unit; milestone→weighted milestone picker; percent→%) · Earned quantity (**capped at scope**, blocked if > planned) · Crew hours · geo + photos. Verification gate `rsw.complete`: no open blocking NCR; weld Items gated by R-WPS-4.

### 4.9 Deliveries / Gate (`site_logistics` + `procurement` GR, E-03-02 — Gate Delivery edge screen, TD-008)
- **List columns:** Delivery ref · Supplier · PO ref (ERP mirror) · Vehicle · Gate · Window start/end · Materials desc · Status [booked/arrived/receiving/received/rejected] · OS&D flag · laydown zone.
- **Filters:** gate · status · supplier · date/window · "OS&D raised" · "awaiting receipt" · PO ref.
- **Saved views:** *Today's bookings · Awaiting gate · OS&D open · By supplier.*
- **Row actions:** Book delivery · Check-in (arrived) · Gate-receive (opens receipt flow) · Raise OS&D · Export for 3-way match.
- **Gate-receipt form (tablet/offline-first, `field_diary` ledger):** scan docket/barcode → match PO line → **+/- quantity per line** vs ordered → rejection reason (*conditional*) → OS&D delta auto-computed → photos of docket + goods → **signature** → writes `site_inventory._movement` + `procurement._goods_receipt`. `goods.receive` rule set validates. Offline via `client_op_id` idempotent sync.

### 4.10 Inspection Requests register (`inspection_requests`, E-01-01)
- **Columns:** IR # (prefixed to disambiguate from RFI) · Inspection/ITP step anchor · Required party [client/third-party/authority/internal] · Required contact · Notice period · Confirmed slot · Status [`draft→requested→acknowledged→confirmed→witnessed|waived|no_show|failed`] · Outcome · Delay badge (cause party) · overdue/ageing.
- **Filters:** status · required party · cause-party (delay) · discipline · asset subtree · overdue · "blocking a hold" · date range.
- **Saved views:** *Awaiting client confirmation · No-show delays (client) · Overdue notices · Witnessed this week · Delay-attribution by cause party.*
- **Row actions:** Raise (from ITP hold/witness or ad-hoc) · Propose slots · Send notice (PDF) · Remind · Record outcome (+reason code) · Open delay · Raise change/EOT from delay · View event ledger.
- **Create form:** Anchor (inspection *or* ITP step) · Required party role + contact · Notice period hours · Waiver rule [allowed/not] · proposed slots · attendees. Every transition writes the **append-only hash-chained event** (actor + reason code). A no-show/decline blocking a hold **auto-opens a `_delay`** with cause party → feeds the **client-vs-contractor delay dashboard** ([F12]).

---

## 5. User walkthroughs (`cases` playbooks — seed these into `cases` [F1], E-06-07)

Eight happy-path journeys, each a `oe_cases_user_case` (title · roles · stage · est_minutes · steps). These are what `cases` seeds per persona (DEVELOPMENT-PLAN §4: "`cases` seeds a walkthrough playbook per persona").

**1. Inspector raises & closes an inspection with a hold gate.** (Inspector, ~10 min)
Open site-scoped Inspections view → *Raise* → pick Type (pins template) → pick Anchor Item/RSW → system defaults discipline + self as inspector (competency checked) → fill dynamic checklist (measurements, photos; instrument-ref field checks gauge calibration via `eligibility`) → mark result → if a **Hold** point, status holds and progress is blocked → QA/QC Lead or Inspector releases the hold (gated `inspection.close`) → PDF filed to MDR via VFS resolver.

**2. QA/QC Lead builds an ITP and releases a hold point.** (QA/QC Lead, ~20 min)
Open ITP builder → header (discipline, acceptance standard) → add ordered control points with H/W/S/R intervention types + signatory roles → publish (pins `forms` revision, `schema_hash`) → ITP attaches to asset subtree → during execution, hold point arms a `construction_control` gate → inspection passes → Lead *releases hold* (A capability; `run_checks` confirms criteria met, no open NCR) → progress unblocks.

**3. Welder assignment + weld acceptance through the WPS gates.** (QA/QC Lead + Inspector, ~15 min)
Create Weld Item → pick **approved WPS** → assign welder → system enforces R-WPS-1/2/3 (valid qualification, range covers weld, continuity unbroken) — unqualified assignment **blocked with a finding** → welder welds (status `welded`) → Inspector records NDE results → when all required NDE = accept and ITP holds released and no open NCR → QA/QC Lead *accepts* (R-WPS-4) → weld register + continuity watch update.

**4. Stores Person books a delivery + gate receipt with OS&D.** (Stores Person, ~8 min)
Book delivery against PO (ERP mirror) with window + gate → vehicle arrives, check-in → open **Gate-receipt** (tablet, offline): scan docket/barcode, match PO lines, enter received qty per line (+/-), flag shortage/damage → OS&D delta auto-computed with reason + photos → sign → writes site-inventory movement + goods receipt → `goods.receive` validates → OS&D exported for ERP 3-way match.

**5. Request for Inspection → client no-show → delay → EOT candidate.** (Inspector + PM, ~12 min + wait)
Raise IR from an ITP hold point → propose slots → client confirms a slot via portal magic-link → notice PDF generated & filed → on the day the client **does not attend** → Inspector records outcome `no_show` + reason code (`client_attendance`) → system **auto-opens a `_delay`** (cause_party=client) linked to the blocked RSW/activity, event ledger hash-chained → PM opens delay-attribution dashboard, sees client-caused delay hours → one-click **raise change notice / EOT candidate** carrying the evidence pack (events + notice + photos) into `variations`.

**6. Supervisor completes an offline workpack stage.** (Supervisor, ~10 min, offline)
Open issued IWP on tablet (drawings + locked batches/MTCs inherited from AWP) → execute stage, collect measurements in the adaptive JSONB collector (validated by versioned JSONSchema) → sign stage off offline (queued in `field_diary` sync ledger) → on reconnect, idempotent replay → supervisor→QC **hold-point gate** arms → QC inspector notified → QC inspects and releases (writes `signing` signature) → stage complete → on pack completion, ARQ compiles QR-coded MDR PDF filed to VFS.

**7. PM reviews the client-vs-contractor delay dashboard.** (PM, ~5 min)
Open `inspection_requests` delay-attribution dashboard → delay-hours grouped by cause party (client / third-party / authority / contractor / force-majeure) with planned-vs-actual on blocked RSWs → drill into a client-caused cluster → view hash-chained event ledger as evidence → export a claims-ready delay timeline (optionally `erp_chat` drafts the narrative, `source="ai"`, human-confirmed) → push to `claims_evidence` binder.

**8. Client co-signs an ITP via the portal.** (Client Portal user, ~5 min)
Client opens magic-link → scoped to their project + asset subtree → views ITP register (read-only) → receives co-sign request (portal v2) for an internally-complete ITP → reviews → **counter-signs** via `signing` (gated `itp.cosign`: all hold/witness points released first) → signature stamped on the register → *or* declines with a reason, which **blocks** "client-accepted". Completing a witness visit (v3) does **not** itself release the gate — `review_authority` + `inspection.close` do.

---

## 6. Recommendations

| # | Recommendation | Rationale | Effort | Priority | Affects |
|---|---|---|---|---|---|
| R1 | Ship a **seeded persona→tier→module-grant preset pack** (§3) consumed by `users._role_preset`, before register UI | Every list view/create form/gate banner reads scope from it; retrofitting is the costliest rework | M | P1 | `users` T-0008, `teams`, config packs |
| R2 | Add **personas 14 (field-worker) and 15 (client-portal)** as first-class presets | They are the highest-volume write/read personas yet appear only inside `field_diary`/`portal` | S | P1 | `users`, `cases` E-06-07 |
| R3 | Adopt the **concrete `oe_users_user` column list** (§2.1) incl. `employer_kind`, `site_scope`, `module_access` jsonb, auth-provider seam | Spec's one-line sketch omits site-scope, employer split and the TD-003 provider seam | S | P1 | `users` T-0007/T-0008 |
| R4 | Add **Inspector site-scope loader criteria** (`site_node_id IN site_scope`) alongside project scope | Inspector is site-scoped not project-scoped — new plumbing the as-built ADR 0014 doesn't cover | M | P1 | `users`, `asset_management`, RLS |
| R5 | Build the **Admin → Users & Access screen** (org member list + role/tier/scope + per-project assignment) | As-built suite §7 flags this as the single biggest admin-UX gap; backend endpoints exist | M | P1 | T-0025, `admin` |
| R6 | Standardise **register UX contract** (columns/filters/saved-views/search/sort/row-actions/create-form) per §4, as a reusable `saved_views`-backed register scaffold | Nine registers share one pattern; specifying once avoids nine ad-hoc screens | L | P1 | `inspections`,`ncr`,`punchlist`,`wps`,`documents`,`progress`,`site_logistics`,`inspection_requests` |
| R7 | Seed the **8 `cases` playbooks** (§5) as the per-persona walkthroughs | DEVELOPMENT-PLAN §4 promises them; concrete steps let onboarding + QA write them to tests | S | P2 | `cases` E-06-07, `onboarding` |
| R8 | Split **QA/QC Lead (project hold authority)** from **Quality Manager (portfolio read+audit)** presets | Spec lists both but gives identical scope; matrix makes the authority split explicit (SoD) | S | P2 | `users`, `qms`, `construction_control` |
| R9 | Add **project toggle `restrict_cross_subcontractor`** read filter on ratings/agreements/rates | A subcontractor supervisor must not see rival subbies' commercial data | S | P2 | `subcontractors`,`contacts`,`field_time` |
| R10 | Make **Admin (ops) tier carry no `A` on quality gates** — separation of duties | Spec's single "admin" tier conflates ops admin with quality authority | S | P2 | `users`, gate rule sets |
| R11 | Define **conditional/required field rules** in create forms via the one-engine (`visible_if`/`required_if`) not bespoke UI code | Keeps forms as data (TD-002) — e.g. NCR severity=critical ⇒ schedule-impact required | S | P2 | `forms`, all gated registers |
| R12 | Add a **per-persona default saved view** seed (e.g. Inspector → "Open holds on my site") | Makes the first screen useful per role; `saved_views` already supports it | S | P3 | `saved_views`, `users.preferences` |

---

## 7. Conflicts & risks

- **Inspector site-scope is not in the as-built scope engine.** The reference ADR 0014 (`scope.py`) injects `project_id IN scope` only; KERP's Inspector needs an **Asset-`Site`-node** filter on inspection/NCR/punch reads. This is genuinely new plumbing, not a port — if it slips, Inspectors see the whole project (over-exposure) and the matrix (§3) is unenforceable. **Risk: P1.** (R4.)
- **Single "admin" capability tier conflates two authorities.** Hard-rule 5's `viewer<editor<manager<admin` plus "13 persona presets" implies one admin tier, but Admin (user/scope admin) and Quality authority (hold release, weld accept) must be **separable** — a project Admin releasing a hold point is a control failure. The preset pack (§3) must express "admin-of-ops ≠ approver-of-gates". **Risk: P1.**
- **TD-002 (one-engine) blocks concrete create-form specs.** Every create/edit form in §4 that is an inspection/ITP/permit/test assumes the unified `forms`/`item_types` engine (MODULE-SPEC-11). If TD-002 resolves *against* the merge, each register needs its own form builder and the §4 forms fragment. **Flag as a dependency, not a conflict** — but the register UX work (R6) should not start until TD-002 is settled.
- **TD-007 (multi-tenant plumbing in a single-company product).** The §2 field lists and §3 matrix assume `tenant_id`+RLS per hard-rule 2. In a single-company deployment this is dead weight on every query, but the persona scope model (project/site) is **orthogonal** to tenancy and is needed regardless. Keep scope; TD-007 can drop tenant plumbing without touching the matrix.
- **`credentials`/`wps`/`resources` all hold competency** — the eligibility banner (`eligibility` service) is the only thing stopping three-way duplication. If `eligibility` (MODULE-SPEC-11 §5) is cut, every create form in §4 that gates on a person's validity re-implements the check. Keep `eligibility` on the critical path for the register UX.
- **Portal personas write through, never fork.** §5 walkthrough 8 and the matrix assume client co-sign/NCR write through `signing`/`ncr` (portal spec [F8], removal impact). Any "client view" that stores its own acceptance would create a second source of truth — watch for this in portal v2/v3 build (E-07-02/03).

---

## 8. Open questions for the owner

1. **(→ new TD, relates TD-003)** Do subcontractor and client users authenticate through the **same** `auth_provider` seam as staff, or is magic-link the *only* external path? The `oe_users_user.auth_provider` enum (§2.1) assumes one seam; confirm before T-0007.
2. **(→ TD-007)** Keep project/site **scope** even if tenant plumbing is dropped? (My read: yes — scope is persona-essential, tenancy is deployment-optional.)
3. **(→ TD-002)** Resolve the one-engine fork before the register-UX scaffold (R6) starts — the create forms in §4 depend on it. Which expression dialect: **CEL or JSONLogic** (MODULE-SPEC-11 §2 leaves it open)?
4. Should **Admin (ops)** ever hold quality-gate `A` capability by configuration, or is the SoD split hard-coded (R10)? This is a governance call only the owner can make.
5. Is **Inspector** truly single-site, or can one inspector cover multiple sites on a project? The matrix assumes `site_scope` is an **array** (§2.1) to allow multi-site without widening to project — confirm.
6. **(→ new TD, relates TD-008)** The Gate Delivery, Look-Ahead Board and Daily Workface Log edge screens (TD-008) have no UI spec; §4.9 fills Gate Delivery — do you want §4-style specs for the other two tier-1 edge screens in the merged plan?

---

## 9. Feeds-the-plan (lift directly)

1. **Persona preset pack** (§1 table + §3 matrix) → seed data for `users._role_preset`, consumed by T-0008; editable in T-0025. Include personas 14–15.
2. **`oe_users_user` + `oe_contacts_contact` concrete column lists** (§2) → schema for T-0007 (`users`) and E-03-01 (`contacts`); note the 1:1 user↔contact link.
3. **Persona×module×capability access matrix** (§3) → the authorization test fixtures for the `run_checks`/RLS contract tests (DEVELOPMENT-PLAN §8); the "where scope bites" bullets are the edge cases to assert (Inspector site-scope, hold-release authority, weld-accept authority, cross-subcontractor filter).
4. **Register UX contract** (§4) → a single reusable `saved_views`-backed register scaffold spec feeding T-0112/T-0129 (inspections), T-0115 (NCR), T-0116 (punch), T-0207 (weld), E-05-01 (documents), E-04-03 (progress), E-03-02 (Gate), E-01-01 (inspection_requests). Each register's columns/filters/saved-views/search/sort/row-actions/create-form are ready to drop into its task.
5. **8 `cases` playbooks** (§5) → seed `cases` [F1] for E-06-07; double as E2E happy-path scripts (Playwright, DEVELOPMENT-PLAN §8).
6. **Conditional-field rules as data** (R11) → wire into the `forms` one-engine `visible_if`/`required_if` (depends on TD-002).

---

*Lens: Feature Filler. Scope kept inside the anchor model (work→RSW, inspection→Item), single-company doctrine (scope = Project Settings), money-is-a-mirror boundary, and the dynamic-forms engine. No hard-rule violations introduced; the one new plumbing need flagged is Inspector Asset-`Site` scope (R4), which the anchor model already supports via `oe_asset_node level='site'`.*
