> ## ⚠ Field-Ops pivot status (authoritative: ../FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md)
> **REMOVED:** ~~purchasing~~ ~~rfq_bidding~~ ~~bid_management~~ ~~tendering~~ · **MODIFIED:** procurement (basic).

# OpenBusinessERP — Module Specifications · Batch 3: Purchasing & Supply

> Editable specs (format in `MODULE-SPEC-00`). Entities = real `models.py` columns @ `a96d525`.

**Modules (5):** `purchasing`, `procurement`, `rfq_bidding`, `bid_management`, `tendering`. (`supplier_catalogs` specced in Batch 2.)

---

### purchasing — Business purchase-to-pay
- **Identity:** category=core · prefix=`/purchasing` · routes≈70 · **Depends on:** [gl] · **Dependents:** banking, inventory
- **Purpose:** PO → goods receipt → supplier bill → payment, posting to GL (the mirror of `sales`).
- **Entities:** `_order`(number, status, supplier_id/name, currency, fx_rate, doc_date, due_date, net/tax/gross_total) · `_order_line`(order_id, item_id, sku, qty, unit_price, discount_pct, tax_rate, net/tax/gross, qty_received, qty_billed) · `_receipt`(number, status, order_id, value_total, gl_entry_id) · `_receipt_line`(order_line_id, item_id, qty, unit_cost, value, stock_tracked) · `_bill`(kind, number, status, supplier_id/ref, order_id, receipt_id, net/tax/gross_total, amount_paid, match_status, gl_entry_id) · `_bill_line`(order_line_id, qty, unit_price, tax_rate, match_status) · `_payment`(bill_id, pay_date, amount, method, fx_rate, gl_entry_id) · `_stock`(item_id, qty, value) · `_sequence`(kind, last_number) · `_account_map`(role, account_code)
- **Endpoints:** orders · receipts · bills (+match) · payments · stock · sequences · account-map.
- **Key logic:** PO→GR draws down `qty_received`; bill matched to PO/GR (`match_status`), posts to GL via `account_map`; payments net `amount_paid`.
- **Features:** [F1] purchase orders · [F2] goods receipts · [F3] supplier bills + PO/GR match · [F4] payments · [F5] GL posting + account map · [F6] numbering · [F7] stock view.
- **Removal impact:** core buy-side; banking/inventory lose receipts/bills.
- **Edit notes:**

### procurement — Construction procurement (project-oriented)
- **Identity:** category=core · prefix=`/procurement` · routes≈42 · **Depends on:** [users, projects, contacts] · **Dependents:** site_inventory, supplier_catalogs, rfq_bidding
- **Entities:** `_requisition`(project_id, req_number, requester/approver_id, status, required_date, lead_time_days, estimated_delivery_date, po_id) · `_req_item`(quantity_requested/ordered/received/consumed, unit_cost, cost_line_id) · `_po`(project_id, vendor_contact_id, po_number, po_type, issue/delivery_date, amount_subtotal/tax/total, retention_percent, retain_on_receipt, retainage_released_amount, status, payment_terms) · `_po_item`(quantity, unit, unit_rate, wbs_id, cost_category, cost_line_id) · `_po_retainage_release`(release_date/amount/reason) · `_goods_receipt`(po_id, receipt_date, delivery_note_number, status) · `_gr_item`(po_item_id, quantity_ordered/received/rejected, rejection_reason)
- **Endpoints:** requisitions (+approve) · POs (+retainage release) · goods receipts.
- **Key logic:** requisition→PO against a project; GR draws down PO items; retainage held + released; cost tie via `cost_line_id`/`wbs_id`.
- **Features:** [F1] requisitions + approval · [F2] project POs · [F3] goods receipts (+rejections) · [F4] retainage hold/release · [F5] BOQ/cost-line tie.
- **Removal impact:** no project procurement; `purchasing` covers business P2P.
- **Edit notes:**

