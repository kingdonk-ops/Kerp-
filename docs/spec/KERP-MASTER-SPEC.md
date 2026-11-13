# KERP: Industrial Field Operations & Commercial Execution Platform
## Unified Master Architecture & Module Specification Catalog

---

### Executive Overview & Architectural Scope

**KERP** is a mission-critical enterprise execution platform purpose-built for heavy industrial contractors, engineering-procurement-construction (EPC) companies, shutdown/turnaround teams, and asset integrity specialists across mining, energy, oil & gas, and infrastructure sectors.

#### Scope Boundaries & Exclusions
In accordance with strict operational domain boundaries, generic back-office functions are **out of scope** and excluded from the core platform:
* **Excluded**: General Ledger / Double-Entry Bookkeeping, Tax Submissions (BAS/VAT), Bank Feeds, Customer Invoicing / Accounts Receivable generation, Employee Payroll / Time-and-Attendance award processing, Retail POS, generic B2C E-commerce.
* **Integrated Interface Boundaries**: KERP tracks all **Progress Claims, Schedule of Rates (SoR) quantities, Subcontractor Payment Applications, Material Receiving, and Project Cost Codes**. These export clean commercial and commitment transaction journals via secure integration points (Webhooks/REST API) to third-party enterprise financial ERPs (e.g., SAP S/4HANA, Xero, MYOB PayGlobal, Pronto, Oracle NetSuite).

---

## 1. System Topology & Core Infrastructure

```
                                  +-------------------------------------------------------------+
                                  |                 KERP Cloud Control Plane                    |
                                  |  (PostgreSQL 16 + Citus / PostGIS / Redis / Object Storage) |
                                  +------------------------------+------------------------------+
                                                                 |
                                              HTTPS / WSS / gRPC Sync Protocol
                                                                 |
                   +---------------------------------------------+---------------------------------------------+
                   |                                                                                           |
+------------------v-------------------+                                                   +-------------------v------------------+
|      Desktop / Web Operations Hub    |                                                   |     Field Mobility Engine (Mobile)   |
|   - React 18 / Vite / TailwindCSS    |                                                   |   - Offline-First PWA / Capacitor    |
|   - Real-Time Coordination Dashboard |                                                   |   - Local SQLite / OPFS              |
|   - Commercial Claims & Estimating   |                                                   |   - Delta Sync & Media Queue Worker  |
|   - MDR Document Auto-Collation      |                                                   |   - ITP Witnessing & Markups         |
+--------------------------------------+                                                   +--------------------------------------+
```

### 1.1 Multi-Tenancy & Tenant Isolation
* **Tenant Isolation Model**: Tenant isolation is enforced at the database layer using Row-Level Security (RLS) on PostgreSQL 16. Every relational table must include a mandatory `tenant_id: UUID NOT NULL` indexed foreign key referencing the `tenants` directory table.
* **Database Session Context**: Application connection pools use parameterized session configuration:
  ```sql
  SET LOCAL app.current_tenant_id = '018f3a20-4e3b-7221-9ac4-b1c098675df1';
  ```
* **Storage Isolation**: Object storage (S3-compatible / MinIO) applies tenant prefix partitioning (`s3://kerp-vault/{tenant_id}/{project_id}/{module_namespace}/{hash}.ext`) with short-lived presigned URLs for viewing and uploading.
* **Amendment (user, 2026-11-13; TD-120).** One tenant per deployment, hidden from users: login resolves it from deployment configuration and no screen or request names it. Admin grants, project creation, the lobby and the project picker: `KERP-CONSOLIDATION-AND-DIFF.md` §4.1. Hosting region is a per-deployment setting (§4.4 there, TD-171).

### 1.2 Timezone & Spatial Anchoring Standards
* **Timestamp Storage**: All database entities store temporal values strictly in UTC using `TIMESTAMPTZ`.
* **Presentation Layer Conversion**: Each Project record holds a target operating IANA timezone (e.g., `Australia/Perth`, `America/Edmonton`). The client application transforms all timestamps into the Project Site Timezone for display, reporting, and daily field logs.
* **Spatial Anchoring**: Every physical asset, workpack, ITP inspection point, and defect pin records PostGIS spatial data (`ST_Point(lon, lat, elevation)` in EPSG:4326/WGS84) alongside relative engineering grid coordinates (e.g., Module Yard Bay 4, Level 02, Axis C-14).

### 1.3 Offline-First SiteSync Engine
Field operations frequently occur in remote mine sites, offshore vessels, tank interiors, and industrial plant structures lacking cellular/Wi-Fi connectivity.
* **Local Storage Layer**: Mobile clients maintain an embedded, schema-mirrored SQLite database (via WASM/OPFS on web or native SQLite via Capacitor/Tauri on tablets).
  **Amended (user, 2026-11-13; TD-161):** the Capacitor Android build (Android 11+, managed Play/MDM) uses SQLCipher-encrypted SQLite with media on the device file system; web-only PWA apps use Dexie (IndexedDB). Both implement one client storage contract and the same HTTP sync contract (`KERP-CONSOLIDATION-AND-DIFF.md` §6.2). Tauri is not a target.
* **Sync Log Queue**: Every mutation initiated offline generates an immutable event envelope in the local `outbox_mutations` table:
  ```json
  {
    "client_mutation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "tenant_id": "018f3a20-4e3b-7221-9ac4-b1c098675df1",
    "user_id": "018f3a20-7f2a-7119-8654-e4c1945620aa",
    "timestamp_utc": "2026-11-08T14:32:00.000Z",
    "entity_name": "itp_step_execution",
    "entity_id": "4fa85f64-5717-4562-b3fc-2c963f66afa6",
    "operation": "UPDATE",
    "vector_clock": { "client_device_node_04": 142, "cloud_server": 389 },
    "payload": {
      "status": "HOLD_PASSED",
      "sign_off_notes": "NDT UT 100% accepted per AWS D1.1",
      "reading_value": 14.28
    }
  }
  ```
* **Conflict Resolution Strategy**:
  1. **Strict Deterministic Ordering**: The sync server validates state transitions using domain-specific state machine rules rather than crude database timestamp overwrites.
  2. **Field Sign-Off Protection (Immutable Approvals)**: If an inspection step has been signed off by a Client Witness Inspector, an offline supervisor device cannot regress that step's status. The server rejects regression and emits an actionable reconciliation event.
  3. **Last-Write-Wins (LWW) with Conflict Log**: Applied to non-critical text fields (e.g., supervisor progress commentary). If both users edit the same text offline, the latest client timestamp wins, but the superseded text is preserved in `entity_history_audit`.
  4. **Amended by ADR 0005 (accepted 2026-11-10).** Tablet mutations carry only the changed paths, each with a base hash (`changes`, not a whole `payload`), and are applied per field against a field-ownership map (`p6` / `office` / `field`); LWW above applies only to `office` text edited concurrently by two desktop users. Office edits to field-owned values of a checked-out record are blocked. Detail: `docs/spec/modules/p6-import-and-rsw-matching.md` §7–§8.

---

## 2. Exhaustive Module Catalog & Operational Mental Models

```
+---------------------------------------------------------------------------------------------------------+
|                                         KERP MODULE SUITE                                               |
+---------------------------------------------------------------------------------------------------------+
|  [MOD-01] Identity, RBAC & Organization Context     |  [MOD-07] Material Control, POs & Traceability    |
|  [MOD-02] Project Master & Engineering Tagging      |  [MOD-08] Quality Control, ITPs & Inspection RFIs |
|  [MOD-03] Estimating, WBS/CBS & Cost Control        |  [MOD-09] Non-Conformance (NCR) & Punchlists     |
|  [MOD-04] Commercial Contracts, Rates & Claims     |  [MOD-10] Asset Integrity, Preservation & Hydro   |
|  [MOD-05] Advanced Work Packaging (EWP/CWP/IWP)     |  [MOD-11] Document Control, Transmittals & MDR    |
|  [MOD-06] Field Execution, Shift Logs & Timesheets  |  [MOD-12] Dynamic Module Builder & Integrations   |
+---------------------------------------------------------------------------------------------------------+
```

