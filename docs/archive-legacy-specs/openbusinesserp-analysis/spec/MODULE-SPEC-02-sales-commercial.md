> ## ⚠ Field-Ops pivot status (authoritative: ../FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md)
> **REMOVED:** ~~sales~~ ~~items~~ ~~inventory~~ ~~catalog~~ ~~contracts~~ · **MODIFIED:** crm (basic people/company directory, no pipeline), supplier_catalogs (basic), subcontractors (basic) · **KEPT:** contacts, site_inventory.

# OpenBusinessERP — Module Specifications · Batch 2: Sales & Commercial

> Editable module specs (format defined in `MODULE-SPEC-00`). Grounded in `backend/app/modules/<name>/{models,router,service}.py` @ `a96d525`. Entities list real columns from `models.py`. Strike a `### module` to drop it (mind *Dependents*), strike a `[F#]` to drop a feature.

**Modules (10):** `sales`, `crm`, `contacts`, `items`, `inventory`, `site_inventory`, `catalog`, `supplier_catalogs`, `contracts`, `subcontractors`.

---

### sales — Order-to-cash
- **Identity:** category=core · always-on · prefix=`/sales` · routes≈68 · **Depends on:** [gl] · **Dependents:** receivables, banking, recurring
- **Purpose:** quote → sales order → invoice → payment; posts to the GL, splits by tax, emails branded PDFs, mints pay-links.
- **Entities:**
  - `oe_sales_document` → kind, number, status, customer_id, customer_name/address/vat_id/email, currency, fx_rate, doc_date, due_date, source_id, net_total, tax_total, gross_total, amount_paid, gl_entry_id, language, delivery_date/end, tax_note, payment_terms, buyer_reference, sent_at/sent_to, notes
  - `oe_sales_line` → document_id, line_no, item_id, sku, description, qty, unit_price, discount_pct, tax_rate, net, tax, gross
  - `oe_sales_payment` → invoice_id, pay_date, amount, method, reference, fx_rate, gl_entry_id
  - `oe_sales_sequence` → kind, last_number · `oe_sales_account_map` → role, account_code · `oe_sales_send_log` → document_id, sent_by/at, to_addr, cc, subject, status, backend · `oe_sales_payment_link` → invoice_id, provider, provider_ref, url, amount, currency, status, paid_amount, payment_id
- **Endpoints:** document CRUD + state transitions (quote→order→invoice) · payments · sequences · account-map · send (email) · payment-links.
- **Key logic:** one polymorphic `document.kind/status` state machine; invoice posting → GL via `account_map` (role→account), tax-split; `sequence` issues numbers; multi-currency fx_rate.
- **Features:** [F1] quotes · [F2] sales orders · [F3] invoices + GL posting (tax-split) · [F4] payments · [F5] numbering sequences · [F6] GL account mapping · [F7] email send + log · [F8] payment links (SEPA/QR/provider) · [F9] multi-currency.
- **Removal impact:** **core sell-side** — receivables/banking lose open items; do not remove (strike features e.g. [F8] pay-links).
- **Edit notes:**

### crm — Sales pipeline
- **Identity:** category=business · optional · prefix=`/crm` · routes≈94 · **Depends on:** [users] · **Dependents:** sales (conversion), contacts
- **Entities:** `oe_crm_pipeline_stage`(code, name, display_order, default_probability_percent, is_final/won/lost, color) · `oe_crm_pipeline_stage_config`(kanban_columns, defaults) · `oe_crm_win_loss_reason`(code, label, category, is_win/loss_reason) · `oe_crm_account`(name, industry, size_category, country, website, primary_contact_id, status, owner_user_id, tags, parent_account_id) · `oe_crm_lead`(account_id, contact_name/email/phone, source, status, assigned_to, qualification_notes, qualified_at, converted_at, converted_opportunity_id) · `oe_crm_opportunity`(account_id, title, estimated_value, currency, expected_close_date, probability_percent, stage_id, weighted_value, source, owner_user_id, status, won_at/lost_at, lost_reason_code, primary_contact_id, project_id, competitor_names) · `oe_crm_opportunity_stage_history`(opportunity_id, from/to_stage_id, changed_at/by, duration_in_previous_seconds) · `oe_crm_activity`(owner_user_id, account/opportunity/lead_id, kind, subject, body, due_at, completed_at, outcome, external_calendar_event_id) · `oe_crm_forecast`(period, owner_user_id, pipeline/weighted/won/committed_value, by_currency, mixed_currency)
- **Endpoints:** accounts · leads (+convert) · opportunities (+stage move) · stages/config · win-loss reasons · activities · forecast.
- **Key logic:** configurable stages w/ recorded stage-history + durations; weighted pipeline; lead→opportunity conversion; forecast aggregation (multi-currency aware).
- **Features:** [F1] accounts · [F2] leads + qualification + conversion · [F3] opportunities + weighted value · [F4] configurable pipeline stages + kanban · [F5] stage history/durations · [F6] win/loss reasons · [F7] activities (+calendar link) · [F8] forecasting.
- **Removal impact:** no pipeline; `sales` still quotes directly.
- **Edit notes:**

