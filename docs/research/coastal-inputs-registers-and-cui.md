# Notes — registers, credentials, training matrix, welding/NDT workbooks, CUI-department inspection system

Reviewed 2026-11-10 against branch `plan/coastal-inputs-planner-scaffold`.
Privacy: source files hold real names, certificate numbers, DOBs, client project numbers. None are
reproduced here. People appear as roles; identifiers appear as **shapes** (`XX-W000`,
`<prefix>-<project>-W-0000`). Do not commit any of the source files as fixtures (TD-069 applies to
all of them, not only the inspection exports).

Already-raised TODOs this note builds on and does **not** duplicate: TD-061 (registers vs items),
TD-062 (check layouts / check settings), TD-063 (H/W/R/M points), TD-064 (typed lookups +
snapshot), TD-065 (CEL severity + limits), TD-066 (issue types + auto-raise + close-out form),
TD-067 (report layout), TD-068 (legacy importer), TD-069 (real data), TD-070 (self-approval),
TD-039 (type inheritance / write-back out of Phase 1). Where a gap below extends one of these, it
says "extends TD-0xx".

---

## 1. Welder Qualification Register (PDF, one page landscape, ~85 text lines)

**What it is.** Project register of welder performance qualifications (WPQ) under AS/NZS 1554.1,
"register current, effective <date>" — a controlled list issued to the client. (Its title row is a
copy-paste error from the steel material certificate register — evidence the register is a
hand-maintained document.)

**Structure (one row per welder × qualification test; a welder has 2–5 rows).**

| Group | Columns |
|---|---|
| Welder details | Site, Welder ID (shape `XX-W000`), Name |
| Original test | Plate/Pipe, material type (C/steel), applicable code (AS1554.1), material grade (Gr 350), consumable classification (E7016, E71T-1, E71T-8, "FM1"), WPS used (shape `XXX-000`, some `XXX-001/002`), position(s) tested (3G, 4G, 3F & 4F), process (MMAW, FCAW), joint type (Butt / Fillet), diameter, thickness ("as specified") |
| Approved qualification range | **Butt welds** F · H · VD · VU · OH (x / -) and **Fillet welds** F · H · VD · VU · OH (x / -) |
| Qualification date & location | original test date, test lab, test report no. |
| Welding record (continuity) | repeated pairs × 6–7: "6-month revalidation/confirmation record" (an NDT report number such as `000-MT-000` / `000-UT-000`) → "valid until" (+6 months) |

**Workflow.** Welder tested at an external lab → test report → range approved → every ~6 months a
production weld of that welder is NDT/visually examined and the report number is recorded as
revalidation evidence, extending validity 6 months. Missing evidence = lapsed.

**Spec coverage.** `welding-and-nde.md` `oe_welder`, `oe_welder_qualification`
(`qualified_range`, `expiry_date`, `continuity_last_welded_at`), R-WPS-2/3, continuity watch
(T-0202, T-0206, T-0207).

**Gaps (testable).**
- W-1 Qualification is keyed to **one WPS** (`wps_id`) in the spec, but the register qualifies a
  welder for a *range* (process + consumable class + positions + joint type + material group) that
  several WPSs fall into, and the same test is cited for different WPSs. Requirement: a
  qualification stores its range independently of any WPS; R-WPS-2 passes when the weld's WPS
  parameters fall inside the qualification range. Test: a welder qualified FCAW/E71T-x/fillet
  3F+4F passes on any approved FCAW fillet WPS in a flat/horizontal/overhead position and fails on
  a butt weld. (T-0202, T-0206)
- W-2 `qualified_range.positions` must hold **separate butt and fillet position sets** and
  distinguish **vertical-up vs vertical-down** (F, H, VU, VD, OH). Test: VU-qualified welder blocked
  on a VD weld. Also add `consumable_classification`, `product_form[plate|pipe]`, `joint_type`.
  (T-0202)
- W-3 **Revalidation history** as append-only events: `(qualification_id, revalidated_on,
  evidence_kind[nde_report|visual|test], evidence_ref → NDE result or document, valid_until)`.
  `valid_until` = last event + configurable period (default 6 months per code/client rule set,
  ADR 0004). Test: no event within the period → eligibility fails with reason
  "revalidation overdue"; adding an MT report linked to one of the welder's welds restores it.
  The spec's single `continuity_last_welded_at` cannot show the chain the client audits. (T-0202,
  T-0207)
- W-4 Revalidation evidence can be **derived**: when an NDE result `accept` is recorded on a weld
  of welder X (T-0204), propose a revalidation event for X's qualifications covering that weld
  (human confirms). (T-0204/T-0207)
- W-5 Original test fields: `test_lab_contact_id` (external party), `test_report_ref`,
  `test_date`, `test_location`. (T-0202)
- W-6 **Register export** as a controlled document: "register current, effective <date>" +
  revision, snapshot of all valid qualifications, filed to documents/MDR. Test: export twice →
  two revisions, second supersedes. (T-0208 or new S task; MDR E-05-05)
- W-7 Importer: welder IDs appear as `XX-W000`, `XX-000`, `WID-000` across workbooks for the same
  person → welder has `stamp` + alias list; import normalises. (T-0208 / mapping profiles T-0107)

---

## 2. Inspector certificate (CSWIP 3.0 card + certificate, 3 scanned pages)

**What it is.** Scanned ID card (front), issuer cover page, and certificate of proficiency for a
Visual Welding Inspector under a personnel certification scheme, accredited by a national
accreditation body.

**Fields a credential record needs (derived from the document, no values copied).**

| Field | Notes |
|---|---|
| `holder_contact_id` | person (contacts) |
| `credential_type_id` | catalogue entry: scheme (CSWIP), level/title ("3.0 Visual Welding Inspector"), category (inspection / NDT / trade ticket / induction / licence) |
| `issuer_contact_id` | certification body (party directory) |
| `accreditation_body` + `accreditation_ref` | e.g. national accreditation mark and number printed on the card |
| `standard_refs[]` | standard the competence is against (e.g. ISO 17637 visual testing of welds) and scheme document + edition |
| `certificate_number` **and** `card_number` | **two different identifiers** on the same credential |
| `issued_on`, `expires_on` | plus `examination_date` if printed |
| `renewal_kind` / `next_interim_due` | schemes have renewal and interim (e.g. 5-yearly renewal, interim confirmations) |
| `verification` | method (online register lookup at issuer), `verified_by`, `verified_at`, evidence (screenshot/PDF), result — the certificate itself instructs employers to verify validity online |
| `documents[]` | front/back card + certificate pages (multi-page) |
| `photo` | holder photo is on the card — sensitive |
| `date_of_birth` | printed on certificate; **do not store** unless an identity rule needs it; if stored, restricted field |
| `status` | valid / expiring / expired / suspended / revoked / unverified |

