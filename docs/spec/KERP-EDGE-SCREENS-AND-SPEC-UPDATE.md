# KERP: Edge Screens, Quality Gates & Consolidated Architecture Addendum
## Milestone 1 & 2 Blueprint: Industrial Field Operations & Quality Enforcement

---

### Executive Summary of Integrated Architectural Upgrades

This document solidifies the architectural consensus across the 19 critical review decisions:
* **TD-002 One-Engine Architecture**: Unified dynamic schema designer, single inspection record lifecycle, and centralized `eligibility` verification service.
* **PostgreSQL `ltree` & JSONB**: High-performance recursive hierarchical asset paths (`tree_path`) coupled with versioned JSONSchema-validated `custom_fields`.
* **Common Expression Language (CEL)**: Sandboxed, dual-runtime (JS client offline / Python server API) rule and dynamic expression engine.
* **Hard Equipment Calibration Gates**: End-to-end OCR/AI ingest of calibration certificates, automated expiry tracking, and hard blocks preventing invalid tools from executing field inspections.
* **Tier-1 Offline Edge Screens**: Complete responsive UI/UX wireframes and interaction specs for **Gate Delivery & OS&D Capture**, **Look-Ahead & Constraint Board**, and **Daily Workface Log**.
* **Project-Isolated Tenancy**: PostgreSQL Row-Level Security (`RLS`) keyed to `tenant_id` bound directly to each specific `project_id`.

---

## 1. The Three Tier-1 Offline Edge Screens (Item 16)

The Tier-1 edge screens represent the highest-frequency operational touchpoints on industrial sites. They operate on a strict **offline-first, touch-optimized, high-contrast** design paradigm compatible with ruggedized tablets (10"–12" landscape/portrait) and personal mobile phones (360px–412px portrait).

```
                      EDGE SCREEN CLIENT SYNCHRONIZATION TOPOLOGY
                      
+-----------------------------------------------------------------------------------+
|                           Mobile Device / Rugged Tablet                           |
|                                                                                   |
|  +--------------------+      +--------------------+      +---------------------+  |
|  |   Gate Delivery    |      |  Look-Ahead Board  |      | Daily Workface Log  |  |
|  +---------+----------+      +---------+----------+      +----------+----------+  |
|            |                           |                            |             |
|            +---------------------------+----------------------------+             |
|                                        |                                          |
|                                        v                                          |
|                         [Reactive UI State (TanStack)]                            |
|                                        |                                          |
|                                        v                                          |
|                     [Local IndexedDB Outbox Engine (Dexie)]                       |
|                      - client_op_id (UUID v4)                                     |
|                      - payload (JSON) + media (Blob)                              |
|                      - sync_status: pending | replaying                           |
+----------------------------------------+------------------------------------------+
                                         |
                                (Network Restored)
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                        KERP Sync Gateway (Central Server)                         |
|  1. Verify Idempotency (`client_op_id` in ledger)                                 |
|  2. Re-evaluate CEL Domain Rules & Invariants                                     |
|  3. Atomic PostgreSQL Commit & Media Pipeline Queue                               |
+-----------------------------------------------------------------------------------+
```

---

### 1.1 Edge Screen 1: Gate Delivery & OS&D Capture (`site_logistics` & `procurement`)

#### 1. Purpose & Field Role
Used by storekeepers, gate receivers, and material controllers at site access points and laydown yards. It matches arriving flatbeds and delivery trucks against mirrored ERP Purchase Orders, captures delivery dockets, records Overs, Shorts, and Damages (OS&D), captures digital signatures, and posts goods receipts directly to site inventory.

#### 2. Key Technical Constraints
* **Offline Storage**: Local IndexedDB holds cached PO lines for the active project; scans process instantly without server roundtrips.
* **Camera Integration**: In-browser WebRTC camera interface for high-speed barcode/QR reading and high-resolution photo documentation.
* **Touch Targets**: Minimum $48 \times 48\text{ px}$ interactive targets; large numeric touchpads for quantity entry with gloves.

#### 3. Responsive ASCII Wireframe