### contacts — Party master
- **Identity:** category=core · prefix=`/contacts` · routes≈30 · **Depends on:** [users] · **Dependents:** sales, crm, finance, purchasing, subcontractors, supplier_catalogs
- **Entities:** `oe_contacts_contact` → contact_type, is_platform_user, user_id, first_name, last_name, company_name, legal_name, vat_number, country_code, address, primary_email, primary_phone, website, certifications, insurance, prequalification_status, qualified_until, payment_terms_days, leitweg_id, currency_code, name_translations, module_tags, custom_properties, is_active, created_by, metadata_
- **Endpoints:** contact CRUD · search · classify.
- **Key logic:** one table for every party role; `module_tags`/`custom_properties` extensibility; `name_translations` for i18n; `tenant_id`←creator (single-tenant).
- **Features:** [F1] contacts CRUD · [F2] company/person split · [F3] VAT/leitweg/payment-terms (invoicing) · [F4] certifications/insurance/prequal fields · [F5] custom properties + module tags · [F6] name translations.
- **Removal impact:** core — all party-referencing modules break.
- **Edit notes:**

### items — Product/service master, price lists, discounts
- **Identity:** category=core · prefix=`/items` · routes≈54 · **Depends on:** [] · **Dependents:** sales, inventory, purchasing
- **Entities:** `oe_items_category`(name, parent_id, description) · `oe_items_item`(sku, name, description, item_type, uom, sales_price, purchase_cost, currency, tax_code, revenue_account, …) · `oe_items_price_list` · `oe_items_price_list_item` · `oe_items_discount_rule`
- **Endpoints:** items + categories CRUD · price lists (+items) · discount rules.
- **Key logic:** price resolution = applicable price list → discount rules; goods vs service via `item_type`; GL `revenue_account` per item.
- **Features:** [F1] items + categories · [F2] UoM · [F3] multiple price lists · [F4] discount rules · [F5] GL revenue mapping.
- **Removal impact:** core — sales/inventory/purchasing depend on it.
- **Edit notes:**

### inventory — Warehouses, movements, moving-average cost, stocktakes
- **Identity:** category=core · prefix=`/inventory` · routes≈36 · **Depends on:** [items] · **Dependents:** sales (dispatch), purchasing (receipt)
- **Entities:** `oe_inventory_warehouse`(code, name, address, is_default, active) · `oe_inventory_movement`(item_id, warehouse_id, kind, move_date, qty, unit_cost, value, source_type/id/number, reversal_of_id, reversed, transfer_group, gl_entry_id) · `oe_inventory_item_cost`(item_id, qty, value, avg_cost) · `oe_inventory_stocktake`(number, warehouse_id, status, count_date, diff_value, gl_entry_id) · `oe_inventory_stocktake_line`(stocktake_id, item_id, expected_qty, counted_qty, unit_cost, diff_qty/value) · `oe_inventory_settings`(allow_negative, inventory_account, cogs_account, diff_account)
- **Endpoints:** warehouses · movements (in/out/transfer/reversal) · costs · stocktakes · settings.
- **Key logic:** each movement updates on-hand + **moving-average cost** (`item_cost.avg_cost`); stocktake posts variance to `diff_account`; GL accounts in settings; reversals via `reversal_of_id`.
- **Features:** [F1] warehouses · [F2] stock movements · [F3] moving-average costing · [F4] transfers · [F5] reversals · [F6] stocktakes + variance posting · [F7] negative-stock toggle · [F8] GL account config.
- **Removal impact:** no stock tracking/valuation.
- **Edit notes:**