**Spec coverage.** T-0118 (type, holder, issue/expiry), eligibility (T-0111/T-0131).

**Gaps (testable).** (T-0118 unless stated)
- C-1 Credential **type catalogue** separate from the record (scheme, level, category, issuer,
  validity period, renewal rule, standards). Test: two holders of the same type share one
  catalogue row; changing the default validity does not rewrite existing expiry dates.
- C-2 Two identifier fields (certificate no., card no.); search by either.
- C-3 External **verification record** with evidence; eligibility can be configured (rule set)
  to require `verified=true` for selected types. Test: unverified CSWIP 3.0 → assignment as
  weld inspector blocked with reason "not verified".
- C-4 **Sensitive-field classification**: DOB, photo, passport/licence numbers readable only by
  the HR/credentials persona (PER-12); exports/MDR omit them. Test: an inspector persona reading
  a colleague's credential gets no DOB/photo.
- C-5 SAQ **OCR ingest** of uploaded cards/certificates with field extraction + human
  confirmation (same pattern as calibration certs T-0112; AI-sourced mutation `source="ai"`).
- C-6 **Credential snapshot on sign-off**: when an inspection/ITP step is signed, store the
  signer's qualifying credential (type, number, expiry) used — the printed report shows the
  inspector's qualification (legacy `printInspectorQualification=YES`; the coating sign-off tab
  has a "CERT No." column next to the QC inspector). Test: credential later renewed → old report
  still shows the number valid at signing. (T-0111 / T-0119)
- C-7 Credential → **role/permission gating**: only holders of a configured credential type may
  sign a step of a given discipline (e.g. VT acceptance requires a visual welding inspector
  credential). Ties to TD-063 signer role per point.

---

## 3. MASTER SITE Training Matrix (controlled guideline doc, rev 2, 17 pages)

**What it is.** HR-owned controlled document: **positions × competencies** requirement matrix
(pages 1–3, colour-coded cells) + the full **competency catalogue from the HR learning system**
(pages 4–17: "All Competencies in <LMS>": name, LMS number, duration in years, competency group).

**Structure.**
- Rows: ~200 position titles (office: accountant, coordinators, engineers, managers; field:
  Insulator, Leading Hand – Insulator/Scaffolder, Scaffolder, Rope access, Painter, Painter
  Assessor, Boilermaker Welder, Welder, Welder – Coded Pipe, Field Superintendent, Supervisor,
  QA/QC Coordinator, insulation-inspector / pressure-equipment-inspector roles, Yard Person …).
- Columns (~70 on the matrix pages) grouped: KAEFER onboarding modules · HR modules · leadership /
  document control modules · management modules · LMS compliance modules (code of conduct,
  anti-corruption, fraud…) · **required qualifications** (degrees, first aid, confined space,
  construction induction card, driver licence, passport, ID photo, electrical licence, mining
  induction, trade certificate, weld testing, working at heights, high-risk-work licences:
  dogging, forklift, rigging B/I/A, scaffolding B/I/A, boom EWP; rope-access card/certificate
  L1–L3; coatings inspector levels) · **verification of competency (VOC)** for each HRWL class,
  EWP, flange management, painter/blaster, rope access.
- Header row "Duration (yrs.months)": validity per competency (NA = no expiry, 1–10 years).
- Cell legend: **Mandatory** · **Task Bound** · **Optional**.
- Catalogue groups also include **client/site-specific inductions** (several mining and oil & gas
  clients, regional site inductions, each with its own duration), KAEFER forms & letters
  (contract amendment, letter of offer), "Interview".
- Duration `99` = never expires; `0` appears on HRWL licences (licence expiry printed on card).

**Workflow.** HR maintains the matrix; on mobilisation to a site, the person's role row gives the
mandatory set, the client site adds its inductions, task-bound items are added when the person is
assigned a task needing them (rope access, EWP, rigging, confined space). The LMS is the record of
completion.

**Spec coverage.** Master spec MOD-01 bullet "enforces site-level worker competency and ticket
verification … prior to assigning field tasks"; T-0118 (credentials register); eligibility;
PER-12 HR master persona (Contacts, Credentials, Roster); look-ahead constraint flag "Labor
(certified trades)" (EDGE §1.1). **No requirement matrix, role catalogue or task-bound logic
exists in the spec.**

**Gaps (testable).**
- TM-1 **Role/position catalogue** (tenant-wide, versioned) — one catalogue reused by credentials
  requirements, crews (field_time), resource demand (E-04-08) and personas. Test: renaming a role
  label does not break existing assignments (key vs label, ADR 0003 terminology).
- TM-2 **Requirement matrix** `(role_key, credential_type_id, level[mandatory|task_bound|optional])`
  versioned like register types (ADR 0003 §6). Test: matrix revision 3 adds a mandatory item →
  people in that role show "missing" from revision-3 effective date; records under rev 2 remain.
- TM-3 **Overlays**: client/site requirement packs (client inductions) applied per project
  (client pack, ADR 0003 §4 item 6 "competency" register type) and per site node. Effective
  requirement set = role baseline ∪ client/site overlay ∪ task-bound triggers.
- TM-4 **Task-bound triggers** as rules (ADR 0004 rule set): e.g. work anchor attribute
  `access ∈ {ropes}` → requires rope-access card + VOC; `access = boom lift` → EWP licence;
  plant type crane → HRWL class; confined-space flag → confined-space ticket. Test: assigning a
  person without EWP licence to a line with access "Boom Lift" is blocked by eligibility with
  the missing item named.
- TM-5 **Validity from duration**: when a credential has no printed expiry, `expires_on =
  completed_on + type.validity` (years.months); `never` sentinel instead of `99`. Test: first-aid
  completed 2024-03-01 with 3-year validity expires 2027-03-01.
- TM-6 **Compliance view**: person × requirement status (held / expiring in N days / expired /
  missing) and role-level compliance %; filter by project/site/crew. (new task under credentials)
- TM-7 **System-of-record decision**: the LMS/HR system is the record for course completions
  (HR is culled, diff §1). KERP should mirror completions read-only by `external_source="lms"` +
  `external_id` (LMS competency number) + `synced_at`, and author only site-captured items
  (VOCs, site inductions, field-verified tickets). Needs a **decision TODO**.
- TM-8 Personal-identity "competencies" (passport, driver licence, ID photo, letters of offer,
  contract amendment, interview) are HR documents, not competencies → exclude from KERP or treat
  as restricted (C-4). Test: matrix import skips/flags items in category "Forms & Letters".
- TM-9 Matrix import: wide grid (roles × columns) with colour-coded cells → import needs a
  value-coded export (M/T/O) or manual entry; mapping profile with header synonyms (truncated
  column names in the PDF). Mapping profile T-0107.

