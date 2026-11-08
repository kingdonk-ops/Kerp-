# Data Model & Schema Advisor — review

**TL;DR.** The anchor model and the external-mirror/physical-metric doctrine are a genuinely strong, consistent data-model spine — keep them exactly. The schema's three real problems are all fixable and all worth fixing before Phase 1 freezes: (1) **the backbone carries *two* dynamic-attribute mechanisms** (`oe_asset_field_value` EAV **and** `custom_fields` JSONB) — pick JSONB-plus-versioned-JSONSchema and delete the EAV table; (2) **TD-002 is really a data-layer decision**: adopt the one-engine model, extract a single shared **field-type registry** and divide the three overlapping inspection tables (`inspections._inspection` / `qms._inspection` / `construction_control._inspection`) into one record + one ITP layer + one conformance layer; (3) **the tree, the ledgers and the JSONB need specific indexes and constraints that the spec names but does not pin down** — I give the canonical shapes below. Freeze the backbone contract (node tree + Item + dynamic schema + external keys + the ledger template + the reusable mixins) as the critical path; everything else anchors to it.

This is the most schema-heavy lens, so it is concrete. Recommendations are atomic in §Recommendations.

---

## 1. The backbone, done right

### 1.1 `oe_asset_node` — recursive tree: materialised-path *and* `ltree`, not one or the other

The spec gives `oe_asset_node` a self-FK `parent_id`, a `level` enum (`site|area|asset|rsw`), plus materialised `path` (`/site1/areaA/TX01/`) and `depth` ([MODULE-SPEC-08 §2.1]). The hot query is **"the Asset-Details Inspections tab aggregates the asset and its descendants"** and the global register "filters across all levels" ([DEV-PLAN §3.2]) — i.e. *subtree* reads on every inspection/progress/cost rollup. A text `path LIKE '/site1/areaA/TX01/%'` works but is fragile (prefix-collision on `TX01` vs `TX010`, no ancestor query, index bloat).

**Recommendation: keep `parent_id` as the source of truth, keep `depth` and a human `path` for display, and add a real `ltree` column `tree_path` with a GiST index.** `ltree` is purpose-built for this: `tree_path <@ :ancestor` (is-descendant) and `:ancestor @> tree_path` (is-ancestor) are index-accelerated, `nlevel()` gives depth, and `subpath`/`lca` support "roll up the Asset subtree" for `costmodel._control_account.asset_subtree_root_id` ([§10 costmodel]) and the `inspections._programme.asset_path_scope` ltree the AIP spec *already* assumes ([MODULE-SPEC-11 §4]). It ships with core Postgres 16 (`CREATE EXTENSION ltree`), no PostGIS/DuckDB concern. Materialised-path-as-text alone cannot do the ancestor direction and makes the "aggregate descendants" tab an N+1 or a recursive CTE per expand.

```sql
-- oe_asset_node (backbone) — key columns beyond BaseRecordMixin
parent_id   uuid REFERENCES oe_asset_node(id),
level       asset_level NOT NULL,            -- enum: site|area|asset|rsw
tree_path   ltree NOT NULL,                  -- e.g. site1.areaA.tx01  (label = id or slug)
depth       smallint NOT NULL,
path        text NOT NULL,                   -- display only
external_source text, external_id text, synced_at timestamptz,  -- P6/excel keys
CONSTRAINT rsw_leaf CHECK (level <> 'rsw' OR id NOT IN (SELECT parent_id FROM ... )) -- see note
```

- **Maintain `tree_path`/`depth` on `move`** inside the `POST /assets/nodes/{id}/move` transaction (update the moved node's subtree with a single `UPDATE … SET tree_path = :new || subpath(tree_path, nlevel(:old))`). This is the one place where `ltree` pays for itself — a move is one statement, not a recursive rewrite.
- **Level-shape constraint** ("`rsw` parents only Items; `asset` may parent sub-`asset` and `rsw`" [§2.1]) can't be a pure column CHECK (it's a parent/child relation). Enforce with a `BEFORE INSERT/UPDATE` trigger function `assert_node_parentage(level, parent_level)` plus a composite index on `(parent_id, level)`. Put the rule table in data so a client's extra level rule is config, consistent with the verification-is-config doctrine.

### 1.2 `oe_asset_item` — the inspection anchor

`oe_asset_item` (asset_id → node where level=asset, `parent_item_id` self-FK, `item_type_id`, `rsw_id` nullable, `inspectable`, `custom_fields` jsonb). A **weld is an Item of type `weld`** and `wps.oe_weld.asset_item_id` FKs to it ([§3.1]). Good. Two notes:

- Add a **partial index** `WHERE inspectable` — the register and the contextual tab only ever scan inspectable Items.
- `parent_item_id` gives Items their own small tree (valve → its welds). Keep it `parent_id`-only (no ltree) — Item trees are shallow; a recursive CTE is fine.

### 1.3 Dynamic type/field schema — **collapse the two mechanisms into one**

This is the single most important backbone fix. MODULE-SPEC-08 ships **both**:
- an **EAV triple** — `oe_asset_type` → `oe_asset_field_def(key, data_type, options, constraints…)` → `oe_asset_field_value(node_id|item_id, field_def_id, value jsonb)`; **and**
- a **JSONB bag** — `oe_asset_node.custom_fields jsonb` *and* `oe_asset_item.custom_fields jsonb`.

Storing the same attribute data two ways is a latent bug and a performance trap: EAV `oe_asset_field_value` forces one row per attribute per entity (a 20-field valve = 20 rows, every register read a 20-way self-join or a crosstab), and it duplicates what `custom_fields` already holds. ADR-001 explicitly blesses the JSONB path: *"Dynamic inspections = relational columns + JSONB + versioned JSONSchema — adopt… matches the dynamic-schema pattern `asset_management` already uses"* ([ADR-001 §1]).

**Recommendation: delete `oe_asset_field_value`. Keep attribute *values* in `custom_fields jsonb` on the node/item; keep `oe_asset_field_def` as the *schema registry only* (labels, data_type, units, constraints, `p6_mandatory`); validate `custom_fields` against the type's effective JSONSchema at the API boundary via `run_checks(asset.attribute_validate)`.** Index with a GIN `jsonb_path_ops` on `custom_fields`, plus **expression indexes on the handful of hot filterable attributes** (e.g. `((custom_fields->>'spec_ref'))`). This is exactly what `workpacks.measurement_data` already does ("JSONB … validated by versioned Pydantic/JSONSchema … GIN-indexed `jsonb_path_ops`" [MODULE-SPEC-10 §2]) — make the backbone consistent with it rather than inventing EAV only here.

This also cleanly **reconciles with the TD-002 `item_types` proposal**, which adds `parent_type_id` (inheritance), `schema_version`, and `oe_item_types_type_version(schema jsonb, immutable once published)` ([MODULE-SPEC-11 §3]). If values are JSONB validated against a *versioned JSONSchema document*, then:
- `item_type_version` pins the schema an asset was authored against (immutable rows, exactly as specced);
- **inheritance** = JSONSchema `allOf: [parent, child]` (add-and-tighten only — child may add/narrow, never drop — enforced by a publish-time lint, not a loosening merge);
- **migration preview** ([F5]) = count assets whose `custom_fields` fail the *new* version's schema (`SELECT count(*) … WHERE NOT jsonschema_valid(:new, custom_fields)`), surfaced before publish.

Keep `oe_asset_layout(type_id, layout_json)` as-is — it is presentation, orthogonal to storage.

### 1.4 The field-type registry — break the circular dependency

MODULE-SPEC-11 makes `forms` **depend on** `item_types` ("field registry") and `item_types` **depend on** `forms` ("shared field registry") — a **circular module dependency**, which violates the clean dependency-graph the plan sequences on. The "field-type registry" is really two things conflated:
1. the **universal palette of field *types*** (text, number, decimal, measurement value+unit, signature, instrument-ref, calculated, …) — a closed, code-defined set shared by both designers;
2. `oe_item_types_attr_registry` — **shared named attributes** (label+type auto-merge), which is asset-schema-specific.

**Recommendation: put the field-*type* palette in a tiny standalone module (`fieldkit` / a sub-package under `validation`) that both `forms` and `item_types` depend on, breaking the cycle.** The shared **evaluator** (CEL *or* JSONLogic — pick one; [MODULE-SPEC-11 §2 F8] mandates TS+Python parity on one fixture set) lives there too. Then `forms_revision.schema`, `item_types_type_version.schema`, and `workpacks.measurement_data`'s schema all reference the same type codes and run through the same sandboxed evaluator. One registry, one evaluator, three consumers — which is the whole thesis of the AIP proposal, expressed as a clean DAG instead of a cycle.

---

## 2. Append-only ledger patterns — one canonical shape

