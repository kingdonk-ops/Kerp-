# Delivery Planner — review

**TL;DR**
1. **Keep the phase sequence (0→1→2→3→4→5→6→6b→7) — it matches the real anchor-dependency graph** — but freeze the *backbone contract* (hierarchy + Items + dynamic schema + external keys + `run_checks` signature) as an explicit gate at the end of Phase 1, because every Phase 3–7 task anchors to it.
2. **Adopt TD-002 (the AIP one-engine)** and sequence it *inside* Phase 1 as a 5-step chain (registry → `forms` → `item_types` → inspection record-integrity + `eligibility` → `inspection_requests`). It replaces three half-built designers (T-0102/T-0109/T-0110) with one, and it is cheaper to do now than to retrofit after `wps`, `qms` and `workpacks` have each grown their own.
3. **MVP cut line = Phase 0 + 1 + 2 + a "documents-lite" slice (VFS scaffold + presigned uploads + the batch inspection/ITP/NCR PDF export that T-0112 already builds).** That is the owner's "inspection management system" and nothing less is demoable; defer all of Phase 3–7 except pull *Gate Delivery + OS&D* forward only if the pilot site receives material.
4. **Three concrete ordering bugs in the current tracker:** `resource_summary` (E-03-07) depends on Phase-4 `tasks`/`schedule_advanced` and cannot live in Phase 3; `site_inventory` (E-03-03) declares only `T-0101` but its spec depends on `procurement` + `progress`; and `procurement` is buried inside a 3-module epic (E-03-05) although `site_logistics` OS&D and `site_inventory` movements both need it first.
5. **Full build ≈ 265–290 S/M tasks.** Worked Phase 3 breakdown (31 tasks) is below; templates for Phases 4–7 follow.

---

## 1. Findings

### 1.1 Build order & dependency rationale — confirm with two fixes

The sequence in DEVELOPMENT-PLAN §7 is correct and I would not reorder the *phases*. The rationale holds on the anchor model (hard rule 6): work anchors to **RSW**, inspections to **Item**, both of which are created in Phase 1 `asset_management`; `run_checks` (hard rule 5) is the Phase 0 shell (T-0016). Everything downstream references `rsw_node_id` / `asset_item_id` and a rule-set id, so the backbone must exist first. The critical path is therefore literally **T-0016 (run_checks shell) → T-0101 (hierarchy) → T-0103 (Items) → T-0124 (rule sets) → [everything]**, plus the offline sync spine **T-0125 → T-0126** which every edge screen in Phases 3/4/6b reuses.

Two sequencing problems to fix at the phase boundary, not inside a phase:

- **Backbone-freeze gate is implicit.** §9 says "freeze the backbone contract before 3–6" but no task asserts it. Add a gate task at the end of Phase 1 (`T-0131`, below) that pins the public surface — `oe_asset_node` / `oe_asset_item` columns, the dynamic-schema version envelope, the external-key triple (`external_source`/`external_id`/`synced_at`), and the `run_checks(context, rule_set) -> findings[]` contract — behind a contract test. Phase 3 breakdown should not start (per the rolling-wave rule) until this is green.
- **Offline sync is correctly in Phase 1 (TD-006) but its *generality* is under-specified.** T-0125 is a generic ledger; the Gate Delivery (Phase 3), Daily Workface Log and Look-Ahead Board (Phase 4) and `workpacks` tablet sign-off (Phase 6b) all reuse it. Keep T-0125's op-id→result ledger module-agnostic and resist baking inspection-only assumptions into it — otherwise Phase 3/4/6b each re-implement sync (TD-008's edge screens have no UI spec, which compounds this).

### 1.2 Where TD-002 lands and how to sequence it

TD-002 is the single biggest fork and it is a **Phase 1** decision, not a cross-cutting one. If adopted (I recommend adopt), it reshapes the existing `blocked_by: [TD-002]` tasks (T-0102, T-0109, T-0110, E-01-01) into one engine and *adds* two surfaces (`eligibility`, `inspection_requests`). The clean insertion, following PROPOSAL §4 and MODULE-SPEC-11 §2–3, is:

| New/▲revised task | Replaces / relates | Size | Why here |
|---|---|---|---|
| **T-0110a** shared field-type registry + sandboxed TS/Python evaluator (same fixtures) | foundation of T-0110 | M | "nothing else lands cleanly first" (PROPOSAL §4.1) |
| **T-0110b** `forms` engine: kinds (inspection/itp/rfi/permit/test_report), `visible_if`/`required_if`, calculated fields (allow-list), revision immutability + `schema_hash`, instrument-cal field, test-fill/lint | T-0110 | M | one designer for every template |
| **T-0102▲ / T-0109▲** `item_types`: asset schema on the shared registry, `auto_create_itp/inspection`, schema versioning + migration-preview, type inheritance, template→attribute write-back, starter packs | T-0102 + T-0109 collapse into one engine | M×2 | removes the second designer |
| **T-0111a** inspection record-integrity: append-only hash-chained multi-party sign-off, idempotent responses (`client_id`+`sync_version`), inline eligibility banner, competency-aware assignment, programmes/recurrence | extends T-0111 | M | the record the MDR relies on |
| **T-0111b** `eligibility` gate service: one "is this person/instrument/material valid now?" answer consumed by `run_checks`, surfaced inline | new | M | unifies `credentials`+`wps`-qual+`compliance_docs`+`qms`-cal |
| **E-01-01 → T-0132…T-013n** `inspection_requests` (raise→notice→confirm→witness/no-show/waive/fail, hash-chained event ledger, reason codes + cause-party attribution, MDR PDF, auto delay-events, client-vs-contractor dashboard) | E-01-01 breakdown | ~6 S/M | SPEC-11 §1; depends on T-0111 |

Net Phase-1 effect: the 30 tasks become **~40** (the one-engine chain nets roughly even on the three designers but adds `eligibility` ~1, inspection record-integrity ~2, and `inspection_requests` ~6). `eligibility` then becomes a dependency of Phase 2 `weld.accept`/`welder.assign` (R-WPS-1…4) — a *good* consolidation: R-WPS rules become `eligibility` queries rather than bespoke `wps` code. **If TD-002 is rejected**, keep the thin `forms` + separate `oe_asset_field_def`, drop `eligibility`/`inspection_requests`, and Phase 1 stays at 30 — but every "is it valid?" check is then re-implemented per module, which I regard as the worse bet.

### 1.3 MVP cut line (TD-009)

The owner calls KERP "the inspection management system"; inspections/ITP/NCR/weld quality is the backbone. The smallest *usable, demoable, sellable* pilot is therefore:

**MVP = Phase 0 + Phase 1 (TD-002 adopted) + Phase 2 + documents-lite + P6 round-trip.**

- **In:** tenancy/RLS/RBAC; projects; asset hierarchy from P6 and back (T-0104/T-0106); Items; the one-engine forms/item_types; inspections/ITP/holds/NCR/punch with enforced gates; welding quality (WPS/qual/weld/NDE/R-WPS); offline capture of inspections; the batch inspection/ITP/NCR **PDF/Excel export** (T-0112 already builds this) filed into a **VFS scaffold + presigned uploads** (a ~4-task slice pulled forward from Phase 5 `documents`: VFS folder tree + `/Site/Asset/RSW` scaffold + upload register + rename-safe links — *no* OCR, *no* AI, *no* CDE states).
- **Out / deferred:** all of Phase 3 logistics (see exception), Phase 4 execution, Phase 5 collaboration + AI assistant, Phase 6 claims/controls, Phase 6b AWP, Phase 7 portal.
- **One exception:** if the first pilot site physically *receives* material, pull forward only **Gate Delivery + OS&D** (T-0306…T-0310 procurement PO-line mirror + T-0318…T-0321 site_logistics gate receipt). Otherwise leave all of Phase 3 out.

That MVP is **~85–95 tasks** (Phase 0: 27, Phase 1: ~40, Phase 2: 8, documents-lite: ~4, plus the P6 tasks already in Phase 1). It delivers the backbone the whole product depends on and nothing speculative. **Do not** ship an MVP that stops "end of Phase 2" *without* the documents-lite slice — inspection PDFs and MDRs would have nowhere to live, which guts the value proposition.

Second pilot increment: Phase 3 logistics + Phase 4 execution core (schedule P6-sync + physical progress + diaries). The AI assistant (Phase 5) and claims/EVM (Phase 6) are a deliberate third wave — they consume live data that only exists once 3–4 are running.

### 1.4 Parallelisation plan

The pipeline (WORKFLOW.md) runs **implementation in parallel, merges serially** under one coordinator keeping `dev` green. Two rules govern concurrency:

- **The backbone freeze is the hard gate.** Nothing in Phase 3–7 may be *implemented* (not just merged) until `T-0131` backbone-freeze is green on `dev`. Before that, parallelism is limited to independent Phase-0/1 tracks (frontend shell T-0024 ∥ backbone T-0101; sync spine T-0125 ∥ P6 import T-0104).
- **After the freeze, parallelise by pillar, serialise within a dependency chain.** Within Phase 3 the independent tracks are: **(A)** contacts→procurement→site_inventory→cutting (one chain, mostly serial), **(B)** site_logistics gates/deliveries (parallel to A until it needs procurement at the OS&D step), **(C)** equipment (fully independent — only needs `asset_management`), **(D)** supplier_catalogs/subcontractors (needs contacts only). So up to **3–4 implementer agents** can run at once in Phase 3; the serial merge queue orders them by `depends_on`.

To keep `dev` green with parallel work: (i) every task is one branch cut from `dev` (CLAUDE.md §2); (ii) cross-module access is via `service.py` only (hard rule 1), so two agents touching `site_logistics` and `procurement` never collide on tables — the collision point is the *contract* of `procurement.service`, which must land (and freeze) before `site_logistics` OS&D starts; (iii) tracker uses one-file-per-task (WORKFLOW "parallel agents never conflict"), so status churn never conflicts; boards are regenerated, never hand-edited.

### 1.5 Definition of done & gates per task

Every task's DoD maps to the CLAUDE.md §4 gate stack, enforced by `ci-gate` + `review-gate`:

| Gate | Command / check | Applies to |
|---|---|---|
| Tracker | `tracker.py validate && render --check && pytest scripts/tests` | every task touching `tracker/` |
| Backend | `ruff check . && mypy app && pytest` (full suite) | every backend task |
| Frontend | `npm run typecheck && test && build` (+ `gen:api` committed if API changed) | every FE task |
| E2E | `npx playwright test` affected suites | user-facing tasks |
| Contract | BaseRecordMixin metadata, RLS cross-tenant→404, module-manifest validity | every table/module |
| **`run_checks` rule-set test** | one test per verifiable transition the task wires | any task adding/using a rule set |
| Review | independent `kerp-reviewer` posts `KERP-REVIEW: APPROVED patch-id=<diff>` | every PR |

Task-authoring rules for the planner / ecc `epic-decompose`: **S < 300 LOC / one PR; M < 800 LOC / one PR; bigger → split.** A table + its RLS + CRUD + tests is typically one M. A rule-set wiring (rule data + `run_checks` call + findings surfaced + test) is typically one S/M. Anything that would touch two modules' tables is wrong by hard rule 1 and must be split so the second module is reached through its `service.py`. No skipped/xfail without a `TD-xxx` in the skip reason.

---

## 2. Worked breakdown — Phase 3 (Logistics), ready to adopt

Dependency-correct order (contacts → procurement → site_inventory → cutting; site_logistics ∥; equipment ∥). IDs `T-0301…T-0331`. `resource_summary` is **moved out of Phase 3** (see §3). Each row: *id · title · module · size · depends-on · acceptance (testable) · spec refs · rule-set(s)*. Acceptance always also includes the standard "all gates green + README/docs" bullet (omitted here for density).

### Track A — contacts (E-03-01)

**T-0301** · `contacts: oe_contacts_contact party master (one table, all roles) + CRUD + RLS` · contacts · **M** · deps `T-0010`
- AC: one table serves client/contractor/staff/supplier (`contact_type`); `is_platform_user`/`user_id` link to a `users` row via `users.service` (no FK across module); RLS cross-tenant→404 test; BaseRecordMixin contract test.
- Spec: MODULE-SPECS §3 `contacts`; SPEC-08. Rule-sets: none.

**T-0302** · `contacts: company/person grouping + notes/interaction log (_note)` · contacts · **S** · deps `T-0301`
- AC: company↔person grouping query; `_note(author_id, body, at)` append; grouping invariant test.
- Spec: `contacts` F2/F3. Rule-sets: none.

**T-0303** · `contacts: tags/module_tags + custom_properties + certifications/insurance/prequal fields` · contacts · **S** · deps `T-0301`
- AC: `qualified_until`/`prequalification_status` queryable; tag filter test; custom_properties JSONB GIN.
- Spec: `contacts` F4/F6/F7. Rule-sets: none.

**T-0304** · `contacts: versioned files (_file)` · contacts · **S** · deps `T-0301`, `T-0015`
- AC: `_file(contact_id, document_id, version)` over presigned uploads (T-0015); in MVP references the upload store, **not** the Phase-5 `documents` VFS — raise a `later` TODO to re-point at the VFS when Phase 5 lands.
- Spec: `contacts` F5. Rule-sets: none.

**T-0305** · `frontend: contacts directory (list, filters, company/person, detail)` · frontend · **M** · deps `T-0301`, `T-0024`
- AC: directory with contact_type/tag filters; detail with notes/files; all strings via `t()`; generated API client only.
- Spec: `contacts`; architecture-suite Part 4. Rule-sets: none.

