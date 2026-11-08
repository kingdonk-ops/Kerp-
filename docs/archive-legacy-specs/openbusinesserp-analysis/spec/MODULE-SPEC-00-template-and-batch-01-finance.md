> ## ⚠ Field-Ops pivot status (authoritative: ../FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md)
> **REMOVED:** ~~gl~~ ~~finance~~ ~~banking~~ ~~receivables~~ ~~payment_clock~~ ~~tax_withholding~~ ~~fx~~ ~~payroll~~ ~~certified_payroll~~ ~~labor_rates~~ ~~allowances~~ ~~funding~~ ~~postcalc~~ · **KEPT:** reconciliation, cost_recovery (re-anchored, no GL posting).

# OpenBusinessERP — Module Specifications (editable)
## Spec format + Batch 1: Finance & Accounting

> Purpose: a per-module spec you can **edit to shape the build plan** — strike whole modules, strike individual features, or add new ones. This file defines the **spec format** (so all 7 batches are consistent) and contains **Batch 1 (Finance, 15 modules)** as the worked sample. Grounded in `backend/app/modules/<name>/{models,router,service}.py` of `kingdonk-ops/openbusinesserp` @ `a96d525`.
>
> **Please confirm this format before I spec the remaining ~176 modules** — the shape (esp. the discrete `[F#]` features and removal-impact) is what makes it editable, so it's worth locking first.

---

## Spec format (per module)

```
### <name>  — <one-line title>
- Identity: category=<…> · optional?/always-on · prefix=/api/v1/<…> · routes≈N
- Depends on: [modules]      Dependents: [modules that import this]   ← removal blast radius
- Purpose: 1–2 lines
- Entities (oe_<module>_*): table → key fields
- Endpoints: grouped
- Key logic / state: service functions, state machines, invariants
- Events: emitted / consumed (if any)
- Features [F1..Fn]: discrete, individually removable — tick/strike to edit scope
- Removal impact: what breaks / who must change if this module is dropped
- Edit notes: (left blank for you)
```

### Blank template for a NEW module (copy to add one)
```
### <new_name> — <title>
- Identity: category=<core|business|department|regional|extension|…> · optional · prefix=/<…>
- Depends on: []              Dependents: []
- Purpose:
- Entities: oe_<name>_<entity> → (fields…)
- Endpoints:
- Key logic / state:
- Events:
- Features: [F1] …
- Removal impact:
- Edit notes:
```

**How to edit:** strike a `### module` to remove it (note its Dependents — they must change); strike a `[F#]` line to drop just that feature; copy the blank template to add one and wire `Depends on`. The build plan's phase/order follows the `Depends on` graph, so edits here reshape the plan deterministically.

---

## Batch 1 — Finance & Accounting (15 modules)

### gl — General Ledger (double-entry)
- **Identity:** category=core · always-on · prefix=`/gl` · routes≈60
- **Depends on:** [] · **Dependents:** finance, banking, sales, purchasing, fx, tax_withholding, payroll (everything that posts)
- **Purpose:** the accounting core; chart of accounts, fiscal calendar, balanced journals. The posting sink for the whole system.
- **Entities:**
  - `oe_gl_account` → code, name, account_type, parent_id, currency, is_postable, active, template
  - `oe_gl_fiscal_year` → name, start_date, end_date, status, last_number, periods
  - `oe_gl_period` → fiscal_year_id, name, start_date, end_date, status
  - `oe_gl_journal_entry` → number, seq, entry_date, period_id, fiscal_year_id, currency, base_currency, fx_rate, status, source_type, source_id, memo, reversal_of_id, reversed_by_id, created_by
  - `oe_gl_journal_line` → entry_id, line_no, account_id, description, debit, credit, base_debit, base_credit, tax_code, cost_center, project_id
  - `oe_gl_attachment` → entry_id, filename, content_type, size, data, uploaded_by