The spec has **five** append-only / hash-chained / idempotent-sync tables, each described slightly differently: `inspection_requests._event` (prev_hash/content_hash, REVOKE UPDATE/DELETE), `inspections._signoff` (same + eligibility snapshot), `inspections._response` (client_id + sync_version, latest-per-key view), `field_diary.oe_field_sync_ledger` (client_op_id idempotency), and the implicit `audit`/`validation._report` finding log. Pick **two canonical templates** and apply them verbatim everywhere, or they will drift.

### 2.1 Hash-chained event/sign-off ledger (tamper-evidence)

Canonical columns and constraints — reuse for `inspection_requests._event`, `inspections._signoff`, and any future audit chain:

```sql
seq          bigint GENERATED ALWAYS AS IDENTITY,     -- per-chain order
chain_key    uuid NOT NULL,                           -- the entity whose history this is
prev_hash    bytea,                                   -- NULL only for the genesis row
content_hash bytea NOT NULL,                          -- sha256(canonical_json(payload) || prev_hash)
payload      jsonb NOT NULL,
actor_kind   text, actor_id uuid,
occurred_at  timestamptz NOT NULL DEFAULT now(),
UNIQUE (chain_key, prev_hash)                          -- forbids two children of one link (fork)
```
- **Integrity at the DB, not just the app:** `REVOKE UPDATE, DELETE ON <table> FROM <app_role>` (the spec says this for `_event`/`_signoff` — make it a **standing migration convention**, grant only INSERT/SELECT). A `BEFORE UPDATE/DELETE` trigger that `RAISE EXCEPTION` is a belt-and-braces second line.
- **Verification hook:** `inspection.signoff`'s "hash chain intact" check recomputes `content_hash` walking `prev_hash` for the `chain_key` — cheap because `(chain_key, seq)` is indexed.
- **RLS still applies** (tenant_id from JWT) — append-only and tenant-isolated are orthogonal.

### 2.2 Idempotent offline response/sync ledger (latest-wins)

Canonical for `inspections._response`, `field_diary` sync, `workpacks` stage deltas, offline `inspection_requests`:

```sql
client_id     text NOT NULL,     -- device-generated op id (idempotency key)
sync_version  bigint NOT NULL,   -- monotonic per (entity, field_key)
entity_id uuid, field_key text, value jsonb,
recorded_by uuid, recorded_at timestamptz,
UNIQUE (entity_id, field_key, client_id)   -- replay of the same op is a no-op
```
- **"Current value = latest row per key"** is a **view**, never a mutation:
```sql
CREATE VIEW oe_inspections_response_current AS
SELECT DISTINCT ON (inspection_id, field_key) *
FROM oe_inspections_response
ORDER BY inspection_id, field_key, sync_version DESC, recorded_at DESC;
```
Corrections are *superseding inserts*, exactly as [MODULE-SPEC-11 §4] says. Index: `(inspection_id, field_key, sync_version DESC)` to make the `DISTINCT ON` an index scan.
- **Physical ledgers follow the same latest-wins / append discipline:** `site_inventory._movement`, `field_time._line`, `progress._entry`, `costmodel._labour_worker_day` are naturally append-only event logs; current stock / earned-qty / hours are **aggregations or snapshot tables** (`costmodel._physical_snapshot`, `_productivity`) refreshed in the ARQ worker — never mutated-in-place balances. Add a partial/covering index per ledger on `(item_id, occurred_at)` / `(rsw_node_id, period_label)`.

---

## 3. External-key & mirror columns — one reusable column set

The anchor doctrine ([DEV-PLAN §3.4 rules 2–3]) touches `schedule`, `progress`, `procurement`, `variations`, `subcontractors`, `changeorders`, `risk`, `resources`, `site_inventory`, `supplier_catalogs`. Each re-spells the same idea. **Define two SQLAlchemy mixins and mandate them**, so the metadata-contract test can assert their presence the way it already asserts `BaseRecordMixin`:

```python
class ExternalRefMixin:          # a system-of-record pointer, never an FK
    external_source: Mapped[str | None]      # 'p6' | 'erp' | 'manual' | ...
    external_id:     Mapped[str | None]
    synced_at:       Mapped[datetime | None]
    # partial index: (external_source, external_id) WHERE external_source IS NOT NULL

class MoneyMirrorMixin:          # read-only money from the SoR; app never writes in a user txn
    amount_mirror:   Mapped[Decimal | None]
    currency_code:   Mapped[str | None]
    mirror_synced_at: Mapped[datetime | None]
```
- **P6 round-trip keys** (`wbs_code`, `activity_id`/`external_activity_id`, `external_wbs_id`) recur on `oe_asset_node`, `schedule._activity`, `schedule._work_order`. Idempotent re-import is "upsert on `(project_id, external_activity_id)`" — make that a **unique index**, not app logic, so a double-import can't duplicate activities (the `schedule.p6_import` check then just reports unmatched rows).
- **Enforce the no-FK-to-money rule with a lint/contract test:** no column may be both named like money (`*_amount`, `*_cost`, `*_rate`) and nullable-false/owned. The spec already marks every such column "(mirror)" — turn that annotation into a check so review catches a regression automatically (CLAUDE.md §4 "never weaken" applies).