### Track A — procurement (pulled out of E-03-05, built before inventory/logistics)

**T-0306** · `procurement: requisition + req_item (approval, required_date, RSW tie)` · procurement · **M** · deps `T-0301`, `T-0101`
- AC: requisition→approval status flow; `_req_item.rsw_node_id` resolves to a real `oe_asset_node` leaf; unit_cost stored as read-only mirror (hard rule 7) test.
- Spec: MODULE-SPECS §3 `procurement` F1. Rule-sets: none.

**T-0307** · `procurement: PO + po_item (internal or ERP-mirrored, physical retention)` · procurement · **M** · deps `T-0306`
- AC: `_po.external_source`/`external_ref` mirror path has no FK into ERP (hard rule 4) test; `retention_percent` is physical only; internal-vs-mirror branch test.
- Spec: `procurement` F2/F4. Rule-sets: none.

**T-0308** · `procurement: goods_receipt + gr_item (draw-down, rejections/OS&D) + goods.receive rule set` · procurement · **M** · deps `T-0307`, `T-0016`
- AC: GR draws down PO item qty; over/short/damaged (OS&D) deltas computed; `goods.receive` rule set runs via `run_checks` and blocks on error findings (e.g. receipt exceeds ordered without waiver); rule-set test.
- Spec: `procurement` F3; §5. **Rule-sets: `goods.receive`.**

**T-0309** · `procurement: ERP PO-line mirror ingest (POST /integrations/purchasing/po-lines, idempotent)` · procurement · **S** · deps `T-0307`
- AC: idempotent on `external_id` (re-POST same line is a no-op) test; staging mirror of PO lines.
- Spec: DEV-PLAN §2 ingestion surface. Rule-sets: none.

**T-0310** · `procurement: OS&D export for three-way match (GR confirmations + deltas + docket refs)` · procurement · **S** · deps `T-0308`
- AC: export file carries GR confirmation + OS&D delta + docket image ref for AP three-way match; non-destructive (export only) test.
- Spec: DEV-PLAN §2 outbound. Rule-sets: none.

### Track A — site_inventory (E-03-03)

**T-0311** · `site_inventory: locations (geo) + items (RSW/Item + procurement link, reorder_point)` · site_inventory · **M** · deps `T-0101`, `T-0307`
- AC: `_item` links `rsw_node_id`/`asset_item_id` and `procurement_req_item_id` (via `procurement.service`, no cross-table read); `standard_unit_cost` is a mirror; location geo (PostGIS) test.
- Spec: MODULE-SPECS §3 `site_inventory` F1/F2. Rule-sets: none.

**T-0312** · `site_inventory: append-only movements ledger (_movement receipt/consume/transfer)` · site_inventory · **M** · deps `T-0311`, `T-0308`
- AC: movements are **append-only** (no update/delete) per hard rule 11 test; receipt movement ties to `goods_receipt_id`; running on-hand derived from movements, never stored mutable.
- Spec: `site_inventory` F3; §1 principle 11. Rule-sets: none.

**T-0313** · `site_inventory: reorder-point alerts + consumption→progress tie (stub)` · site_inventory · **S** · deps `T-0312`
- AC: reorder alert fires below `reorder_point` (ARQ or notification); consumption→progress tie (F5) is **stubbed behind an interface** — `progress` is Phase 4; raise a `deferred` TODO to wire the real tie in Phase 4.
- Spec: `site_inventory` F4/F5. Rule-sets: none.

### Track A — cutting (E-03-04)

**T-0314** · `cutting: plans + cut_list (oe_cutting_plan, _cut_list_item, bar_1d/sheet_2d, scope/custom)` · cutting · **M** · deps `T-0311`
- AC: plan state `draft→optimised→confirmed/cancelled` machine test; cut list from scope or custom; material_profile/kerf stored.
- Spec: MODULE-SPECS §3 `cutting` F1/F2/F5. Rule-sets: none.

**T-0315** · `cutting: optimiser (1-D FFD bars, 2-D guillotine/shelf sheets, kerf, offcut-first) in ARQ` · cutting · **M** · deps `T-0314`, `T-0014`
- AC: 1-D first/best-fit-decreasing and 2-D guillotine nesting honour kerf; **draws available offcuts before new stock** test; runs in ARQ worker (heavy, lazy import) not request path (hard rule 7).
- Spec: `cutting` F3/F4/F6; ADR-001. Rule-sets: none.

