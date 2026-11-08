# Page & Layout Designer — review

**TL;DR.** Adopt a **four-pillar nav over a single app shell** (sidebar + top bar + lazy `<Outlet>`), ported directly from the as-built Inspection-ERP `Layout.tsx`/`nav.ts`, but driven by KERP's own `navCatalog` → **Logistics · Execution · Defense & Claims · Controls** plus **Backbone** and **Platform** groups. The platform needs **~70 distinct top-level pages** (≈130 counting detail/sub-tab views), but the real design risk is concentrated in **~10 screens**: the **Asset-tree explorer + Asset-Details tabs**, the **dynamic page/layout designer** (shared by `asset_management` F4 and `forms`), the **Inspection-detail dynamic-form renderer**, the three **Tier-1 edge screens** (Gate Delivery, Look-Ahead Board, Daily Workface Log — TD-008, no spec exists), the **RFI + client-vs-contractor delay dashboard** (`inspection_requests`), the **Documents/VFS tree + viewer/markup**, and the **Workpack IWP field view with offline stage sign-off**. Everything else is a register (filterable table) + a detail page, and should be built from **two reusable shells** (`RegisterPage`, `DetailPage`) so 50+ modules cost almost nothing to surface. **Design the edge screens and the designer first; they gate Phases 1/3/4 and have no UI spec today.**

The three edge screens, the dynamic-form renderer and the VFS viewer are the only genuinely novel UI; the rest is composition. Build the shells before the modules.

---

## 1. Findings

### 1.1 Global IA / navigation

**Shell.** Keep the as-built topology (`architecture-suite 04 §2`): fixed `w-64` sidebar on `md+`, off-canvas drawer below, sticky blurred top bar, lazy code-split `<Outlet>` under `<Suspense>`, `OfflineBar` directly under the top bar surfacing the IndexedDB mutation-queue state (plan §9, `field_diary` sync ledger). This is proven and matches KERP's needs; do not reinvent it.

**Nav groups — replace the five as-built groups with KERP's four pillars + two.** The as-built `NAV_GROUPS = ['overview','field','ops','commercial','admin']` (04 §3.1) must become KERP's `navCatalog.ts` → the pillars named in FINAL-PLAN §7 and DEVELOPMENT-PLAN §0:

| Group (i18n `nav.group.*`) | Members (module → nav item) |
|---|---|
| **Overview** | Dashboard (all-projects / project), Projects, Global Search, Saved Views, Notifications |
| **Backbone** (Asset & Quality) | Asset Tree, Inspections, ITP, NCR, Punch, Weld Register, WPS, Welder Quals, Requests-for-Inspection, Requirements, Review Authority |
| **Logistics** | Deliveries/Gate, Site Inventory, Cutting, Procurement, Supplier Catalogs, Subcontractors, Equipment, Resource Demand |
| **Execution** | Field Programme, Look-Ahead Board, Progress, Tasks, Diaries, Field Reports, Field Time, Commissioning, Temp Works, Safety/HSE, Workpacks (IWP), AWP |
| **Defense & Claims** | Variations, Change Orders, MoC, Claims Evidence, Reconciliation, Cost Recovery, RFI, Submittals, Transmittals, Correspondence |
| **Controls & Insights** | Physical EVM, Dashboards/BI, Reporting, Project Controls, Risk, Carbon/ESG, AI Assistant |
| **Documents** | CDE / VFS tree, Plan Room, Markups |
| **Admin** | Settings, Users & Access, Module Registry, Config Packs, Integrations, Marketplace/Billing |

Backbone deserves its **own** top group (not folded into a pillar) because the plan calls it the backbone and the owner calls the product "the inspection management system" (BRIEF). Eight groups is the ceiling; collapse group headers on tablet.

**Nav is registry-driven, not hard-coded.** Each module manifest declares `navItems` + `requiresRole` + per-project enablement (04 §3.1/§7; `projects._project_module`). `buildNavGroups` filters items the user may not see. This is essential given ~94 modules and per-project module toggles — a hard-coded nav would be unmaintainable.

**Project switcher & context.** Top-bar `ProjectPicker` (04 §3.2). KERP differs from the as-built "one active project" (ADR 0014 `NoProjectState`) in one critical way: the **persona matrix requires an all-projects context** (DEVELOPMENT-PLAN §4 — Operations Management, Procurement, HSE, HR, Quality Manager all see *all projects*). So the switcher needs an **"All projects"** sentinel that routes to portfolio dashboards/registers, and most registers need a `project_id` column when in all-projects scope. The project-gating overlay should only fire for personas scoped to a single project with none assigned.

**Breadcrumb.** As-built `OrgName › PageTitle` (04 §3.2) is too shallow for KERP's deep hierarchy. Use a **context-aware breadcrumb**: `Project › Pillar › Page` for registers, and `Project › Site › Area › Asset › RSW › Item` when inside the Asset tree (the hierarchy *is* the breadcrumb). Drive it from the active asset-node path (`oe_asset_node.path`).

**Desktop vs tablet split.** DEVELOPMENT-PLAN §9 / FINAL-PLAN §7: **Tier-1 edge screens are tablet/phone-first and offline** (Gate Delivery, Look-Ahead Board, Daily Workface Log, + diaries, inspections, workpack sign-off). Everything else (registers, designer, BI, admin, claims binders) is **desktop office**. Recommend a **route-level `edge: true` flag** in the manifest that (a) renders a simplified full-bleed layout with no sidebar, big touch targets, camera/geo access, and (b) registers the route in the offline sync set. Do not try to make the 50 office registers offline — scope offline to the edge flag only (ties to TD-006).