---

### [MOD-01] Identity, Access Control & Organization Context

#### 1. What It Is & Purpose
The foundational security, enterprise directory, and contextual authentication boundary for KERP. It guarantees that multinational joint ventures, principal contractors, sub-tier trade partners, client representatives, and regulatory certifiers interact under strictly scoped visibility profiles without security boundary leaks.

#### 2. What It Does
* Enforces role-based and attribute-based access control (RBAC & ABAC).
* Isolates enterprise tenants while allowing cross-tenant collaboration (e.g., a Client Inspector holding accounts across multiple EPC contractors).
  **Amended (user, 2026-11-13; TD-120):** one tenant per deployment, invisible to users; such a person holds one account per deployment. Within a deployment, System admins appoint Project Admins, who create projects and assign users to one or several of them (`KERP-CONSOLIDATION-AND-DIFF.md` §4.1).
* Manages biometric/digital cryptographic signatures for official sign-offs (21 CFR Part 11 and ISO 9001 compliance standards).
* Enforces site-level worker competency and ticket verification (e.g., Working at Heights, Confined Space, NDT Level II certification) prior to assigning field tasks.

#### 3. How It Does It
* Integrates with OpenID Connect (OIDC) / SAML 2.0 (Azure AD, Okta, Ping Identity).
* Stores permission bitmasks evaluated in-memory and embedded within short-lived JWT tokens (15-minute expiry) backed by Redis token revocation lists.
* Implements a granular permission structure: `[module]:[resource]:[action]:[scope]` (e.g., `field_ops:itp_step:sign_off:assigned_subcontractor`).

#### 4. Cross-Module Links & Interactions
* **Upstream**: Corporate Identity Provider (SAML/SCIM).
* **Downstream to [MOD-06] & [MOD-08]**: Blocks allocation of workers to high-risk tasks or ITP step sign-offs if tickets/accreditations are expired or missing.
* **Downstream to [MOD-11]**: Generates cryptographically validated audit manifests for every signature embedded in final Handover Data Books.

#### 5. Field & Office Mental Model
* *Field User (QC Inspector)*: Signs into the ruggedized tablet in the morning via PIN or NFC site badge. The app displays only the inspections for projects they are authorized to witness. When signing off an inspection hold point, their registered digital signature and current geolocation/timestamp are stamped permanently into the record.
* *Office User (Project Controls Director)*: Confines subcontractor access strictly to their contracted work packages while granting the Client Representative read-only visibility into live inspection dashboards and punchlists.

---

### [MOD-02] Project Master, Functional Locations & Engineering Tagging

#### 1. What It Is & Purpose
The digital twin backbone of the industrial facility. It breaks down projects into physical and functional structures (ISO 14224, KKS, or custom client naming conventions), ensuring that all physical assets, components, lines, and spools are uniquely identified and referenced across procurement, construction, inspection, and maintenance.

#### 2. What It Does
* Establishes project breakdown hierarchies: Facility $\rightarrow$ Area $\rightarrow$ Unit $\rightarrow$ System $\rightarrow$ Subsystem $\rightarrow$ Tag / Loop / Spool / Equipment Item.
* Coordinates spool and line numbering directly from engineering Piping & Instrumentation Diagrams (P&IDs) and 3D CAD models.
* Stores physical attribute meta-dictionaries (e.g., design pressure, operating temperature, pipe class, material grade, paint system, insulation thickness).

#### 3. How It Does It
* Uses PostgreSQL recursive common table expressions (CTEs) to model deeply nested parent-child hierarchies with fast path-enumeration query indexes (`ltree` extension).
* Provides automated CSV/Excel/IFC engineering tag ingestion engines with deduplication and revision tracking (handling changes from Rev A to Rev 0).

#### 4. Cross-Module Links & Interactions
* **Upstream**: Engineering design data, 3D model exports, and client tag master spreadsheets.
* **Downstream to [MOD-05] (AWP)**: IWPs query functional tags to establish work boundaries.
* **Downstream to [MOD-07] (Materials)**: Purchase orders and heat-number allocations tie directly to specific equipment and spool tags.
* **Downstream to [MOD-08] (QC/ITP)**: ITP templates instantiate unique inspection instances per tag (e.g., 500 individual weld tags on a single pipeline system).
* **Downstream to [MOD-10] (Preservation)**: Sets scheduled preservation maintenance routines on installed mechanical tags (e.g., motor shaft rotation every 30 days).

#### 5. Field & Office Mental Model
* *Field User (Piping Supervisor)*: Walks up to an installed pump package, scans the physical 2D DataMatrix barcode or NFC tag. The tablet immediately resolves the tag hierarchy (`AREA-02 > UNIT-10 > SYSTEM-310 > P-310-01A`), displaying its P&ID, drawing revision, current hydrotest status, punchlist items, and preservation history.
* *Office User (Lead Systems Completion Engineer)*: Reviews subsystem walkdown readiness. Filters all tags under `System 400 (Fuel Gas)` to confirm that 100% of component tags have cleared mechanical completion and hydrostatic testing.

---

### [MOD-03] Estimating, Work/Cost Breakdown (WBS/CBS) & Cost Control

#### 1. What It Is & Purpose
The engineering cost engine. It calculates project estimates, baseline production budgets, and maintains strict structural alignment between the Work Breakdown Structure (WBS - physical execution deliverables) and Cost Breakdown Structure (CBS - labor, equipment, materials, subcontracts).

#### 2. What It Does
* Calculates estimates using multi-tier norm libraries (e.g., man-hours per diameter-inch of pipe welded, square meters of surface blasted/painted, tons of structural steel erected).
* Establishes the Approved Baseline Budget (Control Estimate).
* Tracks commitments, actual cost burn rates, Earned Value Management (EVM), Cost Performance Index ($CPI = EV / AC$), and Schedule Performance Index ($SPI = EV / PV$).
* Manages internal Change Requests (ICRs) and evaluates the budget impact of field trend notices.

#### 3. How It Does It
* High-performance calculation engine for recalculating complex multi-tier line item margins, equipment rate sheets, indirect cost distribution, and escalation factors.
* Real-time ledger of commitments (allocated purchase orders and subcontracts) versus actual incurred costs (field timesheets, plant equipment hours, consumable allocations).

#### 4. Cross-Module Links & Interactions
* **Downstream to [MOD-04] (Commercial)**: Supplies the cost basis and unit rate benchmarks for Schedule of Rates (SoR) and Variation proposals.
* **Downstream to [MOD-05] (AWP)**: IWPs inherit target baseline man-hours and cost codes directly from the WBS/CBS work items.
* **Upstream from [MOD-06] (Field Execution)**: Daily direct field hours from timesheets and machine hours post directly against CBS cost codes to update Actual Cost ($AC$).

#### 5. Field & Office Mental Model
* *Field User (Superintendent)*: Looks at an assigned Installation Work Pack (IWP). The system indicates that the package has a budget of 480 direct man-hours under Cost Code `CC-321-PIPING-ERECT`. The superintendent manages crew size against this target.
* *Office User (Project Controls Engineer)*: Compares Earned Value against Actual Costs across all piping systems. Flags that Area 3 pipe installation has a $CPI$ of $0.82$, triggering a field investigation into productivity bottlenecks before margins erode.

---

### [MOD-04] Commercial Contracts, Schedule of Rates (SoR) & Progress Claims

#### 1. What It Is & Purpose
The commercial enforcement engine that monetizes construction work. It manages client contracts, Schedule of Rates (SoR) catalogs, subcontractor agreements, Variation Orders (VOs), and automated Progress Claims based on verified field progress.