---

## 4. Indexing & performance — per hot table

| Hot table | Query it serves | Index(es) |
|---|---|---|
| `oe_asset_node` | subtree aggregate (Inspections tab, cost rollup), tree render | GiST on `tree_path`; btree `(project_id, parent_id, level)`; partial `(external_source, external_id)` |
| `oe_asset_item` | register filters, contextual inspections | `(asset_id)`, `(item_type_id, status)`, GIN `custom_fields jsonb_path_ops`, partial `WHERE inspectable`, expr idx on 2–3 hot attrs |
| `inspections._inspection` / `_response` | global register across all anchor levels; offline latest-value | `(project_id, status)`, `(rsw_node_id)`, `(asset_item_id)`; response: `(inspection_id, field_key, sync_version DESC)` |
| `_signoff` / `_event` chains | integrity walk, timeline | `(chain_key, seq)`; UNIQUE `(chain_key, prev_hash)` |
| `documents._document` + `_file_link` | VFS tree, orphan set-difference, entity files | `(folder_id)`, partial `WHERE is_orphaned`, `_file_link (target_type, target_id)` + `(document_id)` |
| `site_inventory._movement`, `field_time._line`, `progress._entry` | ledgers, earned-qty feed | `(item_id, occurred_at)` / `(rsw_node_id, period_label)` / `(activity_id)` |
| `documents._document` (geo), `inspections` pins, `site_logistics` | PostGIS | GiST on `geography(Point)` columns |
| `workpacks.measurement_data`, `forms_revision.schema` | formula/rule reads, measurement validate | GIN `jsonb_path_ops` |
| `file_search._file_search_index` | OCR full-text (dockets/claims) | GIN on `tsvector` |

**Tree-aggregation / N+1 risks to design against, now:**
1. **"Aggregate the asset and its descendants"** ([DEV-PLAN §3.2]) — do it as *one* `WHERE tree_path <@ :root` join against the inspection register, **not** a per-node fan-out. Without `ltree` this becomes a recursive CTE on every tab open.
2. **The global inspection register across all anchor levels** — a single table (`inspections`) with both `rsw_node_id` and `asset_item_id` nullable FKs beats a `UNION` across three inspection tables (another reason for §5 dedup). Filter "across all levels" = join the register to `oe_asset_node` by `tree_path` prefix.
3. **AWP readiness / contextual TQ banners** (`GET /awp/tqs/applicable?asset_id=`) resolve across scope levels (product_group ⊂ asset ⊂ project) — index `oe_awp_technical_query (scope_level, scope_ref, status)` and resolve with an `ltree`/scope lookup, not three queries.
4. **BI/KPI rollups** — ADR-001 §2 moves these to the ARQ worker into materialised/summary tables (`reporting._kpi_snapshot`, `dashboards._snapshot`, `bi_dashboards._kpi_value`). Good — keep *all* cross-table aggregation off the request path; refresh on a schedule.

---

## 5. TD-002 at the data layer — the dedup, resolved

**Recommendation: adopt the one-engine model (TD-002 = yes), because it is primarily a *schema* decision, and the current spec stores inspections three ways.** Concrete resolution:

**(a) One field-type registry, physically.** As §1.4 — extract the type palette + evaluator into one module both designers depend on. `forms` templates and `item_types` schemas then genuinely share it. This is a prerequisite; sequence it first (AIP §4 agrees).

**(b) Three inspection tables → a clear division of ownership.** Today: `inspections._inspection` (lightweight register, 18 routes), `qms._inspection` (+signatures/holds, 118 routes), `construction_control._inspection` (party/intervention/criterion, 100 routes). Three tables, overlapping columns, no single register. Canonical set:

| Concern | Owning table | Role |
|---|---|---|
| **The inspection record** (one row per inspection event, any `kind`) | `inspections._inspection` (+ `_response`, `_signoff`, `_assignment`, `_programme`) | the single register; `template_revision_id → forms`; anchors `rsw_node_id`/`asset_item_id`; holds answers + hash-chained sign-off |
| **ITP master plan / templates / audit / calibration** | `qms` (`_itp_template`, `_itp_plan`+`_itp_item`, `_audit`, `_calibration`) | *authoring* of ITP stages and the QMS audit register — **not** a second inspection record |
| **Conformance criteria & hold gates** | `construction_control` (`_acceptance_criterion`, `_hold_gate`, `_handover_package`, `_material_record`, `_test_result`, `_asbuilt_record`) | the gate/criteria engine `run_checks` evaluates; **drop `construction_control._inspection`** — it becomes rows in `inspections` with a `kind`/criterion link |

So: **one `_inspection` record**, templates from `forms`, ITP stages authored in `qms` and *cloned* into execution (`workpacks.oe_workpack_execution_stage` already does this clone — keep that pattern), gates in `construction_control`. This removes the `UNION`-across-three-tables register and gives the AIP record-integrity extensions ([MODULE-SPEC-11 §4]) one place to live.

**(c) `eligibility` holds no table** — correct as specced; it reads `credentials`/`wps`/`qms._calibration`/`compliance_docs` and returns pass/fail. Keep it stateless; cache per request only. The one addition: a `GET /eligibility/expiring` needs a **union query with indexes on every register's `valid_until`/`expiry_date`/`continuity_last_welded_at`** — add those indexes when each register is built.

---

## 6. Migration & schema-evolution strategy

- **Alembic, additive-by-default** (CLAUDE.md §6 rule 8). Destructive or core-table (`oe_asset_node`/`oe_asset_item`) changes need an ADR in `docs/decisions/` — so **freeze the backbone contract early** (it is the critical path; everything anchors to it [DEV-PLAN §Phase 1]) to avoid churn under 90 downstream tables.
- **Data-schema evolution is separate from DB-schema evolution.** The `item_type_version` / `forms_revision` immutable-JSONB-with-`schema_hash` pattern ([MODULE-SPEC-11 §2–3]) is the right tool: live assets/inspections **pin** a version, migration happens through an explicit **preview → migrate** with impact counts, enforced by `item_type.publish_version`. Implement the trigger that **blocks UPDATE of `schema` once status≠`draft`** (the spec names it) as a reusable trigger function, applied to both `forms_revision` and `item_types_type_version`.
- **Partitioning candidates at scale** (30–80 users is modest, but these grow unbounded): `audit`/`validation._report` finding log, `inspection_requests._event` and `inspections._signoff` chains, `site_inventory._movement`, `field_time._line`, `integrations._delivery` (webhook log), `erp_chat._message`, telemetry (`equipment._telemetry`). **Range-partition by `created_at` (monthly)** when any exceeds ~10M rows; not needed day one, but design the ledgers with a partition-friendly PK (`(created_at, id)`), so it is a non-event later.
- **RLS is per-table and mandatory** — bake `ENABLE ROW LEVEL SECURITY` + the `tenant_id = current_setting('app.tenant')::uuid` policy into the table-creation template/migration macro, so a new table cannot ship without it (the RLS contract test enforces, but the macro prevents).

---

## 7. Module → key tables · anchor · notable indexes (backbone + logistics + execution + documents + AIP thorough; rest summarised)

