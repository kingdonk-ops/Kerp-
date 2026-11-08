# OpenBusinessERP — Per-Module Docs · Batch 2: Sales & Commercial

> Batch 2 of 7. Four Pillars per module. Grounded in `backend/app/modules/<name>/`. Tables `oe_*`; routes at `/api/v1/<prefix>`; deps = manifest `depends`.

**Modules (10):** `sales`, `crm`, `contacts`, `items`, `inventory`, `site_inventory`, `catalog`, `supplier_catalogs`, `contracts`, `subcontractors`.

---

## 1. `sales` — Order-to-cash
- **What:** the sell side — quote → sales order → invoice → payment. `category=core`, `depends=[gl]`.
- **Does:** 68 routes over `oe_sales_document`, `oe_sales_line`, `oe_sales_payment`, `oe_sales_sequence`, `oe_sales_account_map`, `oe_sales_send_log`, `oe_sales_payment_link` — a single document that transitions quote→order→invoice, line items, payments, numbering sequences, GL account mapping, send log (emailed PDFs), and payment links (SEPA/QR).
- **How:** one polymorphic `oe_sales_document` carries the state machine; `oe_sales_sequence` issues human numbers; posting an invoice writes the journal into `gl` via `oe_sales_account_map` (line→account), split by tax rate; `oe_sales_send_log` records deliveries; `oe_sales_payment_link` mints pay-by-link references.
- **Interactions:** posts to `gl`; feeds `receivables` (open items) and `banking` (reconciliation targets); consumes `items` (products) and `contacts` (customers). **Failure:** the commercial spine — effectively always-on `core`.

## 2. `crm` — Sales pipeline
- **What:** leads, opportunities, accounts and forecasting. `category=business`, `depends=[users]`.
- **Does:** 94 routes over `oe_crm_account`, `oe_crm_lead`, `oe_crm_opportunity`(+`_stage_history`), `oe_crm_pipeline_stage`(+`_config`), `oe_crm_win_loss_reason`, `oe_crm_activity`, `oe_crm_forecast` — configurable pipeline stages, lead→opportunity conversion, stage history, win/loss reasons, activities, and forecast.
- **How:** opportunities move through configurable stages with a recorded `_stage_history`; `_forecast` aggregates weighted pipeline; win/loss reasons close the loop.
- **Interactions:** converts to `sales` documents; `contacts`/`accounts`. **Failure:** disabled → no pipeline; `sales` still works for direct quoting.

## 3. `contacts` — Party master
- **What:** the shared people/company directory (customers, suppliers, others). `category=core`, `depends=[users]`.
- **Does:** 30 routes over `oe_contacts_contact` — CRUD, search, and party classification. `Contact.tenant_id` backfills to `created_by` (the single-tenant convention — see architecture doc §3).
- **How:** one contact table serves every party role; other modules reference contacts by id.
- **Interactions:** consumed by `sales`, `crm`, `finance`, `subcontractors`, purchasing (Batch 3). **Failure:** core — parties are foundational.

## 4. `items` — Product/service master, price lists, discounts
- **What:** the catalogue of what you sell/buy and how it's priced. `category=core`, no deps.
- **Does:** 54 routes over `oe_items_category`, `oe_items_item`, `oe_items_price_list`(+`_item`), `oe_items_discount_rule` — items & categories, multiple price lists, per-item prices, and discount rules.
- **How:** price resolution walks the applicable price list then discount rules; items can be goods or services.
- **Interactions:** priced into `sales` lines, stocked by `inventory`, bought via purchasing. **Failure:** core — sales/inventory depend on it.

