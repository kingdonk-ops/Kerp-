# Legacy spreadsheet inventory — what the field teams run on today

- **Date:** 2026-11-10
- **Input:** 21 workbooks/CSVs supplied by the operations team: two structural-remediation
  projects and one CUI / insulation inspection scope. Profiled sheet by sheet (headers, levels,
  identifier formats, hidden sheets).
- **Purpose:** evidence for [ADR 0003](../decisions/0003-configurable-breakdown-and-terminology.md).
- **Synthetic values only.** Client, site, project, asset, report, drawing and line identifiers
  below are **invented placeholders of the same shape** (CLAUDE.md §6). No real values from the
  source files appear here.
- **These are guidance, not a fixed format.** KERP must be able to reproduce each layout, but
  every one of them must also be changeable per project: more levels, more items, extra
  columns, extra text areas or sections, different names and numbering.

## 1. The files fall into six families

| # | Family | Source files (by kind) | What it really is | KERP home |
|---|---|---|---|---|
| A | **Breakdown / scope** | Asset register export (CSV), front-end-planning scope workbook, CUI scope plan (Gantt workbook), ITP register with an asset × ITP and RSW × ITP matrix | The client's job-pack structure: what work exists, where, how big | `asset_management` breakdown tree (configurable levels — ADR 0003) |
| B | **Old-system import / bulk-update** | Tree import (`Type, Name, ParentRow, ItemCategory, ItemType, …`), bulk-update exports (`ID, Name, Tag, Description, :BARCODE, :RFID, :EXTERNALID, Contract No, Site, Asset`, some with `Work Order(s)`, `Item`) | Exports/imports of the previous inspection system: a flat tree serialised with `ParentRow`, plus attribute columns | Ingestion wizard (T-0107) with **saved mapping profiles** |
| C | **Equipment registers** | Calibrated equipment register (instruments: salt kits, dew-point meters, DFT/UT gauges, welding machines/ovens; hidden tabs: ladders, harnesses, lanyards, chain blocks, slings, clamps, plate grabs) | Two kinds: *calibrated instruments* (calibration history, next-due) and *periodically inspected plant/PPE* (tag, SWL, last inspection, removed-from-service) | Equipment calibration register (T-0112) + a generic "inspected equipment" register type |
| D | **Material registers** | Paint, abrasive, civil/grout (+ receiving log), bolt, steel material-certificate registers | Batch/lot/heat traceability with expiry/shelf life and certificate-received flags | `materials` (heat/batch traceability) — one register engine, typed by material class |
| E | **People** | Hidden set-up tabs inside the NDT workbook (welder IDs, WPS, inspectors, painters, cert numbers); personnel/supervisor columns on checklists | Staff/competency lists embedded in other workbooks | `credentials` (T-0118) + WPS/welder qualification (eligibility) |
| F | **Inspection forms & their registers** | Field inspection checklists (bolt tensioning, concrete pour, pedestal/wall repair), UT-thickness report (+ sketches/photos) and its register, NDT request form + register (per-weld list, coating tabs: surface prep / application / DFT / sign-off, NDT % tracker) | Checklists bound to a breakdown context (client, contract, site, asset, RSW) with repeating rows, hold/review/monitor points, material & instrument references, and registers that number and track them | One-engine templates + `oe_inspections_inspection` (T-0111) + `inspection_requests` (T-0115) |

## 2. Three different breakdown shapes (the core finding)

| Project type | Levels as the client names them | Where work is anchored | Where inspections sit | Progress unit |
|---|---|---|---|---|
| Structural remediation A | Client → Site → **Asset** (conveyor, building) → **RSW** (`101.1`, `SI-001`, `CV100-200.1`) → **Item** (weld `C1`, `HDNR1`, `CP-1`) | RSW | Checklist on RSW; weld/NDT on Item; UT report per RSW | per RSW, % / weld metres |
| Structural remediation B (front-end planning) | Site → **Asset** → **RSW** (`101`) → **Activity** (`B` blast, `P` paint, `G` grout, `W` weld, `CP`, `HDNR1`, `SR`, `BN`) with a composite control code `Asset / Activity / RSW` | RSW (activity is a sub-line of the RSW) | per activity / RSW | Budget qty + UOM (m², kg, Lm, m³, ea) and budget hours |
| CUI / insulation inspection | **Scope** (a tier of the campaign) → **Job Package** (drawing list `L100-DL-INS-00001.001 Rev 0`) → **Line** (`L-100-XX001-1AA1-C1 C1EA`) → optional sub-line (`…-C1D`) | Line | **on the Line**, by method (e.g. real-time radiography, profile radiography, backscatter) | metres of line scanned |

