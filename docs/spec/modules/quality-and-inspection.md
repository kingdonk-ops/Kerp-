# Module detail — Quality: one-engine inspection, eligibility, qms, construction_control, coatings

Merged from legacy `forms`/`inspections`/`qms`/`construction_control` with TD-002 (one-engine)
applied. Covers MOD-08 (QC/ITP) and the coatings/environmental criteria.

## One field-type registry (`core/fields`)
Shared palette of field types used by every form, ITP and asset-schema field:
`numeric`, `text`, unit-bound **`measurement`** (value + unit), `photo`, `signature`,
**`instrument reference`** (binds a calibrated tool, gated by calibration), `calculated`
(CEL expression). No per-module field designers.

## Unified inspection record — `oe_inspections_inspection`
Single store across all kinds: **`kind IN (inspection, itp, rfi)`**. Anchors at any level
(Asset / sub-Asset / RSW / Item). Holds responses authored against a template revision (pins
`template_revision_id`), `status` lifecycle, `reading_*`, `signed_off_*` (user, signature
hash, UTC timestamp, geolocation). Lifecycle (master spec §4.2):
`PENDING → RFI_ISSUED → INSPECTED_PASSED → LOCKED_FOR_TURNOVER`, with
`INSPECTED_FAILED → (Cat A punch | NCR) → RE_INSPECT`. Every transition calls `run_checks`.

## `eligibility` service (stateless, no tables)
Answers "is this person / instrument / material valid **right now**?" across:
- **WPS/welder** qualifications ([`welding-and-nde.md`](welding-and-nde.md)),
- **credentials** register (tickets/accreditations + expiry),
- **equipment calibration** register (hard gate).
It owns no database tables; it reads the registers and returns pass/fail + reason. It is a
frozen seam at the M1 gate (T-0131).

## `qms` — master ITP authoring + audit + calibration records
Strictly: ITP template authoring (on the one-engine), audit registers, and calibration
records. No parallel inspection store.

## `construction_control` — conformance + hold/witness evaluation
Conformance criteria and hold / witness / surveillance / review point evaluation **via
`run_checks`**. The legacy `construction_control._inspection` table is **dropped**.

## `inspection_requests` — RFI / call-outs
Workflow `draft → requested → confirmed → witnessed | waived | no_show | failed` with minimum
contractual notice, calendar bookings, broadcasts, and **automatic client delay attribution**
on `no_show`.

## Coatings & environmental criteria (preserved)
Recorded as measurement fields with CEL validation:
- ambient `temperature`, relative `humidity`, `dew_point`, substrate `steel_temp`;
- surface `profile` (microns), wet/dry film thickness `WFT`/`DFT`, `holiday_testing` result;
- **dew-point guard (CEL):** `fields.steel_temp >= fields.dew_point + 3.0` — block coating
  application when substrate is within 3 °C of dew point;
- **DFT average (CEL):** `(fields.dft_1 + fields.dft_2 + fields.dft_3) / 3.0`;
- **adhesion (CEL):** `fields.coating_adhesion_psi >= 1450.0` per SP-COAT-001.

## Welding/NDE inspection
Weld visual/NDT acceptance runs through this engine; weld-specific entities/rules live in
[`welding-and-nde.md`](welding-and-nde.md).