**T-0316** · `cutting: offcut register + reuse states + confirm → site_inventory movements + cutting.confirm` · cutting · **M** · deps `T-0315`, `T-0312`, `T-0016`
- AC: `cutting.confirm` rule set checks stock on-hand ≥ required, cut list non-empty, each custom cut ≤ stock size, status `optimised`; on confirm posts net consume + add-offcut movements to `site_inventory` via `service.py` in one transaction, **idempotent** on re-confirm; rule-set test.
- Spec: `cutting` F7/F8/F9; §5. **Rule-sets: `cutting.confirm`.**

**T-0317** · `frontend: cutting plan UI (cut list, optimise, yield/waste, offcut search, confirm)` · frontend · **S** · deps `T-0314`, `T-0024`
- AC: optimise→yield/waste/offcut-reuse % shown; offcut search (`?state=available`); confirm surfaces `cutting.confirm` findings.
- Spec: `cutting`. Rule-sets: surfaces `cutting.confirm`.

### Track B — site_logistics (E-03-02)

**T-0318** · `site_logistics: gates (slots/capacity) + laydown zones` · site_logistics · **M** · deps `T-0101`
- AC: `_gate` open/close + `capacity_per_slot`; laydown zones; slot-capacity booking-collision test.
- Spec: MODULE-SPECS §3 `site_logistics` F1/F2. Rule-sets: none.

**T-0319** · `site_logistics: deliveries (booking windows) + delivery_lines (RSW/Item linked)` · site_logistics · **M** · deps `T-0318`
- AC: `_delivery` window within gate capacity; `_delivery_line.rsw_node_id`/`asset_item_id` resolves to a real anchor; PO ref stored as external ref, not FK.
- Spec: `site_logistics` F3/F4. Rule-sets: none.

**T-0320** · `site_logistics: gate receipt → OS&D vs ERP-mirrored PO lines (calls procurement.service) + goods.receive` · site_logistics · **M** · deps `T-0319`, `T-0308`
- AC: gate receipt reconciles delivery line vs mirrored PO line via `procurement.service` (no cross-module table read); OS&D delta raised; `goods.receive` run; rule-set + service-boundary test.
- Spec: `site_logistics`; `procurement`; §5. **Rule-sets: `goods.receive`.**

**T-0321** · `frontend: Gate Delivery edge screen (offline, tablet-first)` · frontend · **M** · deps `T-0319`, `T-0126`
- AC: reuses `useOfflineSync` (T-0126) idempotent queue; works offline, replays on reconnect; tablet-first; a TD-008 UI spec must be written first (raise `spec-gap` if missing).
- Spec: §1 principle 9; TD-008. Rule-sets: none.

**T-0322** · `e2e: Phase 3 exit journey (delivery booked → gate receipt + OS&D exported; cutting confirmed into inventory)` · e2e · **S** · deps `T-0320`, `T-0316`, `T-0321`
- AC: Playwright journey proves the DEV-PLAN §7 Phase-3 exit criterion end-to-end.
- Spec: DEV-PLAN §7 Phase 3 exit. Rule-sets: exercises `goods.receive`, `cutting.confirm`.

### Track C — equipment (E-03-06, independent)

**T-0323** · `equipment: register + types (service intervals, serial, ownership, hour_meter)` · equipment · **M** · deps `T-0101`
- AC: register CRUD; type-driven service intervals; status machine test.
- Spec: MODULE-SPECS §3 `equipment` F1. Rule-sets: none.

**T-0324** · `equipment: maintenance schedules + work orders + inspections/certs` · equipment · **M** · deps `T-0323`
- AC: schedule trigger→`next_due`; work order lifecycle; inspection `valid_until` expiry query.
- Spec: `equipment` F3/F4. Rule-sets: none.

**T-0325** · `equipment: fuel/parts logs + damage reports + internal rental (physical rate)` · equipment · **S** · deps `T-0323`
- AC: logs append; rental rate is physical/internal only (no money ledger); damage report flow.
- Spec: `equipment` F5/F6/F7. Rule-sets: none.

**T-0326** · `equipment: telemetry ingest + hour-meter maintenance triggers (ARQ)` · equipment · **S** · deps `T-0324`, `T-0014`
- AC: `_telemetry` ingest; hour-meter crossing a service interval raises a due work order in ARQ; idempotent ingest test.
- Spec: `equipment` F2. Rule-sets: none.

**T-0327** · `frontend: equipment register + maintenance board` · frontend · **S** · deps `T-0323`, `T-0024`
- AC: register + maintenance due board; `t()`; generated client.
- Spec: `equipment`. Rule-sets: none.

