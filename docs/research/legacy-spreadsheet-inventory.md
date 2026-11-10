# Legacy spreadsheet inventory — what the field teams run on today

- **Date:** 2026-11-10
- **Input:** 21 workbooks/CSVs supplied by the operations team (structural integrity remediation
  projects and one CUI / insulation inspection scope). Profiled sheet-by-sheet (headers, levels,
  identifier formats, hidden sheets).
- **Purpose:** evidence for [ADR 0003](../decisions/0003-configurable-breakdown-and-terminology.md).
  Every file is listed with the KERP concept that must absorb it. No client data is copied into the
  repo — only column shapes and identifier *patterns*.
- **These are guidance, not a fixed format.** KERP must be able to reproduce each layout, but
  every one of them must also be changeable per project: more levels, more items, extra
  columns, extra text areas or sections, different names and numbering.

## 1. The files fall into six families

| # | Family | Files | What it really is | KERP home |
|---|---|---|---|---|
| A | **Breakdown / scope** | Asset Register Report (CSV), Coastal Front End Planning (FEP DPR / FEP RV), Inpex CUI scope (Gantt xlsm), ITP Register → *ITP Matrix (With RSW)* | The client's job-pack structure: what work exists, where, how big | `asset_management` breakdown tree (configurable levels — ADR 0003) |
| B | **Old-system import / bulk-update** | Book3 (Type/Name/ParentRow/ItemCategory/ItemType…), Bulk Update Data ×2 (`ID, Name, Tag, Description, :BARCODE, :RFID, :EXTERNALID, Contract No, Site, Asset`), asset bulk update (4) (`… Work Order(s) … Item`) | Exports/imports of the previous inspection system: a flat tree serialised with `ParentRow`, plus attribute columns | Ingestion wizard (T-0107) with **saved mapping profiles** |
| C | **Equipment registers** | Calibrated Equipment Register (instruments: salt kits, dew-point meters, DFT/UT gauges, welding machines/ovens; hidden sheets: ladders, harnesses, lanyards, chain blocks, slings, clamps, plate grabs) | Two kinds: *calibrated instruments* (cal history, next-due) and *periodically inspected plant/PPE* (tag, SWL, last inspection, removed-from-service) | Equipment calibration register (T-0111) + a generic "inspected equipment" register type |
| D | **Material registers** | Paint, Garnet, Civil (Fosroc + abrasive + receiving), K3 Bolt, Material Certificate Register (steel: PO, cert no, heat no, spec) | Batch/lot/heat traceability with expiry/shelf-life and certificate-received flags | `materials` (heat/batch traceability) — one register engine, typed by material class |
| E | **People** | Hidden "Set Up"/"Setup"/"Sheet1" tabs inside the NDT workbook (welder IDs, WPS, inspectors, painters, cert numbers); "Mechanical Personnel/Supervisor" columns on FICs | Staff/competency lists embedded in other workbooks | `credentials` (T-0117) + WPS/welder qualification (eligibility) |
| F | **Inspection forms & their registers** | FIC Bolt Tensioning (×2), FIC Concrete Pour, FIC Concrete Pedestal/Wall Repair, UTT-004 report (+ sketches/photos), UTT Register, NDT Request & Register (request form, register, CP list per weld, coating report tabs: surface prep / application / DFT / sign-off, NDT % tracker) | Checklists bound to a breakdown context (Client, Contract, Site, Asset, RSW) with repeating rows, hold/review/monitor points, material & instrument references, and registers that number and track them | One-engine templates + `oe_inspections_inspection` + `inspection_requests` |

## 2. Three different breakdown shapes (the core finding)