##### Tablet View (Landscape: 1024px+)
```
┌─ GATE 2 RECEIVING ──────────────────────────── [Project: LNG Train 2 ▾] ── [● Offline: 2 Queued] ┐
│                                                                                                  │
│ ┌─ Scan & PO Match ────────────────────────────────────────────────────────────────────────────┐ │
│ │ [ 📷 Scan Delivery Docket / Barcode ]  OR Enter PO: [ PO-2026-4471        ] [ Match PO ]     │ │
│ │ Matched: Supplier: Steel Fabrications Ltd | Carrier: Toll Heavy Haulage | Vehicle: 1HT-902    │ │
│ └──────────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                                  │
│ ┌─ Arrived Line Items ─────────────────────────────────────────────────────────────────────────┐ │
│ │ ITEM / DESCRIPTION              ORDERED     ACCEPTED     UOM    STATUS          ACTION       │ │
│ │ ──────────────────────────────────────────────────────────────────────────────────────────── │ │
│ │ 1. 6" Sch 40 CS Seamless Pipe       120         [ 120 ]   LM    ✔ Matched       [📷 Photo]   │ │
│ │ 2. 6" 150# RF WN Flange A105         40         [  36 ]   EA    ⚠ Short (-4)    [📷 Photo]   │ │
│ │ 3. Spiral Wound Gaskets 6"           40         [  40 ]   EA    ⚠ Damaged (2)   [📷 Photo]   │ │
│ └──────────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                                  │
│ ┌─ OS&D Delta & Laydown Allocation ─────────────────┐ ┌─ Receipt Verification ─────────────────┐ │
│ │ Selected: Item 2 (Flanges)                        │ │ Receiver: Marcus Vance (Storeman)      │ │
│ │ Discrepancy: [X] Shortage   [ ] Damage   [ ] Over │ │ Destination: [ Yard 02 - Bay C-14   ▾] │ │
│ │ Quantity Impacted: [ 4.00 ] EA                    │ │ Driver Name: Frank Miller (Toll)       │ │
│ │ Notes: 4 flanges missing from Crate 03.           │ │ Driver Signature:                      │ │
│ │ Photos: [ 📷 Add Photo (2 Captured) ]             │ │ ┌────────────────────────────────────┐ │ │
│ │ Heat Numbers Verified: [X] Yes   [ ] No           │ │ │ ✍ Frank Miller                     │ │ │
│ │                                                   │ │ └────────────────────────────────────┘ │ │
│ │ [ Clear Discrepancy ]                             │ │ [ Clear ]       [ Confirm & Sync ]     │ │
│ └───────────────────────────────────────────────────┘ └────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

##### Mobile Phone View (Portrait: 360px–412px)
```
┌─ GATE RECEIPT ──────── [● Offline] ┐
│ Project: LNG Train 2               │
├────────────────────────────────────┤
│ [ 📷 SCAN BARCODE / DOCKET ]       │
│ PO: PO-2026-4471 (Steel Fab Ltd)   │
├────────────────────────────────────┤
│ LINE ITEMS (3)                     │
│ ┌────────────────────────────────┐ │
│ │ 6" Sch 40 CS Pipe (120 LM)     │ │
│ │ Recv: [ 120 ] LM   ✔ Complete  │ │
│ └────────────────────────────────┘ │
│ ┌────────────────────────────────┐ │
│ │ 6" 150# RF Flange (40 EA)      │ │
│ │ Recv: [ 36  ] EA   ⚠ SHORT -4  │ │
│ │ OS&D Reason: Missing crate.    │ │
│ │ Photos: [📷 1 Attached]        │ │
│ └────────────────────────────────┘ │
│ ┌────────────────────────────────┐ │
│ │ Spiral Wound Gasket (40 EA)    │ │
│ │ Recv: [ 40  ] EA   ⚠ DAMAGED 2 │ │
│ │ Photos: [📷 2 Attached]        │ │
│ └────────────────────────────────┘ │
├────────────────────────────────────┤
│ Staging: [ Yard 02 - Bay C-14   ▾] │
│ Driver Sign:                       │
│ ┌────────────────────────────────┐ │
│ │ ✍ Frank Miller                 │ │
│ └────────────────────────────────┘ │
│ [ CONFIRM RECEIPT (3 QUEUED) ]     │
└────────────────────────────────────┘
```

#### 4. Interaction & Execution Contract
1. **Entry**: User selects project and taps **Scan Barcode / Docket**. Tablet opens camera stream and evaluates code against cached PO delivery lines.
2. **Line Reconciliation**: Quantities default to ordered values. User adjusts any variance using high-contrast increment buttons or direct numeric input.
3. **OS&D Trigger**: Any variance ($\text{Received} \neq \text{Ordered}$) prompts an immediate modal forcing:
   * Discrepancy type (`SHORTAGE`, `OVERAGE`, `PHYSICAL_DAMAGE`).
   * Mandatory camera snap of the shipping label and damaged item.
4. **Signature & Local Commit**: Driver signs on the capacitive glass. Tapping **Confirm Receipt** generates an immutable `client_op_id` in Dexie IndexedDB, updates local inventory, and stores images in browser cache. On connection restoration, the payload dispatches to the central API.

---

### 1.2 Edge Screen 2: Look-Ahead & Workface Constraint Board (`schedule_advanced`)

#### 1. Purpose & Field Role
Used by superintendents, area planners, and general foremen during weekly Last Planner System (LPS) alignment meetings and daily morning toolbox briefings. It visualizes the 3-to-6 week look-ahead window, exposes the 7 execution constraint categories, captures trade commitments, and logs Plan Percent Complete (PPC) root-cause variances.

#### 2. Key Technical Constraints
* **Visual Kanban Model**: Drag-and-drop or tap-to-reassign cards organized by Work Week ($W+1, W+2, W+3, \dots$) or by RSW Discipline.
* **Live Constraint Flags**: Visual badges displaying the real-time status of Engineering (drawings), Materials (staged), Scaffolding (green tagged), Rigging (cranes), Permits (PTW), Labor (certified trades), and Preceding Quality Holds.

#### 3. Responsive ASCII Wireframe

##### Tablet View (Landscape: 1024px+)
```
┌─ LOOK-AHEAD PLANNING (LPS) ──────────── [Filter: Area 02 - Piping ▾] ──── [PPC Metric: 78% ▲] ───┐
│                                                                                                  │
│  WEEK 45 (Make-Ready / Current)      WEEK 46 (Committed)            WEEK 47 (Look-Ahead)         │
│ ┌──────────────────────────────────┐┌─────────────────────────────┐┌────────────────────────────┐ │
│ │ RSW-PIP-104: Spool ISO-042       ││ RSW-PIP-108: Reroute Drain  ││ RSW-STR-201: Pipe Rack Bay │ │
│ │ Plan: 42 LM | Earned: 28 LM      ││ Plan: 18 LM | Crew: 4 Pipe  ││ Plan: 14 Tons | Crew: 6 Rig│ │
│ │ ──────────────────────────────── ││ ─────────────────────────── ││ ────────────────────────── │ │
│ │ Constraints (6/7 Cleared):       ││ Constraints (4/7 Cleared):  ││ Constraints (2/7 Cleared): │ │
│ │  ✔ Dwg  ✔ Mat  ✔ Perm  ✔ Lab     ││  ✔ Dwg  ⚠ Mat  ✔ Perm  ✔ Lab││  ✔ Dwg  ⚠ Mat  ⚠ Scaff    │ │
│ │  ✔ Rig  ✔ Preceding              ││  ⚠ Scaff  ⚠ Rig             ││  ⚠ Perm ⚠ Lab  ⚠ Crane     │ │
│ │  ⚠ SCAFFOLD: Tag due for inspect ││                             ││                            │ │
│ │ ──────────────────────────────── ││ ─────────────────────────── ││ ────────────────────────── │ │
│ │ Assigned: Super J. Miller        ││ Assigned: Super J. Miller   ││ Assigned: Super T. Gomez   │ │
│ │ Daily Status: [ In Progress 70% ]││ Status: [ Promised / Ready ]││ Status: [ Draft Scope    ]│ │
│ └──────────────────────────────────┘└─────────────────────────────┘└────────────────────────────┘ │
│ ┌──────────────────────────────────┐┌─────────────────────────────┐                               │
│ │ RSW-PIP-106: Fuel Gas Manifold   ││ RSW-ELEC-012: Tray Pull     │                               │
│ │ ✖ BLOCKED: RFI No-Show Delay     ││ Plan: 350 LM | Crew: 4 Elec │                               │
│ │ Cause: Client Witness Absent     ││ All Constraints Cleared:    │                               │
│ │ Claim Impact: 1.5 Days Delay     ││  ✔ ALL GATES GREEN          │                               │
│ │ [ View Delay Event ]             ││ Status: [ Committed ]       │                               │
│ └──────────────────────────────────┘└─────────────────────────────┘                               │
│ ──────────────────────────────────────────────────────────────────────────────────────────────── │
│ Bottom Panel: Reasons for Non-Completion (Last Week Variance Pareto)                            │
│  [■■■■ Late Materials (4)]  [■■ Scaffold Missing (2)]  [■ Engineering Query (1)]  [Log Variance] │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

