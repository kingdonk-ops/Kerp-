# Client / QA documents: what they mean for the KERP spec

Scope: the client-facing QA set for a structural-integrity remediation contract (mining client;
named "CLIENT" below): signed ITP, inspection notifications ("RFIs"), completed ITRs/FICs,
client RSW defect reports, a technical query, the client CDRL, and the approved-new vs original
ITR templates.

Privacy: people are referred to by role only. Real document numbers are shown as **shapes**
(e.g. `P-<7d>-<4d>-Q-PLN-<5d>`); anything copied into the repo must use synthetic values
(CLAUDE.md §6, TD-069).

Spec baseline checked: `quality-and-inspection.md`, `documents-and-mdr.md`, `welding-and-nde.md`,
ADR 0003, ADR 0004 (rule-set defaults), `p6-import-and-rsw-matching.md` §9,
`legacy-spreadsheet-inventory.md`, open TODOs (TD-045, TD-062..TD-068 etc.).

---

## 0. Cross-cutting findings (read first)

1. **Two document numbers on everything.** Every client deliverable carries the client's
   controlled number (`P-<project 7d>-<area 4d>-<discipline letter>-<doc type>-<seq 5d>`, e.g.
   `…-Q-PLN-…` for ITPs and report procedures, `…-S-REP-…` for structural reports) **and** the
   contractor number (`<CTR>-<DEPT>-ITP-<6d>`, `<CTR>-<DEPT>-REP-<6d>`), each with its own revision.
   Templates themselves carry a controlled form number + rev in the footer
   (`P-<7d>-<4d>-D-TMP-<5d> Rev <n>` cover sheet; `<contract>-<disc> <tmpl>-<2d>-<2d>-<4d> Rev <n>` weld report;
   `<form>-<6d>_<rev>` corporate forms). The legacy inventory lists ITP double numbering but
   not as a general document property. → **Gap (documents):** every generated document has
   `client_doc_no`, `client_rev`, `contractor_doc_no`, `contractor_rev`, `template_control_no`,
   `template_rev`; all render in header/footer.
2. **Record numbering is per discipline, and the prefixes are inconsistent today.** Seen:
   `<CTR>-<SITE>-CV-RFI-<4d>` (civil), `<CTR>-<SITE>-SS-RFI-<4d>` (mechanical/structural),
   `<CTR>-<SITE>-RFI-<4d>` (coating; no discipline token), `<CTR>-<SITE>-BT-<3d>`, `<CTR>-<SITE>-W-<4d> Rev n`,
   `<CTR>-<SITE>-PAI-<4d>_<n>_Rev<n>`, TQ `<CTR>-<SITE>-TQ-<3d> rev n`. File names use yet another key:
   `<asset abbrev>-<RSW>-<discipline>-<seq>` (e.g. `<ASSET>-<rsw>-W-<4d>`, `<ASSET>-<rsw>-BT-<3d>`).
   → numbering patterns must be per project **and** per record type, with one project prefix
   token (no hand-typed prefixes), plus a configurable **file-name pattern** separate from the
   document number.
3. **RSW identity has three forms.** Full client id `<FLOC>_<seq>` / `<FLOC>-<seq>-<rev>`
   (`<FLOC>_<seq>`, `<FLOC>-<rsw>-<rev>`, `<FLOC>-<rsw>-<n>_Rev<n>`), short site form
   `100.1`, `100.2`, `<rsw>.<sub>` (RSW + sub-number), and grouped form on the ITP (`100`). Several
   records cover several RSWs (`100 & 101`; TQ lists five RSWs; RSW cell `<rsw>.<sub>-<sub> / <asset>`).
   → alias table per RSW node (full id, short code, legacy codes) + multi-anchor link (ADR 0003 §2
   `oe_inspections_request_anchor` must be generalised to **any record**, not only requests).
4. **Two parties, each with its own intervention code per step.** ITP rows and every RFI slot
   carry separate KAEFER and client codes (e.g. KAEFER H / client W, KAEFER W / client R). The
   spec treats hold/witness as one point type per step. → Gap (T-0113/T-0114, TD-063):
   `intervention[party] ∈ {H, W, R, S, M, V, NA}` per ITP step; gating evaluates the party's code.
5. **Code vocabularies differ per form** and must be data: ITP `H/W/R/S`; concrete ITRs `H/R/M`;
   structural ITR `H/M/R/V/W`; RFI `Hold/Witness/Surveillance/Review`. Definitions printed in a
   legend row (`H = Hold, Mandatory Hold Point (formal notification required to the Company)`,
   `W = Witness Point (formal notification required)`, `R = Review point (quality records
   verified and endorsed)`, `S = Surveillance Point (no formal notification required)`).
   CDRL rule: on W the client may not attend and work proceeds; on H work cannot proceed until
   witnessed. → per-project intervention-code catalogue with `requires_notice`,
   `blocks_progress`, `requires_client_signature`, legend text (terminology pack).
6. **Client renamed "FIC" to "ITR"** in every approved template (Field Inspection Checklist →
   Inspection Test Record). The internal supervisor checklist stays "Internal FIC". ADR 0003 §3
   example pack has `"doc.fic": "FIC"`; for this client it must be `ITR`, and the platform needs
   **two document classes**: internal FIC (supervisor, no client sign) and client ITR (QA/QC +
   client sign-off) for the same activity.
7. **The client wants every record traceable to the inspection request that witnessed it.** The
   approved Coating ITR adds an `RFI No.` header field and the approved Welding ITR adds an
   `RFI#` column **per weld**. → link inspection request ↔ ITR (header) and ↔ row/Item.