### rfq_bidding — RFQ & award (enterprise)
- **Identity:** category=enterprise · prefix=`/rfq` · routes≈44 · **Depends on:** [users, projects, contacts, procurement]
- **Entities:** `_rfq`(project_id, rfq_number, title, scope_of_work, submission_deadline, currency, status, issued_to_contacts, cost_line_ids, evaluation_method, technical_weight, require_full_scope) · `_line`(line_no, code, unit, quantity, is_optional, cost_line_id) · `_bid`(bidder_contact_id, bid_amount, submitted_at, validity_days, technical/commercial_score, is_awarded, status, is_late/admitted/disqualified/withdrawn fields, exchange_rate+source) · `_bid_line`(rfq_line_id, unit_rate, amount, unit_conversion_factor, is_excluded) · `_bid_adjustment`(kind, amount, included_in_bid, source) · `_award`(bid_id, method, reason, recommended_bid_id, is_override, awarded_amount, basis)
- **Key logic:** bids compared vs RFQ lines; `_bid_adjustment` + `unit_conversion_factor` normalise for like-for-like; technical/commercial weighted scoring; `_award` (with override vs recommendation); late/admission/disqualification workflow.
- **Features:** [F1] RFQs + lines · [F2] bid submission (validity, late handling) · [F3] bid lines + unit conversion · [F4] adjustments (freight/terms) · [F5] technical+commercial scoring · [F6] award (+override) · [F7] FX normalisation.
- **Removal impact:** no competitive RFQ; direct POs remain.
- **Edit notes:**

### bid_management — Trade-package bid management
- **Identity:** category=business · prefix=`/bid-management` · routes≈126 · **Depends on:** [users, projects]
- **Entities:** `_package`(tender_id, code, scope, instructions_to_bidders, submission_deadline, decision_due_by, total_budget_estimate, confidentiality_level, published/closed/awarded_at) · `_line_item`(code, unit, quantity, alternative_allowed, parent_line_id, boq_position_id, is_mandatory) · `_invitation`(bidder_ref_id, invitee_email/company, sent/opened/received/declined, token_hash) · `_bidder`(company_name, contact, subcontractor_id, contact_id, status, disqualification_reason) · `_submission`(total_amount, completeness_score, exclusions, qualifications, is_valid, open_after_deadline, envelope_payload) · `_submission_line`(unit_price, quantity_priced, alternative_offered, inclusion_status, prevailing_wage_applicable) · `_qa`(question, answer, is_public, visible_to_bidder_ids) · `_comparison`(normalized_low/high, technical_scoring_rule, commercial/technical_weight_pct, recommended_bidder_id) · `_leveling`(raw/normalized_total, commercial/technical/total_score, rank, manual_adjustment) · `_award`(awarded_bidder_id, awarded_amount, decision_signed_by/at, contract_template_ref) · `_rejection`(rejection_code/reason, notified_at)
- **Key logic:** sealed-ish submissions (envelope, open_after_deadline); **leveling** normalises scope before compare; Q&A (public/private); award/rejection + notify.
- **Features:** [F1] bid packages + line items (BOQ-linked) · [F2] invitations (tokenised) · [F3] bidders · [F4] submissions (+alternatives) · [F5] Q&A · [F6] comparison + weighting · [F7] leveling + ranking · [F8] award (signed decision) · [F9] rejections + notification.
- **Removal impact:** no structured trade-bid management.
- **Edit notes:**

### tendering — BOQ-based tender packages
- **Identity:** category=core · prefix=`/tendering` · routes≈76 · **Depends on:** [projects, boq]
- **Entities:** `_package`(project_id, boq_id, name, status, deadline) · `_bid`(company_name, contact_email, total_amount, currency, submitted_at, status, line_items) · `_bid_invitation`(recipient_id, email, token_hash, expires_at, opened/draft/submitted, bid_id, draft)
- **Key logic:** packages derive line items from the `boq`; tokenised invitations with draft save; bids price those lines for comparison.
- **Features:** [F1] BOQ-derived tender packages · [F2] tokenised invitations + drafts · [F3] bid submission · [F4] comparison.
- **Removal impact:** no BOQ-driven tendering.
- **Edit notes:**

---

*Batch 3 of 7 complete. Next: Batch 4 (Estimating & Cost). Grounded @ `a96d525`.*
