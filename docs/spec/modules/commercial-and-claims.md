# Module detail — Commercial, progress claims & variations (`commercial`)

Covers MOD-04. Monetises verified field work. **No GL/AR/AP** — KERP owns commercial
commitment & claim transactions and exports them; money figures are read-only mirrors
(`amount_mirror`, `currency_code`) keyed by `external_source`/`external_id`/`synced_at`.

## Contracts & Schedule of Rates
- Multi-model contracts: Lump Sum, Schedule of Rates (unit rate), Cost-Plus / T&M, Target Cost.
- **SoR catalogs** with breakdown rules (e.g. 20 % delivery / 40 % erection / 30 % test-QA /
  10 % MDR handover).
- `commercial_claim_milestones`: `sor_code`, `required_itp_step_id`,
  `contract_claim_weight_percent`, `is_claim_unlocked`, `unlocked_at`. A milestone unlocks
  **only** when its ITP hold point is `INSPECTED_PASSED` (quality gates commercial).

## Progress claims (Pay Apps)
- Monthly/bi-weekly claims from objective field milestones; AIA **G702/G703**-style or custom
  schedules; retention/retainage + liquidated-damages matrices.
- Subcontractor payment applications evaluated against independent site measurement before
  certification.
- On client certification → emit **`commercial.claim.approved`** webhook (HMAC) to the external
  ERP (GL/AR posting happens **there**, not in KERP).

## Variations
- Registers: Variation Requests (VR), Variation Notices (VN), approved Variation Orders (VO),
  with full scope + rate history.
- **Delay event attribution** + **time-impact analysis (TIA)**; Delay Notices signed with
  pyHanko PAdES at issue.

## Upstream gates
Claim percentages are gated by field progress ([`../modules/asset-and-tagging.md`] RSW earned
qty) and QA/QC hold sign-offs ([`quality-and-inspection.md`](quality-and-inspection.md)); an
incomplete hydrotest blocks its testing milestone.

## Earned value counts verified completion (product intake 2026-11-22, planner default; user to confirm)
- Earned value (EV, for CPI = EV / AC and SPI = EV / PV, master spec MOD-03) credits a scope's
  budget only for **verified** progress: a progress step earns once the ITP line(s) that verify
  it are `INSPECTED_PASSED` or `LOCKED_FOR_TURNOVER` (facts from `inspections.service`), never on
  self-reported percent complete alone. Self-reported progress is still captured and shown
  beside it as "reported", so the gap is visible.
- A **planned vs verified-complete** view per area or work package (planned, reported, verified,
  with the difference) feeds the progress and EVM screens (E-04-03, E-06-04) and the planner
  statistics (planner-and-work-plans.md §9.1).
- Which ITP lines verify which progress steps, and partial credit, are open in TD-563. Money stays a read-only mirror: KERP computes indices from mirrored budget and
  cost, it never authors amounts.
