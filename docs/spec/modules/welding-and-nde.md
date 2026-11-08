# Module detail — Welding (`wps`) & NDE

Preserved KAEFER domain detail, merged from the legacy `wps` spec with the consolidation
overhauls applied (welds are Items; rules run through `run_checks`/CEL; qualifications feed
the stateless `eligibility` service). Covers MOD-08 welding + traceability.

## Entities
- **`oe_wps_record`** — `scope[project|library]`, `code`, `welding_process`, `standard_ref`,
  `base_material_group`, `filler_material`, `positions`, thickness/diameter `min/max`,
  `joint_types`, `pwht`, `pqr_ref`, `revision`, `status[draft|approved|withdrawn]`,
  `document_id`, `approved_by/at`.
- **`oe_welder`** — `contact_id→contacts`, `stamp`, `employer_kind[staff|subcontractor]`,
  `subcontractor_contact_id`, `status`.
- **`oe_welder_qualification`** — `welder_id`, `wps_id`, `qualified_date`, `expiry_date`,
  **`continuity_last_welded_at`**, `qualified_range(positions/thickness/diameter/material)`,
  `test_type`, `certificate_document_id`, `status[valid|expired|suspended|revoked]`,
  `verified_by/at`.
- **`oe_weld`** — `asset_item_id→item (type=weld)`, `weld_number`, **`wps_id` required**,
  **`welder_qualification_id` required**, joint/size/thickness/diameter/material,
  `nde_required`, `nde_types[VT/PT/MT/RT/UT]`, `status[fit_up|welded|nde_pending|accepted|
  rejected|cut_out]`, `welded_at`.
- **`oe_weld_nde_result`** — `weld_id`, `nde_type`, `performed_at`, `technician_contact_id`,
  `result[accept|reject]`, `report_document_id`.

## State machine
`fit_up → welded → nde_pending → accepted` · `… → rejected → cut_out → (re-weld) fit_up`.

## Verification (enforced via `run_checks`, rules as CEL data)
- On **weld create / `welder.assign`:**
  - **R-WPS-1** — an **approved** WPS is referenced.
  - **R-WPS-2** — the assigned qualification is **for that WPS**, `valid`, and its range covers
    the weld (position/thickness/diameter/material).
  - **R-WPS-3** — welder **continuity** within window at `welded_at`.
- On **`weld.accept`:**
  - **R-WPS-4** — all required NDE results `= accept`, the ITP hold/witness point is released,
    and no open NCR on the weld.
- Violations are blocked with **logged findings**. Qualification/continuity validity is answered
  by the stateless **`eligibility`** service (shared with credentials + calibration), not a
  private table.

## Heat / MTR traceability
Each weld joint links the pipe/fitting **heat numbers** and **MTR (mill cert)** chemical/
mechanical properties pulled from `materials` (SAQ OCR of the MTR → joint properties). See
[`materials-and-traceability.md`](materials-and-traceability.md).

## Continuity & notifications
`GET /wps/continuity-watch` report; expiry/continuity alerts via a **SAQ cron** worker.

## MDR
Accepted weld records, NDE reports and weld maps auto-compile into the project MDR
([`documents-and-mdr.md`](documents-and-mdr.md)).