### 1.2 Sitemap (every top-level page)

```
/login                         magic-link / password / PIN (field) / portal token
/onboarding                    first-run wizard (tenant → project → config pack)
/                              Dashboard (all-projects or project rollup)
/projects                      Project list + create
/projects/:id/settings         currency/date/locale/tax/modules/config-pack
/search                        Global semantic + SQL search
/saved-views                   Saved cross-module views
/notifications                 Notification centre + prefs
/profile                       Name/locale/password/API-keys (GAP in as-built)
/admin                         Tenant settings, module registry, audit console
/admin/users                   Users & Access (org members, roles, project scope) (GAP)
/admin/config-packs            Config-pack management (taxonomy/layouts/ITP/P6 rules)
/admin/integrations            P6/ERP/safety connectors + webhooks

BACKBONE
/assets                        Asset tree explorer (Site→Area→Asset→RSW→Items)
/assets/:nodeId                Asset-Details (tabs: Overview/RSWs/Items/Insp&ITP/Files/Tasks/MOC)
/assets/designer               Dynamic page/layout + form designer
/assets/import                 P6/.xer/XML + Excel/CSV import wizard (unmatched-row linking)
/inspections                   Inspections register
/inspections/:id               Inspection detail (dynamic form render)
/itp                           ITP plan builder + hold/witness board
/ncr   /ncr/:id                NCR register + detail
/punch                         Punch list (drawing-pin + map)
/weld  /weld/:id               Weld register + weld detail + continuity watch
/wps   /wps/:id                WPS register + detail
/welders                       Welder qualifications
/inspection-requests           RFI board + client-vs-contractor delay dashboard
/requirements  /review-authority  (registers + detail)

LOGISTICS
/deliveries                    Gate booking calendar + gate-receipt / OS&D (edge)
/inventory                     Site inventory (locations, items, movements)
/cutting  /cutting/:id         Cutting plan list + nesting view
/procurement  (+ /:id)         Requisitions / POs / goods receipts
/suppliers  /subcontractors    Vendor + subbie registers
/equipment                     Fleet register
/resource-demand               Demand rollup

EXECUTION
/schedule                      Field programme (Gantt, P6-synced, RSW-linked)
/lookahead                     Look-Ahead / Last-Planner board + PPC (edge)
/progress                      Progress capture (earned qty, geo+photo) (edge)
/tasks                         Task board
/diary                         Daily Workface Log / diaries / field reports (edge)
/field-time                    Timesheets
/workpacks  /workpacks/:id     IWP register + field view (edge, offline sign-off)
/awp                           AWP constraint board (EWP/PWP/CWP→IWP)
/safety  /hse                  Safety + HSE registers

DEFENSE & CLAIMS
/variations  (+ /:id/measure)  Variations + signed site measurement (edge measure)
/changeorders /moc             Change control + MoC
/claims                        Claims / disruption / EOT binder assembler
/rfi  /submittals  /transmittals  /correspondence   (registers + detail)

CONTROLS & INSIGHTS
/evm                           Physical-EVM dashboard (SPI / physical-CPI / PPC)
/dashboards  /bi               BI dashboards + widget builder
/reporting                     Report templates + generated reports
/controls                      Executive controls board
/risk  /carbon  /esg           (registers)
/assistant                     AI assistant (propose→confirm→apply)

DOCUMENTS
/documents                     CDE / VFS folder tree + viewer
/documents/:id                 Document viewer + markup overlay
/plan-room                     Controlled current drawings + pins

PORTAL (separate shell, magic-link)
/portal/:token                 Client landing (scoped)
/portal/:token/assets          Asset hierarchy viewer (v1)
/portal/:token/registers/:type Registers read-only (v1)
/portal/:token/documents       Document viewer (v1)
/portal/:token/cosign          Co-sign reports/ITPs (v2)
/portal/:token/visits          Visit scheduler (v3)
/portal/:token/ncr             Client NCR raise/sign-off (v3)
```

**Count: ~70 top-level routes; ~130 views including detail pages, the 7 Asset-Details tabs, and the 3 portal phases.**

### 1.3 Platform pages