##### Mobile Phone View (Portrait: 360px–412px)
```
┌─ LOOK-AHEAD ──────── [Wk 45 ▾] ── [PPC 78%] ┐
│ Area: Area 02 - Piping                      │
├─────────────────────────────────────────────┤
│ ACTIVE COMMITMENTS (Wk 45)                  │
│ ┌─────────────────────────────────────────┐ │
│ │ RSW-PIP-104: Spool ISO-042              │ │
│ │ Scope: 42 LM | Completed: 28 LM         │ │
│ │ Status: [ IN PROGRESS (66%)         ▾]  │ │
│ │ ⚠ SCAFFOLD: Tag inspect overdue.        │ │
│ │ GATES: [Dwg✔][Mat✔][Perm✔][Scaff⚠]      │ │
│ └─────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────┐ │
│ │ RSW-PIP-106: Fuel Gas Manifold          │ │
│ │ ✖ BLOCKED (Client Inspection No-Show)   │ │
│ │ Delay Logged: 12.0 Hours                │ │
│ │ [ View Event Evidence ]                 │ │
│ └─────────────────────────────────────────┘ │
├─────────────────────────────────────────────┤
│ UPCOMING LOOK-AHEAD (Wk 46)                 │
│ ┌─────────────────────────────────────────┐ │
│ │ RSW-PIP-108: Reroute Drain (18 LM)      │ │
│ │ GATES: [Dwg✔][Mat⚠][Scaff⚠][Perm✔]      │ │
│ │ Action: Staging anchor bolts required.  │ │
│ └─────────────────────────────────────────┘ │
├─────────────────────────────────────────────┤
│ [ + NEW COMMITMENT ]    [ LOG VARIANCE ]    │
└─────────────────────────────────────────────┘
```