| Client / department | Levels as the client names them | Where work is anchored | Where inspections sit | Progress unit |
|---|---|---|---|---|
| Structural remediation (Yandi P3) | Client → Site → **Asset** (conveyor, building) → **RSW** (`923.1`, `SI-004`, `YCV210-204.1`) → **Item** (weld `C1`, `HDNR1`, `CP-1`) | RSW | FIC on RSW; weld/NDT on Item; UTT report per RSW | per RSW, % / weld metres |
| Structural remediation (Marandoo FEP) | Site → **Asset** → **RSW** (`901`) → **Activity** (`B` blast, `P` paint, `G` grout, `W` weld, `CP`, `HDNR1`, `SR`, `BN`) with `Control_Code = "Asset / Activity / RSW"` | RSW (activity is a sub-line of the RSW) | per activity / RSW | Budget Qty + UOM (m², kg, Lm, m³, ea) and Budget Hrs |
| CUI / insulation inspection (Inpex T4) | **Scope** ("Tier 4 Full Scope") → **Job Package** (drawing list `L420-DL-INS-70009.001 Rev 0`) → **Line** (`L-420-PR013-1LA3-C1 C4EA`) → optional sub-line (`…-C1D`) | Line | **on the Line**, by method (C-Arm, CP160 profile RT, backscatter) | metres of line scanned (736.64 m total) |

There is **no site / asset / RSW** in the CUI scope at all, and the remediation teams do not use
"job package". Same platform, three vocabularies, three depths, three anchor levels.

## 3. Identifier patterns the system must generate or accept

| Pattern (shape, not data) | Example shape | Meaning |
|---|---|---|
| `<prefix>-<project>-<discipline>-<seq:4>` | `KIS-YAN3-W-0001` | Weld/QA report number |
| `<report>-C<n>` | `…-0001-C3` | Weld/complete number inside a report |
| `<project>-NDTR-<seq:3>[_Rev<n>]` | `YANDI3-NDTR-047_Rev1` | NDT request |
| `<prefix>-<project>-UTT-<seq:3>` + Rev letter | `KIS-YAN3-UTT-004 Rev B` | UT thickness report |
| `<proj>-BT-<seq:3> Rev<n>` | `KIS-YD3-BT-076 Rev1` | Bolt-tensioning FIC |
| `<asset code>-ITP-<seq:5>` / client doc no. | `CLFL-ITP-00003` / `P-…-Q-PLN-00003 Rev. 0` | ITP — own number **and** client number |
| RSW free text | `923.1`, `913.1-16`, `SI-004`, `RSW 209.1 and 209.3 ; 906 ; 905` | Must be stored as **text**; one request can cover several RSWs |
| Line number | `L-420-PR013-1LA3-C1 C4EA` | CUI line (unit-service-number-spec-insulation class) |

## 4. Data-quality issues the importer must handle

1. **RSW numbers corrupted into dates** by Excel (FEP `Sheet5`: `913.1-16` survives, `900.1-16`
   became `1902-06-22`). Import must read identifier columns as text and flag date-typed cells in
   identifier columns.
2. **Multi-anchor strings** — one NDT request names several assets/RSWs in one cell. Needs a
   many-to-many link (request ↔ anchors), not one FK.
3. **Duplicated rows / typos** (`Conveyor YCV321` twice; `Asset Catergory`; `Cerificate No.`;
   `Precentage`). Dedup + header-synonym matching in mapping profiles.
4. **Formula debris** (`#N/A`, `#REF!`, `#DIV/0!`, `#VALUE!`) in hidden report tabs — treat as
   null with a warning, never as a value.
5. **Registers copied between workbooks** (the same Abrasive/Receiving tabs appear hidden in
   three files) — one source register, referenced everywhere.
6. **Calibration history as repeated column groups** (`1st / Report No / 2nd / Report No …`) —
   normalise into one row per calibration event.

## 5. What each family needs from the platform (requirements extracted)

- **Breakdown:** configurable level names, depth and anchor level per project; free-text
  codes; composite display codes (`Control_Code`); quantity + UOM + budget hours on any level;
  shutdown/online flags and priority/class as custom fields.
- **Imports:** saved column-mapping profiles per client format, `ParentRow`-style tree import,
  update-by-ID mode ("columns A–D fixed, E+ are attributes"), dry-run with row errors.
- **Registers:** one register engine (JSONSchema-typed) for instruments, inspected plant/PPE,
  materials (batch/heat/lot + expiry + certificate), people/competencies — with "expires in N
  days" status and a hard gate when used on a form.
- **Forms:** header fields bound to breakdown context by **level role**, not by hard-coded
  name; repeating tables; lookup tables (bolt Ø × length → rotation); H/R/M point markers;
  pickers that pull batch numbers from material registers and instruments from the calibration
  register; photo/sketch pages; client + contractor sign-off blocks.
- **Numbering:** per-project, per-template numbering patterns with revision suffixes.