| Page | Purpose · layout · key components · actions · persona · device · offline |
|---|---|
| **Login** | Single card; **four auth modes behind one `AuthProvider` seam (TD-003)**: email+password (staff), magic-link (staff/field), **6-digit PIN** (`field_diary._magic_link` phone+PIN hash), **portal token** (`portal._access_grant.magic_link_token`). Device detects field flow → PIN keypad. All personas · desktop+tablet · the PIN flow must render offline-tolerant (cached last tenant). |
| **Onboarding / first-run** | Non-blocking wizard (`onboarding`, `projects._wizard_draft`): tenant → first project → pick **config pack** (`asset_management` F11 provisions taxonomy/layouts/disciplines/ITP/P6 rules) → invite users. Stepper layout, resumable draft. Owner/Admin · desktop. |
| **Project list** | Card/table grid of projects (name/code/client/phase/%); `+ New project`. Filter by status/phase. Note: as-built `ProjectsPage` is **list+create only** — KERP needs a **project detail** view with member assignment (see Users & Access gap). Most personas · desktop. |
| **Project Settings** | `max-w-4xl` card column (reuse 04 §5): **Currency / date / locale / default VAT / custom units / work calendar** (`projects` F7 — these are *Project Settings, not i18n*, per doctrine), **module enablement** toggles per project (`_project_module`), **validation rule-set / compliance-pack** selection, config-pack re-apply. Owner/Admin/PM · desktop. |
| **Admin** | Tenant/system settings, **module registry** (enable/version/quarantine — `admin` F2, `module_builder`), audit console. Owner/Admin · desktop. |
| **Users & Access** | **Biggest as-built gap (04 §7.2).** Org member list with **role dropdowns** (13 persona presets `users` F3 + capability tier F2) + invite; a **per-project member/scope editor** (project/site scope F4); site-scope picker resolves to an Asset `Site` node. Owner/Admin · desktop. |
| **User profile & prefs** | **Gap (04 §6).** Name / locale / **password change** / API keys; notification channel prefs (`notifications._preference`). All users · desktop+tablet. |
| **Notifications** | Centre: list grouped by type/entity, mark-read, action links; prefs tab (channels/digests/webhooks). All · desktop+tablet · badge count offline-cached. |
| **Saved views** | Grid of saved cross-module views (`saved_views._view`), pin/share-scope, run telemetry. All · desktop. |
| **Global search** | One search box → **two-track results** (vector + SQL over all `oe_*` — `search`), faceted by module/entity/date; result cards deep-link. All · desktop+tablet. |

### 1.4 Backbone pages (Asset & Quality)

| Page | Purpose · layout · components · actions · device/offline |
|---|---|
| **Asset tree explorer** `/assets` | **The defining screen.** Left: lazy-loading recursive tree (`oe_asset_node` Site→Area→Asset→sub-Asset→RSW, with Items under assets); right: node detail / map. Level badges, discipline chips, `%` complete, status, external-key chips (WBS/Activity ID). Actions: add/move node (`POST /assets/nodes/{id}/move`), add Item, open designer, import. PM/Super/Planner/QA · desktop (tablet read). |
| **Asset-Details** `/assets/:id` | **Modular tabs, designer-driven** (spec §2): **Overview · RSWs · Items · Inspections&ITP · Files · Tasks · MOC**. Header = node path + status + P6 keys. Inspections tab **aggregates the asset + descendants** (`GET /assets/nodes/{id}/inspections`). Tab set and Overview layout come from `oe_asset_layout.layout_json`. Desktop. |
| **Page / layout designer** `/assets/designer` | **High design effort.** Drag-drop canvas (left: field palette from `oe_asset_field_def`; centre: form/page WYSIWYG; right: field props). Output = `oe_asset_layout.layout_json` + `forms._template`. Shared engine with `forms` (spec §15 — "unify the two no-code designers"). Needs section library, conditional/calculated fields, test-fill/lint, report-slot mapping. Admin/Config · desktop only. **(TD-005/`module_builder` risk — this is the nearest buildable piece.)** |
| **Config-pack management** `/admin/config-packs` | List built-in + custom packs (`oe_asset_config_pack`); preview taxonomy/layouts/disciplines/ITP templates/P6 rules; apply to project. Admin · desktop. |
| **P6/Excel import wizard** `/assets/import` | **High effort.** Stepper: upload `.xer`/XML or CSV → parse job (`oe_asset_import_job`) → **mapping review** (WBS→Site/Area, activity groups→Assets, discipline bands→RSWs) → **unmatched-row linking table** (the key UX — DEVELOPMENT-PLAN §9 names this the main integration risk) → validate (`asset.import_validate`) → `apply` atomic. Planner/Admin · desktop. |
| **Inspections register** `/inspections` | Filterable table across all anchor levels + **batch PDF/Excel export** (spec §2). Columns: number/type/anchor(RSW/Item)/inspector/date/status/result. Filters: project/site/discipline/status. Row → detail. QA/Inspector · desktop+tablet. |
| **Inspection detail** `/inspections/:id` | **High effort — dynamic form render.** Renders `checklist_data` via the shared dynamic-form renderer; eligibility banner (`eligibility` gate over credentials/WPS/calibration), multi-party **hash-chained sign-off** (spec §15), geo-stamp, photo attach, `inspection.close` gate feedback. Inspector · **tablet, offline** (idempotent responses). |
| **ITP plan builder + hold/witness board** `/itp` | Builder: `_itp_plan`/`_itp_item` with control points, acceptance criteria, **H/W/S/R point** markers, signatories. Board view: hold/witness points across the project as a kanban/matrix, release state. QA/QC Lead · desktop. |
| **NCR register + detail** | Register table (number/type/severity/status/anchor/source); detail: root-cause, corrective/preventive actions, schedule impact, links (inspection/change/asset), `ncr.signoff`. QA · desktop+tablet. |
| **Punch list** `/punch` | **Drawing-pin + map dual view** (`punchlist` — `location_x/y` on `document_id`+page, plus `geo_lat/lon`). Left: pin overlay on drawing; toggle to PostGIS map; right: punch cards with photos, assignee, priority, resolve/verify. Super/QA · tablet+desktop, offline. |
| **Weld register + detail + continuity watch** | Register: welds as Items (`GET /wps/welds/register`, export); detail: weld state machine (`fit_up→welded→nde_pending→accepted`), WPS ref, welder qual, NDE results, R-WPS-1…4 gate banners. **Continuity-watch** = dashboard of welders approaching lapse (`GET /wps/continuity-watch`). QA/Welding · desktop+tablet. |
| **WPS register** `/wps` | Register + detail (process/standard/ranges/revision/status, approve/withdraw, versioned doc). Welding Eng · desktop. |
| **Welder qualifications** `/welders` | Welder list → qualification cards (range/expiry/**continuity**/cert), verify/suspend/revoke. Expiry + continuity visual timeline. QA/Welding · desktop. |
| **Requests-for-Inspection board** `/inspection-requests` | **High effort.** Kanban request→notice→confirm→witness/waive/no-show/fail (`inspection_requests`); append-only hash-chained event ledger view; **client-vs-contractor delay dashboard** (reason codes with cause-party attribution → auto delay-events). QA/Inspector/PM · desktop+tablet. |