- **Endpoints:** accounts CRUD · fiscal-year/period open-close · journal post/list/get · trial balance / ledger · attachments.
- **Key logic:** `build_lines`, `check_balanced` (debits=credits before post), `natural_balance`, `base_currency`, `money` (Decimal). Closed period refuses posting. Reversal via `reversal_of_id`/`reversed_by_id`.
- **Features:** [F1] chart of accounts · [F2] fiscal calendar + period close · [F3] balanced journal posting · [F4] multi-currency base/fx on entries · [F5] journal attachments (evidence) · [F6] reversals · [F7] cost-center/project tagging on lines.
- **Removal impact:** **catastrophic** — no module can post; do not remove. (Strike features instead, e.g. drop [F5] attachments or [F7] cost-center.)
- **Edit notes:**

### finance — Invoices, payments, budgets, EVM, GAAP ledger
- **Identity:** category=core · prefix=`/gaap` · routes≈74 · **Depends on:** [users, projects, contacts] · **Dependents:** receivables, dashboards
- **Purpose:** commercial finance — AR/AP invoices, payments, budgets, earned value, a project GAAP ledger.
- **Entities:**
  - `oe_finance_invoice` → project_id, contact_id, invoice_direction, source_claim_id, purchase_order_id, invoice_number, invoice_date, due_date, currency_code, amount_subtotal, tax_amount, retention_amount, amount_total, tax_config_id, status, payment_terms_days, notes, metadata_
  - `oe_finance_invoice_item` → invoice_id, description, quantity, unit, unit_rate, amount, wbs_id, cost_category, vat_rate, vat_category, cost_line_id, sort_order
  - `oe_finance_payment` → invoice_id, payment_date, amount, currency_code, exchange_rate_snapshot, reference, idempotency_key, is_refund, withholding_amount, source_claim_id, withholding_release_date, metadata_
  - `oe_finance_budget` → project_id, wbs_id, category, currency_code, original_budget, revised_budget, committed, actual, forecast_final
  - `oe_finance_evm_snapshot` → project_id, snapshot_date, bac, pv, ev, ac, sv, cv, spi, cpi, eac, vac, etc, tcpi
  - `oe_finance_ledger` → project_id, transaction_ref, account_code, description, debit_amount, credit_amount, currency_code, posted_at, source_type, source_id, is_reversal, reversal_of_id, idempotency_key
  - `oe_finance_ledger_account` → project_id, account_code, name, account_type, normal_balance, parent_id, statement_section, is_cash, is_active, currency_code
- **Endpoints:** invoice lifecycle · payment application · budgets · EVM snapshots · ledger reporting.
- **Key logic:** `compute_payment_withholding` (nets withholding), `resolve_position_cost_lines`; idempotency keys on payments/ledger; EVM metrics.
- **Features:** [F1] AR/AP invoices + items · [F2] payments (incl. refunds, FX snapshot, idempotency) · [F3] withholding netting (needs tax_withholding) · [F4] project budgets (orig/revised/committed/actual/forecast) · [F5] EVM snapshots · [F6] project GAAP ledger + accounts · [F7] retention on invoices.
- **Removal impact:** receivables & finance dashboards lose their data; a lighter `sales`/`gl`-only AR path would need building. 
- **Edit notes:**

### banking — Accounts, statement import, matching, reconciliation
- **Identity:** category=core · prefix=`/banking` · routes≈44 · **Depends on:** [gl, sales] · **Dependents:** receivables, reconciliation
- **Entities:** `oe_bank_account`(name, iban, bic, currency, gl_account_code, active, opening_balance[_date]) · `oe_bank_statement`(account_id, filename, format, imported_count, duplicate_count) · `oe_bank_transaction`(…) · `oe_bank_match` · `oe_bank_partner_iban` · `oe_bank_settings` · `oe_bank_rule`
- **Endpoints:** accounts · statement import · transactions · matches · rules · settings.
- **Key logic:** import **camt.053/054, MT940, CSV**; `partner_key` normalise; rules auto-classify; match → post reconciling journal to gl; IBAN→partner learning.
- **Features:** [F1] bank accounts · [F2] statement import camt · [F3] import MT940 · [F4] import CSV · [F5] auto-matching · [F6] auto-rules · [F7] IBAN→partner learning · [F8] reconciliation posting to gl.
- **Removal impact:** no statement import/reconciliation; invoices still post manually. (Strippable: drop [F3]/[F4] formats or [F6] rules independently.)
- **Edit notes:**

