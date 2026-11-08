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