#### 2. What It Does
* Manages multi-model contracts: Lump Sum, Schedule of Rates (Unit Rate), Cost Plus / Time-and-Materials (T&M), and Target Cost.
* Maintains SoR catalogs with breakdown rules (e.g., Claim 20% on delivery to site, 40% on placement/erection, 30% on test/QA sign-off, 10% on client MDR handover).
* Collates and calculates monthly/bi-weekly Progress Payment Claims (Pay Apps) based on objective field milestone completions.
* Manages Variation Registers: Variation Requests (VR), Variation Notices (VN), and Approved Variation Orders (VO) with complete scope and rate history.
* Evaluates Subcontractor Progress Claims against independent site measurements before certifying payment authorizations.

#### 3. How It Does It
* Rules-driven valuation engine that matches physical field events (ITP step completions, installed quantities) directly to contract claim milestones.
* Produces formal commercial claim schedules (e.g., AIA G702/G703 style or custom contractual formats) with historical retainage/retention tracking and liquidated damages calculation matrices.

#### 4. Cross-Module Links & Interactions
* **Upstream from [MOD-06] & [MOD-08]**: Physical claim percentages are gated by field progress and QA/QC hold sign-offs. If hydrotesting is incomplete, the associated 30% testing milestone cannot be claimed.
* **Downstream to External Finance Systems**: Exports finalized, approved Progress Claim certificates and Subcontractor Payment Certifications to external enterprise general ledgers via secure REST APIs.

#### 5. Field & Office Mental Model
* *Field User (Field Quantity Surveyor)*: Inspects installed electrical cable trays. Inputs the measured installed quantity of 450 meters. The system links this measurement to SoR Item `E-104 (Cable Tray 300mm HDG)` and prepares it for the commercial claim batch.
* *Office User (Commercial Manager)*: Generates the monthly $8.2M Client Progress Claim with a single action. The engine verifies every claimed dollar against signed ITPs, installed quantities, and approved variation orders, preventing client disputes and commercial leakage.

---

### [MOD-05] Advanced Work Packaging (AWP: CWP, EWP, IWP)

#### 1. What It Is & Purpose
The industrial work packaging orchestration engine compliant with the Construction Industry Institute (CII) AWP framework. It bridges engineering design with field execution by organizing work into Engineering Work Packages (EWPs), Construction Work Packages (CWPs), and small, executable Installation Work Packages (IWPs).

#### 2. What It Does
* Organizes work scope into manageable chunks sized for execution by a single crew over a standard shift cycle (e.g., 1–2 weeks / 500–1,000 man-hours).
* Performs automated Constraint Management: monitors and enforces readiness checks across 7 constraint disciplines before an IWP can be released to the field:
  1. Engineering drawings approved for construction (AFC).
  2. Materials bagged, tagged, and staged in laydown yards.
  3. Scaffolding erected and green-tagged.
  4. Plant, cranes, and heavy rigging equipment reserved.
  5. Permits and safety Safe Work Method Statements (SWMS) approved.
  6. Subcontractor labor and certified personnel confirmed.
  7. Preceding trade tasks completed.
* Dynamically calculates IWP Planned, Actual, and Earned progress.

#### 3. How It Does It
* Visual, drag-and-drop planning Gantt and spatial work-scoping workbench.
* Constraint resolution workflow engine with automated notifications to material controllers, rigging superintendents, and safety teams.
* Generates downloadable, digital IWP "Flight Packs" containing all drawings, cutting lists, bolt torque specs, ITP sign-off sheets, and safety permits.

#### 4. Cross-Module Links & Interactions
* **Upstream from [MOD-02]**: Scopes packages using master engineering tags and drawing registers.
* **Upstream from [MOD-07]**: Verifies material availability via 100% Bill of Materials (BOM) reserve checks.
* **Downstream to [MOD-06]**: Foremen execute shifts directly against released, constraint-free IWPs.
* **Downstream to [MOD-08]**: Binds required ITPs to the IWP flight pack.

#### 5. Field & Office Mental Model
* *Field User (General Foreman)*: Reviews the tablet on Thursday to plan next week’s work. Sees that 3 IWPs are available. Selects `IWP-PIP-310-004`; the system indicates 6 of 7 constraints are resolved, but flags that special alloy flange bolts are still in quarantine inspection. The foreman redirects the crew to `IWP-PIP-310-002`, where all constraints are green.
* *Office User (AWP Workpack Planner)*: Tracks package flow through the standard maturity stages: `Drafting -> Scoped -> Constrained -> Ready for Execution -> In Progress -> Mechanically Complete -> Closed Out`.

---

### [MOD-06] Field Execution, Shift Logs & Direct Resource Tracking

#### 1. What It Is & Purpose
The real-time construction delivery heartbeat. It captures direct daily shift logs, crew allocations, timesheet hours, plant/machinery utilization, weather delays, and daily progress quantities directly from the job site workface.

#### 2. What It Does
* Records daily allocation sheets: crew composition, craft types, direct hours worked, non-productive time (waiting on crane, waiting on permits).
* Logs mobile plant hours (cranes, compressors, welding rigs) and tracks fuel/operating statuses.
* Captures daily weather conditions (temperature, wind velocity, rainfall) to support contractual delay notices.
* Captures real-time work quantities installed per shift (e.g., linear meters of conduit pulled, tons of steel set, welds completed).

#### 3. How It Does It
* Rapid-entry mobile UI designed for supervisor tablets, functioning completely offline with local draft persistence.
* Daily sign-off and approval state machine: `Draft Foreman Entry -> General Foreman Review -> Superintendent Approval -> Commercial / Payroll Export Staging`.

#### 4. Cross-Module Links & Interactions
* **Upstream from [MOD-05] (AWP)**: Shifts are logged against specific released IWPs.
* **Downstream to [MOD-03]**: Pushes direct labor and equipment hours against CBS cost codes for real-time daily cost reporting.
* **Downstream to [MOD-04]**: Feeds actual installed quantities into the SoR progress claim engine.
* **Downstream to [MOD-07]**: Logs consumable drawdowns from field stores.

#### 5. Field & Office Mental Model
* *Field User (Rigging Supervisor)*: At 16:30, sits in the site ute, opens the shift log on the tablet. Assigns 6 riggers, 1 crane operator, and 1 100t crane to `IWP-STR-102`. Logs 8 hours regular time, 1.5 hours rain delay, and records that 4 structural steel roof trusses were successfully set and torqued. Taps "Submit Daily Log".
* *Office User (Site Operations Director)*: Views a consolidated daily shift report across all active work areas at 07:00 the following morning. Instantly flags 32 hours of aggregate delay due to high winds, prompting an automated contractual notice of delay draft.

---

### [MOD-07] Material Control, Purchase Orders & Heat Traceability

#### 1. What It Is & Purpose
The industrial supply chain, material control, and positive material identification (PMI) engine. It manages procurement commitments, site deliveries, positive material receiving inspections (MRIR), warehouse/laydown staging, and heat-number tracking from steel mill to final installed spool.

#### 2. What It Does
* Manages Purchase Requisitions (PR), Purchase Orders (PO), and Material Delivery Schedules.
* Material Receiving and Inspection Reports (MRIR): captures quantities received, shipping damages, Material Test Reports (MTRs / Mill Certs), and quarantine holds.
* Maintains full metallurgical trace: Heat Number, Melt Number, Flange Rating, Plate Thickness, Alloy Type, and assigns unique internal trace barcodes.
* Manages warehouse bin locations, laydown yard coordinates, and bag-and-tag staging for IWPs.

#### 3. How It Does It
* Optical character recognition (OCR) parsing of Mill Test Certificates (MTRs) to extract chemical and mechanical properties (e.g., Yield Strength, Tensile Strength, Carbon Equivalent $\%CE$).
* Dynamic barcode/QR scanning engine supporting industrial Bluetooth handheld scanners and ruggedized tablet cameras.
* Integration with site spatial coordinates for pinpointing materials in open-air laydown yards.