**Landing.** T-0118 extend (C-1..C-5, TM-5); new S/M tasks: role catalogue + requirement matrix
+ overlays + compliance view (Phase 3, with contacts E-03-01); task-bound eligibility rules into
T-0131-frozen eligibility API as a *requirement-set provider* (must be designed before the M1
freeze, see summary); consumers E-04-05, E-04-08, E-06b-02 (AWP labour constraint).

---

## 4. Welding Report Template + Register (xlsx, ~25 sheets)

**What it is.** One workbook per project: a printable **Welding Quality Assurance Report** per RSW
(one sheet per report, numbered `<prefix>-<project>-W-0000`, weld line IDs `<report>-C<n>`), the
**report register**, NDT % trackers and set-up lists.

**Sheets & structure.**
- `Report_` / numbered report sheets — header: contract, site, RSW no., asset, drawing no. + rev,
  report no., document revision, revision date. Body grouped in four bands: *Welding traceability
  & daily weld report* | *Welding consumables report* | *NDT compliance* | *Comments*. Per weld
  row: weld no. (`CP-1`, `HDNR1`, `BW-01`), date welded, welder ID, WPS no., process, consumable
  **batch** no., visual compliance (C/NC), **UT-thickness report no.** (pre-weld substrate check),
  material **heat no.**, weld length (m), electrode type, % NDT, NDT method, joint type, weld
  compliant, NDT report no., location description (e.g. "main column – flange west"),
  **heat validity** (Valid / Un-Valid looked up from the material certificate register).
- `1. Register` — report no., primary asset, RSW no., drawing no., item/location, client
  specification + rev, ITP no., completed (Y), inspector, report submitted date, **client
  sign-off date**, report status (Closed).
- `CP & Welder Summary` (2,883 rows) — flat weld list across all reports: report, asset, RSW,
  lookup key, sequence, weld no., date, welder, WPS, process, consumable batch, UTT report, heat,
  thickness, weld length, NDT %, tested length, NDT type, joint type, NDT report, RFI no.,
  comment, heat validity.
- `NDT Tracker`, `NDT-Asset %`, `NDT-Welder %`, `NDT Tracker per Process`, `Summary` — pivots:
  Σ weld length vs Σ tested length → % tested, by asset, by welder, by process.
- `DWG` — report ↔ asset ↔ item/location (grid + RL) ↔ RSW notation ↔ **P6 RSW number** ↔
  coating report ↔ drawing.
- `Setup` — WPS → process → joint type; welder list; heat list; consumable batch list.
- `Column Map` — a sketch/map of welds per RSW.

**Workflow.** Weld → daily entry in the report sheet (traceability) → visual → NDT selection by %
→ NDT request (see §5) → NDT report numbers back-filled → report completed → submitted to client
→ client sign-off → closed. Trackers prove the specified NDT % is met overall and per welder.

**Spec coverage.** `welding-and-nde.md` (`oe_weld` as Item, state machine, R-WPS-1..4, heat/MTR
traceability, MDR), T-0203/T-0204/T-0205/T-0206; weld IDs/report numbering in the inventory §3 and
ADR 0003 §4.5; portal co-sign E-07-02.

**Gaps (testable).**
- WR-1 `oe_weld` fields missing: `weld_length_m` (decimal), `location_text`,
  `consumable_batch_ref` (materials batch), `substrate_thickness_report_ref` (UT-thickness
  report/inspection), `drawing_ref`, `visual_result[C|NC]`, `heat_numbers[]` per joint. (T-0203)
- WR-2 **Welding report** = an inspection record anchored at the work-anchor node whose body is a
  repeating table of weld Items (one row per weld, rows are Items not free text), numbered per
  template pattern with revision, statuses `draft → completed → submitted → client_signed_off →
  closed`. Register view = list of these records with submitted/signed dates. Test: a weld row
  cannot be added without an existing weld Item; register shows submitted and client sign-off
  dates. (T-0111 + T-0208; client sign-off E-07-02)
- WR-3 **NDT sampling rule**: per project/spec/weld category a required % per method (e.g. MT x %
  of fillet length). KERP computes `tested_length / weld_length` grouped by work anchor, asset
  level, welder, process, WPS; deviation in metres; rule (warn or block per ADR 0004/TD-065) on
  work-anchor completion if any group is below target. Test: welder with 10 m welded, 0.5 m MT
  tested, target 10 % → finding "welder sample 5 % < 10 %". (new S task in welding; T-0206)
- WR-4 `oe_weld_nde_result` needs `tested_length_m` (partial-length testing) and a link to the
  NDT request line (§5). (T-0204)
- WR-5 **Heat validity rule**: a heat number on a weld must exist in the material certificate
  register (materials) with certificate received → R-WPS-5. Phase issue: materials heat/MTR is
  E-03-03 (Phase 3) while welding is Phase 2 → interim: heat register minimal list or rule
  disabled until materials lands (decision). (T-0205)
- WR-6 **Consumable control**: low-hydrogen electrode **baking/holding log** (oven, temperature,
  start/stop time, batch) — present in the NDT workbook `Setup`; weld rows reference the batch.
  Requirement: consumable batch register (materials class "welding consumable") + baking log
  entries; rule warns if a weld's batch has no baking entry on the weld date for consumables that
  require baking. (welding, new S; materials class)
- WR-7 Weld maps/sketches (`Column Map`) — a marked-up drawing/sketch per RSW with weld IDs →
  markups (E-05-02) linked to weld Items; MDR includes it (already promised in spec "weld maps";
  no task owns it). (T-0208 note / E-05-02)
- WR-8 Report ↔ P6 RSW number ↔ drawing cross-reference (`DWG` sheet) → covered by work-anchor
  `schedule_link` + document links; importer must map RSW notation vs P6 code (E-05-10).

---

## 5. NDT Request + Register (xlsx, ~25 sheets)

**What it is.** Workbook for raising **NDT requests to the NDT subcontractor**, the NDT report
register, NDT % trackers, plus (re-used) coating report tabs and set-up lists.

**Sheets & structure.**
- `NDT REQUEST` (form) — date raised, date required, order no. (TBD), request no.
  (`<project>-NDTR-000`, revision), client, inspection address, **construction code + edition**,
  **acceptance code + edition** (structural category e.g. SP), **test specification** (method
  standard), client job no., client specification, requested by, job description, other notes.
  Lines: asset location, weld ID, thickness, weld length (m), material grade, % NDT, NDT method,
  total length to test, welder ID, WPS, process, joint type.
- `1. Register` — request no., primary asset, RSW no(s)., requested by, date requested, date
  required, description, **NDT report status** (Received…), **NDT report number(s)**.