| Module | Key tables | Anchor | Notable indexes / notes |
|---|---|---|---|
| **asset_management** | `oe_asset_node`, `oe_asset_item`, `oe_asset_type`/`_field_def`/`_layout`, `_config_pack`, `_import_job`, `_p6_export` | *is* the anchor | GiST `tree_path`; GIN `custom_fields`; UNIQUE `(project_id, external_activity_id)`. **Drop `_field_value` (§1.3).** |
| **wps** | `oe_wps_record`, `oe_welder`, `oe_welder_qualification`, `oe_weld`, `oe_weld_nde_result` | `oe_weld.asset_item_id` (Item=weld) | `(wps_id, status)` on qualification; `(welder_qualification_id)`; `continuity_last_welded_at`, `expiry_date` idx for eligibility/continuity-watch |
| **qms** | `_itp_template`/`_itp_plan`/`_itp_item`, `_ncr`, `_punch_item`, `_audit`, `_calibration` | RSW/Item via plan | ITP authoring only (§5b); `_calibration.valid_until` idx |
| **construction_control** | `_acceptance_criterion`, `_hold_gate`, `_handover_package`, `_material_record`, `_test_result`, `_asbuilt_record` | `asset_item_id`/`rsw_node_id` | **drop `_inspection`** (§5b); `_hold_gate (blocks_progress, released)` partial idx |
| **inspections** | `_inspection`, `_response`, `_signoff`, `_assignment`, `_programme` | `rsw_node_id`/`asset_item_id` | the single register; response latest-wins view; signoff hash chain; `_programme.asset_path_scope` ltree |
| **ncr / punchlist / requirements / submittals** | `_ncr`, `_item`, `_set`/`_item`/`_gate_result`, `_submittal` | `asset_item_id`/`rsw_node_id` | nullable dual-anchor FKs; `(project_id, status)` |
| **documents (+VFS)** | `_document`, `_folder`, `_file_link`, `_share`, `_sheet`, `_photo` | polymorphic `_file_link.target_*` | `(folder_id)`; partial `WHERE is_orphaned`; `_file_link (target_type,target_id)`; flat object key — rename = DB-only |
| **file_*** | `_file_version`, `_file_search_index`, `_file_reference`, `_file_approval_*`, `_file_transmittal`, `_file_trash` | via document_id | `file_search` GIN tsvector (OCR dockets); `_file_version (file_id, is_current)` |
| **markups / plan_room / transmittals** | `_markup`, `_plan_pin`, `_transmittal` | `document_id` + `linked_asset_item_id` | `_markup (document_id, page, layer)` |
| **contacts** | `oe_contacts_contact`, `_note`, `_file` | — (party master) | `(contact_type)`, GIN `tags`; folds staff/crm (D7) |
| **site_inventory** | `_location` (PostGIS), `_item`, `_movement` | `rsw_node_id`/`asset_item_id` | movement ledger `(item_id, occurred_at)`; `_location` GiST |
| **procurement** | `_requisition`, `_po`, `_po_item`, `_goods_receipt`, `_gr_item` | `rsw_node_id` | `_po` ExternalRefMixin (ERP mirror); unit_cost MoneyMirror |
| **cutting** | `oe_cutting_plan`, `_cut_list_item`, `_stock_bar`, `_offcut`, `_inventory_link` | via site_inventory → RSW | `_offcut (profile, reuse_state)`; `_inventory_link` idempotency on confirm |
| **site_logistics / subcontractors / supplier_catalogs / resources** | `_gate`/`_delivery(_line)`, `_subcontractor`/`_agreement`, `_vendor`/`_catalog_entry`/`_stock_balance`, `_resource`/`_assignment` | `_delivery_line` RSW/Item | contract FKs → `external_contract_ref`; rates MoneyMirror |
| **schedule** | `_schedule`, `_activity`, `_work_order`, `_relationship`, `_baseline`, `_p6_import_job`, `_p6_export` | `_activity.rsw_node_ids`, `_work_order.rsw_node_id` | UNIQUE `(external_activity_id)`; crew_hours not cost; CPM computed internally |
| **schedule_advanced** | `_look_ahead`, `_constraint`, `_weekly_plan`, `_commitment`, `_delay_analysis`(+`_delay_event`/`_fragnet`/`_window`) | RSW | delay windows feed `inspection_requests._delay` + EOT |
| **progress** | `_entry`, `_plan`, `_milestone`, `_quantity_claim` | `rsw_node_id`/`asset_item_id` | earned-qty ledger; cap at anchor scope (constraint); `(activity_id)` |
| **costmodel (physical)** | `_control_account`, `_work_package`, `_physical_snapshot`, `_labour_worker_day`, `_productivity` | `asset_subtree_root_id` (ltree subtree) | snapshots refreshed in ARQ worker; no money columns |
| **variations** | `_notice`, `_request`, `_asset_qty_trace`, `_site_measurement`, `_daywork_*`, `_disruption_claim`, `_eot_claim`, `_final_account` | `rsw_node_id`/`asset_item_id` | physical owned; money `_final_account` MoneyMirror; `external_ref` client change-ref |
| **field_diary / field_time / fieldreports** | `_entry`/`oe_field_sync_ledger`/`_magic_link`, `_timesheet`/`_line`, `_report`/`_workforce` | RSW-coded lines | sync ledger `client_op_id` UNIQUE; time `_line (rsw_node_id, date)` |
| **workpacks** | `oe_workpack`, `_execution_stage`, `_drawing_link`, `_material_allocation`, `_tq_link` | `rsw_node_id` + `component_asset_item_id` | stages cloned from `qms`; `measurement_data` GIN; `work_pack_number` UNIQUE **per tenant** |
| **awp** | `_ewp`, `_pwp`, `_cwp`, `_cwp_iwp`, `_technical_query` | `asset_subtree_root_id` | `_technical_query (scope_level, scope_ref, status)`; constraint engine = `run_checks` |
| **inspection_requests** | `_request`, `_slot`, `_attendee`, `_event` (hash chain), `_reason_code`, `_notice`, `_delay` | `rsw_node_id`/`asset_item_id` | `_event` append-only REVOKE U/D; `_delay (cause_party, blocks_rsw_node_id)` for attribution dashboard |
| **forms / item_types / eligibility** | `oe_forms_template`/`_revision`(schema_hash)/`_option_set`/`_section_library`, `oe_item_types_type_version`/`_attr_registry`/`_status_set`, *(eligibility: none)* | item types → Item | immutable-schema trigger; shared registry (§1.4); eligibility stateless |
| *Integrations / AI / Controls / Ops (summary)* | `integrations._webhook`/`_delivery`, `erp_chat._message`/`_action`, `reporting._kpi_snapshot`, `dashboards._snapshot`(parquet), `bi_dashboards._kpi_value`, `risk._register`, `carbon`/`service`/`equipment` | mostly `project_id` + mirrors | AI `_action` carries `source="ai"` + before/after state (revertible); BI rollups in ARQ worker → materialised tables; webhook/`_delivery` + `_message` are partition candidates |