#### 4. Cross-Module Links & Interactions
* **Upstream from [MOD-03]**: Material purchase requests validate availability against CBS project budgets.
* **Downstream to [MOD-05] (AWP)**: Clears material readiness constraints when 100% of required line items for an IWP are staged.
* **Downstream to [MOD-08] (QC/Welding)**: Inspectors link the exact pipe heat number and fitting heat number to every single weld execution ID.
* **Downstream to [MOD-11] (MDR)**: MTRs and inspection certs auto-compile into the final turnover package.

#### 5. Field & Office Mental Model
* *Field User (Warehouse Material Controller)*: A flatbed arrives with 40 lengths of high-pressure seamless pipe. Scans the delivery packing slip, verifies physical heat numbers stamped on the pipe bevels against the mill certs, snaps photos of bevel conditions, and marks the shipment as "Accepted into Laydown Yard Bay C-2".
* *Office User (Procurement Expeditor)*: Reviews the material readiness dashboard for upcoming structural packages. Identifies that anchor bolts for Substation 2 are arriving 4 days late, automatically notifying the AWP planner to adjust the schedule.

---

### [MOD-08] Quality Control, ITP Execution & Inspection RFIs

#### 1. What It Is & Purpose
The quality assurance and quality control (QA/QC) verification engine. It translates static Inspection and Test Plans (ITPs) into living digital execution workflows, enforcing witness, hold, surveillance, and review check points across fabrication and construction.

**ITP vs Request for Inspection vs ITR:** the ITP is the plan (*what* must be checked, approved once), the RFI (Request for Inspection) is the trigger (*when* an ITP activity is ready, raised per inspection event), and the ITR (Inspection & Test Record) is the evidence (*proof* it was done, feeds the MDR). They are one linked chain, not three separate documents — see `docs/spec/modules/quality-and-inspection.md` §ITP, Request for Inspection and ITR.

#### 2. What It Does
* Digital ITP library: manages standardized quality plans for structural, mechanical, piping, electrical, instrumentation, civil, coatings, and insulation.
* Granular step execution: manages multi-party inspection hold points with mandatory digital sign-offs (Contractor QC, Principal Contractor, Third-Party Verifier, Client Inspector).
* Requests for Inspection (RFI / Inspection Call-outs): manages client inspection notices with automated time-window bookings, calendar invites, and notification broadcasts.
* Welding & NDT Tracking: records welder IDs, weld procedures (WPS), consumable batch numbers, non-destructive testing requirements (RT, UT, MT, PT), and radiography interpretation reports.
* Coatings & Environmental Logging: records ambient temperature, relative humidity, dew point, surface profile (microns), wet/dry film thickness (WFT/DFT), and holiday testing results.

#### 3. How It Does It
* Multi-party cryptographic signature engine with role-gated sign-off authorities.
* Specialized inspection sheet generators (Torque Verification, Pressure Test Certificates, Flange Joint Make-Up, Cable Megger Test Sheets).
* Real-time validation algorithms (e.g., flags an environmental failure if coating application is attempted when the substrate temperature is less than $3^\circ\text{C}$ above dew point).

#### 4. Cross-Module Links & Interactions
* **Upstream from [MOD-02]**: Binds inspection steps to individual asset, spool, cable, or weld tags.
* **Upstream from [MOD-07]**: Pulls verified heat numbers, filler metal batches, and MTRs into weld logs.
* **Downstream to [MOD-04]**: Triggers progress claim milestones upon successful completion of mandatory hold points.
* **Downstream to [MOD-09]**: Generates punchlist items or formal Non-Conformance Reports (NCRs) when an inspection step fails.
* **Downstream to [MOD-11]**: Generates finalized, signed PDF inspection records for direct collation into the MDR.

#### 5. Field & Office Mental Model
* *Field User (QC Inspector)*: Receives an RFI on the tablet: "Weld Inspection on Line 4-P-310-001 at 10:00". Inspects the weld, checks root pass and cap visual tolerances per ASME B31.3. Enters welder stencil `W-042`, accepts visual step, snaps a macro photo of the cap, and applies their digital signature. The system unlocks the next ITP step (radiography testing).
* *Office User (Quality Manager)*: Tracks weld repair rates across all active subcontractors. Notices a sub's structural flux-core repair rate rose to $8.4\%$, filters by welder ID, isolates the root cause to a single welder, and suspends their qualification stencil pending retraining.

---

### [MOD-09] Non-Conformance Reports (NCR), Deviations & Punchlists

#### 1. What It Is & Purpose
The discrepancy, non-conformance, and completion-punchlist resolution engine. It ensures all physical defects, specification deviations, and architectural snags are identified, categorized, tracked, rectified, and formally closed out before subsystem completion.

#### 2. What It Does
* Manages Non-Conformance Reports (NCR) through formal engineering dispositions: *Scrap, Rework to Spec, Repair per Approved Procedure, or Use-As-Is (Engineering Deviation)*.
* Manages construction walkdown punchlists categorized into standardized completion categories:
  * **Category A (Hold Punch)**: Safety, hydrotest, or operational integrity blocker. Must be rectified prior to system testing / energization.
  * **Category B (Post-Completion Punch)**: Minor snag (e.g., touch-up paint, missing label plate). Must be rectified prior to final contractual handover.
  * **Category C (Information / Record Update)**: Drawing redline or document update required.
* Pins spatial punchlist items directly onto 2D engineering drawings, isometric markups, or 3D models with high-resolution photographic evidence.

#### 3. How It Does It
* State machine with multi-party approval loops: `Issue -> Root Cause Analysis -> Proposed Disposition -> Client Engineering Review -> Rectification -> Re-Inspection -> Sign-off Closeout`.
* Interactive canvas markup engine for vector PDFs and mobile photo annotations.

#### 4. Cross-Module Links & Interactions
* **Upstream from [MOD-08]**: Generated directly when an ITP check point fails inspection.
* **Downstream to [MOD-05] (AWP)**: Open Category A punch items automatically lock IWP completion.
* **Downstream to [MOD-10] (Hydrotesting)**: Hydrotest packs cannot be pressurized if any Category A punch items remain open on that test boundary.
* **Downstream to [MOD-11] (MDR)**: Closed NCRs, deviations, and signed punch clearance certificates are compiled into the quality record.

#### 5. Field & Office Mental Model
* *Field User (Client Walkdown Inspector)*: Conducts a final walkdown of System 100 with the contractor superintendent. Spots an ungrounded motor casing. Opens the tablet, taps on the pump location on the drawing, snaps a photo, circles the missing earth bonding wire, selects "Cat A Punch - Electrical", and assigns it to the electrical subcontractor with a 24-hour SLA.
* *Office User (Project Manager)*: Reviews the system handover readiness gauge. The system blocks mechanical completion sign-off because 2 Category A punch items remain open, ensuring incomplete scope is never turned over to the commissioning team.

---

### [MOD-10] Asset Integrity, Equipment Preservation & Hydrotesting

#### 1. What It Is & Purpose
The industrial asset protection and pressure integrity testing suite. It protects capital machinery during storage, erection, and pre-commissioning via scheduled maintenance, while managing the scoping, blinding, filling, testing, and reinstatement of pipe hydrotest and pneumatic test loops.

#### 2. What It Does
* **Equipment Preservation**: Schedules and tracks preservation routines for static and rotating equipment from arrival on site through to commercial operation (e.g., motor winding megger checks, shaft rotations, nitrogen blanket monitoring, desiccant replacement, grease top-ups).
* **Pressure Testing (Hydrotest / Pneumatic)**:
  * Compiles Test Packs: builds boundaries from engineering P&IDs and isometrics.
  * Manages blind lists: tracks placement and removal of test blinds/spades to prevent line over-pressurization or forgotten blinds.
  * Captures test execution: records test pressure, hold times, ambient/pipe temperatures, calibrated gauge serial numbers, and pressure decay logs.
  * Reinstatement walkdowns: verifies de-watering, line drying, flange bolt replacement, and valve restoration.