### 1.5 Logistics pages

| Page | Notes |
|---|---|
| **Deliveries / Gate booking** `/deliveries` | **Tier-1 edge, offline (TD-008).** Two surfaces: (a) desktop gate **booking calendar** (`site_logistics._gate` slots/capacity, `_laydown_zone`, delivery windows); (b) **tablet Gate Delivery** — barcode/docket scan, PO match, +/- qty, OS&D capture, signature pad, photo. Writes goods receipt (`procurement`, `goods.receive`) + exports for 3-way match. Stores/Gate. |
| **Site inventory** `/inventory` | Locations (geo) + items (RSW/Item-linked) + movements ledger; reorder-point alerts. Table + movement form. Stores · desktop+tablet. |
| **Cutting plan + nesting view** `/cutting` | List of plans; detail = cut-list editor + **nesting/yield visualisation** (1-D bar / 2-D sheet guillotine layout SVG), offcut register, `optimise`→`confirm` (writes site inventory, `cutting.confirm`). Planner/Fabrication · desktop. |
| **Procurement** | Requisitions / POs / goods-receipts tabs; PO detail with line items (RSW-tied, mirror rates), retainage. PM/Procurement · desktop. |
| **Suppliers / Subcontractors** | Standard register+detail; subbie detail carries certificates, agreements (external contract ref), ratings. Procurement · desktop. |
| **Equipment** | Fleet register + detail (telemetry, maintenance, work orders). Plant · desktop+tablet. |
| **Resource demand** | Rollup table from tasks/look-ahead with required-by dates; procurement-ready export. Planner · desktop. |

### 1.6 Execution pages

| Page | Notes |
|---|---|
| **Field programme** `/schedule` | Gantt + activity grid, P6-synced, RSW-linked; baselines, CPM floats/critical, import/export-P6 actions, link-RSW. Planner · desktop. |
| **Look-Ahead / Last-Planner board** `/lookahead` | **Tier-1 edge (TD-008).** 3–6 week constraint **kanban** by RSW; make-ready constraints, weekly commitments, **PPC** gauge, RNC reasons. Columns = weeks; cards = commitments. Planner/Super · tablet+desktop, offline. |
| **Progress capture** `/progress` | **Tier-1 edge.** Earned-quantity entry against RSW/Item with **geo + photo**, method (units/milestone/percent), scope cap, crew hours; `rsw.complete` gate. Super/Foreman · tablet, offline. |
| **Daily Workface Log / diaries** `/diary` | **Tier-1 edge (TD-008).** Daily entries: weather, headcount, activities+hours, notes, photos (geo/drone), sign/close. `field_diary` offline ledger; `daily_diary` full desktop version; `fieldreports`. Super · tablet, offline. |
| **Tasks** | Board/list, checklist, deps, Asset/meeting links. All · desktop+tablet. |
| **Field time** | Timesheet grid (resource/equipment hours, RSW-coded, daywork tag); submit/approve/reverse; payroll export. Super/Admin · tablet+desktop. |
| **Workpacks (IWP) field view** `/workpacks/:id` | **Tier-1 edge, offline (see wireframe).** See §1.9. |
| **AWP board** `/awp` | Constraint board: EWP/PWP/CWP swimlanes with status (`blocked/hold/constraint_free/released`), readiness reports, CWP→IWP decomposition, TQ banners. Planner/WP Eng · desktop. |
| **Safety / HSE** | Incident/observation registers; HSE suite (JSA/PTW/toolbox/PPE/audit/CAPA). HSE · desktop+tablet. |

### 1.7 Defense & Claims pages

| Page | Notes |
|---|---|
| **Variations** `/variations` | Register + detail; **signed site-measurement capture** on RSW/Item (`/measure`, `variation.measure`) is a **tablet edge sub-flow** (measured qty + owner signature + photos + geo); export to commercial + priced mirror (read-only money). PM/QS · desktop; measure on tablet. |
| **Change orders / MoC** | Registers + detail (approval steps, impacts, links). PM · desktop. |
| **Claims / EOT binder** `/claims` | **Binder assembler** (`claims_evidence`): timeline builder pulling productivity, delay windows, diaries, late-receipts, MoC into an evidence pack; disruption/EOT claim views (`schedule_advanced` delay analysis). Preview → export. Claims/PM · desktop. |
| **RFI / Submittals / Transmittals / Correspondence** | Standard register+detail with ball-in-court, due dates, links. All · desktop+tablet. |

### 1.8 Controls & Insights pages