### reconciliation — Cross-record linking
- **Identity:** category=controls · prefix (controls) · routes≈6 · **Depends on:** [users, projects] · **Dependents:** (banking-adjacent)
- **Entities:** `oe_record_link` → project_id, left_type, left_id, right_type, right_id, relation, confidence, status, created_by, metadata_
- **Key logic:** `canonical_pair`, `link_key`, `scored_link_key`, `gather_candidates`, `assemble_thread`, `decide_record_link` — entity-URI based, module-agnostic.
- **Features:** [F1] manual record links · [F2] candidate suggestion/scoring · [F3] evidence threads.
- **Removal impact:** no cross-module manual links; native matching (banking) unaffected.
- **Edit notes:**

### receivables — Aging & dunning (draft-only)
- **Identity:** category=core · prefix=`/receivables`? · routes≈44 · **Depends on:** [sales, banking] · **Dependents:** —
- **Entities:** `oe_dunning_level`, `oe_dunning_settings`, `oe_dunning_block`, `oe_dunning_run`, `oe_dunning_letter`, `oe_ar_followup`
- **Key logic:** `bucket_for` (aging), `next_level`, `render`; **letters are draft-only, never auto-sent**; block list skips customers.
- **Features:** [F1] aging buckets · [F2] dunning levels/settings · [F3] dunning runs + letters (draft) · [F4] block list · [F5] AR follow-ups · [F6] statements.
- **Removal impact:** no automated chasing; manual AR only.
- **Edit notes:**

### payment_clock — Statutory payment deadlines (regional)
- **Identity:** category=regional · prefix=`/payment-clock`? · routes≈26 · **Depends on:** [projects, contracts, subcontractors]
- **Entities:** `oe_payment_clock_regime`(code, jurisdiction, country_code, statute, due/notice/final/pay_less bases+days, interest_*) · `oe_payment_clock_application`(project_id, regime_id, source, application_date, period, applied_amount, due/notice/payless/final dates, status, paid_*) · `oe_payment_clock_notice`(application_id, notice_type, issued_at, notified_amount, basis) · `oe_payment_clock_event`
- **Key logic:** `regime_spec`/`ensure_regimes`, `build_schedule`/`recompute_schedule`, `clock_snapshot`, `record_clock_events`, `sync_clock_register`.
- **Features:** [F1] jurisdiction regimes · [F2] payment applications + clocks · [F3] notices (payment/payless) · [F4] interest computation · [F5] clock register sync.
- **Removal impact:** no statutory-clock tracking (UK Construction Act etc.).
- **Edit notes:**

### tax_withholding — Withholding / reverse charge
- **Identity:** category=core · routes≈48 · **Depends on:** [projects, users] · **Dependents:** finance (payment netting)
- **Entities:** `oe_tax_withholding_regime`, `oe_tax_withholding_party`, `oe_tax_withholding_deduction`, `oe_tax_withholding_reverse_charge`
- **Key logic:** `compute_taxable_base`, `compute_tax_withheld` (banded: `bands_of`/`find_band`/`resolve_band`), `verification_is_current` gates rate, `seed_regimes`, Decimal quantise.
- **Features:** [F1] withholding regimes · [F2] per-party status + verification gating · [F3] banded deduction computation · [F4] reverse-charge records · [F5] determinations.
- **Removal impact:** payments don't net withholding (finance [F3] disabled).
- **Edit notes:**

### fx — FX rates & revaluation
- **Identity:** category=core · routes≈28 · **Depends on:** [] · **Dependents:** gl (revaluation), assemblies, any multi-currency
- **Entities:** `oe_fx_rate_set`, `oe_fx_rate_quote`, `oe_fx_policy`, `oe_fx_rate`, `oe_ppp_factor`
- **Key logic:** `parse_ecb_xml`, `cross_rate`, `convert_via_base`, `decompose_movement`, `as_revaluation_lines`.
- **Features:** [F1] rate sets/quotes · [F2] ECB import · [F3] conversion policy · [F4] period-end revaluation → gl · [F5] PPP factors.
- **Removal impact:** single-currency only.
- **Edit notes:**