### site_inventory — On-site material metering
- **Identity:** category=business · optional · prefix=`/site-inventory` · routes≈28 · **Depends on:** [projects, boq, procurement, progress, costs] · **Dependents:** —
- **Entities:** `oe_site_inventory_location`(project_id, name, code, lat/long, address, is_active) · `oe_site_inventory_item`(project_id, name, sku, unit, boq_position_id, procurement_req_item_id, default_location_id, standard_unit_cost, currency, reorder_point) · `oe_site_inventory_movement`(project_id, item_id, movement_type, quantity, unit_cost, location_id, to_location_id, boq_position_id, goods_receipt_id, occurred_at, actor_id)
- **Key logic:** meters material on-site against `boq`/`progress`/`costs`; movements tie to goods receipts + BOQ positions.
- **Features:** [F1] site locations (geo) · [F2] site items linked to BOQ/procurement · [F3] movements (receipt/consume/transfer) · [F4] reorder points · [F5] consumption→cost tie.
- **Removal impact:** no on-site metering; warehouse `inventory` unaffected.
- **Edit notes:**

### catalog — Resource catalog (data layer)
- **Identity:** category=core · no router surface · **Depends on:** [costs] · **Dependents:** costs/estimating
- **Entities:** `oe_catalog_resource` → resource_code, name, resource_type, category, unit, base_price, min_price, max_price, currency, usage_count, source, region, specifications, is_active, metadata_
- **Key logic:** reference resource data read by costing/estimating; loaders/UI removed (data layer only).
- **Features:** [F1] resource reference records · [F2] price band (min/base/max) · [F3] region tagging.
- **Removal impact:** inert if unused; estimating loses a resource source.
- **Edit notes:**

### supplier_catalogs — Vendor management & procure-to-pay (extension)
- **Identity:** category=extension · optional · prefix=`/supplier-catalogs` · routes≈96 · **Depends on:** [projects, users, procurement] · **Dependents:** —
- **Entities (21):** vendor(`_vendor`: code, name, legal_name, tax_id, contact_id, status, currency, payment_terms_days, rating, categories/preferred/contacts_json, tolerance_profile_name) · catalogue(`_item_category`, `_catalog_item`: sku, mpn, gtin, commodity_code, hazard_class, shelf_life; `_price_list`, `_catalog_entry`: vendor_sku, unit_price, min_order_qty, lead_time) · P2P(`_pr`/`_pr_line`, `_po`/`_po_line`: ordered/received/invoiced_qty, `_gr`/`_gr_line`: received/accepted/rejected_qty, batch/serials, `_invoice`/`_invoice_line`: three_way_match_status, peppol_message_id) · matching(`_match_record`: price/qty_variance, tolerance_used_pct) · stock(`_warehouse`, `_stock_balance`: on_hand/reserved, avg cost, `_stock_movement`) · governance(`_commodity_code`, `_tolerance_profile`: price/qty tolerances, require_gr, `_kyc_document`: doc_type, expires_on, verified, `_scorecard`: delivery/quality/price/esg/composite scores)
- **Endpoints:** vendors · catalogues/price-lists · PR→PO→GR→invoice · 3-way match · vendor stock · commodity codes · tolerance profiles · KYC · scorecards.
- **Key logic:** requisition→order→goods-receipt→invoice with **tolerance-profiled 3-way match** (`match_record` vs `tolerance_profile`); KYC/scorecards gate & rank vendors; vendor-side stock.
- **Features:** [F1] vendor master · [F2] vendor catalogues + price lists · [F3] purchase requisitions · [F4] purchase orders · [F5] goods receipts (batch/serial) · [F6] supplier invoices (+Peppol) · [F7] 3-way match + tolerance profiles · [F8] vendor stock (balances/movements) · [F9] commodity codes · [F10] KYC documents · [F11] vendor scorecards (ESG-aware).
- **Removal impact:** no vendor management / 3-way match; `procurement` still covers project POs.
- **Edit notes:**