#### 4. Interaction & Execution Contract
1. **Constraint Resolution**: Tapping any red or yellow constraint badge opens the specific blocking module (e.g., tapping `Mat⚠` links directly to laydown bay inventory or procurement delivery status).
2. **Commitment Locking**: On Friday afternoon, the general foreman transitions items from Look-Ahead to `Promised / Committed`. The system captures the weekly snapshot to anchor the baseline for weekly PPC metrics ($PPC = \frac{\text{Completed Commitments}}{\text{Promised Commitments}} \times 100$).
3. **Variance Logging**: If an item is uncompleted at week's end, the supervisor must select an official Reason for Non-Completion (RNC code: *Late Material, Engineering Query, Weather, Client Hold, Preceding Trade, Labor Shortage*).

---

### 1.3 Edge Screen 3: Daily Workface Log (`field_diary`, `field_time` & `progress`)

#### 1. Purpose & Field Role
The core shift execution tool for trade supervisors, riggers, and pipe superintendents. Used at the workface or in the site vehicle at end-of-shift to record crew hours, mobile plant hours, installed quantities, weather delays, safety observations, and workface progress photos.

#### 2. Key Technical Constraints
* **Single-Screen Compilation**: Eliminates multi-app context switching by combining timekeeping, progress measurement, and diary entries into a single cohesive form.
* **One-Tap Shift Duplicate**: Ability to copy crew rosters and equipment allocations from the prior day with a single tap, only editing deviations.
* **Automatic Spatial & Weather Stamping**: Acquires device geolocation and queries local weather telemetry (wind speed, precipitation, temperature) to support downstream delay analysis.

#### 3. Responsive ASCII Wireframe