### payroll — Payroll batches
- **Identity:** category=core · routes≈24 · **Depends on:** [projects, fieldreports, resources] · **Dependents:** certified_payroll
- **Entities:** `oe_payroll_batch`, `oe_payroll_entry`, `oe_payroll_deduction`
- **Features:** [F1] pay batches · [F2] entries from field hours · [F3] deductions · [F4] post to gl.
- **Removal impact:** no payroll; certified_payroll loses its source.
- **Edit notes:**

### certified_payroll — Prevailing-wage reporting (compliance)
- **Identity:** category=compliance · routes≈38 · **Depends on:** [payroll, projects, resources]
- **Entities:** `oe_certpay_determination`, `_classification`, `_assignment`, `_week`, `_line`
- **Features:** [F1] wage determinations · [F2] worker classifications · [F3] assignments · [F4] weekly certified lines (WH-347-style) · [F5] report output.
- **Removal impact:** no US prevailing-wage certified payroll.
- **Edit notes:**

### labor_rates — Burdened labour rates
- **Identity:** category=core · routes≈20 · **Depends on:** [users] · **Dependents:** estimating, payroll
- **Entities:** `oe_labor_rates_template`, `_oncost`, `_crew_member`
- **Key logic:** `compute_breakdown` (base + on-costs), `describe_template_references`.
- **Features:** [F1] rate templates · [F2] on-cost components · [F3] crew composition · [F4] all-in rate build-up.
- **Removal impact:** manual rates only.
- **Edit notes:**

### allowances — Contract allowances & drawdowns
- **Identity:** category=core · routes≈18 · **Depends on:** [projects, users]
- **Entities:** `oe_allowances_allowance`, `oe_allowances_drawdown`
- **Features:** [F1] allowance pots · [F2] drawdowns · [F3] remaining balance.
- **Removal impact:** no allowance tracking (estimate_rollup loses an input).
- **Edit notes:**

### funding — Grants/funding & proof-of-use
- **Identity:** category=business · routes≈60 · **Depends on:** [users, projects]
- **Entities:** `oe_funding_programme`, `_application`, `_disbursement`, `_proof_of_use`, `_obligation`, `_cost_allocation`
- **Features:** [F1] programmes · [F2] applications · [F3] disbursements · [F4] proof-of-use · [F5] obligations · [F6] cost allocation to funding source.
- **Removal impact:** no grant/funding compliance.
- **Edit notes:**

### cost_recovery — Back-charges & apportionment (controls)
- **Identity:** category=controls · routes≈18 · **Depends on:** [users, projects]
- **Entities:** `oe_cost_recovery_back_charge`, `oe_cost_recovery_apportionment`
- **Key logic:** `create/update/list/get_back_charge`, `apportion_back_charge`, `build_recovery_ledger`, `build_recovery_performance`, `pending_backcharges`.
- **Features:** [F1] back-charges · [F2] apportionment · [F3] recovery ledger · [F4] recovery performance (project + portfolio).
- **Removal impact:** no back-charge recovery.
- **Edit notes:**

### postcalc — Post-calculation feedback
- **Identity:** category=business · routes≈8 · no tables · **Depends on:** [boq, costmodel] · **Dependents:** estimating norms
- **Key logic:** `consumed_material_cost`, `compute_line_productivity`, `aggregate_resources`, `build_feedback_factors`, `compute_project_postcalc`.
- **Features:** [F1] actual-vs-estimate comparison · [F2] productivity calc · [F3] feedback factors to norms.
- **Removal impact:** no actual→norm feedback loop.
- **Edit notes:**

---

*Batch 1 sample of 7. On your confirmation of this format I'll produce Batches 2–7 the same way (entities+fields, endpoints, discrete features, deps-in/out, removal impact) and push them to this branch. Grounded @ `a96d525`.*