There is **no site / asset / RSW** in the CUI scope at all, and the remediation teams do not use
"job package". Same platform, three vocabularies, three depths, three anchor levels.

## 3. Identifier patterns the system must generate or accept

All examples are synthetic.

| Pattern (shape) | Synthetic example | Meaning |
|---|---|---|
| `<prefix>-<project>-<discipline>-<seq:4>` | `ABC-PRJ1-W-0001` | Weld/QA report number |
| `<report>-C<n>` | `ABC-PRJ1-W-0001-C3` | Weld number inside a report |
| `<project>-NDTR-<seq:3>[_Rev<n>]` | `PRJ1-NDTR-001_Rev1` | NDT request |
| `<prefix>-<project>-UTT-<seq:3>` + Rev letter | `ABC-PRJ1-UTT-001 Rev B` | UT thickness report |
| `<prefix>-<project>-BT-<seq:3> Rev<n>` | `ABC-PRJ1-BT-001 Rev1` | Bolt-tensioning checklist |
| `<asset code>-ITP-<seq:5>` **and** client doc no. | `AREA-ITP-00001` / `C-000000-Q-PLN-00001 Rev. 0` | ITP carries both our number and the client's |
| RSW free text | `101.1`, `101.1-16`, `SI-001`, `RSW 101.1 and 101.3 ; 102 ; 103` | Must be stored as **text**; one request can cover several RSWs |
| Line number | `L-100-XX001-1AA1-C1 C1EA` | CUI line (unit-service-number-spec-insulation class) |

## 4. Data-quality issues the importer must handle

1. **RSW numbers corrupted into dates** by Excel: a code shaped like `101.1-16` is sometimes kept
   as text and sometimes converted to a 1900s date. The importer must read identifier columns as
   text and flag date-typed cells in identifier columns.
2. **Multi-anchor strings** — one NDT request names several assets/RSWs in one cell. Needs a
   many-to-many link (request ↔ anchors), not one FK.
3. **Duplicated rows / typos** (duplicate asset rows; misspelt headers such as `Catergory`,
   `Cerificate`, `Precentage`). Dedup + header-synonym matching in mapping profiles.
4. **Formula debris** (`#N/A`, `#REF!`, `#DIV/0!`, `#VALUE!`) in hidden report tabs — treat as
   null with a warning, never as a value.
5. **Registers copied between workbooks** (the same abrasive/receiving tabs appear hidden in
   three files) — one source register, referenced everywhere.
6. **Calibration history as repeated column groups** (`1st / Report No / 2nd / Report No …`) —
   normalise into one row per calibration event.

## 5. What each family needs from the platform (requirements extracted)

- **Breakdown:** configurable level names, depth and anchor level per project; free-text
  codes; composite display codes (control code); quantity + UOM + budget hours on any level;
  shutdown/online flags and priority/class as custom fields.
- **Imports:** saved column-mapping profiles per client format, `ParentRow`-style tree import,
  update-by-ID mode ("fixed key columns, remaining columns are attributes"), dry-run with row
  errors.
- **Registers:** one register engine (JSONSchema-typed) for instruments, inspected plant/PPE,
  materials (batch/heat/lot + expiry + certificate), people/competencies — with "expires in N
  days" status and a hard gate when used on a form.
- **Forms:** header fields bound to breakdown context by **level role**, not by hard-coded
  name; repeating tables; lookup tables (bolt Ø × length → rotation); H/R/M point markers;
  pickers that pull batch numbers from material registers and instruments from the calibration
  register; photo/sketch pages; client + contractor sign-off blocks.
- **Numbering:** per-project, per-template numbering patterns with revision suffixes.