#### 3. How It Does It
* Automated scheduling engine based on calendar intervals or environmental triggers.
* Specialized pressure test safety logic: calculates target test pressures per ASME B31.1/B31.3 based on design pressure and temperature/material stress factors:
  $$P_{\text{test}} = 1.5 \times P_{\text{design}} \times \left(\frac{S_{\text{test}}}{S_{\text{design}}}\right)$$
* Generates physical tag barcodes for every blind installed, requiring mobile scan-in and scan-out verification during reinstatement.

#### 4. Cross-Module Links & Interactions
* **Upstream from [MOD-02]**: Gathers lines, spools, valves, and instruments into test boundaries.
* **Upstream from [MOD-08]**: Verifies that 100% of welds within the test pack boundary have completed visual and NDT clearance prior to filling.
* **Downstream to [MOD-09]**: Reinstatement walkdown creates punch items for leaks or damaged gaskets.
* **Downstream to [MOD-11]**: Generates certified Hydrostatic Test Verification Dossiers with calibration charts and sign-off sheets.

#### 5. Field & Office Mental Model
* *Field User (Hydrotest Supervisor)*: Performs a hydrotest on Test Pack `HT-310-02`. Pressurizes the line to 45 bar. The calibrated chart recorder logs pressure over the 4-hour hold window. Enters the start pressure, finish pressure, water temperature, verifies zero pressure drop, and prompts the Client Inspector to apply their digital sign-off. Then opens the "Reinstatement Checklist" to scan off every temporary test blind.
* *Office User (Maintenance Preservation Coordinator)*: Monitors 450 rotating equipment packages across a large project. The dashboard flags that 14 turbine compressors require their monthly 1.25-turn shaft rotation this week, automatically generating preservation work orders for the field mechanical team.

---

### [MOD-11] Document Control, Transmittals & Automated MDR Collation

#### 1. What It Is & Purpose
The project document management and automated turnover compilation engine. It controls engineering drawing revisions, tracks technical transmittals, and eliminates the labor-intensive process of assembling Manufacturer Data Reports (MDRs) and Handover Dossiers at project closeout.

#### 2. What It Does
* Engineering Drawing Register: revision control for P&IDs, Isometrics, Single-Line Diagrams (SLDs), structural plans, with redline markup capabilities.
* Formal Transmittal Management: records incoming and outgoing project communications with vendor, contractor, and client receipt tracking.
* **Automated MDR / Turnover Collation Engine**: dynamically assembles thousands of pages of inspection records, MTRs, weld maps, NDT reports, punchlist clearances, and calibration certs into structured, fully bookmarked, client-compliant PDF Handover Books with auto-generated tables of contents.

#### 3. How It Does It
* High-performance background PDF rendering and stream-merging engine.
* Intelligent document linkage: connects source quality data directly to document indexes using metadata tags (`system_id`, `subsystem_id`, `spool_tag`, `weld_id`).
* Automated optical bookmarking, page numbering, cover sheet stamping, and hyperlinked index generation.

#### 4. Cross-Module Links & Interactions
* **Ingests from ALL Modules**: Gathers data and attachments from [MOD-07] (MTRs), [MOD-08] (Signed ITPs, NDT records), [MOD-09] (Punchlist sign-offs), and [MOD-10] (Hydro certificates).
* **Downstream to Client Operations**: Exports the final verified digital asset dossiers directly to the Client Asset Management system (e.g., SAP PM, IBM Maximo).

#### 5. Field & Office Mental Model
* *Field User (Lead Quality Inspector)*: At the end of a milestone, does not spend days printing, scanning, or hand-collating paper binders. Simply confirms that all ITP steps for the subsystem are signed off in the app.
* *Office User (Document Controller / Turnover Lead)*: Selects `System 100 - High Pressure Gas`, selects the Client's MDR specification template, and clicks "Compile MDR Dossier". The background engine gathers 1,840 signed inspection records, MTRs, weld maps, and torque sheets, generates an indexed, searchable, bookmarked 4,000-page dossier with hyperlinked tables of contents in under 3 minutes, and logs a formal outgoing Transmittal to the client.

---

### [MOD-12] Dynamic Module Builder, Custom Fields & Enterprise Integrations

#### 1. What It Is & Purpose
The extensibility and interoperability layer of KERP. It allows project administrators to define custom data fields, configure bespoke inspection sheet templates, design event-triggered notification workflows, and integrate with external enterprise systems without altering core codebase logic.

#### 2. What It Does
* Dynamic schema builder: adds custom project attributes (text, numeric, date, dropdown, user reference, formula) to any native entity (e.g., adding "Mine Safety Incident Class" to Shift Logs).
* Custom Form/Checksheet Designer: drag-and-drop designer for bespoke field inspection forms, safety audits, and environmental walkdown checklists.
* Outbound Event Bus & Webhook Dispatcher: emits standardized JSON payloads on key domain events.
* Enterprise ERP Sync Bridge: provides specialized endpoints for bi-directional synchronization with SAP, Oracle, Xero, and Primavera P6.

#### 3. How It Does It
* Uses PostgreSQL `JSONB` with JSON Schema validation and GIN indexing for performant querying of dynamically extended fields.
* Event-driven architecture powered by transactional outbox tables and Redis stream workers.
* OpenAPI 3.1 compliant REST API and secure webhook delivery service with retry backoff and HMAC-SHA256 signature verification.

#### 4. Cross-Module Links & Interactions
* **Omnipresent across Modules**: Injects custom schema attributes and custom form definitions into [MOD-01] through [MOD-11].
* **Downstream to External Systems**: Dispatches approved progress claims, committed purchase orders, verified equipment timesheets, and completed milestone notices to corporate systems.

#### 5. Field & Office Mental Model
* *Field User (Environmental Field Officer)*: Opens the tablet and selects a custom form created that morning: "Quarterly Tailings Dam Sump Inspection". The form appears seamlessly within the native mobile interface with proper validations, offline caching, and photo attachment fields.
* *Office User (Enterprise Systems Architect)*: Sets up a webhook: `event: claim.approved`. Whenever a commercial progress claim is signed off by the client, KERP dispatches the full line-item breakdown payload directly to the corporate finance bus, creating the corresponding accounts receivable invoice in SAP without manual data re-entry.

---

## 3. Unified Cross-Module Interaction Matrix

The following matrix maps the concrete data contracts, shared keys, and dependencies across the active modules.