##### Tablet View (Landscape: 1024px+)
```
┌─ DAILY WORKFACE LOG ────────── [Date: 09-Nov-2026] ── [Shift: Day 06:30–17:00] ── [● Offline] ───┐
│ Supervisor: John Doe | Area: Area 02 - Gas Plant | Crew: Rigging & Mechanical (Crew R-04)        │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ ┌─ Labor Roster (8 Workers) ─── [Copy Yesterday] ┐ ┌─ Plant & Machinery (2 Items) ─────────────┐ │
│ │ NAME / TRADE       HOURS  NPT   NPT REASON     │ │ PLANT ID / TYPE    HOURS  FUEL  STATUS      │ │
│ │ ────────────────────────────────────────────── │ │ ───────────────────────────────────────── │ │
│ │ J. Miller (Rigger)   10    1.5  Wind Delay     │ │ CR-08 (100t Crane)   8.5   Full  Operating   │ │
│ │ K. Thorne (Welder)   10    0.0  None           │ │ CP-02 (Compressor)   6.0   Half  Operating   │ │
│ │ D. Smith (Welder)    10    0.0  None           │ │ [ + Add Equipment / Generator ]           │ │
│ │ S. Clark (Trade)     10    2.0  Waiting Permit │ └───────────────────────────────────────────┘ │
│ │ [ + Add Worker ]                               │ ┌─ Weather & Environmental ─────────────────┐ │
│ └────────────────────────────────────────────────┘ │ Temp: 32°C | Wind: 28 km/h | Rain: 0.0 mm   │ │
│ ┌─ Physical Quantities Installed Today ──────────┐ │ Downtime: 1.5 hrs high wind stoppage crane│ │
│ │ WORKPACK / RSW          QTY INSTALLED    UOM   │ └───────────────────────────────────────────┘ │
│ │ ────────────────────────────────────────────── │ ┌─ Site Photos & Diary Notes ───────────────┐ │
│ │ RSW-PIP-104 (Spool 42)      [ 14.0 ]     LM    │ │ Notes: Set spools 42-A and 42-B in place. │ │
│ │ RSW-STR-201 (Pipe Rack)     [  4.2 ]     TONS  │ │ Torqued flanged joints 01 through 04.     │ │
│ │ [ + Add Scope Line Item ]                      │ │ Photos: [📷 3 Attached] [ Geo: -21.4, 115.1]│ │
│ └────────────────────────────────────────────────┘ └───────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ [ Save Offline Draft ]                  [ Submit Shift Log (Locks & Dispatches to Superintendent) ]│
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

##### Mobile Phone View (Portrait: 360px–412px)
```
┌─ DAILY LOG ────────── [● Offline] ┐
│ 09-Nov-2026 | Day Shift (Crew R-04)│
├────────────────────────────────────┤
│ CREW HOURS (8 Men) [Copy Y'day]    │
│ ┌────────────────────────────────┐ │
│ │ J. Miller (Rigger)             │ │
│ │ Reg: [ 8.5 ] hrs | OT: [ 1.5 ] │ │
│ │ NPT Delay: [ 1.5 ] (High Wind) │ │
│ └────────────────────────────────┘ │
│ ┌────────────────────────────────┐ │
│ │ K. Thorne (Welder)             │ │
│ │ Reg: [ 8.5 ] hrs | OT: [ 1.5 ] │ │
│ └────────────────────────────────┘ │
├────────────────────────────────────┤
│ EQUIPMENT USAGE                    │
│ ┌────────────────────────────────┐ │
│ │ 100t Crane (CR-08): [ 8.5 ] hrs│ │
│ └────────────────────────────────┘ │
├────────────────────────────────────┤
│ INSTALLED WORK (PROGRESS)          │
│ ┌────────────────────────────────┐ │
│ │ RSW-PIP-104: [ 14.0 ] LM       │ │
│ │ RSW-STR-201: [  4.2 ] TONS     │ │
│ └────────────────────────────────┘ │
├────────────────────────────────────┤
│ DIARY & EVIDENCE                   │
│ Weather: High wind recorded.       │
│ Photos: [ 📷 3 Attached ]          │
│ [ SUBMIT DAILY LOG (LOCAL) ]       │
└────────────────────────────────────┘
```

#### 4. Interaction & Execution Contract
1. **Roster Fast-Path**: Tapping **Copy Yesterday** clones the previous shift's names, trades, and plant items. The supervisor adjusts individual deviations or non-productive time (NPT) hours.
2. **Direct CBS Linkage**: Hours worked automatically post against the Cost Breakdown Structure (CBS) cost codes mapped to the target RSW, updating live project burn rates.
3. **Quantity Ingest**: Entered quantities calculate earned physical progress. If the entered quantity exceeds the total engineered scope cap for that RSW, the field highlights in amber and flags for superintendent approval.
4. **Submission Integrity**: Once submitted, the local SQLite database locks the shift record from further edits, computes the hash signature, and queues it for the superintendent's morning approval review.

---

## 2. Common Expression Language (CEL) Implementation Engine (Item 4)

CEL provides mathematically verified, non-Turing-complete, portable logic across TypeScript (offline clients) and Python (server-side verification).

```
                            CEL UNIFIED EVALUATION GRAPH
                            
  [Authoring UI (Designer)] ────► Stores CEL String in Form / ITP Template JSON
                                                   │
                   +-------------------------------+-------------------------------+
                   │                                                               │
                   v (Embedded in App Bundle)                                      v (Executed on Sync Ingest)
       [Offline Tablet / Phone]                                           [KERP FastAPI Backend]
          (cel-js / WebAssembly)                                            (cel-python / celpy)
                   │                                                               │
   Evaluates against local form state:                             Evaluates against DB session context:
     - `fields.reading_1`                                            - `request.payload.fields`
     - `device.ambient_temp`                                         - `asset.attributes`
     - `tool.calibration_due`                                        - `equipment.status`
                   │                                                               │
                   v                                                               v
  Instant UI Validation & Dynamic Display                         Hard Server-Side Gating & Ingestion
```

### 2.1 Concrete CEL Rule Syntax Reference

The following table demonstrates the precise syntax deployed across KERP templates:

| Rule Type | Purpose | Expression Syntax Example | Execution Outcome |
| :--- | :--- | :--- | :--- |
| **Visibility** | Reveal joint defect fields only if cap visual check fails | `fields.visual_result == 'REJECT'` | Field renders when condition evaluates to `true`; otherwise unmounted and sanitized. |
| **Validation** | Enforce substrate temperature rule for protective coatings | `fields.steel_temp >= fields.dew_point + 3.0` | Client and server block submission if steel temperature is within 3°C of dew point. |
| **Dynamic Math**| Calculate average Dry Film Thickness (DFT) | `(fields.dft_1 + fields.dft_2 + fields.dft_3) / 3.0` | Live calculated property rendered read-only in form field. |
| **Range Check** | Verify pipe wall thickness meets minimum engineering spec | `fields.measured_thickness >= asset.attributes.min_wall_thickness` | Flags out-of-tolerance reading; prompts immediate Non-Conformance (NCR) generation. |
| **Hold Gating** | Release test pack for hydrostatic fill | `size(test_pack.open_cat_a_punches) == 0 && size(test_pack.uninspected_welds) == 0` | Blocks test pack status transition to `FILL_AND_PRESSURIZE` until zero Category A snags remain. |

### 2.2 Template Schema Integration
CEL expressions are stored directly in the versioned JSON definition of the form template:

```json
{
  "field_key": "coating_adhesion_psi",
  "label": "Hydraulic Pull-Off Adhesion Test (PSI)",
  "data_type": "number",
  "validation": {
    "cel_expression": "fields.coating_adhesion_psi >= 1450.0",
    "error_message": "Tensile pull-off adhesion failed: Minimum allowable threshold is 1450 PSI per Specification SP-COAT-001."
  },
  "visible_if": "fields.substrate_type == 'STRUCTURAL_STEEL' && fields.coating_system == 'SYSTEM_5_EPOXY'"
}
```

---

## 3. Equipment Calibration Hard Quality Gates (Item 12)

The equipment calibration verification pipeline completely stops invalid measurements from corrupting project turnover dossiers.

```
                           EQUIPMENT CALIBRATION VERIFICATION PIPELINE
                           
 [PDF Calibration Certificate] ───► Presigned Upload ───► /Equipment/{asset_id}/Certs/
                                                                 │
                                                                 v
                                                     [SAQ Worker Background Task]
                                                                 │
                                                   +-------------+-------------+
                                                   │ Tesseract OCR & Extraction│
                                                   +-------------+-------------+
                                                                 │
                                          Extracts: Serial No, Cert No, Cal Date, Expiry
                                                                 │
                                                                 v
                                                   [Automated Matching Engine]
                                      ┌──────────────────────────┴──────────────────────────┐
                                      ▼ (Match)                                             ▼ (Discrepancy)
                        [Status = 'VERIFIED']                                  [Status = 'FLAGGED']
                                      │                                                     │
                        • Prior cert marked 'SUPERSEDED'                       • Task sent to QA Lead inbox
                        • Tool status set to 'ACTIVE'                          • Tool status set to 'QUARANTINED'
                        • Expiry date updated                                  • Inspection selection blocked
                                      │
                                      v
                        [Active in Field Tool Register]
                                      │
                                      v
                        [Inspector Opens Mobile ITP]
                                      │
                                      ├── Gauge Valid: Selectable in dropdown
                                      └── Gauge Expired: LOCKED in Red; Submission Blocked
```

### 3.1 Data Schema DDL (PostgreSQL)

```sql
-- 1. Master Plant & Tool Asset Register
CREATE TABLE oe_equipment_asset (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    asset_number VARCHAR(64) NOT NULL,
    brand VARCHAR(64) NOT NULL,
    model VARCHAR(64) NOT NULL,
    serial_number VARCHAR(128) NOT NULL,
    equipment_type VARCHAR(32) NOT NULL, -- 'calibrated_tool', 'plant_machinery', 'rigging'
    status VARCHAR(32) NOT NULL DEFAULT 'out_of_service',
    -- 'active', 'out_of_calibration', 'sent_for_calibration', 'quarantined', 'out_of_service'
    active_calibration_id UUID,
    calibration_frequency_days INTEGER NOT NULL DEFAULT 365,
    last_calibrated_at DATE,
    calibration_due_date DATE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_equipment_project_asset UNIQUE (project_id, asset_number)
);

CREATE INDEX idx_equipment_lookup ON oe_equipment_asset(project_id, equipment_type, status);

-- 2. Immutable Calibration Certificate History Ledger
CREATE TABLE oe_equipment_calibration_cert (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    equipment_id UUID NOT NULL REFERENCES oe_equipment_asset(id) ON DELETE CASCADE,
    document_id UUID NOT NULL, -- Storage reference to raw PDF certificate in VFS
    cert_number VARCHAR(128) NOT NULL,
    calibration_facility VARCHAR(255) NOT NULL,
    calibrated_date DATE NOT NULL,
    expiry_date DATE NOT NULL,
    verification_status VARCHAR(32) NOT NULL DEFAULT 'draft',
    -- 'draft', 'pending_ai_verification', 'verified', 'discrepancy_flagged', 'superseded'
    ai_extracted_data JSONB DEFAULT '{}'::jsonb,
    verified_by_user_id UUID,
    verified_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    lock_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cal_cert_active ON oe_equipment_calibration_cert(equipment_id, is_active);
```

### 3.2 Hard Field Gate Validation Query
When an inspection checklist is rendered on client tablets, available instruments are dynamically scoped:

```sql
SELECT 
    id, asset_number, brand, model, serial_number, calibration_due_date
FROM oe_equipment_asset
WHERE project_id = :current_project_id
  AND equipment_type = 'calibrated_tool'
  AND status = 'active'
  AND calibration_due_date >= CURRENT_DATE;
```
If an inspector attempts to submit a form containing a tool where `calibration_due_date < CURRENT_DATE`, the backend transaction fails with error `ERR_QUALITY_GATE_EXPIRED_TOOL`.

---

## 4. Reserved Condition Monitoring Location (CML) Schema (Item 11)

To support initial construction and structural remediation without carrying maintenance degradation overhead, the following standard schema definition is reserved on `oe_asset_item`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "ItemCMLStubSchema",
  "type": "object",
  "properties": {
    "cml_tag": { "type": "string", "description": "Identifier e.g. CML-01, TML-104" },
    "nominal_thickness_mm": { "type": "number", "description": "Original design thickness" },
    "minimum_structural_thickness_mm": { "type": "number", "description": "Retirement threshold" },
    "design_corrosion_allowance_mm": { "type": "number" },
    "active_corrosion_rate_mm_yr": { "type": "number", "readOnly": true },
    "last_measured_thickness_mm": { "type": "number", "readOnly": true },
    "last_inspection_date": { "type": "string", "format": "date", "readOnly": true },
    "reinspection_interval_months": { "type": "integer", "default": 12 }
  },
  "required": ["cml_tag", "nominal_thickness_mm", "minimum_structural_thickness_mm"]
}
```

---

## 5. Persona Access Matrix & Site Scoping (Item 13)

The enterprise access model formalizes 15 distinct operational roles with explicit separation of duties (e.g., project administrators cannot release quality hold points without designated QA/QC authority).

### 5.1 Capability Tiers
* **Viewer (V)**: Read-only access across scoped records.
* **Creator (C)**: Ability to draft records, logs, and upload field photos.
* **Editor (E)**: Ability to modify active, unapproved records.
* **Approver / Gate Authority (A)**: Authority to release hold points, accept welds, and sign off commercial pay apps.
* **Signatory (S)**: Authority to apply cryptographic signatures to formal handover packages.

### 5.2 Consolidated Matrix

| Persona Code | Role Name | Default Tier | Operational Scope | Quality Hold Authority | Commercial Authority | Primary Modules |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **PER-01** | Operations Management | Manager | Portfolio (All Projects) | Read-wide | Read-wide | Dashboards, Reporting, Risk |
| **PER-02** | Project Manager | Manager | Assigned Projects | Review only | Full (`A`) | Commercial, Schedule, Variations |
| **PER-03** | Site Superintendent | Manager | Single Project | Review only | Progress sign-off | Workpacks, Schedule, Diary |
| **PER-04** | Trade Supervisor | Editor | Assigned Area / Discipline | None | Labor capture | Field Diary, Workpacks, Punch |
| **PER-05** | Area Planner | Editor | Single Project | None | Earned quantities | Schedule, Look-Ahead, AWP |
| **PER-06** | QA/QC Lead | Manager | Single Project | **Full (`A`)** | None | QMS, ITP, Weld Register, NCR |
| **PER-07** | Site Inspector | Editor | **Site Node Only (Scoped)** | **Site Holds (`A`)** | None | Inspections, ITP Items, Punch |
| **PER-08** | Storekeeper / Yard Lead| Editor | Single Project | Material receipt only| Delivery confirmation| Logistics, Gate, Inventory |
| **PER-09** | Project Administrator | Admin | Single Project | **None (SoD Block)** | None | Users, Settings, Config Packs |
| **PER-10** | Procurement Specialist | Editor | All Projects (Read-wide)| None | PO staging | Procurement, Catalogs, Subbies |
| **PER-11** | HSE Manager | Manager | Portfolio (All Projects) | Safety holds only | None | Safety, Incident, PTW |
| **PER-12** | Human Resources Master | Editor | Portfolio (All Projects) | None | None | Contacts, Credentials, Roster |
| **PER-13** | Quality Director | Manager | Portfolio (All Projects) | Audit oversight | None | QMS Audits, Trend Analytics |
| **PER-14** | Field Worker (Mobile) | Editor | Single Project (Module Grant)| None | Direct timesheet | Field Diary, Deliveries, Workpack |
| **PER-15** | Client Representative | Portal | Granted Subtree Only | **Witness / Co-sign (`S`)**| Claim verification | Client Portal, RFI, ITP Witness |

---

## 6. Project-Scoped Row-Level Security Architecture (Item 8)

KERP enforces tenant isolation where every individual industrial project runs as its own secure logical tenant:

```sql
-- Enforce Row Level Security on Core Table
ALTER TABLE functional_tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE functional_tags FORCE ROW LEVEL SECURITY;

-- Dynamic Tenant Context Policy (no TO clause: applies to PUBLIC, TD-054)
CREATE POLICY project_isolation_policy ON functional_tags
    FOR ALL
    USING (project_id = NULLIF(current_setting('app.current_project_id', true), '')::UUID)
    WITH CHECK (project_id = NULLIF(current_setting('app.current_project_id', true), '')::UUID);
```

**Amendments (user, 2026-11-10).**
- **Policies have no `TO` clause (TD-054).** They apply to `PUBLIC`, i.e. to every role that is
  not `SUPERUSER`/`BYPASSRLS`; the runtime role name is deployment configuration (CI uses
  `kerp_app`). The implemented policies (`project_isolation_policy` with tenant + exact project
  scope, `tenant_level_read_policy` for tenant-level rows) are described in
  `backend/app/core/tenancy/README.md` (T-0007).
- **Explicit multi-project scope (TD-053).** Cross-project reads (portfolio dashboards, PER-01 /
  PER-10 / PER-11 / PER-12 / PER-13) bind an explicit list of allowed project ids to the session
  (`app.allowed_project_ids`, derived from the user's memberships, never from request
  parameters). Reads see rows of any listed project; writes stay limited to the single bound
  `app.current_project_id`. No session has an implicit "all projects of the tenant" scope.
- **Membership-based policy on tenant-level tables (TD-053).** The `projects` table and other
  tenant-level tables that describe a project (e.g. memberships) use a stricter policy: a row is
  visible only when its project is in the session's allowed list (or the session is a tenant
  administrator). A client-portal user bound to project A cannot list project B.

### Session Initialization Handshake
Every API request and background job sets the runtime project identifier within the database transaction:
```python
async def set_db_project_context(session: AsyncSession, project_id: UUID) -> None:
    await session.execute(
        text("SET LOCAL app.current_project_id = :proj_id"),
        {"proj_id": str(project_id)}
    )
```

---

## 7. Delivery Plan & Contract Freeze Gate (Items 17–19)

### 7.1 Phase 1 Backbone-Freeze Contract Gate (`T-0131`)
The rolling-wave development model enforces a formal automated test gate ending Phase 1. 

**Automated Validation Invariants**:
1. **Schema Immutability**: Continuous integration tests assert that no columns in `oe_asset_node` or `oe_asset_item` can be modified or dropped without an accepted, peer-reviewed Architectural Decision Record (`docs/decisions/ADR-xxxx.md`).
2. **CEL Evaluator Conformance**: A test suite executes 120 shared mathematical and logical expressions across both Python (`celpy`) and TypeScript (`cel-js`) environments, asserting exact parity of results and error codes.
3. **Seam Verification**: Service contracts for `run_checks`, `eligibility`, and the VFS routing resolver are cryptographically hash-checked to block unapproved signature drift.

**Freeze scope amendments (ADR 0003, ADR 0004, ADR 0005; accepted 2026-11-10).** The freeze also covers: the `oe_asset_level_def` shape and the **role names** (`grouping`, `physical`, `work_anchor`, `inspection_anchor`, `progress_unit`, `schedule_link`) — level names and keys stay project data; `oe_asset_item.work_anchor_id`; the rule-store shape (`oe_validation_rule_set`, `oe_validation_rule`) with its `kind` values (`check`, `digest`, `autolink`, `notify`, `p6_grammar`, `p6_eligibility`) and `engine` values (`cel`, `regex`); the per-record override record (ADR 0004 Amendment A); and the field-ownership map (`p6` / `office` / `field`).

### 7.2 Updated Task Sequencing Roadmap
```
PHASE 0: Platform Substrate & Security Core
  └── Tenancy RLS, SAQ Jobs, BaseRecordMixin, Asymmetric JWT, CEL Runtimes
  
PHASE 1: Asset Backbone, One-Engine & Quality Core
  ├── T-0101: Asset Hierarchy via PostgreSQL ltree
  ├── T-0110: Unified Dynamic Field Registry & CEL Evaluator
  ├── T-0111: Single Inspection Record & Eligibility Service
  ├── T-0112: Equipment Calibration Register & Hard Ingest Gates
  └── T-0131: [CRITICAL GATE] Phase 1 Backbone Contract Freeze

PHASE 2: Welding Quality, Traceability & WPS
  └── Weld Registers, Welder Continuity Tracking, Heat Batch Verification

PHASE 3: Logistics & Supply Chain
  ├── Track A: Contacts Master Directory & Procurement Staging
  ├── Track B: Site Inventory & Reorder Movement Ledgers
  ├── Track C: Cutting Optimization (1D FFD & 2D Guillotine via rectpack)
  └── Track D: Gate Delivery Edge Screen (Offline OS&D Capture)

PHASE 4: Execution & Last Planner System (LPS)
  ├── Look-Ahead Constraint Board Edge Screen
  ├── Daily Workface Log Edge Screen & CBS Burn Ingest
  └── Resource Demand Rollups (Re-anchored from Phase 3)

PHASE 5: Documents, CDE & Automated MDR Collation
  └── VFS Hierarchy, Direct S3 Presigned Uploads, Background PDF Collation Engine

PHASE 6: Defense, Variations & Commercial Progress Claims
  └── Delay Event Attribution, Time-Impact Analysis, Schedule of Rates Progress Billing

PHASE 7: Multi-Party Client Portal
  └── Scoped Magic-Link Access, Client Witness Scheduling & Digital Co-Signing
```