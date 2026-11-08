# OpenBusinessERP — Per-Module Docs · Batch 3: Purchasing & Supply

> Batch 3 of 7. Four Pillars per module. Grounded in `backend/app/modules/<name>/`.

**Modules (5):** `purchasing`, `procurement`, `rfq_bidding`, `bid_management`, `tendering`.

> Note: the platform has **two purchasing lineages** — a general-business **procure-to-pay** (`purchasing`, GL-posting, PO→GR→bill→payment) and a **construction procurement** chain (`procurement`, project/requisition-oriented) plus three **sourcing/competitive-bidding** modules (`rfq_bidding`, `bid_management`, `tendering`). `supplier_catalogs` (Batch 2) is the vendor-management/P2P superset.

---

## 1. `purchasing` — Business purchase-to-pay
- **What:** general-business buying: PO → goods receipt → supplier bill → payment, posting to GL. `category=core`, `depends=[gl]`.
- **Does:** 70 routes over `oe_purchasing_order`(+`_line`), `_receipt`(+`_line`), `_bill`(+`_line`), `_payment`, `_stock`, `_sequence`, `_account_map` — the full P2P document chain, numbering, GL mapping, and a stock view.
- **How:** mirrors `sales` in reverse — a PO is received (GR), matched to a supplier bill, then paid; posting writes journals into `gl` via `_account_map`; `_sequence` issues numbers.
- **Interactions:** posts to `gl`; feeds `banking` (bills to reconcile) and `inventory`/`_stock`. **Failure:** core — the buy-side spine.

## 2. `procurement` — Construction procurement (project-oriented)
- **What:** project requisitions, purchase orders and goods receipts with retainage. `category=core`, `depends=[users, projects, contacts]`.
- **Does:** 42 routes over `oe_procurement_requisition`(+`_req_item`), `_po`(+`_po_item`, `_po_retainage_release`), `_goods_receipt`(+`_gr_item`) — requisition→PO→GR with retainage release.
- **How:** a requisition becomes a PO against a project; goods receipts draw down PO items; retainage is held and released via `_po_retainage_release`.
- **Interactions:** `projects`/`contacts`; upstream of `site_inventory`, `supplier_catalogs`, `rfq_bidding`. **Failure:** disabled → no project procurement; `purchasing` still covers business P2P.

## 3. `rfq_bidding` — Request-for-quote & award (enterprise)
- **What:** solicit supplier quotes for procurement lines and award. `category=enterprise`, `depends=[users, projects, contacts, procurement]`.
- **Does:** 44 routes over `oe_rfq_rfq`(+`_line`), `_bid`(+`_bid_line`, `_bid_adjustment`), `_award` — issue an RFQ, collect bids, normalise with adjustments, award.
- **How:** bid lines are compared against RFQ lines; `_bid_adjustment` normalises (freight, terms) for apples-to-apples; `_award` records the winner and can feed a PO in `procurement`.
- **Interactions:** builds on `procurement`; `contacts` (bidders). Enterprise add-on. **Failure:** disabled → no competitive RFQ; direct POs still possible.

## 4. `bid_management` — Trade-package bid management (sell-side/estimating)
- **What:** manage bid packages, invitations, submissions, Q&A, leveling and award — the estimator's sourcing of trade bids. `category=business`, `depends=[users, projects]`.
- **Does:** 126 routes over `oe_bid_management_package`, `_line_item`, `_invitation`, `_bidder`, `_submission`(+`_line`), `_qa`, `_comparison`, `_leveling`, `_award`, `_rejection` — packages and line items, invite bidders, receive submissions, run Q&A, compare and **level** bids, award or reject.
- **How:** submissions are compared line-by-line; **leveling** normalises scope differences before comparison; award/rejection close the package.
- **Interactions:** `projects`; relates to estimating (Batch 4) and `tendering`. **Failure:** disabled → no structured trade-bid management.

## 5. `tendering` — Tender package management & comparison
- **What:** BOQ-based tender packages, invitations and bid comparison. `category=core`, `depends=[projects, boq]`.
- **Does:** 76 routes over `oe_tendering_package`, `_bid`, `_bid_invitation` — assemble a tender package from a BOQ, invite bidders, and compare returned bids.
- **How:** packages derive line items from the `boq`; returned bids price those lines for side-by-side comparison.
- **Interactions:** `boq` (Batch 4) as the source of scope; `projects`. **Failure:** disabled → no BOQ-driven tendering.

---

### Batch-3 note
`purchasing` (business P2P, posts to `gl`) and `procurement` (project requisitions/retainage) are parallel buy-side chains; `supplier_catalogs` is the vendor-management superset with 3-way match. Sourcing is split by audience: `rfq_bidding` (supplier quotes on procurement lines), `bid_management` (trade-package bids for estimating), `tendering` (BOQ-based tender comparison). Each is independently gated; disabling one leaves the others and the GL untouched.

*Batch 3 of 7. Next: **Estimating & Cost** (boq, takeoff, costs, costmodel, cost_plan, estimate_*, assemblies, eac/full_evm/cvr). Grounded @ `a96d525`.*