- `CP list` — flattened weld lines per request.
- `NDT-Tracker` — weld length vs tested length per weld, % and deviation (m), welder, report.
- Coating tabs (`Report`, `2. Surface Prep`, `3. Application`, `4. DFT`, `6. Sign-off`) keyed on
  request no. — surface-prep standard, garnet batch, chloride (mg/m²) tests, washing/cleaning,
  profile min/max/avg, coat products, colours, part A/B batches, thinner %, applicator, stripe
  coat, DFT spec vs readings, QC inspector + **cert no.**, contractor supervisor, client rep
  sign-off. (Re-used template; belongs to coatings inspection.)
- `Setup` — NDT method list (MT, UT, VIS, MT/VIS), WPS/process/electrode, **baking temp + time
  start/stop + electrode batch**, welder IDs, welding inspectors; `Set Up` asset → responsible
  inspector; `Sheet1` painter list, coating batch numbers, instrument serials (profile gauge,
  chloride kit, DFT gauge/probe); `Material Reference` — material certificate register
  (description, type, size, qty, thickness, PO, certificate no., heat no., pages, supplier,
  client, specification).

**Workflow.** Inspector selects welds needing NDT (by % rule) → request raised to the NDT
contractor with codes/specs → contractor tests → report received (number logged; status) →
results per weld back-filled into the welding report → trackers updated.

**Spec coverage.** `oe_weld_nde_result` (T-0204); ADR 0003 §2 multi-anchor request link
`oe_inspections_request_anchor` (T-0115) and NDTR numbering (§4.5); `inspection_requests`
(T-0115) is the *client* call-out workflow; TD-045 (Request-for-Inspection spec pending).

**Gaps (testable).**
- N-1 **NDT request to a provider** is a distinct request kind from a client witness call-out:
  `requester`, `provider_contact_id` (NDT subcontractor), `order_ref` (external PO), dates
  raised/required, codes (construction, acceptance + category, test spec, client spec), lines =
  weld Items with method, % and length to test. Status `draft → issued → in_progress →
  report_received → results_recorded → closed`. Test: recording a provider report against a
  request creates/links `oe_weld_nde_result` rows for each line and moves the request to
  `results_recorded`; a line without a result keeps the request open. Extend TD-045/T-0115 or a
  new welding task — **decide where** (spec-gap).
- N-2 One request covers several RSWs/assets → `oe_inspections_request_anchor` (already in
  ADR 0003) plus **line-level** weld links. (T-0115)
- N-3 Request selection helper: propose welds not yet tested to reach the sampling target per
  welder/process (WR-3). (welding new S)
- N-4 Asset → **responsible inspector** assignment (role assignment at a breakdown node) used to
  route requests/notifications. (ties to role catalogue TM-1; T-0101 custom field or
  approval_routes)
- N-5 Coating tabs prove the coating report is a separate template (surface prep / application
  per coat / DFT per coat / sign-off with QC cert no.) → coatings template in the remediation
  client pack; instrument serials via instrument-reference field (T-0112). Already broadly in
  `quality-and-inspection.md` coatings criteria; new: **per-coat repeating block** (1st/2nd/3rd
  coat) and batch pickers for part A/B/thinner from the paint material register (TD-064 pattern).

---

## 6. CUI-department inspection system (item-category, use-case and template exports)

### 6.1 What the exports contain
- **Item categories** (12): `CUI Inspection` (types Backscatter, C-Arm, C-Arm / X-Arm, X-Arm —
  30 attribute usages each, can have children), `CUI Inspection client A` (C-Arm), `CUI Inspection
  client B` (C-Arm, C-Arm/X-Arm, X-Arm, Module), `Divider`, `Equipment` (Backscatter, C-Arm,
  X-Arm, X-Ray Crawler units — 5 attributes: name, serial, calibration due, in use, onsite),
  `Inspection Equipment`, `Inspection Point` (child, no tag number), `Inspections` (Weld → Dust
  Assessment, Coating ITR), `Inspection Scope` (**MODULE**: client scopes), `Inspection Scope
  client A`, `Progress Report` (Daily Report), `Weld Locating` (C-Arm → Weld Location).
  `applicableFor` = ITEM | MODULE | ANY.