8. **Spec/TQ-driven acceptance limits.** Acceptance criteria cite clause numbers
   (`Section 6.15.2 of <client spec P> Rev <n>`), and TQs grant project-specific deviations ("refer TQ-<3d>
   for approval to use <product>"). Confirms TD-065 (limits tied to spec/TQ reference).
9. **Legacy outputs leak formula debris** to the client: `0/1/1900`, `00/01/00 0:00`, `#N/A`,
   `#REF!`, `0 µm` rows, `12:00 AM` in empty RFI slots, contact email `#N/A`. → render rule:
   empty values print blank; renderer refuses to issue a document with error tokens.
10. **Retrospective bulk signing.** In the signed ITP every contractor cell has the same date
    and every client cell the same date (approx. 10 days later), i.e. signed at close-out, not at
    each step. KERP will timestamp per step. → **decision TODO:** is a close-out "sign all
    remaining R/S rows" allowed for review/surveillance rows (never for H)? Affects T-0119 and
    TD-070.
11. **Approved templates still contain filled real data** (sample sheets, ~20 site photos in the
    bolt template, names in lookup sheets). Client packs must ship clean templates (TD-069).

---

## 1. Signed ITP (coating) — signed ITP (THE target style)

**What it is.** Client-format ITP "Surface Preparation and Protective Coating – Two Coat System
(Corrosivity C5 – lower 600 mm columns and base plates)", contractor rev 1, 6 pages, signed by
e-signature envelope (envelope ID stamped top-left on every page). Instance for one asset
(transfer tower) and one RSW group.

### Layout for the template engine

**Page 1 – client "Contractor Document Cover Sheet" (portrait A4, client template).**
- Header: client logo (left), title "CONTRACTOR DOCUMENT COVER SHEET"; rule; project
  title banner in grey caps.
- *Document details* (2-col label/value grid): Document Title (2 lines) · Sustaining Capital
  Document No (client doc no) · Asset Document Number · Project / Job Number · Document Sub Type
  (`QITP-Inspection and Test Plan (ITP)`) · Project Title · Tag Reference(s) · Drawing or Model
  Reference(s).
- *Contractor details*: Contractor's Name · Contractor Document No · Contractor Revision.
- Total Number of Pages (including coversheet) · Volume (if required).
- *Revision history* table: `Rev | Issue Status | Revision Date | Prepared By | Reviewed By |
  Approved By`. Superseded rows greyed. Rev sequence `A, B` = Issued for Review, `0, 1` = Issued
  for Use. (In the source the dates/names are shifted by one row – a hand-edit error; generated
  output must bind each row from the revision record.)
- *Document Review Status **<client> OFFICE USE ONLY** (red-bordered box): four check boxes
  `A Accepted · B Accepted – Subject to Comments · C Rejected – Revise and Resubmit · D Reviewed
  for Information only (Receipt acknowledged)`; Reviewed by: Name / Signature / Review Date;
  disclaimer "Review or acceptance … does not relieve the originator of contractual or statutory
  requirements…".
- Footer: cover-sheet template number + Rev + template date.

**Pages 2–6 – ITP body (landscape A4).** Repeated page header (every page):
- Row 1: client logo cell | 3-line centred title (activity / corrosivity category / "2 COAT SYSTEM
  INSPECTION AND TEST PLAN") | blank cell.
- Row 2: "Inspection and Test Plan" | client doc no | blank | "Date Issued:".
- Row 3 (legend): `Action:` | H = Hold, Mandatory Hold Point *(Formal notification required to
  the Company)* | W = Witness Point *(Formal notification required…)* | R = Review point
  *(Quality Records Verified and endorsed)* | S = Surveillance Point *(No formal notification
  required)*.
- Row 4–5: `Job No | Contract No. | Project | Location` labels then values.
- First body page only: block `Asset: | ITP ID: | RSW Numbers:` (instance binding).

Body table columns (11):
| # | Column | Content |
|---|---|---|
| 1 | Item No. | 1…30, continuous across sections |
| 2 | Construction Stages | activity text |
| 3 | Type of Insp/Test | Visual, Calibration test, Set out, NDT Ultrasonic thickness testing, Profile Test AS 3894.5 Method A, AS 3894.6 Method A Alt 2, Dust Tape AS 3894.6 Method C, Hygrometer & Thermometer, Visual & DFT AS 3894.3 Method B, Visual/Review |
| 4 | Test/Insp Done by | party ("Kaefer") |
| 5 | Freq. of Test / Tolerance | "Prior to works commencing", "Per work lot", "Minimum two (2) tests per work lot", "one reading per every hour", "Each coat", "Each mix", "12–48 hrs after final coat", "Each RSW" |
| 6–9 | **Approved by** (group header) → `Kaefer` (code) · `Date & Sign` · `CLIENT` (code) · `Date & Sign` | per-party intervention code + dated signature; "NA" allowed in a sign cell |
| 10 | N.C.R No. | NCR reference if raised at this step |
| 11 | Proof of Conformance / Verifying Documents | record type and, where it exists, the client doc no of the record procedure (UTT report, coating report) |
| 12 | Acceptance Criteria | bullet list: numeric limits + clause references + standards |

Full-width grey **section rows**: (preliminaries, untitled) items 1–7, ACCESS 8–9, SURFACE
PREPARATION 10–18, COATING APPLICATION 19–27, CLOSEOUT 28–30.

Examples of party codes (Kaefer/client): 1–6 H/H (spec, ITP, QC docs, OSH plan, waste plan,
MSDS approved); 7 R/R calibration; 8–9 H/R access, encapsulation; 10 H/H area defined; 11 H/R;
12 H/S; 13 W/R compressed-air quality; 14 W/R UTT after blast; 15 H/W blast cleanliness Sa 2½;
16–18 H/R profile 40–70 µm, chloride < 50 mg/m², dust rating ≤ 1 sizes 3–5; 19 H/R ambient
RH 20–85 %, substrate 10–45 °C, ΔT dew point > 3 °C; 20 R/R materials; 21 S mixing; 22/24 W/S
coat application; 23/25 H/W DFT 250 µm / 500 µm cumulative; 26 H/S sealant; 27 H/H final
inspection; 28 W/S clean-up; 29 R/R QA/QC docs → "RSW Completion Pack" (client-signed ITP +
coating report); 30 H/H area handover each RSW → "Area Handover Sheets completed and signed".

Signatures: contractor signed every row (dated, handwritten-style e-signature); client signed
rows where its code is H (and some W), stamped `DD-MON-YY`.

**Completion Approval block (last page):** `Client Project Representative Name / Signature /
Date` and `KAEFER Representative Name / Signature / Date` side by side (digital signature
appearance with timestamp).

### Workflow implied
Master ITP (client doc no, rev, client review code A on cover) → instantiated per asset + RSW
set (`ITP ID` = asset code + client doc suffix) → steps signed as work progresses, RFIs raised for
H/W steps → completion approval → ITP goes into the RSW Completion Pack (MDR).

### Coverage today
`quality-and-inspection.md` (kind=itp, run_checks on hold/witness), T-0113 (authoring), T-0114
(evaluation), T-0119 (signing), TD-063 (H/W/R as signed steps), TD-067 (report layout).

### GAPS (testable)
- G-ITP-1 ITP step model: `item_no`, `section`, `stage_text`, `test_type`, `standard_refs[]`,
  `performed_by_party`, `frequency_text`, `intervention{party→code}`, `verifying_record_type`
  (+ client doc no), `acceptance_criteria[]` (text + optional CEL limit + clause ref),
  `ncr_id`. Test: a step with Kaefer=H, client=W blocks the next step until both the Kaefer H
  signature exists and the client W is witnessed/waived. (T-0113, T-0114)
- G-ITP-2 Per-party sign cells with `NA` as an explicit signed value (reason required). (T-0119)
- G-ITP-3 Two-level ITP: **master** (client-controlled document with revisions and client review
  code) and **instance** (bound to asset + work-anchor set, own ITP ID). Instance pins master
  revision; a new master rev does not change open instances without explicit migrate. (T-0113)
- G-ITP-4 Render target: cover sheet (client template) + landscape body with repeated header
  band, legend row, instance block, grey section rows, 11 columns as above, completion block.
  Golden-file test: rendering the synthetic fixture reproduces column order, section rows,
  legend text, page count and "Page n of N". (T-0113 + TD-067; renderer in T-0115/T-0119
  reporting port, TD-038)
- G-ITP-5 Cover sheet fields = document metadata (§0.1) + revision history from the revision
  log (A/B = Issued for Review, 0/1… = Issued for Use) + client review status A/B/C/D captured
  back from the client. (documents E-05-01/E-05-03; CDRL §11)
- G-ITP-6 Verifying-document column links to the record template (UTT report, coating report)
  and, after execution, to the actual record instances; the RSW Completion Pack collects them.
  (E-05-05)
- G-ITP-7 Closeout steps "QA/QC documentation completed" and "Area handover each RSW" become
  gates on `work_anchor.complete` (T-0124) and handover (E-04-11 is shift handover, **not** area
  handover – area handover sheet is a separate record type; raise as missed).
- G-ITP-8 Signature appearance: name, role, signed date (per step) printed; e-sign envelope/
  PAdES at issue (E-07-02).

---

## 2. Inspection notifications ("RFIs") – 5 files

Files: civil `…-CV-RFI-0001`, `…-CV-RFI-0002`; mechanical `…-SS-RFI-0003` (result); coating
`…-RFI-0004` (results), `…-RFI-0005` ("for DS" = day shift).

**What they are.** Requests for Inspection (TD-045 – *not* Request for Information): one
notification per **discipline per day** with **up to 4 inspection slots**; the same sheet is
later updated with results and re-issued as "Result(s)".

**Structure.**
- Title by discipline: *Civil / Mechanical / Coating Inspection Notification*; KAEFER logo.
- Header: Contract No. · RFI No. · Project · Contact Name · ITP No. · Contact Email · Scheduled
  Inspection Date.
- Slot × 4: `Inspection Type` (one tick from a **discipline-specific list**:
  civil = Visual after removing loose concrete/grout, Inspection prior to pour, Inspection after
  removal of formwork, Visual finishing, Review; mechanical = Visual weld, Bolt tensioning,
  Gridmesh & clip check, Final inspection; coating = Blast, 1st/2nd/3rd coat, Final) ·
  `Intervention Point` grid with **one row per party** (KAEFER, client) × Hold/Witness/
  Surveillance/Review · `Asset` · `RSW No.` (may hold several: `100 & 101`) · `Scheduled
  Inspection Time` · `Inspection Result` (Accepted / Rejected / Rescheduled; coating adds
  Other) · `Comments` (location – grids, levels, batch numbers, items inspected e.g. CP numbers,
  bolt counts `18 x M20-90/100`, deviations found).
- Footer: Requested By · Position · Contact No. · Email.
- No client signature or result author/time on the sheet.

**Data-quality notes.** Civil sheets carry a *different contract number format* from the other
disciplines (template constant, wrong); ITP No. sometimes full client number, sometimes `002`;
unused slots print `0` and `12:00 AM`; contact email `#N/A`. Coating "batch" = coating work lot
(`Batch #<n> [<grid ref>]`) – a sub-anchor of the RSW, not a material batch.

**Workflow.** Supervisor drafts by ~day before → sent to client → client attends/doesn't →
result recorded per slot (Accepted / Rejected / Rescheduled) → re-issued as result copy →
referenced by ITR (`RFI No.`) and ITP step.

**Coverage.** T-0115 (blocked by TD-045, which already lists most fields), ADR 0003 §2
multi-anchor link.

### GAPS (testable)
- G-RFI-1 Request = header + **1..N slots** (N configurable, default 4); result is **per slot**,
  not per request. Test: one slot Accepted, one Rescheduled → request is partially closed; the
  rescheduled slot spawns a new slot on a new request referencing the original. (T-0115)
- G-RFI-2 Slot intervention captured per party (KAEFER code, client code), defaulted from the
  linked ITP step; mismatch with the ITP raises a warning. (T-0115/T-0114)
- G-RFI-3 Inspection-type list is per discipline template and maps to ITP step(s); selecting a
  type pre-fills ITP No. and the step. (T-0115, T-0113)
- G-RFI-4 Slot anchors: asset + 1..n work-anchor nodes + optional sub-location/work lot
  ("batch", grid/level) + optional Items (CP numbers, weld numbers). (ADR 0003 multi-anchor)
- G-RFI-5 Result record: result, recorded_by, recorded_at, client attendee, comments, photos;
  `Other` result needs a reason. Rejected → NCR/punch prompt (T-0116/T-0117).
- G-RFI-6 Numbering per discipline: `{client_project_prefix}-{disc}-RFI-{seq:4}` with discipline
  token from config (CV, SS, coating has none today → make it configurable, recommend `PC`).
  Shift tag (DS/NS) on the request. (TD-045 Q4)
- G-RFI-7 Rendered notification = discipline layout above, blank slots printed blank, no `#N/A`.
  Re-issue as "Result" keeps the same number with a result suffix/revision.
- G-RFI-8 Contract no., project, contact pulled from project/user config, never template text.
- Answers to TD-045 open questions from evidence: Q3 – "rescheduled" is a slot outcome, not a
  request state; Q4 – numbering as G-RFI-6.

---

## 3. Signed welding report (client-signed) — Welding Quality Assurance Report (completed)

**Structure.** Landscape, 2 pages. Header: Contract · Site · RSW No (`100.1`) · Document Revision ·
Asset · DWG Number (+ drawing rev) · Report no (`<prefix>-W-<4d> Rev 1`) · Revision Date. Column
groups: *Welding traceability & daily weld report* | *Welding consumables report* | *NDT
compliance* | Comments. Columns: Weld No. (`XX-01…`, prefix by item type – grating) · Date
Welded · Welder ID No. · WPS No. · Process (MMAW) · Consumables Batch No · Visual Compliance (C) ·
UT Thickness Report No. · Material Identification / Heat Nos (values like "Unknown Existing",
"<heat>, Unknown Existing") · Weld Length (m) · Electrode Type · % NDT · NDT Method · Joint Type ·
Weld Compliant · NDT Report No. · Comments (location: gridmesh panel # banding repair).
Page 2: **Weld traceability maps** (marked-up sketches with weld IDs), Specifications block
(acceptance code and edition AS 1554.1; test specification; client specification <client spec C1>), NDT
method legend (Vis/RT/UT/PT/MT), "Repair complete and weld compliant", Y / N/A legend. Reviewer
table: Contractor / client / 3rd Party × Name, Position, Date (QC/QA Inspector, QA/QC
Advisor). Footer: template control no + "1 of 2" + Rev.

**Coverage.** `welding-and-nde.md` (oe_weld, NDE results, weld maps into MDR), T-0201..T-0209.

### GAPS
- G-W-1 Remediation welds onto **existing steel with unknown heat**: material identification must
  accept `existing_unknown` (with reason) instead of a heat number. Conflicts with the MDR gating
  invariant "100 % heat MTRs attached" (`documents-and-mdr.md`). Test: compile allowed when
  every weld's base metal is either MTR-linked or flagged existing-unknown and client-accepted.
  (T-0205, E-05-05) — **raise spec-gap**.
- G-W-2 Weld report is a **document grouping welds** (report no + rev, drawing + rev, revision
  date) – spec has welds but no report entity. Report revision re-issues keep weld rows. (T-0203)
- G-W-3 Fields missing from `oe_weld`: consumables batch no (link to materials register),
  electrode classification, weld length (m), UT thickness report no (link to UTT ITR), visual
  compliance, % NDT, comments/location, **RFI#** (approved template), **weld size specified /
  achieved / compliant** (approved template validates specified 3–12 mm, achieved 3–20 mm).
  (T-0203)
- G-W-4 Weld map page: sketch/marked-up drawing with weld IDs (markups E-05-02) – CDRL G054.
- G-W-5 Three-party review block (contractor, client, third party) with name, position, date.
  (T-0119, E-07-02)
- G-W-6 Report footer shows standards block (acceptance code + edition, client spec) from the
  project rule set.

---

## 4. Signed bolt-tensioning report and the supervisors' bolt tensioning FIC

**Signed client record (FIC → now ITR).** Header: Client · Contract · Site · Report Number
(`<prefix>-BT-<3d>`) · Asset · RSW No. · SI No (N/A; meaning unconfirmed – likely Site
Instruction) · Date · Batch No/Cert No. Table: Test Location · Total Bolts/Nuts (as `n/m`) · Bolt/
Nut Size · Test Percentage · Bolt/Nut Grade (`8.8 / 8.8`) · Batch/Cert No · Bolt Length ·
Tension Method (Part Turn / Snug) · Tension Rotation (⅓, ½, ⅔, N/A) · Contractor Initials ·
Visual Compliance. Remarks · Photos · Final verification and sign-off: Contractor (QA/QC
Inspector) name+position, signature, date; client (Project Engineer) name+position, signature,
date (2 days later).

**Internal FIC for supervisors.** Same header (no report no), adds nut type ring/square,
mechanical personnel and supervisor columns, a **lookup table** bolt Ø (M16/M20/M24) × length
band → rotation (⅓ turn = 4–5 o'clock etc.), the part-turn and snug procedures (numbered steps),
and **photo checkpoints** "After step 2 / 3 / 5". No client sign-off.

**Coverage.** Inventory family F (lookup tables), ADR 0003 pack contents.

### GAPS
- G-BT-1 Rotation must be **derived** from the lookup (Ø × length → rotation) and the entered
  rotation validated against it (warn). Lookup table = versioned config table. (T-0110, TD-064)
- G-BT-2 Photo checkpoints bound to procedure steps (required photos per step). (T-0110)
- G-BT-3 Internal FIC and client ITR for the same bolt group: internal record can be promoted /
  summarised into the client ITR rows (no double entry). (T-0113)
- G-BT-4 Bolt batch/cert no picks from the bolt material register; grade pair bolt/nut. (E-03-03)
- G-BT-5 Totals as `inspected/total` counts per row with % computed.

---

## 5. Sample coating report — Structural Integrity Coating Inspection Report (completed)

**Structure (landscape, client-branded "CLIENT Structural Integrity" footer + contractor doc no
`<CTR>-<DEPT>-REP-<6d>`).**
- Project details: Project (+contract) · RSW No. (asset abbrev + RSW) · Report No.
  (`<prefix>-PAI-<4d>_<n>_Rev<n>`) · Item/Location · Asset · Dwg No (+rev) · Specification ·
  ITP No. · Coating System (C5 – steelwork requiring colour-coded topcoats) · Work Description.
- Surface preparation monitoring: chloride test method + specified (< 50 mg/m²) + 2 results;
  HP water wash / solvent clean / compressed-air clean Y/N; surface defects, defects rectified,
  presence of oil Y/N/NA; garnet (abrasive) batch no; substrate; replica tape panels;
  cleanliness grade tick (SSPC SP5/SP10/SP6/SP7/SP11); surface profile specified / min / max /
  average; acceptance date/time/QC inspector.
- Application monitoring rows: date, time, coat (1st/2nd/3rd), product, colour, batch part A,
  batch part B, thinner, % added, thinner batch, applicator, stripe coat Y/N, application method.
- DFT readings (AS 3894.3 Method B): date, time, coat, DFT spec, no. of readings, min, max, avg,
  QC inspector. Test equipment: ambient gauge, profile gauge, chloride kit, DFT gauge body, DFT
  probe – serial numbers.
- Sealant applied Y/N.
- Ambient monitoring (hourly): date, time, RH, ambient, surface, dew point, ΔT (two columns of
  readings).
- Final inspection & release: Contractor Supervisor (conforms Y/N), Contractor QC Inspector
  (conforms Y/N, **cert no** = coating-inspector qualification), client representative "Review
  only"; names, signatures, dates.
- Additional comments: references to TQs authorising product substitutions; post-dated note of
  sealant confirmation.

**Coverage.** `quality-and-inspection.md` coatings section (dew point guard, DFT avg, adhesion).

### GAPS
- G-C-1 DFT statistics: per coat n readings, min, max, avg, cumulative DFT, and the **80/20 rule**
  (<client spec P> §6.17.1 cited in TQ) as CEL; the spec's fixed 3-reading average is too narrow.
  (T-0110 / coatings rule set)
- G-C-2 Dew-point guard uses ΔT = surface − dew point > 3 °C per hourly reading; RH 20–85 %,
  substrate 10–45 °C from the ITP. Limits must be project parameters with clause refs (TD-065).
- G-C-3 Coat records link products and part A/B/thinner batches to the paint material register
  (shelf life) – E-03-03.
- G-C-4 Instrument serials pick from the calibration register (hard gate, T-0112).
- G-C-5 QC inspector cert no printed and gated by credentials (CDRL F033 "NACE requirements –
  coating inspector quals"). (T-0118)
- G-C-6 TQ reference field on the report / per coat (product substitution authority). (E-05-04)
- G-C-7 Report revisioning `_Rev2` with history. Work lots ("batches" of steel) as sub-anchor.
- G-C-8 Client box "Review only" = client code R; no client conformity tick.

---

## 6. Client structural-integrity inspection detailed report, and a single-RSW client report

**What they are.** The **source documents for RSWs**: client structural-inspection report
(`P-<7d>-<area>-S-REP-<5d>` Rev 1) by a third-party inspecting company; one RSW per defect,
3–4 pages each on client template `<client template> rev 2`. the second file is a single-RSW
extract of the same template (scope/drawing sheet for one RSW).

**Per-RSW page set and fields.**
- Common header (every page): Site · Asset · Asset FLOC · Inspection date · Inspecting company ·
  Inspector names · Next inspection (year) · RSW number (`<FLOC>_<3d>` or `<FLOC>-<3d>-<rev>`) ·
  RSW rev · Risk class (I–IV / Class 1–4) · Secondary location (`component – zone – level – side`,
  e.g. "<component> – <member> – <position> – <section>").
- *Defect Identification*: captioned photos, observation text (thickness-loss %).
- *Defect Risk Assessment*: risk scenario, consequence (safety / production / other, production
  rate t/h, production loss h), likelihood A–G, personnel exposure %, exposure-adjusted
  likelihood, 5×7 risk matrix → class, explanatory notes, "will this get worse with time?" Y/N
  (if Y a remedial scope is mandatory regardless of class; if N only for class 3/4).
- *Remedial Scope of Works*: corrosion environment (C3 – Medium / Class 3), **mandated completion
  date** (year), **notification no.** (client CMMS notification; "No Noti required" allowed),
  remedial works text referencing standard repair drawings (`AS-0000-S-<5d>` detail/section),
  general requirements (standard notes drawing, steel spec <client spec C1>, concrete <client spec C2>, coatings
  <client spec P>, design <design doc>), client-only preliminary considerations (online/offline + isolations,
  access, quantities).
- *Defect Map*.

**Link to KERP.** These are exactly what E-05-09 ingests (template recognition – versioned
client template id + rev is printed on every page, which makes recognition deterministic).

### GAPS
- G-RSW-1 RSW node custom fields (client pack field schema): FLOC, RSW full id, short code,
  RSW rev, risk class, secondary location (4 parts), inspection date, next inspection year,
  inspecting company, corrosion environment, mandated completion date, notification no.,
  remedial works text, referenced standard drawings[], governing specs[], preventive Y/N.
  (T-0101/T-0102 field schema; E-05-09 extraction targets)
- G-RSW-2 Template recognition keys on the printed template id + rev
  (`<client template> rev 2`); one PDF → many RSWs, page→RSW map by header RSW number. (E-05-09)
- G-RSW-3 Mandated completion date drives schedule risk findings (E-05-10 reconciliation).
- G-RSW-4 Governing specs on the RSW (<client spec C1>/<client spec C2>/<client spec P>) select which ITPs / rule sets apply
  to the work anchor (auto-suggest ITP instances per RSW; TD-039 noted auto_create_itp as Phase 3
  candidate).
- G-RSW-5 RSW revision handling: RSW rev changes (client re-issues) must version the scope text
  without breaking existing inspection links.

---

## 7. Technical query register — Technical Queries register + TQ forms

**Register** (supplier-side, page header: client, PO no, client contact, project, job
description, scope job no.): TQ Number (+rev) · TQ Date · Author · Issue · Action · Target Date ·
Close Date · Variation · NOD · EOT.

**TQ form**: TQ no (`<prefix>-TQ-<3d> rev n`) · Contract No · Site Location · Discipline
(Material Specs, Coating) · Asset(s) · RSW #(s) (multi, or "-" site-wide) · Reference documents ·
Subject · 1.0 Site Query · 2.0 Proposed Action · Impact: Technical / Schedule / Cost (Y/N) each
with "attachments included" · Affected documents · Requested by (name, position, signature,
date) · Client Response (text; response by name, specialist title, date). The client response
can **narrow scope** ("relates to RSW 100.1 only") and **supersede** the response to an earlier
revision.

**Coverage.** E-06b-02 title mentions "Technical Queries"; E-05-04 rfi/submittals (Request for
Information); no spec text.

### GAPS
- G-TQ-1 TQ record type (in the Request-for-Information module, E-05-04): numbered per project,
  revisions, multi-anchor (assets/RSWs), discipline, references, query, proposal, impact flags,
  attachments, response, responder, dates, status (draft/issued/responded/closed/superseded).
- G-TQ-2 Approved TQ can act as a **concession**: it attaches parameter overrides to rule sets /
  acceptance limits for the named anchors (e.g. alternative coating system, profile 30–50 µm),
  and records citing it show the TQ number. (TD-065 extension; T-0122)
- G-TQ-3 Impact flags Cost/Schedule Y → prompt variation / NOD / EOT records (E-06-03).
- G-TQ-4 Response can change the anchor set (client narrows) – stored as response-side anchors.
- G-TQ-5 Register view with target vs close dates (overdue).

---

## 8. Client CDRL — Contractor Data Requirements List

**What it is.** Client controlled document `<contract>-CDRL-0001`, Rev 1 Issued for Use,
originator / reviewer / approver (roles: contractor-side QA advisor; client), contract phase
`IMP`. Page 1 = matrix, pages 2–3 = description/contents per code.

**Matrix columns.** Doc Code · Doc Type · Delivery Critical (X) · For Tender (X) · **Initial
submission**: Due Date (code), For Information (X), For Review (X), Native Required for Review
Revisions (Yes/No) · **Final submission**: Due Date ("With IOM" / "With MDR" / -), Native
Required of Final Revision (Yes/No) · No. of Hard Copies · Comments.

**Due-date codes.** `<n> <milestone>` where n = weeks and milestone ∈ ACA (after contract award),
APA (after PO award), PMB (pre-mobilisation – i.e. *before*), PCM (pre-commissioning), PTC
(pre-practical completion); recurring PD / PW / PM (per day / week / month). Comments state the
typical rules ("4 weeks prior to pre-mobilisation – 4 PMB", "4 weeks prior to practical
completion – 4 PTC"). Descriptions add time-of-day rules: daily report by 09:00 next day;
weekly the following Monday; monthly 3 working days after the last Friday of the month.

**Categories / codes.** A Contractor management (A001 Contractor Data Schedule – company template,
both numbers, first-submission date; A002 HSEC plan; A003–A005 progress reports D/W/M; A006
schedule (critical path, milestones, CDRL-aligned activities); A007–A009 guarantees, insurance;
A010 controls plan; A011 document management plan; A013 execution plan/work methodology for
complex RSWs; A014 site construction plan; A015 ER plan; A017 QMP; A018 TMP; A019 local
participation; A020 risk register; A021 notice of commencement; A028 pricing; A031 ERP; A032
risk assessment plan; A034 cashflow; A035–A037 fuel/accommodation/flight forecast + 7-day
report; A040 NGER reportable activities). B–E headers empty. F033 coating inspector quals.
**G Quality** (the part KERP generates): G020 ITP, G021 sub-contractor ITP, G022 instrument
calibration certificates & records, G031 materials certification (EN 10204, traceable unique
numbering, verified by acceptable inspection authority), G040 NCRs / concession requests, G041
NDE operator qualifications (certs to state company contract number), G042 NDE procedures
(method, extent, acceptance; cross-ref weld location plan), G043 NDE reports/records (operator,
procedure, acceptance level, results, company order no.), G049 surface protection certificate of
compliance, G050 painting inspection & test results/reports, G051 painting test procedures
(referenced in the ITP), G054 weld & NDE traceability / location plans / maps, G055 WPQR, G056
WPS (all WPS in one submission with WPQ and a status register; fabrication cannot start before
WPS approval), G057 welder qualification certs, G058 welder qualification register, G999 (used
twice!) WPS/PQR register and weld inspector/supervisor quals. H packing (empty). J007 MDR/MRB
(4 PTC, final, native yes), J008 MDR index (6 ACA). Legend: all submissions electronic, PDF +
native per the Contractor Data Requirement Specification. ITP rule (G020 text): "purpose
specific, sequential activities, attribute to be confirmed, procedure, verification record,
inspection activity code for all parties"; must reference supporting documents and acceptance
criteria. Rows are colour-highlighted yellow/orange/green on the G block (meaning not stated –
probably contractor status marking; ask).

Typical G row: Due 4 PMB (procedures, quals, ITPs) or 4 PTC (records, reports, certificates),
For Review, native for review = No, final "With MDR", native final = Yes for G020, G040, G042,
G051, G056.

**Coverage.** `documents-and-mdr.md` (MDR compile, transmittals purpose-coded), T-0120
(requirements & prerequisite docs register), T-0121 (external review cycles), E-05-03, E-05-05.
**No CDRL concept anywhere in the spec.**

### Proposed model (per-project CDRL register) – see summary for the compact version
Tables (module `documents`, project-scoped, `BaseRecordMixin` + RLS):
1. `oe_doc_cdrl` – one per contract: client doc no/rev/status, contract phase, source file
   (immutable), imported_at. Revisions of the CDRL itself are versioned; requirement rows pin
   the CDRL revision.
2. `oe_doc_cdrl_requirement` – `doc_code` (text, not unique: G999 appears twice → unique on
   (cdrl_id, row_no)), `category` (A–J), `doc_type_title`, `description`, `delivery_critical`,
   `for_tender`, `initial_purpose` (information|review), `initial_due` (rule),
   `native_for_review`, `final_with` (none|IOM|MDR), `native_final`, `hard_copies`, `comments`,
   `recurrence` (PD/PW/PM + time-of-day rule), `kerp_sources[]` (mapping to KERP record types /
   templates / registers that satisfy it: G020 → ITP masters; G040 → NCRs; G043 → NDE results;
   G050 → coating ITRs; G054 → weld maps; G058 → welder qualification register export; G022 →
   calibration certs; G031 → MTRs/material certs).
3. `oe_project_milestone` – contract award, PO award, pre-mobilisation, pre-commissioning,
   practical completion dates (forecast/actual); due dates are **computed**
   (`n weeks after ACA`, `n weeks before PMB`) and recalc when milestones move.
4. `oe_doc_deliverable` – a document (E-05-01) bound to a requirement: client doc no,
   contractor doc no, revision log (A,B… Issued for Review; 0,1… Issued for Use), current client
   review code, next due date, status (not started / in prep / submitted / under review / A / B /
   C-resubmit / D / closed).
5. Submission = transmittal (E-05-03) carrying purpose (information/review/final), formats
   (PDF + native flags), hard-copy count; client return records **review code A/B/C/D**
   (the cover-sheet box) + comments; C forces a new revision and re-submission due date.

Checks (`run_checks` rule set `cdrl.*`, ADR 0004 defaults):
- `cdrl.submit`: deliverable has client doc no + cover sheet; PDF present; native present when
  `native_for_review`/`native_final`; revision code follows A→B→0→1 sequence; purpose matches
  requirement.
- `cdrl.due`: overdue / due-in-N-days findings; delivery-critical items escalate.
- Prerequisite gates: e.g. `weld.create` blocked until G056 WPS deliverable has code A/B
  ("fabrication shall not commence before WPS approved"); mobilisation blocked until 4-PMB
  items submitted (warn by default).
- `mdr.compile` (E-05-05): every requirement with `final_with=MDR` has a deliverable at code A
  or B with final native when required; J008 index generated from the requirement list; J007
  dossier follows CDRL category order.
- Content checks per code (e.g. G031 certs state EN 10204 type; G041 certs show contract no.)
  as warn-level rules.
- Recurring A003–A005: generated obligations from recurrence rules (feeds daily report module).
- A001 Contractor Data Schedule is an **export** of this register (both numbers + first
  submission date) in the client's template.
Import: CDRL arrives as client PDF/XLSX → import mapping profile (ADR 0003 §5) incl. due-code
parser (`4 PMB` → {weeks: 4, milestone: PMB, direction: before}).

Lands on: new spec section in `documents-and-mdr.md`; tasks under E-05-01/E-05-03/E-05-05;
T-0120 (requirements/prerequisite register is the natural home for the requirement table) and
T-0121 (external review cycles = review codes A–D).

---

## 9. ITR templates — approved-new vs original

General diff pattern (client demands):
1. Title `FIC for …` → `ITR for …` everywhere (blank template sheets; the filled example sheets
   left in the files still say FIC).
2. Link to the inspection request: `RFI No.` (coating header), `RFI#` per weld row.
3. Explicit numeric acceptance limits and time limits printed on the form (grout).
4. Deferred follow-up checks (7 days after formwork removal) replace one-off checks.
5. More measured fields with validation (weld size specified/achieved/compliant with dropdown
   and integer ranges).
No change in UTT, concrete pour, structural, pedestal templates other than the title. Branding,
images, footers identical (zip media/footers compared) – so the client approved the existing
layout and asked for content changes only.

### 9.1 ITR – Bolt Tensioning (xlsx) — see §4. Diff: title only.

### 9.2 ITR – UTT Report (xlsx) "Ultrasonic Thickness Testing & Approved Comp Plate Report Rev1"
Header: Contract, Client, Report No, Location, Asset No, Date of Inspection, Inspection
Specification (Condition Assessment), Asset Description, Acceptance Criteria, Material Type,
Material Specification, Equipment Used, Plans/Ref Dwgs Y/N, Scope of Inspection.
Results (post blast) per member block: Item description; rows Top flange / Bottom flange / Web;
spots A–F; Minimum allowable steel thickness; Original wall thickness; Compliance.
Approved comp plate list: Location, CP #, Length, Width, Plate (thk), Weight (kg), Weld (m),
Weld size, Comment, TOTAL. "SR" list: Location, Item description, Length, Qty, Comments, Total.
Conclusion: Install comp plate / Blast and paint / Other + Client comment. Notes 1–4 (uniform
spacing, mark readings on member with location ID, mark corrosion boundary, measure E→W/N→S).
Review: Contractor PE, client PE, 3rd party (name, position, signature, date). Sheets: Photos;
BOQ (member list with lengths → m²). Diff: none.
GAPS: G-U-1 computed compliance (min reading ≥ min allowable) and plate weight
(L×W×t×density); G-U-2 approved CP list **creates Items** (CP-n, type compensation plate) under
the RSW with weld metres → weld report and progress (T-0103, T-0203, E-04-03); G-U-3 client
engineer approval of the CP list is a hold before fabrication (T-0114); G-U-4 BOQ m² per member
feeds progress budget quantities (ADR 0003 progress_unit).

### 9.3 ITR – Coating Report (xlsx) — structure as §5.
Diff: adds **RFI No.** header field; ambient table columns reordered (Surface before Ambient),
single-line headers; hourly time rows pre-seeded; "extra ambient crosscheck" folded into the
report sheet; the original's "Residual Dust Rating Result" row is **gone** although ITP item 18
requires dust-tape rating ≤ 1 → **check with QA** whether the approved template dropped a
required ITP record (raise `missed`/`decision`). Original had a hidden date list sheet (debris).

### 9.4 ITR – Concrete Pour (xlsx)
Legend H / R / M. Header: Contract No, Location, Project, Date of Pour, Asset No, Area of Pour,
Asset Description, RSW No, Concrete Class/Grade, Specified Strength, Docket No, Supplier,
Supplied Slump. Sections with rows `Item | Description | YES/NO/N/A | Initial & Date (Contractor
QA/QC | client)`: 1 Preparation (1.1–1.6 break-out ≥ 50 mm, rebar clearance 20–30 mm, replace
rebar > 10 % loss lap 30 d, 25 mm square edge, clean surface), 2 Blast (2.1–2.3 galvanic anodes
at 500 mm, zinc primer 135 µm WFT within 1 h), 3 Formwork (watertight, 20×20 chamfer),
4 Pre-pouring, 5 Pour (max aggregate mm, slump mm, ambient/substrate/concrete temp, vibrated,
cylinders taken), 6 Curing (7 days min, AS 3600 Table 4.4; cylinder results 7 d / 28 d (2) /
56 d with test certificate no and MPa), 7 Finish (<client spec C2> §14.8). Sign-off rows: Sub-contractor,
Contractor, client project representative (name, signature, date). Photos sheet. Diff: title.
GAPS: G-CP-1 record stays **open for deferred results** (7/28/56-day cylinders) with due-date
reminders; completion of the work anchor is blocked until the 28-day result (configurable).
G-CP-2 inline measurements in check rows (aggregate, slump, temps) with limits. G-CP-3 docket
no + supplier from material receiving (E-03-03).

### 9.5 ITR – Structural (xlsx) "ITR for Remediation of Structural Steel Rev 2"
Legend H/M/R/V/W. Header: Contract, Client, Project, Location, Report No, Date, RSW No +
description, Asset No + description, Plans/Ref drawings attached and marked up Y/N.
Rows `Item | Description | Sign-offs (Contractor | client) | N/A or P/L (punchlist)`:
1 Initial inspection & paint removal (1.1–1.4 incl. UTT and remediation requirements agreed),
2 Steel/flooring replacement & dust plate (panel gap < 10 mm), 3 Hilti clips (≥ 4 discs/m², 6 in
high vibration; 8 Nm torque with calibrated wrench), 4 Site welding (fabricate per approved
remediation list, fit-up, record welding data, visual per <client spec C1> Rev <n>, NDT), 5 Blast & paint
(<client spec P> Rev <n>), 6 Final inspection & punch listing. Remarks; final verification contractor /
client (name/position, signature, date). Diff: title (FIC → ITR).
GAPS: G-S-1 `P/L` outcome on a row creates a punch item (T-0117) and the row shows as
"complete with punch"; G-S-2 rows 1.3 / 4.3 / 4.5 link to the UTT ITR, weld report and NDE records
(record cross-links, not free text); G-S-3 torque check binds a calibrated instrument (T-0112).

### 9.6 ITR – Pedestal or Wall Concrete Repair (xlsx)
As concrete pour plus: product type/name, batch no, date of manufacture (shelf life);
1.1 engineered temporary support if > 25 % section removed; 4 pre-soaking ≥ 2 h; 5 mixing per
manufacturer; 6 temperature control table (ambient, substrate, bag, water, mix temps, acceptable,
limit > 5 °C); 7 placed within 30 min of mixing; 8 curing (AS 3610.1 Table C2; curing compound;
fill blow holes + 2-coat EP410); finish check 7 days after form removal. Sign-off sub-contractor
/ contractor / client. Diff: title on the blank sheet only.
GAPS: G-PR-1 time-window rule (placed ≤ 30 min after mix time) as CEL with timestamps;
G-PR-2 product batch from material register with manufacture date → expiry check (E-03-03).

### 9.7 ITR – Welding Report (xlsx) — structure as §3.
Diff: adds `RFI#` column; adds `Weld Size – Specified | Achieved | Compliant`; header "Weld
Length m"; data validations: compliant Yes/No list, specified size integer 3–12, achieved
integer 3–20. Rows are formula-fed from the weld register workbook (`#REF!` in the template) →
in KERP the report is a **view over weld Items**, not a typed form. (T-0203, G-W-3)

### 9.8 Grout Placement Record (docx) – approved vs `<form>-<6d>_<rev>` original
Structure: General data (project name/no, area, location/facility, system, sub-system,
description, drawing no.); Pre-pour checks 1–6 (OK / N/A / Punch); Pour general info (date,
time, grout product, batch no); Temperature checks; Pour checks 7–9; Post-pour 10–14; Remarks;
Completed by / Reviewed by (Engineer/Supervisor) / Accepted by (Client) – signature, full name,
position, date.
Diff (client demands):
- item 1 adds "surface under the base and underside of baseplate free of dirt, oil, grease and
  loose material";
- saturation recommendation threshold 30 °C → **35 °C**;
- temperature block: Ambient limit **5–35 °C** (if > 35 °C grout temp controlled < 35 °C),
  **Substrate > 5 °C**, **Product < 35 °C**; Foundation and Baseplate temperature fields
  **removed**; Water temp and final mix temp kept;
- item 8: placement **within 20 min of mixing**;
- item 12: cementitious chamfers **45°**;
- item 14 "curing compound applied" replaced by **"Grout check 7 days after formwork removed for
  cracking, surface imperfections & finishes"**.
- approved file lost the corporate form number (`<form>-…`) – template control number must
  survive (§0.1).
GAPS: G-G-1 limits printed next to fields **and** validated (block/warn per TD-065);
G-G-2 conditional rule (ambient > 35 → grout temp < 35); G-G-3 deferred 7-day check
(same mechanism as G-CP-1).

### 9.9 <form>-<6d>_<rev> Grating & Gridmesh ITR (original only)
11 OK/N/A/Punch checks (component damage, kick plates and banding, load-bar direction, collars
welded, handrail sleeves, panel fastening, gridmesh clips, weight signage on removable panels,
touch-ups, swarf removed, area safe), remarks, completed/reviewed/accepted by. **No approved
counterpart** – apparently superseded by the Structural ITR §2–3 and the mechanical RFI type
"Gridmesh & clip check". Confirm with QA (decision) before shipping it in the client pack.

---

## 10. Where it lands (task / epic map)

| Topic | Lands on |
|---|---|
| Per-party intervention codes, ITP step model, master vs instance ITP, ITP render style | T-0113, T-0114, TD-063, TD-067 |
| Per-step signatures, NA cells, close-out signing policy, 3-party review blocks | T-0119, TD-070, E-07-02 |
| Request for Inspection slots/results/numbering/layout | T-0115 (TD-045) |
| Multi-anchor link for all records, RSW alias table | ADR 0003 §2, T-0101, T-0111 |
| Deferred results (cylinders, 7-day checks) | T-0111 lifecycle, T-0124 |
| Lookup tables, derived values, photo checkpoints, limits with refs | T-0110, TD-064, TD-065 |
| FIC vs ITR document classes, terminology | ADR 0003 §3–4, T-0108 |
| Weld report entity, weld fields, unknown existing heat | T-0203, T-0205, E-05-05 |
| UTT → CP Items | T-0103, T-0203, E-04-03 |
| Coating DFT stats, 80/20, instrument + credential gates | T-0110, T-0112, T-0118 |
| RSW source fields + template recognition | T-0101/T-0102, E-05-09, E-05-10 |
| Technical Queries + concessions | E-05-04 (or E-06b-02), T-0122, E-06-03 |
| CDRL register, review codes, due rules, MDR completeness | new spec in documents-and-mdr; T-0120, T-0121, E-05-01, E-05-03, E-05-05 |
| Document dual numbering, template control no, cover sheet | E-05-01, T-0113 |
| Area handover sheet (per RSW) | missed – not E-04-11 (shift handover) |

## 11. TODOs to raise (none duplicate existing ones)
- spec-gap: per-party intervention codes on ITP steps and RFI slots (extends TD-063).
- spec-gap: CDRL register + client review codes A/B/C/D + milestone-relative due dates (documents).
- spec-gap: document metadata – client/contractor numbers + revisions, template control no,
  file-name pattern vs document number.
- spec-gap: weld report entity + missing weld fields (RFI#, weld size spec/achieved, consumable
  batch, UT report link) + existing-unknown heat vs MDR "100 % MTR" invariant.
- spec-gap: Technical Query record + TQ concessions overriding acceptance limits.
- spec-gap: deferred-result checks (7/28/56-day) keeping records open.
- missed: area handover sheet per RSW (ITP item 30).
- decision: close-out bulk signing of R/S rows allowed? (TD-070 adjacent).
- decision: approved coating ITR dropped residual-dust rating required by ITP item 18.
- decision: Grating & Gridmesh ITR retired or still required?
- decision: meaning of "SI No" header field; meaning of CDRL colour highlights.
- risk: approved client templates contain real filled data/photos (extend TD-069).
