# OpenBusinessERP — Per-Module Docs · Batch 5: Project Delivery & Field

> Batch 5 of 7. Four Pillars per module (compact). Grounded in `backend/app/modules/<name>/`. `category` / `depends` / route counts from manifests & routers.

**Modules (25):** `schedule`, `schedule_advanced`, `timeline`, `deadlines`, `recurring`, `daily_diary`, `field_diary`, `field_time`, `fieldreports`, `site_prep`, `site_logistics`, `site_supervision`, `construction_control`, `commissioning`, `closeout`, `defects_liability`, `punchlist`, `ncr`, `inspections`, `safety`, `hse_advanced`, `temporary_works`, `formwork`, `equipment`, `assets`, `methodology`.

---

### Scheduling & time
- **`schedule`** — *core, depends[projects], 110 routes.* **What:** the 4D schedule (activities, WBS, dependencies, dates linked to 3D). **Does:** activities, logic links, baselines, critical path. **How:** CPM scheduling tied to BOQ/model for 4D; the backbone for progress/EVM. Core.
- **`schedule_advanced`** — *business, depends[users, projects], 208 routes.* **What/Does:** **Last Planner System** (lookahead, weekly work plans, constraints, PPC, make-ready). **How:** collaborative pull-planning layered over `schedule`.
- **`timeline`** — *core, 4 routes.* Lightweight project timeline view.
- **`deadlines`** — *core, depends[notifications], 4 routes.* Deadline register that raises notifications.
- **`recurring`** — *core, depends[sales], 22 routes.* **What/Does:** recurring-invoice templates that materialise into ordinary `sales` invoices each period. **How:** a template + schedule emits a sales invoice per cycle. (Finance-adjacent but delivery-cadence in nature.)

### Field capture
- **`fieldreports`** — *core, depends[projects], 58 routes.* **What/Does:** field reports (daily logs, labour/plant/weather) — the hours source for payroll/EVM.
- **`daily_diary`** — *business, depends[users, projects], 108 routes.* **What/Does:** the full Daily Site Diary (weather, labour, plant, events, photos, sign-off).
- **`field_diary`** — *core, depends[projects, users], 44 routes.* **What/Does:** the field-worker Daily Diary MVP (the mobile/low-trust persona surface, Epic F) — lighter sibling of `daily_diary`.
- **`field_time`** — *business, 38 routes.* **What/Does:** field time capture (clock-in/out, timesheets) feeding payroll.
- **`site_prep`** — *business, depends[projects], 20 routes.* Pre-construction mobilisation / site-setup readiness checklist.
- **`site_logistics`** — *business, depends[projects, users, boq], 48 routes.* Site logistics & delivery scheduling (gate bookings, lay-down, crane/hoist windows).
- **`site_supervision`** — *core, depends[users, projects], 38 routes.* Design-side site supervision (site visits, instructions, observations).

### Quality, safety & control
- **`construction_control`** — *core, depends[users, projects, ncr], 100 routes.* **What/Does:** construction control — site quality/conformance control plans tied to NCRs.
- **`inspections`** — *core, depends[users, projects], 18 routes.* Inspection records (QA/QC checks). (Namesake of inspection-erp's core, but a lighter module here.)
- **`ncr`** — *core, depends[users, projects], 14 routes.* Non-Conformance Reports (raise → disposition → close).
- **`punchlist`** — *core, depends[projects], 30 routes.* Punch/snag list items to closeout.
- **`safety`** — *core, depends[users, projects], 34 routes.* Core safety (observations, toolbox talks, incidents).
- **`hse_advanced`** — *business, depends[users, projects], 162 routes.* **What/Does:** the full HSE suite — incidents, investigations, permits-to-work, audits, actions — "sister to `safety`" with the enterprise depth.
- **`temporary_works`** — *business, depends[projects], 20 routes.* Safety-critical temporary-works governance register (TWC sign-off, design checks).
- **`defects_liability`** — *business, depends[projects], 22 routes.* Post-handover warranty / Defects-Liability-Period register.

### Handover & commissioning
- **`commissioning`** — *business, depends[projects, users], 46 routes.* Commissioning (Cx) — systems, checksheets, witness tests.
- **`closeout`** — *core, depends[users, projects, documents], 26 routes.* Handover & closeout pack assembly (O&M, as-builts, certificates).

### Formwork, equipment, assets, methodology
- **`formwork`** — *estimation, depends[projects, boq], 50 routes.* **What/Does:** temporary-mould catalogue, priced assignments and pour cycles (formwork planning + cost).
- **`equipment`** — *business, depends[users, projects], 96 routes.* Equipment & fleet management (register, allocation, utilisation, maintenance).
- **`assets`** — *core, depends[users, projects], 10 routes.* Asset operations register (owned assets lifecycle).
- **`methodology`** — *core, depends[projects, boq], 42 routes.* Estimating/construction methodology statements tied to BOQ scope.

---

### Batch-5 map
```
schedule (4D) ◀──lastplanner── schedule_advanced ; timeline/deadlines (views/alerts)
fieldreports / field_time / daily_diary / field_diary ──hours & events──▶ payroll, EVM, progress
site_prep → site_logistics → site_supervision ──delivery/ops──▶ projects
construction_control ──ties──▶ ncr ; punchlist / inspections / commissioning / closeout → handover
safety ◀──sister── hse_advanced ; temporary_works (governance) ; defects_liability (post-handover)
formwork / equipment / assets / methodology ──plant, moulds, methods──▶ cost & delivery
recurring ──period──▶ sales invoices
```
`schedule` is the delivery backbone; the quality/safety/handover modules are mostly independent registers that gate individually (disable → routes 402). Field-capture modules feed payroll (Batch 1) and EVM/progress (Batch 4).

*Batch 5 of 7. Next: **Documents & Collaboration** (documents + file_* subsystem, rfi, submittals, transmittals, correspondence, meetings, markups, plan_room, collaboration, approval_routes, bim_hub, cad, dwg_takeoff viewer). Grounded @ `a96d525`.*