| Page | Notes |
|---|---|
| **Physical-EVM dashboard** `/evm` | **Physical metrics only** — SPI, physical-CPI (earned-qty per crew-hour), PPC, earned-qty %; control-account rollup over Asset subtree; productivity trends. **No money columns** (doctrine). KPI tiles + trend charts + CA table. Controls/PM · desktop. |
| **BI dashboards** `/bi` | Widget builder (KPI defs, drill/cross-filter), alert rules. Dashboards render per-role layouts. Controls · desktop. |
| **Reporting** | Template builder + generated-reports list (PDF/Excel/HTML), schedules, publish. Controls · desktop. |
| **Executive controls board** `/controls` | Cross-module physical rollup (productivity + schedule + risk). Ops Mgmt · desktop, all-projects. |
| **Risk / Carbon / ESG** | Registers. Controls/Sustainability · desktop. |
| **AI assistant** `/assistant` | Chat with rich renderers; **every mutation proposed → human-confirmed → applied → revertible** (`erp_chat._action`, `source="ai"`); operational tools (open constraints / PO delivery / PPC / claims timeline). The **confirm/revert affordance is a first-class UI pattern**, not a toast. All · desktop+tablet. |

### 1.9 Documents / CDE / VFS pages

| Page | Notes |
|---|---|
| **CDE / VFS tree** `/documents` | **High effort.** Left: VFS folder tree (`_folder` — system-preset `/Site/Asset/RSW` scaffold, personal `/Users/{name}` lockers, custom); centre: file list (CDE state, suitability/revision codes, is-current, orphan badge); right: file preview + metadata. Rename-safe (DB-only). Actions: route-and-store, share (`document.share`), versioning toggle per folder. Doc Controller · desktop+tablet. |
| **Document viewer + markup** `/documents/:id` | PDF/CAD/image viewer with **markup overlay** (`markups` — geometry/measurement/stamps/scale calibration), version compare, Asset/Item links, comments (`file_comments` anchored). All · desktop+tablet. |
| **Plan room** `/plan-room` | Controlled current-drawings grid; pin notes/photos on current revision. Field · tablet. |

### 1.10 Portal pages (separate magic-link shell)

Portal is a **separate thin shell** (no sidebar, client branding, scoped to project(s)), phased (DEVELOPMENT-PLAN §6):

- **v1 read-only:** landing → Asset hierarchy viewer, registers read-only (inspections/ITP/NCR/punch/weld/documents), document viewer (current revisions). Filtered by `_access_grant` subtree; cross-tenant → 404.
- **v2 co-sign:** pending co-sign queue (reports/ITPs); signature pad via `signing`; decline-with-reason. Gated by `report.cosign`/`itp.cosign` (target internally complete first).
- **v3 scheduler + NCR:** visit-booking calendar (hold-point-witness / final sign-off → `meetings`); client-raised NCR form + sign-off. Gated by `portal.visit`/`ncr.signoff`.

Client persona · desktop+tablet · read paths offline-tolerant.

### 1.11 ASCII wireframes (the 8 highest-risk screens)