- **Attribute definitions** (51): typed `STRING / NUMERIC / DATE / SELECTOR / MULTISELECTOR /
  MULTI_LINE_STRING`, `uom` (Celsius, Meters, hr), options as `key / displayText / order`,
  `allowOtherOption`, min/max/scale/precision, `editable`, `display`, `showPrintIcon`.
  Content: plant area, functional location, equipment no./description, service, damage
  mechanism (default "Corrosion Under Insulation"), operating temperature, **inspection type**
  (X-Arm/C-Arm/Backscatter keyed DRT/Realtime DRT/Backscatter), metres to scan, **access**
  (ground, ropes, scaffold, scissor lift, boom lift, ladder), elevation, **tier** (1–8), P&ID,
  ISO, insulation class, Pspec, IS schedule, diameter, schedule, thickness, damage loop, module,
  doc number, CTL no., external refs (work order, order #, CTR, WBS, quotation no., PO #), test
  technique, testing procedure, **estimated hours / actual hours / total days / delay & man-hour
  totals**, equipment name/serial/calibration due/in use/onsite.
- **Attribute usages** per item type: subset of the catalogue with per-type default value +
  mandatory flag (e.g. Damage Mechanism default, Test Technique default, Testing Procedure
  default).
- **Inspection categories** (6): Weld Location (WL), CUI Inspection (CUI, discipline RT,
  supervisor review on, concurrent allowed), Coating ITR, Dust Assessment (supervisor review),
  Time Management (TM, concurrent), Daily Report (DR). Settings: workflowType, hasSupervisorReview,
  issuesMustBeAssessed, reinspectOnlyOpenDefects, reinspectCarryOverPhotos/IssuePhotos,
  allowConcurrentInspection, nextInspectionDateFrequency/nextInspectionDate,
  partialInspectionMethodType/partialTotalMeasure, copyItemTypeAttachments,
  includeReportInAttachments (+ category "Report"), restrictEditableAttachmentTypes,
  scope description (HTML), enabledItemTypes.
- **Templates** (13): KCUIR family (base, C-Arm & X-Arm, client A, client A no-inspection-point, client B ×2,
  client C), KIPS (per-component: C-ARM, X-ARM, C-ARM/X-ARM), KWLR (weld location), TM, TM (client B),
  DU (daily update). Revisions 0–23, all APPROVED. One section, one check each (except TM: four).
  CUI check = FORM with pass/fail/N-A ("Pass = no CUI, Fail = CUI, NA = not tested") +
  confirmation texts, allowed issue types + default, `minRequiredPhotos=1`, 12 photo-caption
  snippets, `carryOverValues/Issues=true`, `repeatForComponents` (KIPS/client A: one block per child
  Inspection Point, heading "Inspection Point – {{name}}").
  Fields: HEADER, ITEM_SELECTOR (test equipment filtered by equipment item-type uuid), STRING bound
  to line attribute (insulation class), RADIO (insulation consistent with isometrics? Yes/No →
  enables notes + mismatch photos), RADIO jacketing / mastic (seal) condition Good/Fair/Poor/Bad
  (**Poor, Bad auto-raise** issue of type X, priority "Remaining Actions"), MULTISELECT water
  presence / ice present (Nil/Jacket/Mid/Substrate), RADIO water drained (enabled when water ∈
  {Jacket, Mid, Substrate}; Yes auto-raises; take-photo decorated), MULTISELECT substrate anomaly
  (Nil/Mild/Moderate/Heavy), RADIO scan restrictions (Yes auto-raises Test Restriction), comments.
  JS check validator: "Poor jacketing requires an issue" — **broken**: it reads
  `JacketingCondition` while the field key is `jacketingCondition`, so it never fires.
- **Defect/issue types** (6 base + 5 client A + 6 client C + Weld Location): CUI (prefix ID), Test
  Restriction (TR), Damage to Cladding (CC), Mastic Condition (MC), Water Presence (WP), Ice
  Presence (IP); client A and client C copies with suffixes and own prefixes (LOC, SC); Weld Location (WL).
  Each: scope DEFECT, business-key prefix, required photos, dynamic fields (inspection point or
  pipe support, CUI class Mild/Moderate/Heavy, clock location 360°/12/3/6/9 o'clock/Other,
  water location, "does it affect your scan"), 8–27 text snippets, priority shown, JS that sets
  priority: **CUI class Mild/Moderate → "High Priority Actions", Heavy → "Critical Actions",
  else "Remaining Actions"**.
- **Assessment forms** (`formDefinitions`): CUI Assessment (further investigation? further
  scanning? next action: strip & inspect | monitor), Test Restriction Assessment (further
  investigation? action: re-test | seek further actions), Water Presence Assessment (drained?
  drain required? retest required?).
- **reportMetadata** (`key=value` lines): printItemAttributes + printedAttributes list,
  headerRows `Label^path` (project.name, equipment.rootModule.moduleName, itemCategory/itemType
  name, dynamicAttribute.Work Order, inspectionKey), issueSummaryColumns `Label^path^width`
  (key, businessKey, createdDate, statusText, dynamicAttribute.inspectionPoint, defectTypeName,
  dynamicAttribute.location / cuiClass / pipeSupport), photoLayout (two-column inline / at end),
  formFieldPhotoLayout, printDateWithPhotos, printInspectorQualification, showWitnessInformation,
  includeSignatureSection, includeSupervisorInSignatureSection, includedAttachmentTypes
  (Photo|ISO|P&ID), includeItemAttachments, includeIssueAttachments,
  includeEquipmentCertificates, excludeChecksWithNaResult, excludeIssueSummary,
  issuesOnSeparatePages, header/footer type, template code in footer, "UNCONTROLLED WHEN
  PRINTED", print borders, compact layout, header rows on subsequent pages.
- **TM / TM (client B)** — see §7. **DU** — Daily Update FORM: date, daily work scope, changes to scope
  by client, planned activities next day, issues/concerns (with predefined texts).

### 6.2 Concept mapping (legacy → KERP)

Status: **C** covered by spec/ADR, **T** covered by an open TODO, **P** partial, **G** gap (see 6.3).

| # | Legacy concept | KERP concept | Status |
|---|---|---|---|
| 1 | Project | `project` (tenant + RLS) | C |
| 2 | Item category `applicableFor=MODULE` "Inspection Scope" (client scope) | ADR 0003 level def `scope` (role `grouping`); header row "Scope" = `context.level('scope').name` | C |
| 3 | (implicit) job package / drawing list | level def `job_package` (cui-line pack) | C |
| 4 | Item category "CUI Inspection" item (a line) | level def `line`, roles `work_anchor`, `inspection_anchor`, `progress_unit` | C |
| 5 | **Item type = test technique** (C-Arm, X-Arm, Backscatter, C/X) | line attribute `technique` (choice) + template applicability rule on that attribute; *not* a separate level or item type | P → G-CUI-1 |
| 6 | Client copies of categories (client A, client B) | client pack field schema for the `line` level; no forks | P → G-CUI-9 |
| 7 | Item category "Inspection Point" (child, no tag) | Item of item type `cui_test_point` under the line (ADR 0003 §4 item types) | C |
| 8 | Repeat check per Inspection Point (`repeatForComponents`, `{{name}}`) | repeating block per child Item | T (TD-062) |
| 9 | Item category "Equipment" (C-Arm/X-Arm/Backscatter/crawler units; serial, calibration due, in use, onsite) | equipment register T-0112 / E-03-07 (`oe_equipment_asset`) | P → G-CUI-16 |
| 10 | ITEM_SELECTOR "Test Equipment" filtered by equipment type uuid | `instrument reference` field (core/fields), calibration-gated | C (+T TD-064) |
| 11 | Item category "Inspections"/Weld + Coating ITR/Dust Assessment | weld Item type + coating/dust templates (other dept) | C |
| 12 | Item category "Divider" | UI grouping only — not imported | — |
| 13 | Progress Report / Daily Report + DU template | field_diary / Daily Workface Log (E-04-04) | P → G-CUI-17 |
| 14 | Attribute definitions (typed, uom, options key/display/order, allowOther, min/max/scale) | `core/fields` palette + JSONSchema `field_schema` (T-0102 `oe_asset_field_def`) | P → G-CUI-3 |
| 15 | Attribute usages (subset + default + mandatory per item type) | per-level / per-item-type field schema referencing shared definitions | P → G-CUI-10 |
| 16 | External refs (Work Order, Order #, CTR, WBS, Quotation, PO #) | `external_source` + `external_id` refs | P → G-CUI-12 |
| 17 | Estimated Hours, Mtrs to Scan | `progress_unit` `budget_hours`, `budget_qty` + `uom=m` (ADR 0003 §1) | C |
| 18 | Actual hours / totals attributes written by TM | derived roll-up from `field_time` (no write-back, TD-039) | G-TM (§7) |
| 19 | Access, Tier, Elevation attributes | line custom fields → planner constraints + task-bound competencies | G (TM-4, P-2) |
| 20 | Inspection category (CUI, WL, CI, DA, TM, DR) + settings | `oe_inspections_inspection.kind` + rule sets — no "inspection profile" | G-CUI-2 |
| 21 | Discipline (RT) on category / issue type | discipline catalogue (NDT method / trade); RBAC scope "Discipline" exists | P |
| 22 | Supervisor review | approval_routes on inspection; SoD TD-070 | P (T) |
| 23 | Template (code, revision, APPROVED, applicability by category/type/inspection category) | one-engine template with `template_revision_id` pin; applicability by required roles (ADR 0003 §6) | C/T (TD-062) |
| 24 | Section → check FORM/LIST/TABULAR, pass/fail/NA + confirm texts | template section/check layouts | T (TD-062) |
| 25 | Field types HEADER/STRING/TEXTAREA/INTEGER/BIG_DECIMAL/DATE/RADIO/MULTISELECT/PHOTOS/ITEM_SELECTOR | `core/fields`: text, numeric, measurement, photo, signature, instrument ref, calculated | P → G-CUI-3 |
| 26 | referencedCombo / enabledForValue / disabledForValue | CEL `enabled_if` / `visible_if` per field | T (TD-065 kind "show/enable") |
| 27 | referencedAttributeName (read line attribute into the form) | role-bound context fields (ADR 0003 §6), snapshot | C/T (TD-064) |
| 28 | raiseIssueForOptions + issueType + issuePriority | auto-raise issue on option | T (TD-066) |
| 29 | minRequiredPhotos, takePhotoDecorated, PHOTOS | photo field + min-count rule | T (TD-062) |
| 30 | predefinedTexts / photo caption snippets | snippet lists on fields and photo captions | P → G-CUI-18 |
| 31 | carryOverValues / carryOverIssues; reinspectOnlyOpenDefects; carry-over photos | re-inspection carry-over (lifecycle `RE_INSPECT`) | T (TD-062) + G-CUI-2 |
| 32 | JS customizers (validate, derive, totals) | CEL rules with severity (TD-065) | T + G-CUI-4/5 |
| 33 | sequentialCheckCompletion / "complete checks 1–3 before Totals" | check ordering / dependency rule | P (G-CUI-4) |
| 34 | Defect types (code, scope, prefix, photos, dynamic fields, snippets) | issue types as versioned config (TD-066) | T |
| 35 | Which register a CUI finding lives in | NCR (T-0116) = *our* nonconformance; punch (T-0117) = completion items; **client-asset condition finding has no home** | G-CUI-6 |
| 36 | Priority scale (Critical / High Priority / Remaining Actions) + JS derivation from CUI class | configurable priority scale + CEL derivation | G-CUI-7 |
| 37 | Assessment forms (CUIA, TRA, WPA) + issuesMustBeAssessed | issue lifecycle step "assessment" (≠ close-out form of TD-066) | G-CUI-8 |
| 38 | Issue type forks per client (CUI client A, CUI-client C) | base issue type + client-pack overlay | G-CUI-9 |
| 39 | businessKeyPrefix | numbering pattern per issue type (ADR 0003 §4.5 extended) | T (TD-066) |
| 40 | Weld Location issue type (position marker, near pipe support) | create/locate a child **Item** (weld) from an inspection | G-CUI-15 |
| 41 | reportMetadata (header rows, printed attributes, issue summary columns, photo layout, signature, footer) | report profile | T (TD-067) |
| 42 | printInspectorQualification | credential snapshot on sign-off (C-6) | G |
| 43 | includeEquipmentCertificates | append calibration certs of instruments used | G-CUI-11 |
| 44 | showWitnessInformation | witness on inspection (TD-063 / T-0115) | T |
| 45 | Attachments by type (Photo, ISO, P&ID, Report), copyItemTypeAttachments, includeReportInAttachments | documents linked to nodes with a document type; reference docs at type/template level; generated report filed back | G-CUI-11 |
| 46 | allowConcurrentInspection | several open inspections on one anchor; interacts with ADR 0005 checkout lease | G-CUI-2 |
| 47 | nextInspectionDateFrequency / next-inspection fields | recurring inspection scheduling | G-CUI-13 |
| 48 | partialInspectionMethodType / partialTotalMeasure | partial completion → earned qty (metres scanned) | G-CUI-14 |
| 49 | showOffline, availableOfflineEquipment, excludeByDefault | offline sync scope (T-0126/T-0127) | P |
| 50 | showGeolocation | sign-off geolocation (`signed_off_*`) | C |
| 51 | Roles (Inspector perform + approve) | personas/RBAC + SoD | T (TD-070) |
| 52 | TM template (time as an "inspection") | `field_time` (E-04-05) + delay codes + roll-ups | G-TM |
| 53 | Usecase bundle (template + categories + attributes + issue types + forms) | client pack (ADR 0003 §4) + importer | T (TD-068) |

### 6.3 Capabilities the KERP spec lacks (testable)
- **G-CUI-1 Template applicability by anchor attribute value.** A template declares
  `applies_when` (CEL over the anchor's fields, e.g. `anchor.technique in ['c_arm','x_arm']`) in
  addition to required roles. Test: a line with technique Backscatter does not offer the KCUIR
  C-Arm template. (T-0111 / T-0110)
- **G-CUI-2 Inspection profile (category) config** per project: `discipline`, `requires_review`
  (supervisor), `issues_must_be_assessed`, `reinspect_scope[open_issues|all]`,
  `carry_over[values, issues, photos, issue_photos]`, `allow_concurrent`, `copy_type_attachments`,
  `file_report_to_documents(doc_type)`, `next_due_rule`. Templates belong to a profile. Test: with
  `allow_concurrent=false`, opening a second CUI inspection on a line with one open is refused;
  TM and CUI on the same line both open when TM's profile allows it. Must state how this
  interacts with the ADR 0005 field checkout lease. (T-0111; spec-gap)
- **G-CUI-3 Field palette additions** in `core/fields`: `choice` (single / multi; options with a
  stable key + i18n label + order; optional "Other" free text; option deprecation without data
  loss), `date`, `integer` vs decimal with scale, `multiline text`, display-only `heading /
  instruction` (rich text). Test: renaming option label "Poor" keeps stored key `poor`; a
  deprecated option still renders on old records but is not offered on new ones. (T-0110)
- **G-CUI-4 CEL aggregates over repeating rows and cross-check dependencies.** Calculated fields
  must sum/count/count-distinct over a repeating table (Σ hours, Σ hours×headcount, distinct
  dates) and a check can require other checks complete. Test: three TM rows on two dates → total
  days = 2, man-hours = Σ(hours × technicians); totals recompute automatically (no manual
  "re-complete Totals" step). Both runtimes (celpy + cel-js) give identical results (TD-041).
  (T-0110)
- **G-CUI-5 Publish-time template validation**: every field key referenced by a rule, condition,
  auto-raise, summary column or report path must resolve; publish fails otherwise. Test: rule
  referencing `JacketingCondition` when the field is `jacketingCondition` blocks publish with the
  unresolved name. (T-0110 / T-0113)
- **G-CUI-6 Finding / anomaly register for client-asset condition** (CUI found, water presence,
  damaged cladding, test restriction) distinct from NCR (our nonconformance) and punch (completion
  defects). Same issue-type engine (TD-066) but an issue *class* with its own lifecycle, client
  visibility (portal) and reporting. Test: raising "CUI – Heavy" does not create an NCR or block
  work-anchor completion; it appears in the client findings register and report issue summary.
  (decision + T-0111/T-0116; portal E-07-01)
- **G-CUI-7 Priority scale as configuration**: per issue type/pack an ordered priority list
  (e.g. Critical Actions > High Priority Actions > Remaining Actions) and a CEL derivation from
  issue fields. Test: CUI class Heavy → Critical; Mild → High; class cleared → Remaining.
  (TD-066 extension)
- **G-CUI-8 Issue assessment step**: issue type may require an assessment form (engineering
  review) before close-out; outcome fields can **spawn follow-up** (re-test, strip & inspect,
  monitor → new inspection or task on the same anchor). Lifecycle `raised → assessed → actioned →
  closed`. Test: `issues_must_be_assessed=true` blocks inspection approval while any issue is
  unassessed; outcome "Re-test" creates a pending re-inspection. (TD-066 extension; T-0111)
- **G-CUI-9 Client variants by overlay, not fork**: a client pack overlays a base template /
  issue type / field schema (labels, option subsets, printed attributes, issue-type allowed list,
  prefixes) so fixes to the base propagate; forks remain possible. Note TD-039 dropped "type
  inheritance" from Phase 1 — this is the evidence to revisit it (decision). Test: base KCUIR
  rev 19 change appears in the client B overlay without editing it; an overlay field override wins.
- **G-CUI-10 Shared attribute catalogue + per-type usage**: one project/pack-level attribute
  definition reused by several levels / item types, each usage adding default value and
  mandatory flag. Test: changing the unit of "Operating Temperature" in the catalogue applies to
  all line item types; default "Corrosion Under Insulation" prefilled only on CUI lines.
  (T-0102 / ADR 0003 field_schema)
- **G-CUI-11 Report appendices and filing**: report profile can (a) append calibration
  certificates of the instruments referenced in the inspection, (b) include anchor attachments
  of selected document types (ISO, P&ID, photos), (c) file the generated PDF back to the anchor's
  documents with type "Report" and revision; template/type-level **reference attachments**
  (e.g. cladding class chart) are shown to the inspector in the field. (TD-067 extension;
  T-0112, E-05-01)
- **G-CUI-12 Several typed external references per node** (work order, client order, CTR, WBS,
  quotation, PO) — each `(external_source, external_kind, external_id)`. Test: a line holds a
  work order and a PO; header row "Work Order #" reads the work-order ref. (T-0101/T-0102)
- **G-CUI-13 Recurring inspection due dates**: profile `next_due_rule` (fixed interval, interval
  chosen by inspector from a list, manual date, or from an anchor attribute) sets `next_due_at`
  on the anchor on approval; due/overdue list. Test: approval with interval 12 months sets due
  date and the line appears in "due in 30 days" 11 months later. (new; asset_integrity E-06b-01
  or quality)
- **G-CUI-14 Partial inspection progress**: an inspection records measured quantity done (metres
  scanned) against the anchor's `budget_qty`; feeds progress (E-04-03) as earned quantity. Test:
  two partial inspections of 40 m and 60 m on a 100 m line → line 100 % earned.
- **G-CUI-15 Create/locate Items from an inspection**: a finding type can be configured to create
  a child Item (e.g. weld location near pipe support N) under the anchor, with the photo and
  position. Test: KWLR-style inspection creates 3 weld Items under the line, each linked back to
  the inspection. (T-0103 / T-0111)
- **G-CUI-16 Equipment availability state**: `onsite`, `in_use_by` (inspection/crew), and
  equipment-type → required operator credential (radiography units need a licensed operator).
  Test: selecting an X-Arm unit for an inspection by an inspector without the configured
  radiation-operator credential fails eligibility. (E-03-07, T-0112, T-0118)
- **G-CUI-17 Daily report at scope/project level** with free-text sections + predefined texts
  (work done, client scope changes, plan for tomorrow, issues) for teams whose daily log is
  narrative, not labour lines. (E-04-04)
- **G-CUI-18 Snippet libraries** on text fields and photo captions (managed list per field /
  pack). Small; TD-066 covers issue-type snippets only. (T-0110)
- Data-quality for the importer (add to TD-068 when worked): technician names hard-coded as
  options in TM → must become roster lookups (personal data in templates); placeholder/test
  item types ("Module", "Divider"); category typo "Item Catergory" in header rows; the broken JS
  field reference (G-CUI-5).

---

## 7. Time Management templates (TM, TM (client B)) — impact on `field_time`

**What it is.** Time is captured as an "inspection" on each line (inspection category TM,
concurrent with the CUI inspection). TM (client B) is a byte-for-byte copy (99.95 %) bound to the client B
category — client variant by fork.

**Structure.**
- CHECK-1 LIST "Enter Dates and Hours": date, hours worked on this scope (int), how many
  technicians (int, default 3), equipment (two item selectors: Backscatter / C-Arm / X-Arm
  units), non-productive hours (decimal, **default 4.5**), were there any delays? (Y/N).
- CHECK-2 LIST "Delays": date, delay hours, technicians (default 3), **delay code** (multi-select
  from 14 codes: 50 meetings; 60–64 permits: area conflict, field sign-on delay, not ready for
  issue, production impact, rework permit/risk; 70/72/73 planning: insufficient access, scope
  growth, workpack detail; 80–82 tools & equipment: broken, missing, not available; 90 weather;
  100 training), details.
- CHECK-3 LIST "Mobilisation & Demobilisation": date, mob or demob, technicians (multi-select of
  **named people hard-coded in the template**), other technician (enabled when "Other").
- CHECK-4 TABULAR "Totals": estimated hours (from line attribute), total hours, mobilisation
  hours (= technician count × **4 h**), productive = total − non-productive, non-productive,
  delay hours, hours − delays, total days (distinct dates, "not for billing"), man-hours
  (Σ hours × technicians), man-hour delays, man-hours worked. Every cell is **written back** to a
  line attribute (Actual Hours, Total Man Hours, …).

**Spec coverage.** Diff §1: `field_time._line` (worker, equipment, RSW code → `work_anchor_id`,
crew hours); E-04-05 field_time; Edge screen 3 Daily Workface Log (crew roster, NPT + NPT reason,
copy yesterday, plant hours); look-ahead RNC codes; E-06-03 delay event attribution; T-0115 client
delay attribution on no-show; ADR 0003 `budget_hours` on progress units.

**Gaps (testable)** — for E-04-05 / E-04-04 breakdown.
- TT-1 **Headcount mode**: a time line may record `headcount` + hours without named workers
  (CUI practice) as well as named worker lines; man-hours = hours × headcount. Pack setting
  decides which is required. Test: a CUI pack accepts "3 technicians × 8 h" → 24 man-hours.
- TT-2 **Delay-code catalogue** as pack configuration: numeric code, group (meetings, permits,
  planning, tools/equipment, weather, training), label, `attributable_to[client|kaefer|neutral]`,
  `billable` flag; one catalogue shared by NPT reasons, look-ahead RNC and delay events
  (E-06-03). Test: code 61 "Permitry – field sign-on delay" marked client-attributable appears in
  the delay-attribution report.
- TT-3 **Delay vs non-productive time are different measures**: NPT (standing allowance, default
  4.5 h/day here — travel/pre-start) vs coded delay hours. Field_time line carries `npt_hours`
  and zero-or-more `delay` entries (hours, headcount, code(s), details). Defaults are pack
  parameters, not template literals.
- TT-4 **Mobilisation / demobilisation events** per person (or headcount) with a standard hours
  allowance parameter (4 h each here) that posts as mob hours against the work anchor/scope.
- TT-5 **Plant/equipment hours** per day pick units from the equipment register (calibration-gated,
  G-CUI-16), not free text.
- TT-6 **Roll-ups are derived, not written back**: per work anchor — estimated (budget_hours),
  actual, productive, NPT, delay, hours − delays, man-hours, distinct days worked
  ("not for billing"), mob hours — computed from field_time (SAQ rollup / read model, E-06-05).
  Test: editing a time line updates the line's totals without a manual "recalculate" step.
  Consistent with TD-039 (no write-back in Phase 1).
- TT-7 Time capture must not be blocked by an inspection checkout lease on the same anchor
  (ADR 0005) — time is a separate record.
- TT-8 Crew pickers read the project **roster** (people mobilised to the project), never options
  embedded in a template (privacy + maintenance).

---

## 8. Planner / resources impact (trade / role groups)

- P-1 **One role/trade catalogue** (TM-1) drives: credential requirements (training matrix),
  crew composition in field_time, resource demand roll-up by trade (E-04-08), look-ahead
  "Labor (certified trades)" constraint (EDGE §1.1) and AWP labour gate (E-06b-02). Field trades
  seen: insulator, scaffolder, rope-access technician, painter/blaster, boilermaker welder,
  welder, coded-pipe welder, rigger/dogger, EWP/forklift operator, leading hands, supervisors,
  QA/QC coordinator, NDT technician (RT), CUI inspector, coatings inspector, pressure-equipment
  inspector.
- P-2 **Access method + tier + elevation** on the work anchor (CUI lines) are planning
  constraints: scaffold → scaffold green-tag constraint (exists in AWP), boom/scissor lift →
  EWP plant + EWP licence, ropes → rope-access team + IRATA levels, ground/ladder → none.
  Requirement: constraint and task-bound credential rules derived from these fields (TM-4).
- P-3 **Default crew size** (3 technicians) and **equipment unit type** (C-Arm vs X-Arm) per line
  are resource demands: demand = hours × crew + one radiography unit per crew-day; units have
  onsite/availability (G-CUI-16) → resource summary should count equipment units as well as
  people.
- P-4 **Eligibility as a planning filter**, not only at execution: when planning a welding RSW the
  planner sees only welders whose qualification range + revalidation cover the WPSs on that RSW
  (W-1..W-3); when assigning a crew to a line, members missing task-bound credentials are flagged.
- P-5 Budget vs actual per work anchor (estimated hours, metres to scan → productivity m/h) feeds
  look-ahead capacity and EVM (E-06-04); requires TT-6 roll-ups.
- P-6 **Responsible inspector per asset/scope** (NDT workbook set-up) = role assignment on a
  breakdown node; used for routing requests and inspection workload planning.

---

## 9. Suggested tracker actions (for the planner; not done here — repo is read-only)

| Kind | Title (draft) | Tasks |
|---|---|---|
| spec-gap | Welder qualification range independent of WPS, butt/fillet + VU/VD positions, revalidation event history | T-0202, T-0206, T-0207 |
| spec-gap | NDT sampling % per spec/method with tested length and per-welder/asset/process compliance | T-0204, T-0206 |
| spec-gap | NDT request to an NDT provider (lines = welds, codes/specs, report received → results) vs client call-out | T-0115, T-0204 (TD-045) |
| decision | Weld heat validity rule in Phase 2 before materials (E-03-03) exists | T-0205 |
| missed | oe_weld fields: weld length, location text, consumable batch, UTT report ref, drawing, visual result; consumable baking log | T-0203 |
| spec-gap | Welding report record (repeating weld-Item rows, submitted / client sign-off / closed) + register | T-0111, T-0208 |
| spec-gap | Credential type catalogue, card vs certificate number, external verification, sensitive fields, OCR ingest | T-0118 |
| spec-gap | Role catalogue + competency requirement matrix (mandatory / task-bound / optional) + client/site overlays + compliance view | T-0118, E-03-01, E-04-08 |
| decision | Training/LMS as system of record: KERP mirrors completions or authors them | T-0118 |
| risk | Eligibility API frozen at M1 (T-0131) must accept a requirement-set provider (role + overlays + task-bound) | T-0131 |
| spec-gap | Credential snapshot on sign-off + signer-credential requirement per step | T-0111, T-0119 |
| spec-gap | Inspection profiles (review, assessment, reinspection, concurrency, next-due, report filing) | T-0111 |
| spec-gap | core/fields: choice (stable keys, Other, deprecation), date, integer, multiline, heading | T-0110 |
| spec-gap | CEL aggregates over repeating rows + publish-time reference validation | T-0110, T-0113 |
| decision | Client-asset finding register vs NCR vs punch | T-0111, T-0116, T-0117 |
| spec-gap | Issue priority scale + derivation; assessment step with follow-up spawn | T-0111 (TD-066 ext.) |
| decision | Revisit template/issue-type overlay (inheritance) for client packs (TD-039) | T-0108, T-0113 |
| spec-gap | Template applicability by anchor attribute value | T-0110, T-0111 |
| later | Recurring inspection due dates; partial-inspection earned quantity; create Items from inspection | E-06b-01, E-04-03, T-0103 |
| spec-gap | field_time: headcount mode, delay-code catalogue, NPT vs delay, mob/demob allowance, derived roll-ups, roster pickers | E-04-05, E-04-04, E-06-03 |
| spec-gap | Equipment availability + operator credential per equipment type | E-03-07, T-0112 |