## 5. `inventory` — Warehouses, movements, moving-average cost, stocktakes
- **What:** stock control with valuation. `category=core`, `depends=[items]`.
- **Does:** 36 routes over `oe_inventory_warehouse`, `oe_inventory_movement`, `oe_inventory_item_cost`, `oe_inventory_stocktake`(+`_line`), `oe_inventory_settings` — warehouses, in/out movements, moving-average cost per item, and stocktakes.
- **How:** each movement adjusts on-hand qty and recomputes **moving-average cost** (`oe_inventory_item_cost`); stocktakes reconcile counted vs system and post the variance; a delivery out of stock is the movement that feeds a sales dispatch.
- **Interactions:** `items` (what's stocked); `sales` (dispatch) and purchasing (receipt). **Failure:** disabled → no stock tracking/valuation.

## 6. `site_inventory` — On-site material metering
- **What:** track materials held and consumed **on a project site** (distinct from warehouse stock). `category=business`, `depends=[projects, boq, procurement, progress, costs]`.
- **Does:** 28 routes over `oe_site_inventory_location`, `_item`, `_movement` — site locations, on-site item balances, and deliveries/consumption movements.
- **How:** movements meter material against `boq`/`progress`/`costs` so consumed quantity ties to measured progress and cost.
- **Interactions:** `procurement` (deliveries in), `boq`/`progress`/`costs` (consumption). **Failure:** disabled → no on-site metering; warehouse `inventory` unaffected.

## 7. `catalog` — Resource catalog (data layer)
- **What:** a resource catalogue table — **data layer only** (the loaders/UI were removed; the docstring says so). `category=core`, `depends=[costs]`.
- **Does:** `oe_catalog_resource` only; no router surface of its own.
- **How:** holds resource reference data other modules (costing/estimating) read.
- **Interactions:** read by `costs`/estimating. **Failure:** inert if unused.

## 8. `supplier_catalogs` — Vendor management & procure-to-pay (extension)
- **What:** a full vendor/P2P suite — the richest commercial add-on. `category=extension`, `depends=[projects, users, procurement]`.
- **Does:** 96 routes over 21 tables: vendors (`_vendor`, `_kyc_document`, `_scorecard`, `_tolerance_profile`), catalogues (`_item_category`, `_catalog_item`, `_price_list`, `_catalog_entry`, `_commodity_code`), and a complete P2P chain — **PR → PO → GR → Invoice** (`_pr`/`_pr_line`, `_po`/`_po_line`, `_gr`/`_gr_line`, `_invoice`/`_invoice_line`) with **3-way match** (`_match_record`) and vendor stock (`_warehouse`, `_stock_balance`, `_stock_movement`).
- **How:** requisition→order→goods-receipt→invoice with tolerance-profiled 3-way matching; KYC documents and scorecards gate/rank vendors.
- **Interactions:** `procurement` (Batch 3), `inventory`, `finance`. Extension add-on. **Failure:** disabled → vendor management + 3-way match unavailable; simpler `procurement` still works.

## 9. `contracts` — Contract Types Engine (largest commercial module)
- **What:** head/sub contracts across contract types (lump-sum, cost-plus, target/gainshare, etc.) with the full commercial machinery. `category=business`, `depends=[users, projects]`.
- **Does:** **260 routes** over 21 tables incl. `oe_contracts_contract`(+`_line`), `_type_configuration`, `_retention_schedule`/`_retention_release`, `_fee_structure`, `_gainshare_configuration`, `_ld_clause`, `_progress_claim`(+`_line`), `_final_account`, `_party`, `_security`, `_eot_claim`, `_milestone`, `_template`(+`_clause`), `_sov_adjustment`, `_stored_material`(+`_movement`) — contract setup by type, schedule-of-values, progress claims, retention, liquidated-damages clauses, extensions-of-time, securities/bonds, stored materials, and final account.
- **How:** a **type configuration** parametrises behaviour (fees, gainshare, retention, LDs) per contract type; progress claims draw against the SOV with retention schedules; EOT claims and LD clauses manage time/penalty; templates assemble clause sets.
- **Interactions:** `projects`; feeds `payment_clock`, `subcontractors`, `cost_recovery`, finance. **Failure:** disabled → no formal contract administration.

## 10. `subcontractors` — Subcontractor management
- **What:** manage subcontractors end-to-end: prequal, agreements, work packages, payments, retention, compliance. `category=business`, `depends=[users, projects, contacts, contracts]`.
- **Does:** 126 routes over `oe_subcontractors_subcontractor`(+`_contact`), `_prequalification`, `_certificate`, `_agreement`, `_work_package`, `_payment_application`(+`_line`), `_retention_ledger`, `_rating`, `_lien_waiver` — subbie register, prequalification, certificates (insurance/licences), agreements, work packages, payment applications with retention, lien waivers and performance ratings.
- **How:** payment applications draw on work packages/agreements, net retention into `_retention_ledger`, and require current certificates + lien waivers before release; ratings accrue performance history.
- **Interactions:** `contracts` (flow-down), `contacts` (party), `projects`, payment/finance. **Failure:** disabled → no subcontractor administration; `contracts` still handles the head contract.

---

### Batch-2 interaction map
```
crm ──convert──▶ sales ──post──▶ gl ; sales ──open items──▶ receivables/banking
items ──price──▶ sales lines ; items ──stocked──▶ inventory ──site──▶ site_inventory
contacts ──parties──▶ sales/crm/finance/subcontractors/purchasing
contracts ──flow-down──▶ subcontractors ; contracts ──▶ payment_clock / cost_recovery
supplier_catalogs (PR→PO→GR→Invoice, 3-way) ──▶ procurement/inventory/finance
```

*Batch 2 of 7. Next: **Purchasing & Supply** (purchasing, procurement, rfq_bidding, bid_management, tendering). Grounded @ `a96d525`.*