| Source Module | Event / Trigger | Target Module | Shared Business Key | Transactional Data Handshake |
| :--- | :--- | :--- | :--- | :--- |
| **[MOD-02] Project Master** | Tag Hierarchy Ingested / Revised | **[MOD-05] AWP** | `tag_id`, `area_id`, `system_id` | Populates scope boundaries for CWP and IWP creation. |
| **[MOD-07] Materials** | MRIR Accepted & Heat Staged | **[MOD-05] AWP** | `po_line_id`, `tag_id`, `heat_number` | Automatically resolves Material Constraints on dependent IWPs. |
| **[MOD-05] AWP** | IWP Released to Field | **[MOD-06] Field Ops** | `iwp_id`, `wbs_id` | Unlocks work package for foreman shift allocation and daily logging. |
| **[MOD-06] Field Ops** | Direct Hours & Quantities Logged | **[MOD-03] Estimating** | `cbs_cost_code_id`, `project_id` | Posts actual labor/equipment hours to calculate live $AC$, $CPI$, and $SPI$. |
| **[MOD-06] Field Ops** | Installed Quantities Submitted | **[MOD-04] Commercial** | `sor_item_id`, `contract_id` | Feeds actual installed quantities into the unbilled claim accumulation register. |
| **[MOD-07] Materials** | Heat Number Assigned to Spool | **[MOD-08] Quality/QC** | `heat_number`, `spool_tag_id` | Populates weld log joint properties with MTR chemical/physical data. |
| **[MOD-08] Quality/QC** | ITP Hold Point Passed & Signed | **[MOD-04] Commercial** | `itp_step_id`, `sor_milestone_id` | Certifies milestone quality gates, unlocking SoR line items for progress billing. |
| **[MOD-08] Quality/QC** | Inspection Step Fails Criteria | **[MOD-09] Punch / NCR** | `itp_step_id`, `tag_id` | Automatically instantiates a Category A Punch or formal NCR workflow. |
| **[MOD-09] Punch / NCR** | Cat A Punch Item Raised | **[MOD-10] Hydrotest** | `system_id`, `test_pack_id` | Locks test pack status, preventing pressurization until punch item is cleared. |
| **[MOD-10] Hydrotest** | Reinstatement Sign-Off Finished | **[MOD-11] MDR Collate** | `test_pack_id`, `subsystem_id` | Transmits signed pressure test dossiers directly into the Turnover compilation queue. |
| **[MOD-08] Quality/QC** | Final ITP Sign-Off Complete | **[MOD-11] MDR Collate** | `system_id`, `tag_id`, `itp_id` | Merges inspection sheets, photo appendices, and NDT reports into the master MDR. |
| **[MOD-04] Commercial** | Progress Claim Formally Approved | **[MOD-12] Integrations** | `claim_id`, `contract_id` | Emits `claim.approved` webhook to external enterprise ERP for GL and AR posting. |

---

## 4. Deep-Dive Technical Specifications

```
+-------------------------------------------------------------------------------------------------------+
|                                  CORE DOMAIN ENTITY ARCHITECTURE                                      |
+-------------------------------------------------------------------------------------------------------+
|                                              tenants                                                  |
|                                                 |                                                     |
|                                              projects                                                 |
|                                                 |                                                     |
|                 +-------------------------------+-------------------------------+                     |
|                 |                               |                               |                     |
|         functional_tags                   wbs_nodes                       cbs_cost_codes              |
|                 |                               |                               |                     |
|                 |                      installation_workpacks                   |                     |
|                 |                               |                               |                     |
|                 +---------------+---------------+                               |                     |
|                                 |                                               |                     |
|                        itp_step_executions                             shift_allocation_lines         |
|                                 |                                               |                     |
|                 +---------------+---------------+                               |                     |
|                 |                               |                               |                     |
|           punch_items                 commercial_claim_lines -------------------+                     |
+-------------------------------------------------------------------------------------------------------+
```

### 4.1 Domain Relational Schemas (PostgreSQL DDL)

The following production DDL models the central operational relationships bridging projects, engineering tags, work packages, field inspections, commercial claim links, and turnover data:

```sql
-- Core Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "postgis";
CREATE EXTENSION IF NOT EXISTS "ltree";

-- 1. Project Master & Spatial Definition
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    code VARCHAR(32) NOT NULL,
    name VARCHAR(255) NOT NULL,
    site_timezone VARCHAR(64) NOT NULL DEFAULT 'Australia/Perth',
    spatial_bounds GEOMETRY(Polygon, 4326),
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- ACTIVE | ON_HOLD | CLOSED (TD-185)
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_project_tenant_code UNIQUE (tenant_id, code)
);
-- Amendment (user, 2026-11-13; TD-185, TD-120): the project also holds currency_code (ISO 4217)
-- and locale (BCP 47) as built in T-0012 (oe_projects_project), and visible_in_lobby BOOLEAN
-- NOT NULL DEFAULT false. Defaults for status, site_timezone (Australia/Perth), currency_code
-- (AUD) and locale (en-AU) come from the tenant (deployment) settings
-- (KERP-CONSOLIDATION-AND-DIFF.md §4.4); the literals here are the shipped defaults.

CREATE INDEX idx_projects_tenant ON projects(tenant_id);

-- 2. Functional Hierarchy & Asset Tags
CREATE TABLE functional_tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tag_number VARCHAR(128) NOT NULL,
    description TEXT,
    hierarchy_path LTREE NOT NULL, -- e.g. "Facility1.Area02.Unit10.System310.Spool04"
    discipline VARCHAR(32) NOT NULL, -- PIPING, ELECTRICAL, STRUCTURAL, MECHANICAL, etc.
    engineering_data JSONB NOT NULL DEFAULT '{}'::jsonb, -- Design pressure, material grade, etc.
    spatial_location GEOMETRY(PointZ, 4326),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_tag_project UNIQUE (project_id, tag_number)
);

CREATE INDEX idx_tags_path ON functional_tags USING GIST (hierarchy_path);
CREATE INDEX idx_tags_tenant_project ON functional_tags (tenant_id, project_id);

-- 3. Advanced Work Packaging: Installation Work Packages (IWPs)
CREATE TABLE installation_workpacks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    package_number VARCHAR(64) NOT NULL,
    name VARCHAR(255) NOT NULL,
    discipline VARCHAR(32) NOT NULL,
    wbs_path LTREE NOT NULL,
    cbs_code VARCHAR(32) NOT NULL,
    budget_man_hours NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
    earned_man_hours NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
    planned_start_date DATE NOT NULL,
    planned_finish_date DATE NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT', 
    -- DRAFT, CONSTRAINED, READY_FOR_EXECUTION, IN_PROGRESS, COMPLETE, CLOSED
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_iwp_project_num UNIQUE (project_id, package_number)
);

CREATE INDEX idx_iwp_tenant_project ON installation_workpacks(tenant_id, project_id);

-- 4. Workpack Constraint Gate Register
CREATE TABLE iwp_constraints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    iwp_id UUID NOT NULL REFERENCES installation_workpacks(id) ON DELETE CASCADE,
    category VARCHAR(32) NOT NULL, -- ENGINEERING, MATERIALS, SCAFFOLDING, RIGGING, PERMITS, ACCESS
    description TEXT NOT NULL,
    is_resolved BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_by_user_id UUID,
    resolved_at TIMESTAMPTZ,
    due_date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_constraints_iwp ON iwp_constraints(iwp_id, is_resolved);

-- 5. Quality Control: ITP Step Execution & Field Verification
CREATE TABLE itp_step_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    project_id UUID NOT NULL REFERENCES projects(id),
    tag_id UUID NOT NULL REFERENCES functional_tags(id),
    iwp_id UUID REFERENCES installation_workpacks(id),
    step_sequence INTEGER NOT NULL,
    step_description TEXT NOT NULL,
    inspection_type VARCHAR(32) NOT NULL, -- WITNESS, HOLD, SURVEILLANCE, REVIEW
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    -- PENDING, RFI_ISSUED, INSPECTED_PASSED, INSPECTED_FAILED, WAIVED
    reading_numeric NUMERIC(12, 4),
    reading_text TEXT,
    reading_metadata JSONB DEFAULT '{}'::jsonb,
    signed_off_by_user_id UUID,
    signed_off_signature_hash VARCHAR(128),
    signed_off_at TIMESTAMPTZ,
    signed_off_location GEOMETRY(Point, 4326),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_itp_step_tag UNIQUE (tag_id, step_sequence)
);

CREATE INDEX idx_itp_exec_tag ON itp_step_executions(tag_id);
CREATE INDEX idx_itp_exec_iwp ON itp_step_executions(iwp_id);

-- 6. Discrepancy & Punchlist Management
CREATE TABLE punch_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    project_id UUID NOT NULL REFERENCES projects(id),
    tag_id UUID REFERENCES functional_tags(id),
    itp_step_execution_id UUID REFERENCES itp_step_executions(id),
    item_number VARCHAR(32) NOT NULL,
    category VARCHAR(8) NOT NULL, -- 'CAT_A', 'CAT_B', 'CAT_C'
    description TEXT NOT NULL,
    pin_coordinates_canvas JSONB, -- Normalized X,Y on drawing canvas
    status VARCHAR(32) NOT NULL DEFAULT 'OPEN', 
    -- OPEN, RECTIFIED, VERIFIED_CLOSED, CANCELLED
    rectified_by_user_id UUID,
    rectified_at TIMESTAMPTZ,
    closed_by_user_id UUID,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_punch_project_num UNIQUE (project_id, item_number)
);

CREATE INDEX idx_punch_project ON punch_items(project_id, category, status);

-- 7. Commercial Claim Linkage
CREATE TABLE commercial_claim_milestones (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    project_id UUID NOT NULL REFERENCES projects(id),
    sor_code VARCHAR(32) NOT NULL,
    description TEXT NOT NULL,
    required_itp_step_id UUID REFERENCES itp_step_executions(id),
    contract_claim_weight_percent NUMERIC(5, 2) NOT NULL, -- e.g. 30.00 for 30%
    is_claim_unlocked BOOLEAN NOT NULL DEFAULT FALSE,
    unlocked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_milestone_claim ON commercial_claim_milestones(project_id, is_claim_unlocked);
```

