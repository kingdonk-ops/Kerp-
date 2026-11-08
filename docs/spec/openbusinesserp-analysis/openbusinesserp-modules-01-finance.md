# OpenBusinessERP — Per-Module Docs · Batch 1: Finance & Accounting

> Part of the full per-domain module documentation (inspection-erp Part-3 style) for OpenBusinessERP's 192 modules. **Batch 1 of 7.** Each module gets the Four Pillars (What It Is · What It Does · How It Does It · Interactions/failure). Grounded in `backend/app/modules/<name>/`. Tables are `oe_*`; routes mount at `/api/v1/<prefix>`; dependencies are manifest `depends`.

**Modules in this batch (15):** `gl`, `finance`, `banking`, `reconciliation`, `receivables`, `payment_clock`, `tax_withholding`, `fx`, `payroll`, `certified_payroll`, `labor_rates`, `allowances`, `funding`, `cost_recovery`, `postcalc`.

The README's "month in the software" is the spine these implement: quote → order → delivery → **invoice** (post the journal, split by tax) → **bank import + reconciliation** → **dunning** → period close → **SAF-T / DATEV** export.

---

## 1. `gl` — General Ledger (double-entry bookkeeping)
- **What:** the accounting core — chart of accounts, fiscal calendar, and balanced journal entries. `category=core`, no deps (it's foundational; other modules post *into* it).
- **Does:** ~60 routes over `oe_gl_account`, `oe_gl_fiscal_year`, `oe_gl_period`, `oe_gl_journal_entry`, `oe_gl_journal_line`, `oe_gl_attachment` — chart-of-accounts CRUD, fiscal-year/period open-close, journal posting, trial balance / ledger views, and attachments as posting evidence.
- **How:** service primitives `build_lines`, `check_balanced`, `natural_balance`, `base_currency`, `money` enforce **debits = credits** before a journal entry posts; accounts carry a natural (debit/credit) balance side; periods gate posting (a closed period refuses entries). Money is Decimal throughout.
- **Interactions:** the sink for `finance` (invoice postings), `banking` (reconciliation postings), `fx` (revaluation lines), `tax_withholding`. **Failure:** if disabled, nothing can post — it's effectively always-on `core`.

## 2. `finance` — Invoices, payments, budgets, EVM, GAAP ledger
- **What:** the commercial finance layer — customer/supplier invoices, payments, budgets, earned-value, and a GAAP-oriented ledger view. `category=core`, `depends=[users, projects, contacts]`. Router prefix `/gaap`.
- **Does:** 74 routes over `oe_finance_invoice(_item)`, `oe_finance_payment`, `oe_finance_budget`, `oe_finance_evm_snapshot`, `oe_finance_ledger(_account)` — invoice lifecycle, payment application, budget vs actual, EVM snapshots, ledger reporting.
- **How:** `compute_payment_withholding` nets withholding on payments; `resolve_position_cost_lines` ties cost lines to positions; EVM snapshots capture PV/EV/AC over time.
- **Interactions:** posts to `gl`; consumes `contacts` (parties) and `projects`; feeds `receivables` (open invoices) and dashboards.

## 3. `banking` — Bank accounts, statement import, matching, reconciliation
- **What:** the bank side — import statements and reconcile each line against the open invoice/bill behind it. `category=core`, `depends=[gl, sales]`.
- **Does:** 44 routes over `oe_bank_account`, `oe_bank_statement`, `oe_bank_transaction`, `oe_bank_match`, `oe_bank_partner_iban`, `oe_bank_settings`, `oe_bank_rule` — import **camt.053/054, MT940, CSV**, propose matches, apply reconciliation, and auto-rules.
- **How:** `partner_key` normalises counterparties; rules auto-classify recurring lines; a matched transaction posts the reconciling journal entry into `gl`; `oe_bank_partner_iban` learns IBAN→partner mappings.
- **Interactions:** `gl` (postings), `sales`/`finance` (open items to match), `reconciliation` (cross-record links). **Failure:** disabled → no statement import/reconciliation; invoices still post manually.

## 4. `reconciliation` — Cross-record linking
- **What:** a generic "these two records are the same money/event" linker. `category=controls`, `depends=[users, projects]`.
- **Does:** 6 routes over `oe_record_link` — list/get/decide links and assemble evidence threads across modules.
- **How:** `canonical_pair`/`link_key`/`scored_link_key` build a stable key for a pair; `gather_candidates` + `assemble_thread`/`build_event_thread` surface likely matches; `decide_record_link` confirms/rejects. Module-agnostic (works on entity URIs, not foreign keys).
- **Interactions:** complements `banking` matching; used by controls/audit flows. **Failure:** disabled → no manual cross-links; module-native matching still works.

## 5. `receivables` — Aging & dunning (draft-only)
- **What:** chase overdue customers safely. `category=core`, `depends=[sales, banking]`.
- **Does:** 44 routes over `oe_dunning_level`, `oe_dunning_settings`, `oe_dunning_block`, `oe_dunning_run`, `oe_dunning_letter`, `oe_ar_followup` — aging buckets, dunning runs, block list, AR follow-ups, statements.
- **How:** `bucket_for` ages open items; `next_level` proposes the dunning level; `render` builds the letter. **Dunning is draft-only** — letters are never sent automatically (module docstring); a blocked customer is skipped.
- **Interactions:** reads open invoices from `sales`/`finance`, payment status from `banking`. **Failure:** disabled → no automated chasing.

## 6. `payment_clock` — Statutory payment deadlines (regional)
- **What:** construction payment-term regimes (application → notice → due/final-date clocks). `category=regional`, `depends=[projects, contracts, subcontractors]`.
- **Does:** 26 routes over `oe_payment_clock_regime`, `_application`, `_notice`, `_event` — model a jurisdiction's payment regime, log applications/notices, and compute the statutory clock.
- **How:** `regime_spec`/`ensure_regimes` seed jurisdiction rules; `build_schedule`/`recompute_schedule` compute due/payless dates; `clock_snapshot`, `notified_sum`, `record_clock_events`, `sync_clock_register` keep a live register; applications/notices are CRUD with specs.
- **Interactions:** `contracts`/`subcontractors` (the paying relationships), `projects`. Regional add-on. **Failure:** disabled → no statutory-clock tracking.

## 7. `tax_withholding` — Withholding tax / reverse charge (e.g. CIS)
- **What:** compute and track tax withheld on supplier payments and reverse-charge VAT. `category=core`, `depends=[projects, users]`.
- **Does:** 48 routes over `oe_tax_withholding_regime`, `_party`, `_deduction`, `_reverse_charge` — regimes, per-party status/verification, deductions, reverse-charge records.
- **How:** `compute_taxable_base` + `compute_tax_withheld` with banded rates (`bands_of`/`find_band`/`resolve_band`), Decimal `to_decimal`/`quantise`; `verification_is_current` gates the rate on a party's current verification status; `seed_regimes` + `apply_*_body` drive config. Pairs with `finance.compute_payment_withholding`.
- **Interactions:** `finance` (payment netting), `gl` (postings), `projects`. **Failure:** disabled → payments don't net withholding.

## 8. `fx` — Foreign exchange rates & revaluation
- **What:** multi-currency rates, policy, and period-end revaluation. `category=core`, no deps.
- **Does:** 28 routes over `oe_fx_rate_set`, `oe_fx_rate_quote`, `oe_fx_policy`, `oe_fx_rate`, `oe_ppp_factor` — rate sets/quotes, conversion policy, and PPP factors.
- **How:** `parse_ecb_xml` ingests ECB reference rates; `cross_rate`/`convert_via_base` convert through a base currency; `decompose_movement` + `as_revaluation_lines` generate the FX gain/loss journal lines for `gl`.
- **Interactions:** `gl` (revaluation postings); any module handling non-base-currency amounts. **Failure:** disabled → single-currency only.

## 9. `payroll` — Payroll batches
- **What:** payroll runs built from field time/resources. `category=core`, `depends=[projects, fieldreports, resources]`.
- **Does:** 24 routes over `oe_payroll_batch`, `oe_payroll_entry`, `oe_payroll_deduction` — assemble a pay batch, per-person entries and deductions.
- **How:** entries draw hours from `fieldreports`/`resources` against `projects`; deductions net per entry; batches post to `gl`.
- **Interactions:** upstream of `certified_payroll`; `fieldreports`/`resources` (hours). **Failure:** disabled → no payroll runs.

## 10. `certified_payroll` — Prevailing-wage certified payroll (compliance)
- **What:** US-style certified payroll (Davis-Bacon determinations, weekly WH-347-type reporting). `category=compliance`, `depends=[payroll, projects, resources]`.
- **Does:** 38 routes over `oe_certpay_determination`, `_classification`, `_assignment`, `_week`, `_line` — wage determinations, worker classifications, assignments, and weekly certified lines.
- **How:** classifications map workers to determination rates; weekly lines derive from `payroll` entries and are checked against the determination; produces the certified-payroll report.
- **Interactions:** builds on `payroll`; `projects`/`resources`. Compliance add-on. **Failure:** disabled → no prevailing-wage reporting.

## 11. `labor_rates` — Fully-burdened labour rate build-up
- **What:** compute all-in crew/labour rates from base pay + on-costs. `category=core`, `depends=[users]`.
- **Does:** 20 routes over `oe_labor_rates_template`, `_oncost`, `_crew_member` — rate templates, on-cost components, crew composition.
- **How:** `compute_breakdown` applies on-costs (tax, insurance, overhead) to base rates; `describe_template_references` reports what a template feeds.
- **Interactions:** feeds estimating/cost modules (Batch 4) and `payroll`. **Failure:** disabled → manual rates only.

## 12. `allowances` — Contract allowances & drawdowns
- **What:** track contractual allowance pots and draw against them. `category=core`, `depends=[projects, users]`.
- **Does:** 18 routes over `oe_allowances_allowance`, `oe_allowances_drawdown` — define an allowance, record drawdowns, show remaining.
- **How:** drawdowns decrement the allowance; balance = allowance − Σ drawdowns.
- **Interactions:** `projects`; relates to change orders/variations (Batch 5). **Failure:** disabled → no allowance tracking.

## 13. `funding` — Grants/funding programmes & proof-of-use
- **What:** manage funding programmes, applications, disbursements and obligations (grant compliance). `category=business`, `depends=[users, projects]`.
- **Does:** 60 routes over `oe_funding_programme`, `_application`, `_disbursement`, `_proof_of_use`, `_obligation`, `_cost_allocation` — programme setup, applications, disbursement tracking, proof-of-use, obligations and cost allocation.
- **How:** `render_detail` composes a programme view; obligations carry title keys (i18n) and `server_authored_fields_replaced` guards server-owned fields; cost allocation ties project costs to a funding source.
- **Interactions:** `projects` (allocated costs), `finance`. **Failure:** disabled → no grant tracking.

## 14. `cost_recovery` — Back-charges & apportionment (controls)
- **What:** recover costs from a responsible party and apportion shared costs. `category=controls`, `depends=[users, projects]`.
- **Does:** 18 routes over `oe_cost_recovery_back_charge`, `_apportionment` — raise back-charges, apportion, and report recovery performance.
- **How:** `create/update/list/get_back_charge`; `apportion_back_charge` + `build_recovery_ledger`; `build_recovery_performance` / `build_portfolio_recovery_performance` roll up; `pending_backcharges` lists open items.
- **Interactions:** `projects`; relates to `subcontractors`/`contracts`. **Failure:** disabled → no back-charge recovery.

## 15. `postcalc` — Post-calculation (actual vs estimate feedback)
- **What:** close the loop from actuals back to estimating norms. `category=business`, `depends=[boq, costmodel]`. (No tables of its own — it computes over `boq`/`costmodel` data.)
- **Does:** 8 routes — project post-calculation and productivity feedback.
- **How:** `consumed_material_cost`, `compute_line_productivity`, `aggregate_resources`, `build_feedback_factors`, `compute_project_postcalc` compare actual consumption/productivity against the estimate and emit feedback factors to tune future norms.
- **Interactions:** reads `boq`/`costmodel` (Batch 4); feeds estimating. **Failure:** disabled → no actual-vs-estimate feedback.

---

### Batch-1 interaction map (within finance)
```
sales/finance ──invoices──▶ receivables (dunning)
   │                           ▲
   └──postings──▶ gl ◀──reconcile── banking ◀──camt/MT940/CSV
                    ▲    ▲
          revaluation │   │ withholding
                 fx ──┘   └── tax_withholding ──nets──▶ finance payments
labor_rates ──rates──▶ payroll ──▶ certified_payroll
funding / allowances / cost_recovery ──▶ projects (cost allocation)
boq/costmodel ──actuals──▶ postcalc ──feedback──▶ estimating
payment_clock ──statutory clocks──▶ contracts/subcontractors
```
All post into **`gl`**, which is the always-on sink. Each add-on fails closed/independently: disabling one removes its surface and its routes 402/404, but `gl` and the invoice→post path keep working.

---

*Batch 1 of 7. Next in order: **Sales & Commercial** (sales, crm, contacts, items, inventory, contracts, quotes/invoicing, subcontractors). Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525`.*