---

## Recommendations

| # | Recommendation | Rationale | Effort | Priority | Affects |
|---|---|---|---|---|---|
| 1 | **Delete `oe_asset_field_value` (EAV); store dynamic attrs in `custom_fields jsonb` + versioned JSONSchema, GIN-indexed; keep `_field_def` as schema registry only** | Spec ships two attr mechanisms; EAV is slow + duplicative; ADR-001 blesses JSONB | M | **P1** | asset_management, item_types, TD-002, Phase 1 |
| 2 | **Add `ltree tree_path` + GiST to `oe_asset_node`; maintain on move; keep `parent_id` as truth** | Subtree aggregation is on every register/rollup; text `path LIKE` can't do ancestors and prefix-collides | M | **P1** | asset_management, inspections, costmodel, awp |
| 3 | **Adopt TD-002: one shared field-type registry + evaluator in a standalone module both designers depend on** (breaks the `forms`↔`item_types` circular dep) | TD-002 is a schema decision; current spec has a module dependency cycle | M | **P1** | TD-002, forms, item_types, inspections, workpacks |
| 4 | **Resolve the three inspection tables: one `_inspection` record; `qms`=ITP/audit authoring; `construction_control`=gates/criteria; drop `construction_control._inspection`** | Three overlapping inspection stores; no single register; blocks the global cross-level filter | L | **P1** | inspections, qms, construction_control, TD-002 |
| 5 | **Define two canonical ledger templates (hash-chain; idempotent latest-wins view) + `ExternalRefMixin`/`MoneyMirrorMixin`; enforce via contract tests** | Five ledgers + ~10 mirror-carrying modules re-spell the same thing; drift risk | M | **P1** | inspection_requests, inspections, field_diary, schedule, procurement, variations |
| 6 | **Make REVOKE UPDATE/DELETE + RLS-enable + tenant policy part of the table-creation migration macro** | Spec names them per-table; a macro prevents a table shipping without them | S | **P1** | every module, Phase 0 |
| 7 | **Index every eligibility register's expiry column (`valid_until`/`expiry_date`/`continuity_last_welded_at`); `eligibility` stays tableless** | `GET /eligibility/expiring` is a union over registers | S | P2 | eligibility, wps, credentials, qms, compliance_docs |
| 8 | **Immutable-schema trigger (block UPDATE of `schema` once status≠draft) as one reusable function on `forms_revision` + `item_types_type_version`; migration = preview→migrate with impact counts** | Legal defensibility; safe evolution over live assets | M | P2 | forms, item_types |
| 9 | **UNIQUE `(project_id, external_activity_id)` and upsert-on-import for P6 keys; lint that no owned column is money-named** | Makes P6 re-import idempotent at the DB; enforces the mirror boundary automatically | S | P2 | asset_management, schedule, procurement, variations |
| 10 | **Design ledgers with `(created_at, id)` PKs for later monthly range-partitioning (audit, events, movements, telemetry, erp_chat)** | Unbounded-growth tables; cheap now, non-event later | S | P3 | audit, inspection_requests, site_inventory, equipment, erp_chat |

---

## Conflicts & risks