### Track D — supplier_catalogs + subcontractors (rest of E-03-05)

**T-0328** · `supplier_catalogs: vendor master + catalog items + price lists (basic)` · supplier_catalogs · **S** · deps `T-0301`, `T-0307`
- AC: vendor references a `contacts` party via service; catalog/price-list CRUD; dropped-scope (no 3-way match/KYC/Peppol) asserted absent.
- Spec: MODULE-SPECS §3 `supplier_catalogs` F1/F2/F5. Rule-sets: none.

**T-0329** · `supplier_catalogs: vendor stock + purchase-requests route to procurement` · supplier_catalogs · **S** · deps `T-0328`
- AC: stock balances/movements; purchase request creates a `procurement` requisition via `procurement.service`.
- Spec: `supplier_catalogs` F3/F4. Rule-sets: none.

**T-0330** · `subcontractors: register (+block list) + certificates (insurance/licence expiry)` · subcontractors · **S** · deps `T-0301`
- AC: subbie references `contacts`; `is_blocked` honoured; cert `valid_until` expiry query.
- Spec: MODULE-SPECS §3 `subcontractors` F1/F2. Rule-sets: none.

**T-0331** · `subcontractors: agreements (external contract ref, physical retention, versioned files) + ratings` · subcontractors · **S** · deps `T-0330`
- AC: agreement uses `external_contract_ref` (no contract FK, hard rule 4); retention physical; ratings.
- Spec: `subcontractors` F3/F4. Rule-sets: none.

**Phase 3 total: 31 tasks** (10 M / backbone-heavy, 21 S; 5 frontend, 1 e2e). Rule sets wired: `goods.receive`, `cutting.confirm`.

---

## 3. Sanity-check of existing Phase 0–2 tasks

- **Ordering bug — E-03-07 `resource_summary`** declares `depends_on: [E-04-04]` (a Phase-4 epic) yet sits in Phase 3. It rolls demand up **from tasks/look-ahead**, which don't exist until Phase 4. **Move `resource_summary` to Phase 4** (schedule after `tasks` + `schedule_advanced` look-ahead land), or it will block the whole Phase 3 serial queue. This is why it is excluded from the §2 breakdown.
- **Dependency gap — E-03-03 `site_inventory`** declares only `T-0101`, but MODULE-SPECS lists its dependencies as `[projects, asset_management, procurement, progress]`. `procurement` (Phase 3) is fine once procurement is pulled forward (§2); `progress` (Phase 4) means the **consumption→progress tie (F5) must be deferred** — captured as T-0313's stub + a `deferred` TODO. Update the epic's `depends_on` to include procurement.
- **Epic too big / mis-bundled — E-03-05** bundles `procurement` + `supplier_catalogs` + `subcontractors` (three modules, size L). `procurement` is a hard dependency of both `site_inventory` and `site_logistics` OS&D, so it must be *first*, not last. Split as in §2 (procurement = T-0306…T-0310 up front; catalogs/subbies = T-0328…T-0331 at the end, Track D).
- **T-0124 rule-set task is overloaded** — it wires five rule sets (`asset.status_change`, `inspection.close`, `rsw.complete`, `asset.p6_fields`, `schedule.p6_import/export`) in one M task. At one rule set ≈ rule data + `run_checks` wiring + findings surfaced + test, five is likely >800 LOC. **Split into 2–3 tasks** (completion-gate set vs P6 set), each with its own `run_checks` rule-set test.
- **T-0110 `blocked_by: [TD-002]`** is correct but the task text ("one engine … otherwise thin forms") hides a large fork. Once TD-002 is decided, the planner must *re-author* T-0110/T-0102/T-0109 into the §1.2 chain rather than implement the ambiguous version.
- **Phase 2 `eligibility` opportunity** — T-0205 (R-WPS-1…4) and T-0202 (welder continuity) duplicate the "is it valid?" logic that TD-002's `eligibility` service centralises. If TD-002 is adopted, **make R-WPS rules `eligibility` queries** and add `depends_on: [T-0111b]` to T-0205 — otherwise `wps` grows its own validity engine that `eligibility` then has to reconcile.
- **Good, keep as-is:** the Phase 0 critical chain (T-0005→T-0006→T-0007→T-0008), the sync spine (T-0125→T-0126), and the exit-journey e2e tasks per phase (T-0026/T-0130/T-0208) — these correctly assert each phase's §7 exit criterion.

---

## 4. Estimate & milestone view