---

### 4.2 State Machine Transition Rules

The core platform enforces strict workflow states. Entities cannot skip intermediate phases or bypass quality checks.

```
Installation Workpack (IWP) Lifecycle:
[DRAFT] ---> [CONSTRAINED] ---> [READY_FOR_EXECUTION] ---> [IN_PROGRESS] ---> [MECHANICALLY_COMPLETE] ---> [CLOSED]
                 ^                      |                         |                        |
                 |-- (Unresolved Gate)--+                         |-- (Cat A Raised)-------+

ITP Hold Point Lifecycle:
[PENDING] ---> [RFI_ISSUED] ---> [INSPECTED_PASSED] ---> [LOCKED_FOR_TURNOVER]
                     |
                     +---------> [INSPECTED_FAILED] ---> [CAT_A_PUNCH_OR_NCR] ---> [RE_INSPECT]
```

#### 1. Installation Workpack (IWP) State Machine
* `DRAFT`: Initial engineering scoping and tag association.
* `CONSTRAINED`: Workpack is fully defined, but one or more of the 7 execution gates (drawings, materials, permits, scaffolding, rigging, plant, labor) remain unresolved.
* `READY_FOR_EXECUTION`: All constraints are verified and signed off. Flight pack generated. Cannot transition to `READY` if `COUNT(unresolved_constraints) > 0`.
* `IN_PROGRESS`: Package accepted by General Foreman; active shifts logged against it.
* `MECHANICALLY_COMPLETE`: All direct physical work finished. Transition is blocked if any associated `itp_step_executions` are in `PENDING` or `INSPECTED_FAILED` status.
* `CLOSED`: Commercial milestone achieved, MDR records generated, package archived.

#### 2. ITP Step & Quality Hold Point State Machine
* `PENDING`: Step awaiting preceding execution completion.
* `RFI_ISSUED`: Formal inspection call-out dispatched to Client Inspector / Verifier with minimum required contractual notice (e.g., 24 hours).
* **Amended (TD-045, decided 2026-11-10; terminology amended 2026-11-13):** `RFI_ISSUED` is renamed — `INSPECTION_REQUESTED` (name confirmed by the user on TD-045) — and the call-out itself is a separate `inspection_request` record. See `docs/spec/modules/quality-and-inspection.md` §Unified inspection record. In KERP, RFI = Request for Inspection; a Request for Information is a TQ (Technical Query).
* `INSPECTED_PASSED`: Verifier signs off the physical check point. Requires authenticated digital signature, current UTC timestamp, and geographic coordinates.
* `INSPECTED_FAILED`: Inspection fails to meet technical criteria. The engine automatically forces creation of an associated `punch_items` record (Category A) or formal `non_conformance_reports` record.
* `LOCKED_FOR_TURNOVER`: Record verified and immutable. No modifications permitted; queued for MDR document collation.
* **Amended (Pilot-0, planner spec 2026-11-13; TD-165, Q5a):** an inspection record is reviewed before its result stands: the inspector submits (`SUBMITTED`), and a reviewer approves (→ `INSPECTED_PASSED` / `INSPECTED_FAILED` as submitted) or returns it with a reason (`RETURNED` → edited → `SUBMITTED` again). See `docs/spec/modules/quality-and-inspection.md` §Review and return.

---

### 4.3 Delta Sync & Conflict Resolution Protocol

Mobile clients operating offline use an append-only transaction envelope to synchronise state with the central cloud engine upon reconnecting.

```
Client Device                                              Sync Gateway Server
     |                                                              |
     |--- 1. POST /api/v1/sync/push-mutations [Batch Envelope] ---->|
     |                                                              |-- Validate Tenant & Auth
     |                                                              |-- Verify Vector Clock State
     |                                                              |-- Check Business Invariants
     |                                                              |-- Apply Atomic DB Transaction
     |<-- 2. 200 OK { applied: [...], conflicts: [...] } -----------|
     |                                                              |
     |--- 3. GET /api/v1/sync/pull-deltas?since_version=X --------->|
     |                                                              |-- Query Server Commit Stream
     |<-- 4. 200 OK { deltas: [...], new_version: Y } --------------|
     |                                                              |
     |-- Update Local SQLite Master                                 |
     |-- Prune Sent Outbox Records                                  |
```

#### Deterministic Conflict Rules
1. **Critical Quality Protection**: If the central database state for an `itp_step_executions` record is already `INSPECTED_PASSED`, any incoming offline update attempting to revert the status or edit technical measurement data is rejected with code `ERR_CONFLICT_IMMUTABLE_QUALITY_RECORD`. The server writes the rejection to the `sync_conflict_log` for administrative review.
2. **Concurrent Entity Modification**: When two supervisors edit non-conflicting attributes of the same entity (e.g., Supervisor A logs installed linear meters while Supervisor B adds a crane delay note on the shift log), the mutations are merged automatically.
3. **Array Mutation Preservation**: For child collections (e.g., punchlist photos, shift worker allocations), mutations are treated as additive list operations rather than destructive array overwrites.
4. **Per-field three-way apply (ADR 0005).** Rule 2 is decided per changed path: a path not owned by `field` is rejected (`ERR_FIELD_NOT_OWNED`), a locked record is rejected (rule 1), an unchanged server value is overwritten, a changed server value is kept and the tablet value goes to `sync_conflict_log` with a review item. `base_revision` stays on the envelope for idempotency and diagnostics only. See `docs/spec/modules/p6-import-and-rsw-matching.md` §8.
5. **Scope, paging and client ids (Pilot-0, planner spec 2026-11-13; TD-162).** `pull-deltas` is scoped per device and project to the device's download picks plus project reference data, pages with `has_more`, and signals `remove` for records that leave the scope; offline creates carry client-generated UUIDv7 ids; submit waits for attachments. See `KERP-CONSOLIDATION-AND-DIFF.md` §6.4.

---

### 4.4 Automated MDR Dossier Collation Engine

The Turnover & MDR Collation Engine ([MOD-11]) automates the generation of client handover data books via a scheduled, memory-efficient streaming pipeline.