### contracts — Contract Types Engine (largest commercial module)
- **Identity:** category=business · optional · prefix=`/contracts` · routes≈260 · **Depends on:** [users, projects] · **Dependents:** payment_clock, subcontractors, cost_recovery
- **Entities (21):** `_contract`(code, title, contract_type, counterparty, project_id, parent_contract_id, dates, total_value, original_contract_value, retention_percent, retention_release_event, status, signed_at, template_code/version) · `_contract_line`(line_type, unit, quantity, unit_rate, total_value, cost_line_id, origin) · `_type_configuration`(contract_type, allowed_fields, default_fee_structure) · `_retention_schedule`(accrual/release_rule) · `_fee_structure`(fee_type/percent/fixed, sliding_scale, max_fee) · `_gainshare_configuration`(target_cost, gmp_cap, savings split, overrun) · `_ld_clause`(per_day_amount, max_amount, milestone_id) · `_progress_claim`(+`_line`: period, gross/retention/net, assessed/certified_qty, materials_stored_value) · `_final_account` · `_party` · `_security`(bonds/guarantees) · `_eot_claim`(days_claimed/granted) · `_milestone`(planned_date, value, trigger, forecast/reached dates) · `_document` · `_template`(+`_template_clause`: risk_level) · `_sov_adjustment` · `_retention_release` · `_stored_material`(+`_movement`: title_transferred, offsite docs)
- **Endpoints:** contracts + lines · type configs · fee/gainshare/retention/LD · progress claims (+lines) · SOV adjustments · retention releases · EOT · securities · milestones · stored materials · templates/clauses · final account · documents.
- **Key logic:** **type configuration** parametrises behaviour per contract type; progress claims draw on SOV with retention schedules; EOT/LD manage time/penalty; stored-material title tracking; templates assemble clause sets.
- **Features:** [F1] contracts + SOV lines · [F2] contract-type configuration engine · [F3] fee structures · [F4] gainshare/GMP · [F5] retention schedule + releases · [F6] LD clauses · [F7] progress claims + certification · [F8] stored materials (on/offsite, title) · [F9] EOT claims · [F10] securities/bonds · [F11] milestones (+payment trigger) · [F12] templates + clause risk · [F13] final account · [F14] contract documents.
- **Removal impact:** no formal contract administration; payment_clock/subcontractors/cost_recovery lose linkage.
- **Edit notes:**

### subcontractors — Subcontractor management
- **Identity:** category=business · optional · prefix=`/subcontractors` · routes≈126 · **Depends on:** [users, projects, contacts, contracts]
- **Entities (11):** `_subcontractor`(contact_id, legal/trade_name, tax_id, trade_categories, prequal_status/score, insurance_expiry/doc, is_blocked/blocked_reason, rating_score) · `_subcontractor_contact` · `_prequalification`(answers, status, decision) · `_certificate`(cert_type, valid_until, revoked) · `_agreement`(project_id, total_value, retention_percent/release_event, requires_lien_waiver, prime_contract_id, contract_id, status) · `_work_package`(scope, planned_value, completion_percent, contract_line_id) · `_payment_application`(+`_line`: application_number, gross/retention/net, foreman_approved, finance_approved, approved amounts, progress_claim_id) · `_retention_ledger`(accrued/released) · `_rating`(quality/hse/schedule/cost/overall) · `_lien_waiver`(waiver_type, through_date, amount)
- **Endpoints:** subbie register · prequalification · certificates · agreements · work packages · payment applications (+lines, 2-step approve) · retention ledger · ratings · lien waivers.
- **Key logic:** payment applications draw on work packages/agreements, net retention into `_retention_ledger`, gate on current certificates + lien waivers before release; two-step (foreman→finance) approval; ratings accrue.
- **Features:** [F1] subbie register (+block list) · [F2] prequalification · [F3] certificates (insurance/licence) · [F4] agreements (+retention/lien-waiver policy) · [F5] work packages · [F6] payment applications + 2-step approval · [F7] retention ledger · [F8] lien waivers · [F9] performance ratings.
- **Removal impact:** no subcontractor administration; `contracts` still handles the head contract.
- **Edit notes:**

---

*Batch 2 of 7 complete at ⟦spec⟧ depth. Next: Batch 3 (Purchasing & Supply). Grounded @ `a96d525`.*