Rough S/M task counts (TD-002 adopted). Phases 4–7 are epic-count × ~4–5 tasks/epic, biased up where a module is heavy (`schedule_advanced` routes≈208, `documents`/VFS, AWP).

| Phase | Scope | Epics | Est. tasks | Notes |
|---|---|---|---|---|
| 0 | Foundations | — | **27** (actual) | critical path start |
| 1 | Asset & Quality backbone + one-engine + inspection_requests | 1 (E-01-01) | **~40** | 30 existing + TD-002 chain; + `T-0131` freeze gate |
| 2 | Welding quality | — | **8** (actual) | wire R-WPS to `eligibility` |
| 3 | Logistics | 7 | **31** (worked §2) | `resource_summary` → Phase 4 |
| 4 | Execution | 10 | **~50** | `schedule_advanced` + progress + diaries + edge screens; +resource_summary |
| 5 | Documents/collab/AI | 10 | **~50** | VFS, OCR, search, assistant (proposals only) |
| 6 | Defense/claims/controls | 7 | **~32** | physical EVM, claims binder, BI |
| 6b | AWP + work packs | 2 | **~14** | constraint engine + MDR compile |
| 7 | Client portal | 3 | **~14** | v1/v2/v3 sub-phases |
| | **Total** | **40** | **≈264–290** | |

**Milestones:** M1 = backbone freeze (end Phase 1, ~67 tasks in). **M2 = MVP pilot** (end Phase 2 + documents-lite, ~85–95 tasks). M3 = logistics + execution core (Phase 3 + half of 4). M4 = AI + claims (Phase 5–6). M5 = AWP + portal (6b + 7).

**Feeding the ecc plugin:** each `needs-breakdown` epic (E-03-xx…E-07-xx) is the unit `ecc:epic-decompose` consumes; the §2 Phase-3 set is the worked exemplar for the planner/`epic-decompose` output format — one epic → 3–6 S/M task files with id-slot, module, size, `depends_on`, 2–4 testable AC, spec refs, and the `run_checks` rule-set(s). `ecc:epic-validate` should assert each generated task is S(<300)/M(<800), carries ≥1 spec ref, and that any task naming a verifiable transition includes a rule-set test (CLAUDE.md §4). Run `ecc:epic-claim`→`epic-publish`→`epic-sync` so parallel implementers draw from the frozen set without colliding, matching the serial-merge pipeline.

---

## 5. Recommendations table

| # | Recommendation | Rationale | Effort | Priority | Affects |
|---|---|---|---|---|---|
| R1 | Add `T-0131` backbone-freeze gate (contract test on `oe_asset_node`/`oe_asset_item`/schema envelope/external keys/`run_checks` sig) at end of Phase 1; block Phase 3+ breakdown on it | §9 demands the freeze but no task asserts it; everything anchors here | S | P1 | Phase 1→3+, all modules |
| R2 | Adopt TD-002 and sequence it as the §1.2 five-step chain inside Phase 1 | removes 3 duplicate designers; `eligibility` consolidates every validity check | L | P1 | TD-002; T-0102/0109/0110/E-01-01; wps |
| R3 | Set MVP = Phase 0+1+2 + documents-lite (VFS+uploads+inspection PDF) + P6 round-trip | smallest demoable "inspection management system"; nothing speculative | M | P1 | TD-009; Phase 5 slice |
| R4 | Move `resource_summary` (E-03-07) to Phase 4 | depends on Phase-4 tasks/look-ahead; cannot build in Phase 3 | S | P1 | E-03-07; Phase 3/4 |
| R5 | Pull `procurement` to the front of Phase 3; split E-03-05 | site_inventory + site_logistics OS&D both need it first | S | P1 | E-03-05; E-03-02/03 |
| R6 | Adopt the §2 Phase-3 breakdown (T-0301…T-0331) as-is | dependency-correct, S/M-sized, rule-sets wired | M | P1 | Phase 3 |
| R7 | Split T-0124 into 2–3 rule-set tasks (completion-gate vs P6) | one task wiring 5 rule sets exceeds M budget | S | P2 | T-0124 |
| R8 | Keep offline sync ledger (T-0125) module-agnostic; write TD-008 edge-screen UI specs before Gate Delivery (T-0321) | reused by Phase 3/4/6b; UI spec gap blocks implementers | M | P2 | T-0125; TD-008; Phase 3/4/6b |
| R9 | Wire Phase-2 R-WPS rules as `eligibility` queries (add dep T-0205→T-0111b) if TD-002 adopted | avoids a second validity engine in `wps` | S | P2 | T-0205; wps; eligibility |
| R10 | Defer `site_inventory` consumption→progress tie (T-0313) behind an interface + `deferred` TODO | `progress` is Phase 4 | S | P2 | E-03-03; progress |
| R11 | Use `ecc:epic-decompose`/`epic-validate` with the §2 set as the exemplar + S/M+rule-set-test lint | consistent, machine-checkable breakdown for Phases 4–7 | M | P2 | all epics; pipeline |