```
                             MDR Compilation Pipeline
                             
[Trigger: Handover Target] ---> [1. Subsystem Scope Query]
                                      |
                                      v
                                [2. Dependency Graph Verification]
                                      |-- Confirm 100% ITPs Passed
                                      |-- Confirm 0 Open Cat A Punches
                                      |-- Confirm 100% Heat MTRs Attached
                                      |
                                      v
                                [3. Document Ingestion & Streaming]
                                      |-- Stream S3 Inspection PDFs
                                      |-- Stream S3 Mill Certs (MTRs)
                                      |-- Stream S3 NDT Radiography Plates
                                      |
                                      v
                                [4. Transformation Engine]
                                      |-- Inject Dynamic Pagination (e.g., Page 42 of 1850)
                                      |-- Apply Client Cover Sheets & Title Stamps
                                      |-- Generate Hierarchical PDF Bookmark Tree
                                      |-- Generate Hyperlinked Table of Contents (TOC)
                                      |
                                      v
                                [5. Atomic Output Archive]
                                      |-- Store Compiled PDF to S3 Vault
                                      |-- Compute SHA-256 Checksum
                                      +-- Generate Client Outgoing Transmittal Notice
```

#### Compilation Rules & Verification Criteria
* **Gating Invariant**: An MDR compilation job cannot execute if:
  $$\sum \text{Open Cat A Punch Items} > 0 \quad \lor \quad \sum \text{Unsigned ITP Hold Points} > 0$$
* **Bookmark Hierarchy Standard**:
  * Level 1: Subsystem Identifier (`System 310 - High Pressure Hydrocarbon Gas`)
  * Level 2: Discipline Dossier (`Piping Quality Dossier`)
  * Level 3: Asset Identification (`Spool ISO-310-0042`)
  * Level 4: Document Type (`1. Inspection Test Record`, `2. Weld Summary Sheet`, `3. Radiography Interpretations`, `4. Material Test Reports`)
* **Integrity Hashing**: Every completed dossier generates an immutable SHA-256 cryptographic hash recorded in the project handover ledger, ensuring client verifiable non-tampering during facilities turnover.

---

## 5. Domain Event Catalog (Event Bus Payloads)

All system state changes emit structured, versioned domain events to the internal event bus. The following payloads illustrate the data contracts governing inter-module automation.

### 5.1 `itp.step.signed_off`
Emitted when an authorized inspector signs off an ITP inspection point. Triggers commercial milestone unlock checks and MDR document indexing.

```json
{
  "event_id": "018f3a35-c491-7d12-a1b2-04e389b418cd",
  "event_type": "itp.step.signed_off",
  "schema_version": "1.0",
  "occurred_at": "2026-11-08T06:14:22.184Z",
  "tenant_id": "018f3a20-4e3b-7221-9ac4-b1c098675df1",
  "project_id": "018f3a22-9011-789a-bcde-fe1234567890",
  "payload": {
    "itp_step_execution_id": "4fa85f64-5717-4562-b3fc-2c963f66afa6",
    "tag_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "tag_number": "310-SP-042",
    "system_id": "SYS-310-GAS-PROC",
    "iwp_id": "a8c0e12a-3b56-42d1-9f12-98e3b1c045aa",
    "step_sequence": 4,
    "inspection_type": "HOLD",
    "status": "INSPECTED_PASSED",
    "reading_numeric": 14.28,
    "signed_off_by": {
      "user_id": "018f3a20-7f2a-7119-8654-e4c1945620aa",
      "full_name": "Marcus Vance",
      "organization": "Third Party Verification Ltd",
      "role": "Client Representative Inspector",
      "signature_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    "coordinates": {
      "latitude": -21.43892,
      "longitude": 115.12398,
      "elevation_meters": 14.5
    }
  }
}
```

### 5.2 `punch.item.created`
Emitted when an inspection check fails or a snag is recorded during a system walkdown. Blocks workpack closeout and hydrotesting if category is `CAT_A`.

```json
{
  "event_id": "018f3a38-89ab-7c34-b2c3-15f498c529de",
  "event_type": "punch.item.created",
  "schema_version": "1.0",
  "occurred_at": "2026-11-08T07:22:10.012Z",
  "tenant_id": "018f3a20-4e3b-7221-9ac4-b1c098675df1",
  "project_id": "018f3a22-9011-789a-bcde-fe1234567890",
  "payload": {
    "punch_item_id": "7ca34b89-21ef-4589-98ab-bc4561230def",
    "item_number": "PUN-310-0084",
    "category": "CAT_A",
    "discipline": "PIPING",
    "tag_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "tag_number": "310-SP-042",
    "system_id": "SYS-310-GAS-PROC",
    "itp_step_execution_id": "4fa85f64-5717-4562-b3fc-2c963f66afa6",
    "description": "Flange face scratched across phonographic serration gasket seating surface. Exceeds ASME B16.5 allowable depth.",
    "raised_by_user_id": "018f3a20-7f2a-7119-8654-e4c1945620aa",
    "assigned_subcontractor_id": "018f3a25-2345-7890-abcd-1234567890ab",
    "target_rectification_date": "2026-11-09T18:00:00.000Z",
    "attachments": [
      {
        "attachment_id": "018f3a3a-12bc-7de4-a1b2-cdef01234567",
        "storage_uri": "s3://kerp-vault/tenant_01/proj_01/punch/PUN-310-0084_scratch.jpg",
        "file_hash_sha256": "5d41402abc4b2a76b9719d911017c592b23a9d3e589cb5103a8f5b8e90637b98"
      }
    ]
  }
}
```

### 5.3 `commercial.claim.approved`
Emitted when a formal Progress Payment Claim is certified by the Principal/Client. Dispatches line-item commitment adjustments and triggers outbound ERP synchronization.

```json
{
  "event_id": "018f3a40-23de-7f56-c3d4-26a509d630ef",
  "event_type": "commercial.claim.approved",
  "schema_version": "1.0",
  "occurred_at": "2026-11-08T10:45:00.000Z",
  "tenant_id": "018f3a20-4e3b-7221-9ac4-b1c098675df1",
  "project_id": "018f3a22-9011-789a-bcde-fe1234567890",
  "payload": {
    "claim_id": "3be56c90-45fa-4789-90bc-df6782341abc",
    "claim_number": "CLAIM-2026-10",
    "contract_id": "CNT-LNG-TRAIN2-MECH",
    "period_start_date": "2026-10-01",
    "period_end_date": "2026-10-31",
    "currency": "AUD",
    "certified_gross_amount": 8425120.50,
    "retention_withheld_amount": 421256.03,
    "certified_net_payable": 8003864.47,
    "external_erp_target": "SAP_S4HANA",
    "approved_by_client_representative": "Eleanor Vance, Project Commercial Director",
    "line_items": [
      {
        "sor_code": "SOR-PIP-CS-02",
        "cbs_cost_code": "CC-310-ERECT-PIPING",
        "description": "Carbon Steel Piping Erection >= 6 inch",
        "unit_of_measure": "LM",
        "quantity_claimed_period": 1420.00,
        "unit_rate": 450.00,
        "line_total_amount": 639000.00
      }
    ]
  }
}
```

---

## 6. Verification & Architectural Sign-Off Matrix

To ensure system integrity across design, deployment, and testing, every implementation phase must satisfy the following acceptance criteria:

1. **Security & Boundary Enforcement**: RLS context is validated across all multi-tenant queries; no single-tenant query executes without an authenticated `tenant_id` session variable.
2. **Offline-First Resilience**: Mobile workers can conduct inspections, record readings, and capture photographs for up to 7 consecutive days offline. Upon reconnection, all operations sync through the deterministic delta engine without data loss.
   **Amended (user, 2026-11-13; TD-170):** the offline window is a tenant setting, default 7 days, maximum 14; sync and sessions are built and tested for 14. Offline work continues on local PIN unlock, independent of the 12-hour server session (`KERP-CONSOLIDATION-AND-DIFF.md` §4.2).
3. **Quality & Commercial Immutability**: No commercial progress claim milestone can be unlocked without its corresponding ITP hold point holding an `INSPECTED_PASSED` status verified by cryptographic signature.
4. **Turnover Readiness**: The MDR collation engine generates bookmarked, numbered, and fully indexed client data dossiers directly from verified operational records without manual desktop collation.