- **Hard-rule conflict (two attr mechanisms):** `oe_asset_field_value` EAV + `custom_fields` JSONB in the *same* backbone module is an internal contradiction; ADR-001 §1 only sanctions the JSONB form. Rec 1 resolves it. **Flag before Phase 1 freezes.**
- **Module-dependency cycle:** `forms` ⇄ `item_types` ([MODULE-SPEC-11 §2–3]) violates the clean DAG the plan sequences on and the modular-monolith "call the other module's service" rule (CLAUDE.md §6.1). Rec 3 breaks it.
- **Three inspection records** (`inspections`/`qms`/`construction_control`) — the spec itself says "resolve the dedup in the rebuild" ([MODULE-SPECS §2 qms note]). Left unresolved, the global register becomes a 3-way UNION and sign-off integrity has no single home. Rec 4.
- **Anchor model — intact and good.** No BOQ anchor anywhere; dual nullable `rsw_node_id`/`asset_item_id` throughout; external keys not FKs; money is mirror. I recommend **no change** to the doctrine — only that it be enforced by mixins + contract tests (Recs 5, 9) rather than by per-module discipline.
- **Risk — backbone churn:** 90+ tables anchor to `oe_asset_node`/`oe_asset_item`. Any later core-table change needs an ADR (CLAUDE.md §6.8) and ripples widely. Mitigation: freeze the backbone contract (tree + Item + dynamic schema + external keys + ledger templates) as the very first Phase-1 deliverable, with the metadata/RLS/anchor contract tests in place before Phase 3+ build on it.
- **Risk — JSONB without expression indexes** degrades register filters as data grows; mandate expr indexes on the 2–3 hot filterable attributes per type (not a blanket GIN only).

## Open questions for the owner

1. **TD-002 — adopt the one-engine model?** My lens says **yes**: it is mostly a schema consolidation (one registry, one inspection record), and the current spec stores inspections three ways and has a module cycle. *(Existing TD-002.)*
2. **Dynamic attributes: JSONB-plus-JSONSchema vs EAV?** Recommend JSONB (Rec 1). Decidable independently of TD-002 and should be settled before the backbone freezes. *(Propose new TD — "backbone dynamic-attribute storage".)*
3. **`ltree` extension acceptable in the Postgres image?** It is core Postgres, no DuckDB/licensing concern, but it is a second extension beside PostGIS — confirm the ops posture. *(Tie to ADR-001.)*
4. **CEL vs JSONLogic for the shared evaluator?** [MODULE-SPEC-11 §2] says "pick one in the rebuild." This is a data-model decision (it defines what lives in `schema` JSONB) — pick now so forms/item_types/measurement schemas are portable. *(Propose new TD.)*
5. **TD-007 (keep tenant_id + RLS in a single-company product)?** From the data lens: **keep it** — the cost is one indexed column + one policy per table (covered by the Rec 6 macro), and it is the cheapest possible future-proofing against a second tenant or a hosted offering. Removing it later is far more expensive than carrying it.

## Feeds-the-plan

Lift directly into the merged plan:
1. **Backbone freeze (Phase 1, critical path):** `oe_asset_node` (parent_id + `ltree tree_path` + depth + external keys), `oe_asset_item`, dynamic schema = `_field_def` registry + `custom_fields` JSONB + versioned JSONSchema (**no `_field_value`**), `oe_asset_layout`. Level-shape + scope-cap as triggers/constraints.
2. **Two reusable mixins** (`ExternalRefMixin`, `MoneyMirrorMixin`) + the existing `BaseRecordMixin`, all three asserted by the metadata-contract test.
3. **Two canonical ledger templates** (hash-chain with REVOKE U/D + UNIQUE `(chain_key, prev_hash)`; idempotent `client_id`/`sync_version` with a `DISTINCT ON` current-value view) — applied to `inspection_requests._event`, `inspections._signoff`/`_response`, `field_diary`, `workpacks` deltas.
4. **Table-creation migration macro** that bundles `BaseRecordMixin` columns, RLS enable + tenant policy, and (for ledgers) the REVOKE — so no table ships non-compliant.
5. **One field-type registry + one evaluator** in a standalone module; `forms`/`item_types`/`workpacks` schemas all reference it (TD-002 enabler).
6. **One inspection record** + the qms(authoring)/construction_control(gates) division; drop `construction_control._inspection`.
7. **The index set in §4 and §7** as the per-table index baseline, plus P6 idempotency UNIQUE and the money-naming lint.
8. **Partition-ready PKs** on the unbounded ledgers (audit, events, movements, telemetry, erp_chat) for later monthly range-partitioning.