---

## 6. Conflicts & risks

- **No hard-rule conflicts in the proposed breakdown** — all Phase-3 tasks keep the anchor model (RSW/Item, no BOQ), external refs not FKs for PO/contract (hard rule 4), money as mirror (rule 7), append-only inventory movements (rule 11), and cross-module access via `service.py` (rule 1). The one place to watch is T-0320 (site_logistics reading PO lines): it **must** go through `procurement.service`, never read `procurement` tables — flagged in its AC.
- **Risk — backbone churn after freeze.** If Phase 2/3 reveals the schema envelope is wrong, a post-freeze change is a core-table change needing an ADR (rule 8) and re-validates every downstream rule-set test. Mitigation: R1 contract test + a deliberate Phase-1 "bake" before breaking down Phase 3.
- **Risk — TD-002 decided late.** T-0102/0109/0110/E-01-01 are `blocked`; the whole of Phase 1's engine and all of Phase 2's `eligibility` consolidation wait on it. This is the single schedule risk on the critical path — **decide TD-002 before T-0102 is picked up.**
- **Risk — P6 round-trip (per §9).** WBS→Asset matching is imperfect; keep import idempotent on Activity ID, surface unmatched rows (T-0128 UI), export actuals only. Already well-handled in T-0104/0106/0124 — keep the `schedule.p6_import/export` rule-set tests strict.
- **Risk — edge-screen UI specs (TD-008) absent.** Gate Delivery (T-0321), and later Look-Ahead/Daily Workface, have no UI spec. Implementers will guess. Raise the specs before those tasks, else quality varies and offline behaviour is inconsistent.
- **Risk — parallelism starving on serial merges.** The pipeline merges serially; with 4 implementers and long review rounds (max 3), `dev` can stall behind one slow chain. Mitigation: order the merge queue by `depends_on` and keep independent tracks (equipment, subbies) available to fill gaps.

## 7. Open questions for the owner

1. **TD-002 — adopt the one-engine?** (blocking). Recommend **yes**; it is cheapest in Phase 1 and consolidates Phase-2 validity rules. — *existing TD-002*
2. **TD-009 — confirm the MVP cut line** = Phase 0+1+2 + documents-lite + P6 round-trip, with Gate Delivery/OS&D only if the pilot site receives material? — *existing TD-009*
3. **Backbone freeze (new TD):** do you accept a formal Phase-1 freeze gate (`T-0131`) that makes post-freeze core-schema changes ADR-only? Needed to let Phases 3–7 parallelise safely. — *propose new TD-011*
4. **TD-008 — who writes the Tier-1 edge-screen UI specs (Gate Delivery, Look-Ahead, Daily Workface)**, and before which tasks? They gate Phase 3/4 edge work.
5. **Concurrency budget:** how many parallel implementer agents should the coordinator run (I assume 3–4 given the serial merge queue)? Sets the realistic throughput for the ~265–290 tasks.

## 8. Feeds-the-plan (lift directly)

1. The **phase sequence 0→1→2→3→4→5→6→6b→7 stands**, with an added **`T-0131` backbone-freeze gate** ending Phase 1 and gating all later breakdown.
2. **TD-002 adopted**, sequenced as the §1.2 chain (registry → `forms` → `item_types` → inspection record-integrity + `eligibility` → `inspection_requests`); Phase 1 grows to ~40 tasks; R-WPS rules become `eligibility` queries.
3. **MVP = Phase 0+1+2 + documents-lite + P6 round-trip (~85–95 tasks).** Everything else is wave 2/3.
4. **Phase 3 breakdown = the 31 tasks T-0301…T-0331 in §2** (dependency-correct; `goods.receive` + `cutting.confirm` rule sets wired), adopt as-is.
5. **Three tracker fixes:** move `resource_summary` to Phase 4; pull `procurement` to the front of Phase 3 and split E-03-05; split the overloaded T-0124 rule-set task.
6. **Estimate ≈ 264–290 tasks / 40 epics**, milestones M1 freeze → M2 MVP → M3 logistics+execution → M4 AI+claims → M5 AWP+portal; feed each `needs-breakdown` epic through `ecc:epic-decompose`/`epic-validate` using §2 as the format exemplar and an S/M + rule-set-test lint.