**A. Asset tree explorer** `/assets`
```
┌ Backbone › Asset Tree ───────────────── [All projects ▾][+Node][Import][Designer] ┐
│ ┌ Tree ──────────────┐ ┌ Node: AREA-02 › Pump House › RSW-PIPE-14 ─────────────┐ │
│ │▾ ◑ Site A          │ │  Path: Site A / Area 02 / Pump House / RSW Piping 14  │ │
│ │ ▾ ◑ Area 02 (62%)  │ │  Level: RSW · Disc: Piping · Status: In progress 62% │ │
│ │  ▾ ■ Pump House    │ │  WBS A.02.PH  Activity P6-10420  ⟲ synced 2d ago     │ │
│ │   • RSW Civil  ✓   │ │  ┌ tabs: Overview·RSWs·Items·Insp&ITP·Files·Tasks·MOC┐│ │
│ │   • RSW Piping ◑ ◀ │ │  │ Items (8)   Inspections ▸ aggregates descendants ││ │
│ │     ▸ Items (8)    │ │  │ ◻ VLV-221 valve  ✓ insp   ◻ WELD-118 ⚠ NDE pend  ││ │
│ │   • RSW Elec   ○   │ │  │ [+ Item]  [Raise inspection]  [Book progress]    ││ │
│ │ ▸ ○ Area 03        │ │  └──────────────────────────────────────────────────┘│ │
│ └────────────────────┘ └──────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**B. Inspection detail (dynamic-form render)** `/inspections/:id`  (tablet, offline)
```
┌ ◁ INSP-2041  Hydro test · RSW-PIPE-14 ──────────── ⦿offline-queued · geo✔ ┐
│ Eligibility: ✔ Inspector cert valid · ✔ WPS approved · ⚠ calib due 3d    │
│ ┌ Checklist (rendered from layout_json) ───────────────────────────────┐ │
│ │ 1. Test pressure (bar)   [■■■■ 24.0 ]  spec 22–26  ✔                  │ │
│ │ 2. Hold duration (min)   [■■ 60 ]                                     │ │
│ │ 3. Visual — joints       ( )Pass (•)Fail → raises NCR                 │ │
│ │ 4. Photo evidence        [📷 2 attached]                              │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│ Sign-off (hash-chained):  Contractor ✔ J.Doe   QC ▸ pending   Client ▸ — │
│ [Save draft] [Close inspection]  ← blocked: inspection.close (open fail) │
└──────────────────────────────────────────────────────────────────────────┘
```

**C. Requests-for-Inspection + client-vs-contractor delay dashboard** `/inspection-requests`
```
┌ Requests for Inspection ────────────── [+ Request] [Delay dashboard ▾] ┐
│ Requested → Notice sent → Confirmed → Witnessed/Waived/No-show/Fail     │
│ ┌ REQ ─┐ ┌ NOTICE ┐ ┌ CONFIRMED ┐ ┌ OUTCOME ─────────────────────────┐ │
│ │RFI-88│ │RFI-84  │ │RFI-80 Wed │ │RFI-77 ✔ witnessed               │ │
│ │RSW-14│ │client  │ │09:00 QC+  │ │RFI-75 ✖ client no-show → delay  │ │
│ └──────┘ └────────┘ └───────────┘ └──────────────────────────────────┘ │
│ ── Delay attribution (this period) ─────────────────────────────────── │
│  Client-caused  ████████ 6 events / 11.5 days                          │
│  Contractor     ███ 2 events / 3.0 days     [Raise EOT] [Change notice]│
└────────────────────────────────────────────────────────────────────────┘
```

**D. Gate Delivery (Tier-1 edge, offline)** `/deliveries` (tablet)
```
┌ GATE 2 · Delivery ───────────────────── ⦿offline · 3 queued ⟳ ┐
│ [▭ Scan docket / barcode]      PO-4471 (ERP mirror) matched ✔  │
│ Supplier: Steel Co    DN# 55120    Vehicle: HGV                │
│ ┌ Lines ──────────────────────────────────────────────────┐   │
│ │ Beam UB305  ordered 40 │ received [ 38 ]  ✎  short -2 ⚠ │   │
│ │ Plate 10mm  ordered 12 │ received [ 12 ]  ✔            │   │
│ └──────────────────────────────────────────────────────────┘   │
│ OS&D: ☑ Shortage  ☐ Damage  [📷 photo docket + damage]        │
│ Receiver signature: [✍________]   [Confirm goods receipt]      │
│ → goods.receive gate · exports OS&D for 3-way match           │
└────────────────────────────────────────────────────────────────┘
```

**E. Look-Ahead / Last-Planner board (Tier-1 edge)** `/lookahead`
```
┌ Look-Ahead (3–6 wk) · PPC 78% ──────── [Week ▾][by RSW ▾][+Commit] ┐
│  Wk 45 (make-ready) │ Wk 46         │ Wk 47        │ Wk 48         │
│ ┌ RSW-14 Piping ──┐ │┌ RSW-14 ────┐ │┌ RSW-22 ───┐ │┌ RSW-30 ────┐ │
│ │⚠ constraint:    │ ││commit 120m ││ ││commit 80m ││ ││ draft     ││ │
│ │  mat'l not recvd│ ││promised✔   ││ │└───────────┘│ │└───────────┘│ │
│ │[clear]          │ │└────────────┘│ │             │ │             │ │
│ └─────────────────┘ │              │ │             │ │             │ │
│ RNC last wk: ▸ 2 late materials · 1 design query  → feeds PPC      │
└────────────────────────────────────────────────────────────────────┘
```

**F. Documents / VFS tree + viewer** `/documents`
```
┌ Documents · CDE / VFS ───────────── [Route+store][Share][Version:ON ▾] ┐
│ ┌ Folders ─────────┐ ┌ Files ───────────────────┐ ┌ Preview ────────┐ │
│ │▾ /Site A         │ │ DWG-PIPE-14 r3  S2/WIP   │ │ [ PDF viewer ]  │ │
│ │ ▾ /Area 02       │ │ ITP-PIPE-14     A1/Appr  │ │  + markup layer │ │
│ │  ▾ /Pump House   │ │ WELD-MAP r1   ⚠ orphaned │ │  scale: 1:50    │ │
│ │   • /RSW-PIPE-14◀│ │ MDR-PH-compiled.pdf ✔    │ │  [✎ measure]    │ │
│ │ ▸ /Users/jdoe    │ │ [upload] [new rev]       │ │  rev chain ▸    │ │
│ └──────────────────┘ └──────────────────────────┘ └─────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘
```

**G. Workpack IWP field view + offline stage sign-off (Tier-1 edge)** `/workpacks/:id`
```
┌ IWP WP-00231 · RSW-PIPE-14 ──────────────── ⦿offline · stage 3/6 ⟳ ┐
│ Scope: Spool install & hydro   Drawings: DWG-PIPE-14 r3 ✔ (locked)   │
│ Batches/MTC: Pipe heat H-5521 ✔cert  Gasket B-204 ✔cert              │
│ ┌ Execution stages (from qms ITP) ─────────────────────────────────┐ │
│ │ 1 Fit-up        ✔ Super J.Doe  08:40                             │ │
│ │ 2 Alignment     ✔ Super        09:15                             │ │
│ │ 3 Weld  ▸ defers to wps weld.accept (NDE pending) ⚠              │ │
│ │ 4 Hydro [H]     ▸ hold-point — QC release required              │ │
│ │ 5 Insulation [W]  6 Handover [S]                                 │ │
│ └───────────────────────────────────────────────────────────────────┘ │
│ [Sign stage 2] → arms QC hold · notifies inspector · signing sig      │
│ [Compile MDR] (ARQ, QR, page X/Y) → files into /RSW-PIPE-14 VFS       │
└────────────────────────────────────────────────────────────────────────┘
```

**H. Physical-EVM dashboard** `/evm`  (office, no money)
```
┌ Controls › Physical EVM ───────────── [All projects ▾][Period ▾][Export]┐
│ ┌SPI 0.92┐ ┌Phys-CPI 1.04┐ ┌PPC 78%┐ ┌Earned qty 61%┐ ┌Open NCR 7┐    │
│ └────────┘ └────────────┘ └───────┘ └──────────────┘ └──────────┘    │
│ ┌ Control accounts (Asset subtree) ────────────────────────────────┐  │
│ │ CA Piping   planned 1200m earned 740m  SPI .88  rate 12.1/ch  ▾  │  │
│ │ CA Civil    planned 900m³ earned 610m³ SPI .96  rate 3.4/ch   ▾  │  │
│ └──────────────────────────────────────────────────────────────────┘  │
│ Productivity trend ▁▂▃▅▆▅▆  (earned-qty per crew-hour vs target)       │
└────────────────────────────────────────────────────────────────────────┘
```

### 1.12 Design system / component kit

Port **"Datum"** (04 §1): CSS-custom-property tokens (brand green `#1a7f37`/`#40ae62`, semantic info/warn/crit, mono-for-data), shadcn/ui primitives, Tailwind with `darkMode: ['selector', ...]`, radius/shadow tokens. It is modern-clean-SaaS and already proven. **But the as-built kit has only primitives** — KERP needs a **domain component layer** built once and reused across ~94 modules:

| Shared component | Reused by | Why build once |
|---|---|---|
| **`RegisterPage` shell** (filter bar + table + batch export + saved-view bind) | ~40 registers (inspections/NCR/punch/weld/RFI/submittals/variations/risk…) | 40 modules become config, not code |
| **`DetailPage` shell** (header + tabs + gate-banner + audit log) | every `/:id` view | consistent gate feedback (`run_checks` findings) |
| **Dynamic-form renderer** | inspections, ITP, forms, workpack stages, asset custom fields | the `forms`/`item_types` unified engine (spec §15) — single renderer + evaluator (TS+Python) |
| **Page/layout designer** | asset layouts + forms | `oe_asset_layout.layout_json` producer (TD-005 adjacency) |
| **Tree component** | Asset tree, VFS folder tree, WBS | recursive lazy-load + path breadcrumb |
| **Drawing/PDF viewer + markup** | documents, punch, plan-room, workpacks | `markups` geometry/scale/stamps |
| **PostGIS map** | punch, inventory locations, site logistics, progress geo | one Leaflet/MapLibre wrapper |
| **Kanban board** | look-ahead, AWP, tasks, RFI, inspection-requests | drag-drop columns |
| **Signature pad** | inspections, workpacks, variations measure, portal co-sign, deliveries, diaries | `signing` + offline capture |
| **Gate-finding banner** | every verifiable transition | surfaces `run_checks` errors(block)/warnings(advise) uniformly |
| **Offline indicator / sync queue** | all edge screens | `field_diary` ledger + conflict replay (TD-006) |
| **Gauge / KPI tile / trend** | EVM, PPC, dashboards, reporting | physical-metric charts (follow `dataviz`) |

**Rule: no module ships its own table or its own form. Build the eight shells above in Phase 0/1 before the module pages that consume them.**

---

## 2. Recommendations

| # | Recommendation | Rationale | Effort | Priority | Affects |
|---|---|---|---|---|---|
| R1 | Adopt the as-built shell (sidebar+topbar+lazy Outlet+OfflineBar) but replace nav groups with **KERP's 4 pillars + Backbone + Platform + Documents** via `navCatalog.ts` | Proven topology; four-pillar IA is the plan's organising idea | S | **P1** | all FE, FINAL-PLAN §7 |
| R2 | Build **two register/detail shells + a dynamic-form renderer** before module pages | ~90 pages collapse to config; one gate-banner pattern | M | **P1** | all modules, Phase 0/1 |
| R3 | Add an **"All projects" context** to the project switcher + portfolio registers/dashboards | Persona matrix requires all-projects scope (DEV-PLAN §4); as-built is single-project only | M | **P1** | projects, dashboards, RBAC |
| R4 | Spec & design the **3 Tier-1 edge screens** (Gate Delivery, Look-Ahead, Daily Workface Log) with the wireframes here | **TD-008 — no UI spec exists**; they gate Phases 3/4 | M | **P1** | TD-008, site_logistics, schedule_advanced, field_diary |
| R5 | Design the **Asset tree explorer + Asset-Details modular tabs** first | Backbone is the critical path; everything anchors to it (DEV-PLAN §9) | M | **P1** | asset_management, Phase 1 |
| R6 | Build the **P6/Excel import wizard with an unmatched-row linking table** | Named the main integration risk (DEV-PLAN §9) | M | **P1** | asset_management, schedule, integrations |
| R7 | Build **one shared page/layout+form designer** (asset layouts + forms), not two | Spec §15 unifies the two no-code engines; nearest buildable piece of TD-005 | L | **P2** | asset_management F4, forms, TD-005 |
| R8 | Add **Users & Access** and **User Profile** screens (the two as-built gaps) | 04 §6/§7 — backend endpoints exist, no UI; RBAC unusable without them | M | **P1** | users, teams, admin, Phase 0 |
| R9 | Make **AI propose→confirm→revert** a first-class UI pattern (not a toast) | Hard rule 6 / DEV-PLAN §10 — AI mutations need human confirmation | S | **P2** | erp_chat, ai_agents |
| R10 | Give **portal its own thin magic-link shell**, phased v1→v2→v3 | Separate auth/branding/scope; reuse `signing`/`ncr`/`meetings` | M | **P2** | portal, Phase 7, TD (D8) |
| R11 | Add a manifest **`edge: true` route flag** that drives the simplified offline layout + registers the offline sync set | Clean desktop/tablet split; scopes offline to Tier-1 only | S | **P2** | module contract, TD-006 |
| R12 | Make the **breadcrumb context-aware** (Asset path inside the tree; Pillar›Page elsewhere) | Deep hierarchy; as-built 2-level breadcrumb is too shallow | S | **P3** | shell, asset_management |
| R13 | Build the **VFS tree + viewer/markup** as one documents screen (tree/list/preview) | VFS is folded into `documents`; central to CDE + workpacks | M | **P2** | documents, markups, Phase 5 |
| R14 | Standardise **signature pad, map, kanban, tree, gate-banner, offline indicator** as kit components | Reused by 5–40 modules each | M | **P2** | design system, all phases |

---

## 3. Conflicts & risks

- **Single-active-project overlay vs persona matrix (conflict).** As-built ADR 0014 / `NoProjectState` assumes one active project; KERP's RBAC (DEV-PLAN §4) gives 5+ personas an *all-projects* view. The shell must support an all-projects context — otherwise Operations Management, HSE, HR, Quality and Procurement have no home screen. **Resolve before Phase 0 shell is frozen.**
- **TD-008 (risk).** The three Tier-1 edge screens have **no UI spec and are the offline/field heart of the product**. They are also the only screens with genuinely novel interaction (offline queue, conflict replay, barcode/geo/signature). If they slip, Phases 3/4 ship an office-only product that misses the "field ground truth" mission.
- **TD-005 / module_builder (risk).** The runtime no-code module UI has no buildable spec. The **page/layout designer** (`asset_management` F4 + `forms`) is adjacent and *is* buildable; don't let the undefined `module_builder` block the designer that Phase 1 needs.
- **Designer duplication (conflict).** Spec §15 says unify the two no-code designers; MODULE-SPECS still lists `asset_management` F4 **and** `forms` separately. One renderer + one designer, or they drift. Decide in the rebuild.
- **qms vs construction_control vs inspections overlap (risk).** Three modules expose inspection/ITP/NCR surfaces (MODULE-SPECS §2 flags the dedup). The UI must present **one** Inspections register, one ITP builder, one NCR register — not three competing pages. Pick `qms`=ITP/audit layer, `construction_control`=conformance/hold-gate engine, `inspections`=lightweight register, and compose a single page per concept.
- **Offline scope creep (risk).** Trying to make all 50 office registers offline (TD-006) would explode conflict-replay complexity. Constrain offline to `edge: true` routes.
- **Money leaking into UI (watch).** EVM/controls/variations pages must show money only as a read-only mirror (doctrine). A "total cost" column that looks editable re-grows commercial ownership — keep mirrors visually distinct (muted, lock icon).

## 4. Open questions for the owner

1. **All-projects context** — confirm the shell supports a portfolio scope (not just one active project) so overview personas have a landing page? *(new TD, or fold into TD-007 RBAC scope.)*
2. **TD-008** — approve the three edge-screen wireframes here as the baseline UI spec so Phase 3/4 breakdown can proceed?
3. **TD-005 / designer** — build the page/layout+form designer now (decoupled from `module_builder`), or defer the whole no-code surface?
4. **One designer or two?** — commit to the unified `forms`/`item_types` engine (spec §15 / TD-002) at the UI layer, or keep asset-layout and form designers separate?
5. **Portal shell** — confirm portal is a separate magic-link SPA shell, not a role inside the main app? (affects routing + build, D8.)
6. **Theming** — single Datum theme, or per-tenant/per-client brand (logo+colours already exist for report branding, 04 §4)? Portal especially may want client branding.

## 5. Feeds-the-plan

1. **Nav IA:** `navCatalog.ts` with 8 groups (Overview, Backbone, Logistics, Execution, Defense & Claims, Controls, Documents, Admin); registry-driven, `requiresRole`+per-project-enablement filtered; icons via lucide map.
2. **Sitemap:** the ~70-route tree in §1.2 as the canonical page list for task breakdown.
3. **Shell contract (Phase 0):** sidebar+topbar+lazy Outlet+OfflineBar+**ProjectPicker with All-projects**+context-aware breadcrumb; `edge: true` manifest flag.
4. **Reusable kit (Phase 0/1, before module pages):** `RegisterPage`, `DetailPage`, dynamic-form renderer, tree, drawing-viewer+markup, map, kanban, signature pad, gate-finding banner, offline indicator, KPI tile/gauge.
5. **Phase-1 priority screens:** Asset tree explorer, Asset-Details (7 tabs), page/layout designer, P6/Excel import wizard (unmatched-row linking), Inspection detail (dynamic render), ITP hold/witness board.
6. **Phase-0 gap-closers:** Users & Access, User Profile (close 04 §6/§7 gaps).
7. **Edge-screen specs (TD-008):** Gate Delivery, Look-Ahead Board, Daily Workface Log, Progress capture, Workpack IWP field view — tablet-first, offline, per the §1.11 wireframes.
8. **Portal:** separate magic-link shell, phased v1→v2→v3.
9. **Design system:** port Datum tokens + shadcn primitives; add the domain component layer; mirror-money styled read-only.
