# Field Operations Platform — Module Specifications

> **Complete, self-contained spec** for every in-scope module. Companion to `DEVELOPMENT-PLAN.md`. Only in-scope modules appear; cross-references point only to in-scope modules. The anchor model (§3 of the plan) is applied throughout: the lowest **work** anchor is the **RSW** (`oe_asset_node level='rsw'`), the lowest **inspection** anchor is the **Item** (`oe_asset_item`); anything owned by a system of record is a **mirror** via `external_source`+`external_id`.

**Spec format per module:** `Identity · Depends/Dependents · Purpose · Entities (key fields) · Endpoints · Key logic/state · Verification · Features [F#] · Removal impact`. `[F#]` features are individually removable. Table prefixes are `oe_<module>_*`.

**Contents**
1. Platform & foundation · 2. Asset & Quality backbone · 3. Logistics · 4. Execution · 5. Documents & CDE (incl. the VFS) · 6. Collaboration · 7. Integrations & ingestion · 8. AI & search · 9. Defense & Claims · 10. Controls & Insights · 11. Ops & sustainability · 12. Governance & workflow · 13. Client portal · 14. Work packs & AWP (`workpacks` / `awp`) · 15. Request-for-Inspection & ported Project-AIP capabilities (`inspection_requests` + `forms`/`item_types`/`inspections`/`eligibility`/`rfi` extensions).

> The **Virtual File System** (folder tree, routing, orphans, lockers, revisions) is **part of `documents`** (§5), not a separate module. The two new modules are `workpacks` and `awp` (§14); both have **full** entity/endpoint/interaction detail in `spec/MODULE-SPEC-10-workpacks-awp.md` (which also carries the folded VFS tables on `documents`); runtime/storage decisions are in `ADR-001-runtime-and-storage-architecture.md`. Summarised here for the index.

---

## 1 · Platform & foundation

### projects — Project master & settings
- **Identity:** core · prefix=`/projects` · **Dependents:** ~every module.
- **Entities:** `_project`(name, region, currency, locale, default_vat_rate, custom_units, work_calendar_id, validation_rule_sets, compliance_rule_packs, status, owner_id, country/subdivision_code, project_code/type, phase, client_id, parent_project_id, planned/actual dates, gross_floor_area, custom_fields, storage_path_override) · `_status_history` · `_wbs`(parent_id, code, level, wbs_type, planned_hours) · `_milestone`(milestone_type, planned/actual_date) · `_project_profile`(preset, phases, role, focus_mode, setup_completion) · `_project_module`(module_name, enabled, tier, source) · `_wizard_draft`.
- **Key logic:** **Project Settings** carry currency/date/locale/tax (single company — no separate i18n subsystem); per-project module enablement drives the loader; WBS tree seeds the Asset import mapping.
- **Features:** [F1] projects (+hierarchy) · [F2] WBS tree · [F3] milestones · [F4] status history · [F5] per-project module enablement/profile · [F6] setup wizard · [F7] currency/calendar/VAT/locale settings.
- **Removal impact:** catastrophic — almost everything is `project_id`-scoped.

### users — Identity, roles, access
- **Identity:** core · prefix=`/users`. **Entities:** `_user`(email, name, status, auth refs, business_role, capability_tier, project/site scopes, module_access) · `_role_preset` · `_access_grant`.
- **Key logic:** three-tier authZ — capability tier (viewer<editor<manager<admin) × business-role preset (13 personas) × per-user project/site/module scope; claims rehydrated from DB per request. **Features:** [F1] users · [F2] capability tiers · [F3] business-role presets · [F4] per-user project/site/module scope · [F5] session/JWT. **Removal:** no identity — catastrophic.

### admin — System administration
- **Identity:** core · prefix=`/admin`. **Features:** [F1] tenant/system settings · [F2] module registry admin · [F3] audit/console. **Removal:** no admin surface.

### teams — Teams, membership & entity visibility
- **Identity:** core · routes≈72 · **Depends on:** [projects, users].
- **Entities:** `_team`(project_id, name, is_default) · `_membership`(team_id, user_id, role) · `_visibility`(entity_type/id, team_id) · `_roster_member`(contact_id, trade, site_role, allocation_percent, certifications, resource_id).
- **Features:** [F1] teams · [F2] membership/roles · [F3] entity visibility grants (access-control input) · [F4] project roster. **Removal:** project-member visibility breaks (`verify_project_access` consults it).

### module_builder — Runtime/no-code modules
- **Identity:** core · routes≈22 · runtime-generated modules (files on disk, no fixed tables). **Features:** [F1] no-code/AI runtime module creation (preview→review-token→install, quarantine, upgrade, uninstall+drop-data). **Removal:** no runtime extensibility.

### onboarding — First-run provisioning
- **Identity:** core · no tables. **Features:** [F1] non-blocking first-run provisioning (presets/starter data). **Removal:** empty first-run.

### validation — Rule-set runner (foundation of the verification engine)
- **Identity:** core · routes≈32 · **Dependents:** the verification engine, every gated module.
- **Entities:** `_report`(target_type/id, rule_set, status, score, passed/warning/error_count, results).
- **Key logic:** runs a named rule set over any target and records findings; the substrate of `run_checks(context, rule_set)` (plan §5). **Features:** [F1] validation reports (rule-set runs over any target) · [F2] finding log (auditable). **Removal:** no central verification.

### forms — Templated forms/checklists
- **Identity:** business · routes≈30. `_template`(category, version, fields_data, tags) · `_submission`(template_snapshot, answers_data, status, result, linked_inspection_id). **Features:** [F1] versioned templates · [F2] snapshotted submissions · [F3] inspection linkage. **Removal:** no custom forms/checklists.

### notifications — In-app notifications
- **Identity:** core · routes≈30. `_notification`(user_id, type, entity_type/id, title/body keys, is_read, action_url) · `_preference`(event_type, channel, digest) · `_digest_queue` · `_webhook_target`(url, event_filter, secret). **Features:** [F1] in-app notifications · [F2] per-user channel prefs · [F3] digests · [F4] webhook targets. **Removal:** no notifications.

### saved_views — Saved cross-module views
- **Identity:** core · routes≈24. `_view`(entity_type, name, spec, share_scope, is_pinned) · `_run`(row_count, truncated, elapsed_ms, outcome). **Features:** [F1] saved views · [F2] run telemetry. **Removal:** no saved views.

### jobs / global_presence / client_errors / backup / uploads / resumable_uploads
- **jobs** — core, no tables (over `core/job_runner`): [F1] background-job surface (P6 import, exports, OCR).
- **global_presence** — core, WebSocket, no tables: [F1] who's online/where.
- **client_errors** — infra, no tables: [F1] client-side error sink.
- **backup** — core, no tables: [F1] export/import user-data backups.
- **uploads** — core, no tables: [F1] file intake. **resumable_uploads** — `_session`(filename, total/chunk size, received_chunks, storage_key, document_id, expires_at): [F1] chunked/resumable uploads (GB-scale dockets/scans).

### enterprise_workflows / approval_routes — generic approvals
- **approval_routes** — core, routes≈40 · **Dependents:** many. `_route`(target_kind, system_key) · `_step`(approver_role/user_id, mode, required_count, sla_hours) · `_instance`(target_kind/id, current_step, status, assignee) · `_step_state`(decision, comment) · `_delegation`. **Features:** [F1] configurable routes (role/user steps, quorum, SLA) · [F2] instances · [F3] step decisions · [F4] delegations.
- **enterprise_workflows** — enterprise, routes≈22. `_approval`(entity_type, name, steps, is_active) · `_request`(entity_id, current_step, status). **Features:** [F1] configurable approval workflows · [F2] requests. *(Overlaps `approval_routes`; pick one engine in the rebuild.)*

---

## 2 · Asset & Quality backbone

### asset_management — Asset hierarchy, Items & operations **(backbone)**
- **Identity:** core · prefix=`/assets` · **Depends on:** [projects, users, teams, validation] · **Dependents:** inspections, qms, construction_control, tasks, schedule, progress, documents, file_*, moc, wps, cutting.
- **Purpose:** decompose scope into a recursive **Site → Area → Asset(→sub-Asset…)→ RSW** tree, hang **typed Items** (valves/welds/structural) off assets, and anchor tasks, quality, files and MoC to it. Populated by **P6 import**, **Excel/CSV** or **manual**; exports progress + dates back to P6.
- **Entities — hierarchy & schema:**
  - `oe_asset_node`(parent_id self-FK, level[`site|area|asset|rsw`], is_sub_asset, path, depth, name, code, asset_tag, category_id, type_id, discipline_code, status, percent_complete, **external keys** external_source[`p6|excel|manual`]/external_id/wbs_code/activity_id/synced_at, planned_start/finish, budgeted_units, primary_resource_id, subcontractor_contact_id→contacts, custom_fields). *Constraints:* `rsw` parents only Items; `asset` may parent sub-`asset` and `rsw`; path/depth maintained on move.
  - `oe_asset_category` · `oe_asset_type`(category_id, name, applies_to[`asset|item`]) · `oe_asset_field_def`(type_id, key, data_type, options, unit, mandatory, constraints, p6_mandatory) · `oe_asset_field_value` · `oe_asset_layout`(type_id, layout_json — Form/Inspection-Designer output).
- **Entities — items:** `oe_asset_item`(asset_id→node level=asset, parent_item_id self-FK, item_type_id→type where applies_to=item, tag, spec_ref, discipline, rsw_id→node nullable, status, inspectable, custom_fields). *A weld is an Item with type `weld`; its weld-specific fields/rules live in `wps`.*
- **Entities — provisioning/ingestion:** `oe_asset_config_pack`(key, taxonomy_json, layouts_json, disciplines, itp_templates_json, p6_field_rules, is_builtin) · `oe_asset_import_job`(source[`p6_xer|p6_xml|excel|manual`], filename, status, parsed_summary, mapping, errors) · `oe_asset_p6_export`(kind[`progress|dates|full`], format[`xer|xml|csv`], storage_key, filter).
- **Endpoints:** tree CRUD + `POST /assets/nodes/{id}/move`; items CRUD; schema (categories/types/fields/layout); contextual inspections (`GET /assets/nodes/{id}/inspections` aggregating descendants); ingestion (`POST /assets/import` → job → `/apply` atomic); **P6 export** (`POST /assets/p6-export`, `GET …/{id}` download; mirrored under `integrations`); config packs (`POST /assets/config-packs/{key}/apply`).
- **Key logic / state (P6 round-trip, D5):** import parses `.xer`/XML → WBS top→Site/Area, activity groups→Assets, discipline bands→RSWs; carries WBS/Activity IDs as permanent external keys, durations, planned dates, budgeted units, activity codes; `review`→`apply`; idempotent on Activity ID. Excel/CSV wizard maps columns→4-level tree with line-by-line validation. Export emits progress actuals (% / earned units) + dates keyed by Activity ID — file-based, non-destructive; no live sync.
- **Asset-Details tabs (modular, designer-driven):** Overview · RSWs · Items · Inspections & ITPs · Files · Tasks · MOC.
- **Verification:** `asset.status_change` (required fields on status; children complete before parent), `asset.p6_fields` (WBS/Activity/dates/discipline mandatory when external_source=p6), `asset.import_validate` (parent-child + schema + mandatory).
- **Features:** [F1] recursive Site/Area/Asset/sub-Asset/RSW tree · [F2] typed Items · [F3] dynamic category/type/field schema · [F4] page/layout designer · [F5] modular asset tabs · [F6] P6 import · [F7] P6 export/round-trip · [F8] Excel/CSV import · [F9] manual build · [F10] external integration keys · [F11] config packs · [F12] contextual + global inspection anchoring.
- **Removal impact:** core backbone — tasks/quality/items lose their anchor.

### wps — WPS register, welder qualification & welds
- **Identity:** compliance · prefix=`/wps` · **Depends on:** [projects, contacts, asset_management, inspections, qms, construction_control, file_versions, validation] · **Dependents:** inspections (weld inspection), reporting (weld KPIs).
- **Purpose:** hold the WPS register, record who is qualified to each WPS and over what range, enforce that every weld references an approved WPS and a qualified welder, and gate acceptance on NDE — all through the engine.
- **Entities:** `oe_wps_record`(scope[`project|library`], code, welding_process, standard_ref, base_material_group, filler_material, positions, thickness/diameter min/max, joint_types, pwht, pqr_ref, revision, status[`draft|approved|withdrawn`], document_id, approved_by/at) · `oe_welder`(contact_id→contacts, stamp, employer_kind[`staff|subcontractor`], subcontractor_contact_id, status) · `oe_welder_qualification`(welder_id, wps_id, qualified_date, expiry_date, **continuity_last_welded_at**, qualified_range(positions/thickness/diameter/material), test_type, certificate_document_id, status[`valid|expired|suspended|revoked`], verified_by/at) · `oe_weld`(asset_item_id→item type=weld, weld_number, **wps_id required**, **welder_qualification_id required**, joint/size/thickness/diameter/material, nde_required, nde_types[VT/PT/MT/RT/UT], status[`fit_up|welded|nde_pending|accepted|rejected|cut_out`], welded_at) · `oe_weld_nde_result`(weld_id, nde_type, performed_at, technician_contact_id, result[`accept|reject`], report_document_id).
- **Endpoints:** WPS CRUD + `/approve` `/withdraw`; welders + qualifications (+verify/suspend/revoke; `GET /wps/qualifications?wps_id=&status=valid`); welds CRUD + `/nde` `/accept` `/reject` `/cut-out`; `GET /wps/welds/register` (export), `GET /wps/continuity-watch`.
- **State machine:** `fit_up → welded → nde_pending → accepted` / `rejected → cut_out → (re-weld) fit_up`.
- **Verification (enforced):** `welder.assign`/weld create — **R-WPS-1** approved WPS; **R-WPS-2** assigned qualification is for that WPS, `valid`, range covers the weld; **R-WPS-3** continuity within window at `welded_at`. `weld.accept` — **R-WPS-4** all required NDE = accept, ITP hold/witness released, no open NCR. Violations blocked with logged findings; rules are data.
- **Features:** [F1] WPS register · [F2] welders · [F3] welder qualifications (range/expiry/continuity/cert) · [F4] welds as Items requiring approved WPS + qualified welder · [F5] enforced welder↔WPS qualification · [F6] NDE results + acceptance gating · [F7] weld inspection via qms/inspections · [F8] weld register + continuity watch + export · [F9] versioned WPS/qualification documents.
- **Removal impact:** no welding quality control; welds become plain inspectable Items with no enforcement.

### construction_control — Quality/conformance control
- **Identity:** core · routes≈100 · **Depends on:** [users, projects, ncr, asset_management].
- **Entities (8):** `_acceptance_criterion`(standard_ref, method, acceptance_rule, nominal/tolerance) · `_inspection`(inspection_number, party_role, intervention_point, criterion_id, result, measured_value, raised_ncr_id) · `_element_ref`(asset_item_id/rsw_node_id, tag — *optional; BIM refs softened to plain tags*) · `_material_record`(cert_type/number, dop_number, ce/ukca_marking, batch/heat/lot, criterion_id, goods-receipt link, raised_ncr_id) · `_test_result`(material_record_id, test_method, lab_accreditation, measured_value, result) · `_asbuilt_record`(capture_method, instrument_calibration_ref, accuracy, deviation, valid_for_legal_record, signed) · `_hold_gate`(point_type, required_party_role, blocks_progress, released/waived) · `_handover_package`(completion_regime, gating_state, open_ncr_count, unreleased_hold_count, certificate_no, dossier_key).
- **Key logic:** ITP-style criteria → inspections/tests/material certs/as-builts (all raising NCRs); **hold gates block progress** until released (via `run_checks`); handover packages gate on open NCRs/holds.
- **Features:** [F1] acceptance criteria · [F2] inspections (party/intervention) · [F3] material records + certs (CE/UKCA/DoP) · [F4] test results (accredited labs) · [F5] as-built records · [F6] hold/witness gates · [F7] handover packages + dossier · [F8] Asset/Item element refs · [F9] NCR raising. **Removal:** no formal QA/conformance control.

### qms — Quality management (ITP/inspection/NCR/audit)
- **Identity:** extension · routes≈118 · **Depends on:** [projects, users, asset_management].
- **Entities (15):** `_itp_plan`(+`_itp_item`: control_point, acceptance_criteria, hold/witness_point, signatories), `_inspection`(+`_signature`, `_attachment`, `_hold_point_release`), `_ncr`(+`_ncr_action`: verification_method), `_punch_item`, `_audit`(+`_audit_finding`), `_itp_template`, `_audit_log`, `_calibration`(instrument, valid_until, certificate).
- **Features:** [F1] ITP plans/items · [F2] inspections (+signatures, holds) · [F3] NCRs + actions · [F4] punch items · [F5] audits + findings · [F6] ITP templates · [F7] instrument calibration · [F8] immutable audit log. **Removal:** no ISO-style QMS. *(Overlaps construction_control/ncr/inspections — resolve the dedup in the rebuild; recommend `qms` as the ITP/audit layer, `construction_control` as the conformance/hold-gate engine.)*

### inspections — QA/QC inspections (lightweight register)
- **Identity:** core · routes≈18 · **Depends on:** [users, projects, asset_management].
- **Entities:** `_inspection`(inspection_number/type, title, location, rsw_node_id/asset_item_id, inspector_id, inspection_date, status, result, checklist_data, geo_lat/lon). **Verification:** `inspection.close`. **Features:** [F1] inspections (checklist, result) · [F2] geo-stamp · [F3] Asset/RSW/Item anchor. **Removal:** no lightweight QA register.

### ncr — Non-Conformance Reports
- **Identity:** core · routes≈14 · **Dependents:** construction_control, qms, portal.
- **Entities:** `_ncr`(ncr_number, ncr_type, severity, root_cause(+category), corrective/preventive_action, status, schedule_impact_days, location(+geo), source[`internal|client`], linked_inspection_id, linked_asset_item_id/rsw_node_id, change_order_id). **Verification:** `ncr.signoff`. **Features:** [F1] NCRs (type/severity) · [F2] root cause + corrective/preventive · [F3] schedule impact · [F4] links (inspection/change/asset) · [F5] client-raised + client sign-off (via portal). **Removal:** others lose NCR raising.

### punchlist — Punch/snag
- **Identity:** core · routes≈30. `_item`(asset_item_id/rsw_node_id, external_ref, document_id, page, location_x/y, title, priority, status, assigned_to, due_date, trade, photos, resolution_notes, resolved/verified_at, reopen_history, geo_lat/lon). **Features:** [F1] punch items (drawing pin + geo) · [F2] assignment/priority/due · [F3] photos · [F4] resolve/verify + reopen history. **Removal:** no snag tracking.

### requirements — Requirements & quality gates
- **Identity:** core · routes≈68. `_set`(source_type, vocabulary, gate_status) · `_item`(entity, attribute, constraint_type/value, priority, verification_method, parent_requirement_id, linked_rsw_node_id/asset_item_id) · `_gate_result`(gate_number, status, score, findings) · `_deliverable`(deliverable_type, due_milestone_id). **Features:** [F1] requirement sets · [F2] requirement items (constraints) · [F3] scored quality gates · [F4] Asset/RSW links · [F5] deliverables. **Removal:** no requirements/gates.

### review_authority — External review cycles
- **Identity:** core · routes≈30. `_cycle`(authority_name/kind, submission_ref, pinned/current_document_version, status, due_at, sla_days, jurisdiction) · `_remark`(ordinal, section, norm_reference, classification, severity, status, response_text, repeat_of_id). **Features:** [F1] review cycles · [F2] classified remarks (+repeat tracking) + responses. **Removal:** no authority-review / hold-point-witness tracking (portal v3 scheduler feeds this).

### project_route — Work-type routing gate
- **Identity:** core · routes≈20. `_assessment`(work_type, criteria, determined_route, rationale, confidence, status). **Features:** [F1] work-type classifier gate (delivery/permit route). **Removal:** no routing gate.

### credentials — Professional credentials
- **Identity:** core · routes≈46. `_credential`(holder_name/kind/user_id, credential_type, discipline, authority, identifier, jurisdiction, issued/valid_until, notify_days_before, status, verified_by/at) · `_requirement`(credential_type, applies_to, is_blocking, grace_days). **Features:** [F1] credentials registry · [F2] requirements (blocking/grace) · [F3] expiry notifications · [F4] verification. *(General tickets; `wps` holds the weld-specific qualification layer.)* **Removal:** no credential compliance.

### signing — e-signature abstraction
- **Identity:** core · routes≈22. `_session`(document_ref, content_hash, provider/capability, signatory_map, status, expires_at) · `_signature`(signatory_name/role, signed_at, cert_subject/valid_until, content_hash). **Features:** [F1] e-sign sessions · [F2] signatures + attestation (cert/hash). *(Used by report/ITP co-sign and NCR sign-off in `portal`.)* **Removal:** no e-signing.

### source_data — Prerequisite-documents register
- **Identity:** core · routes≈30. `_document`(doc_type, owner, authority, identifier, issued/valid_until, shelf_life_days, blocks_schedule, status, superseded_by_id) · `_checklist_item`(label, required, doc_type, satisfied_by_id). **Features:** [F1] prerequisite-docs register (blocks schedule) · [F2] checklist satisfaction. **Removal:** no prerequisite-doc gating.

### authority_submission — Jurisdiction XML submissions
- **Identity:** core · routes≈24. `_profile`(jurisdiction, format_key, schema_version, root_element, field_spec) · `_submission`(payload, status, generated_xml, validation_report). **Features:** [F1] XML submission profiles · [F2] submissions (generate/validate XML). **Removal:** no authority XML factory.

### record_publishing — Publish & distribute a record
- **Identity:** core · no tables. **Features:** [F1] one-tap publish-a-record-and-distribute. **Removal:** no record publishing.

### compliance / compliance_ai / compliance_docs
- **compliance** — core, routes≈14. `_dsl_rule`(rule_id, name, severity, standard, definition_yaml, owner, is_active): [F1] user-authored validation DSL rules (feeds the verification engine).
- **compliance_ai** — core, no tables: [F1] DSL engine + NL rule builder (AI-assisted, human-confirmed).
- **compliance_docs** — core, routes≈14. `_doc`(doc_type, issuer, policy_number, coverage_amount, effective/expires, notify_days_before, status, attachment_document_id): [F1] insurance/permit/bond/cert tracker (+expiry). **Removal:** no custom compliance rules / doc tracking.

---

## 3 · Logistics

### contacts — Party master & people/company directory
- **Identity:** core · prefix=`/contacts` · **Dependents:** procurement, subcontractors, supplier_catalogs, wps, variations.
- **Entities:** `oe_contacts_contact`(contact_type[`client|contractor|staff|supplier|…`], is_platform_user, user_id, first/last_name, company_name, legal_name, vat_number, country_code, address, primary_email/phone, website, certifications, insurance, prequalification_status, qualified_until, payment_terms_days, currency_code, tags, module_tags, custom_properties, is_active) · `_note`(contact_id, author_id, body, at) · `_file`(contact_id, document_id, version) — *the people/company directory is folded in here (D7).*
- **Key logic:** one table for every party role (clients, contractors, **staff**, suppliers); company/person grouping; notes/interaction log; tags; versioned files. **No sales pipeline** (no leads/opportunities/stages/forecast).
- **Features:** [F1] contacts CRUD · [F2] company/person grouping · [F3] notes/interaction log · [F4] tags · [F5] versioned files · [F6] certifications/insurance/prequal fields · [F7] custom properties + module tags. **Removal:** core — all party-referencing modules break.

### site_inventory — On-site material metering
- **Identity:** business · prefix=`/site-inventory` · **Depends on:** [projects, asset_management, procurement, progress] · **Dependents:** cutting.
- **Entities:** `_location`(project_id, name, code, lat/long, address, is_active) · `_item`(project_id, name, sku, unit, rsw_node_id/asset_item_id, procurement_req_item_id, default_location_id, standard_unit_cost (mirror), reorder_point) · `_movement`(project_id, item_id, movement_type, quantity, unit_cost, location_id, to_location_id, rsw_node_id, goods_receipt_id, occurred_at, actor_id).
- **Key logic:** meters material on-site against RSW/Item and progress; movements tie to goods receipts. **Features:** [F1] site locations (geo) · [F2] site items linked to RSW/Item + procurement · [F3] movements (receipt/consume/transfer) · [F4] reorder points · [F5] consumption→progress tie. **Removal:** no on-site metering.

### procurement — Project procurement (basic)
- **Identity:** core · prefix=`/procurement` · **Depends on:** [users, projects, contacts] · **Dependents:** site_inventory, supplier_catalogs.
- **Entities:** `_requisition`(project_id, req_number, requester/approver_id, status, required_date, lead_time_days, estimated_delivery_date, po_id) · `_req_item`(quantity_requested/ordered/received/consumed, unit_cost (mirror), rsw_node_id) · `_po`(project_id, vendor_contact_id, po_number, po_type, issue/delivery_date, external_source/external_ref (ERP mirror), retention_percent (physical), status) · `_po_item`(quantity, unit, unit_rate (mirror), rsw_node_id, cost_category) · `_po_retainage_release` · `_goods_receipt`(po_id, receipt_date, delivery_note_number, status) · `_gr_item`(po_item_id, quantity_ordered/received/rejected, rejection_reason).
- **Key logic:** requisition→PO (internal or **ERP-mirrored**) against a project; GR draws down PO items and raises **gate receipt / OS&D** deltas; no monetary posting. **Verification:** `goods.receive`. **Features:** [F1] requisitions + approval · [F2] project POs (internal or ERP mirror) · [F3] goods receipts (+rejections/OS&D) · [F4] retainage hold/release (physical) · [F5] RSW tie. **Removal:** no project procurement.

### supplier_catalogs — Vendor management & stock (basic)
- **Identity:** extension · prefix=`/supplier-catalogs` · **Depends on:** [projects, users, procurement, contacts].
- **Entities (basic subset):** `_vendor`(code, name, legal_name, tax_id, contact_id, status, categories, rating) · `_catalog_item`(sku, mpn, gtin, commodity_code) · `_price_list`/`_catalog_entry`(vendor_sku, unit_price, min_order_qty, lead_time) · `_warehouse` · `_stock_balance`(on_hand/reserved) · `_stock_movement` · purchase requests → `procurement`.
- **Key logic:** vendor master + price lists + vendor stock; **purchase requests route to `procurement`**; goods receipts and stock. *(Dropped in the pivot: 3-way match, KYC, scorecards, Peppol, supplier invoices/POs.)*
- **Features:** [F1] vendor master · [F2] vendor catalogues + price lists · [F3] purchase requests → procurement · [F4] vendor stock (balances/movements) · [F5] commodity codes. **Removal:** no vendor catalogue; `procurement` still covers project POs.

### subcontractors — Subcontractor management (basic)
- **Identity:** business · prefix=`/subcontractors` · **Depends on:** [users, projects, contacts].
- **Entities (basic subset):** `_subcontractor`(contact_id, legal/trade_name, tax_id, trade_categories, insurance_expiry/doc, is_blocked, rating_score) · `_certificate`(cert_type, valid_until, revoked) · `_agreement`(project_id, external_contract_ref, retention_percent (physical), status, versioned files) · `_rating`(quality/hse/schedule/overall). *(Dropped: prequalification workflow, payment applications, lien waivers; contract FK → external ref.)*
- **Features:** [F1] subbie register (+block list) · [F2] certificates (insurance/licence) · [F3] agreements (external contract ref, physical retention, versioned files) · [F4] performance ratings. **Removal:** no subcontractor administration.

### resource_summary — Demand rollup
- **Identity:** core · routes≈10 · **Depends on:** [tasks, schedule_advanced].
- **Entities:** `_snapshot`(generated_at, currency, total_cost (mirror), line_count, payload).
- **Key logic:** rolls resource demand **from tasks / look-ahead** (not a bill of quantities) into a procurement-ready statement **with required-by dates**. **Features:** [F1] resource-demand rollup from tasks/look-ahead · [F2] required-by dates · [F3] procurement-ready export. **Removal:** no demand rollup.

### cutting — Cutting optimisation
- **Identity:** business · prefix=`/cutting` · **Depends on:** [projects, site_inventory, validation] · feeds `site_inventory`.
- **Purpose:** cutting plans for **bars (1-D)** and **sheets/plates (2-D)** from a known or custom cut list; measured yield/waste/cost; **offcut reuse**; on confirm, a verified site-inventory update.
- **Entities:** `oe_cutting_plan`(project_id, name, material_profile, stock_length_mm | sheet_w/h_mm, kerf_mm, optimisation_kind[`bar_1d|sheet_2d`], source[`scope|custom`], status[`draft|optimised|confirmed|cancelled`], stock_count, used/yield/waste/offcut_reuse_pct, unit_cost (mirror), confirmed_at/by) · `_cut_list_item`(ref, profile, length | w/h, quantity, from_offcut_id) · `_stock_bar`(stock_index, source_offcut_id, cuts_json, offcut/waste_length_or_area) · `_offcut`(profile, dims, location_id→site_inventory, reuse_state[`available|reserved|consumed`], created_from_stock_bar_id) · `_inventory_link`(site_inventory_movement_id, kind[`consume_stock|add_offcut`], confirmed).
- **Endpoints:** plans CRUD; set/import cut list; `POST …/optimise`; offcut register (`GET /cutting/offcuts?state=available`); `POST …/confirm` → `cutting.confirm` then site-inventory movements (idempotent).
- **State/logic:** `draft→optimised→confirmed`/`cancelled`. Optimiser: 1-D first/best-fit-decreasing for bars, 2-D guillotine/shelf nesting for sheets, honours kerf, **draws from available offcuts before new stock**. On confirm, posts net stock consumption + new offcuts to `site_inventory` under a transaction.
- **Verification (`cutting.confirm`):** stock on-hand ≥ required, cut list non-empty, every custom cut ≤ stock/sheet size, status `optimised`.
- **Features:** [F1] known cut list · [F2] custom cut list · [F3] 1-D bar optimisation · [F4] 2-D sheet optimisation · [F5] kerf handling · [F6] measured yield/waste/cost · [F7] offcut register + reuse · [F8] confirmed site-inventory update · [F9] reusable-offcut search. **Removal:** no cutting plans; `site_inventory` unaffected.

### equipment — Equipment & fleet
- **Identity:** business · routes≈96. `_type`(service intervals) · `_equipment`(serial, ownership, status, hour_meter, odometer) · `_telemetry`(fuel/meter/geo/engine) · `_maintenance_schedule`(trigger, next_due) · `_work_order`(technician, cost) · `_inspection`(valid_until, certificate) · `_rental`(internal rate/day/hour) · `_fuel_log` · `_parts_log` · `_damage_report`. **Features:** [F1] equipment register + types · [F2] telemetry · [F3] maintenance schedules + work orders · [F4] inspections/certs · [F5] internal rental billing · [F6] fuel/parts logs · [F7] damage reports. **Removal:** no fleet management.

### site_logistics — Logistics & delivery
- **Identity:** business · routes≈48 · **Depends on:** [projects, users, asset_management]. `_gate`(open/close_time, capacity_per_slot) · `_laydown_zone` · `_delivery`(gate_id, supplier_name, vehicle_type, materials_desc, window_start/end, status, po_ref) · `_delivery_line`(rsw_node_id/asset_item_id, quantity, unit). **Features:** [F1] gates (slots/capacity) · [F2] laydown zones · [F3] deliveries (booking windows) · [F4] delivery lines (Asset/RSW-linked). **Removal:** no logistics scheduling.

---

## 4 · Execution

### schedule — Field programme (P6-synced, RSW-linked)
- **Identity:** core · prefix=`/schedule` · **Depends on:** [projects, asset_management] · **Dependents:** progress, costmodel(physical), commissioning, variations (dates), portal(read).
- **Entities:** `_schedule`(name, type, start/end_date, data_date, status, external_source[`p6|manual`], external_id, source_file_ref, synced_at, sync_direction[`import|export|none`]) · `_activity`(parent_id, wbs_code, start/end_date, duration_days, progress_pct, activity_type, dependencies, rsw_node_ids, floats, is_critical, constraint_type/date, remaining_duration, budgeted/installed_units (physical), calendar_id, revision, assignee_id, external_activity_id, external_wbs_id) · `_work_order`(rsw_node_id, asset_item_ids, assigned_to, planned/actual_start/end, crew_hours_planned/actual, status) · `_relationship`(predecessor/successor_id, type, lag_days) · `_baseline`(snapshot_data, is_active, source[`p6_import|internal`]) · `_progress`(activity_id, progress_pct, actual_start/finish — mirror from `progress`) · `_eac_link`(task_id, rule_id, predicate_json) · `_p6_import_job`(file_ref, format, status, parsed_activities/relationships, mapping_report, unmatched_wbs) · `_p6_export`(scope, generated_file_ref, fields, data_date, status).
- **Endpoints:** `POST /schedule/import-p6` · `GET …/{id}` · `POST /schedule/{id}/export-p6` · `GET …/download` · `POST /activities/{id}/link-rsw` · `GET /rsw/{node_id}/activities`.
- **Key logic:** P6 import matches WBS→Asset/RSW (idempotent on Activity ID; unmatched surfaced); CPM computed internally (floats/critical/constraints/calendars) so the board stays live between syncs; export writes progress actuals only, keyed on Activity ID (non-destructive). No cost on activities — `crew_hours` replaces money.
- **Verification:** `schedule.p6_import`, `schedule.p6_export`.
- **Features:** [F1] schedules + WBS activities · [F2] CPM · [F3] relationships (lag) · [F4] calendars · [F5] baselines (incl. P6) · [F6] work orders (RSW/Item, crew-hours) · [F7] field progress mirror · [F8] Asset/RSW links · [F9] EAC rule links (physical) · [F10] P6 import · [F11] P6 export round-trip. **Removal:** delivery backbone; progress/EVM lose structure.

### schedule_advanced — Last Planner / Takt / delay analysis
- **Identity:** business · routes≈208 · **Depends on:** [users, projects, schedule].
- **Entities (18):** `_master_schedule`, `_phase_plan`, `_look_ahead`, `_constraint` (make-ready), `_weekly_plan`(ppc_percent), `_commitment`(promised/actual_qty), `_rnc` (reasons-for-non-completion), `_baseline`(+`_baseline_delta`), `_calendar`, `_takt_schedule`(+`_takt_location`/`_takt_activity`), `_delay_analysis`(method, windows, entitlement_days, eot_claim_id)(+`_delay_event`, `_fragnet`, `_delay_window`). *All lodge at **RSW**.*
- **Features:** [F1] master schedule · [F2] phase/pull planning · [F3] look-ahead + constraints · [F4] weekly plans + commitments + PPC · [F5] RNC tracking · [F6] takt planning · [F7] baselines + variance deltas · [F8] forensic delay analysis (windows/fragnets/apportionment → EOT). **Removal:** no LPS/takt/delay analysis.

### progress — Physical progress (earned quantity)
- **Identity:** core · routes≈18 · **Depends on:** [projects, asset_management, schedule] · **Dependents:** costmodel(physical), eac(physical), schedule (actuals→P6), portal(read).
- **Entities:** `_entry`(rsw_node_id/asset_item_id, activity_id, period_label, earned_quantity + quantity_unit, percent_complete, crew_hours, recorded_by/at, geo, photos, method[`units|milestone|percent`], status[`draft|submitted|verified`]) · `_plan`(rsw_node_id/asset_item_id, period_label, planned_pct, planned_quantity) · `_milestone`(rsw_node_id, label, weight_pct, reached_at, evidence_ref) · `_quantity_claim`(entry_id, claimed/verified_quantity, verifier_id, verified_at, finding_ref).
- **Key logic:** books **earned quantity** against RSW/Item; % derived from earned/planned or milestone weights; **caps at anchor scope**; feeds P6 export and physical EV.
- **Verification (`rsw.complete`):** no open blocking Items/NCRs; earned ≤ planned; a weld Item to complete is gated by R-WPS-4.
- **Features:** [F1] progress by earned quantity (geo+photo) · [F2] planned-vs-actual (qty + %) · [F3] milestone/rules-of-credit earning · [F4] claim verification vs inspection acceptance · [F5] crew-hours capture · [F6] scope cap. **Removal:** no physical progress → no earned value, no P6 actuals, no PPC input.

### tasks — Generic tasks
- **Identity:** core · routes≈32. `_task`(task_type, title, checklist, responsible_id, persons_involved, rsw_node_id/asset_item_id, due_date, milestone_id, meeting_id, status, priority, is_private, depends_on). **Features:** [F1] tasks (checklist, deps, Asset/meeting links, private). **Removal:** no generic tasks.

### daily_diary — Full site diary
- **Identity:** business · routes≈108. `_diary`(diary_date, site_supervisor_id, weather_summary, labour/equipment_count, status, closed_at/by, signatures, pdf_export_ref) · `_weather` · `_entry`(entry_type, time, title, source_module/ref, author_id, photo_ids) · `_photo`(taken_at, geo, is_drone) · `_video` · `_drone_survey` · `_reality_capture` · `_archive_signature`. **Features:** [F1] diary + close/sign · [F2] weather · [F3] timeline entries (cross-module) · [F4] photos (geo/drone) · [F5] video · [F6] drone surveys · [F7] reality capture · [F8] signed archive. **Removal:** no full diary.

### field_diary — Field-worker diary (mobile/offline)
- **Identity:** core · routes≈44. `_entry`(author_id, entry_date, weather, headcount, notes_md, field_source, status, labour_published_at) · `_activity`(type, hours, location) · `_attachment` · `oe_field_module_grant`(user_id, module_key) · `_magic_link`(phone, token/pin hash) · `oe_field_sync_ledger`(client_op_id, op_kind, result_type/id) · `_session`. **Key logic:** mobile persona — magic-link/PIN auth, per-module grants, **idempotent offline sync ledger**. **Features:** [F1] field entries · [F2] activities (hours) · [F3] attachments · [F4] magic-link + PIN · [F5] per-module grants · [F6] offline sync ledger · [F7] approval → labour publish. **Removal:** no field-worker mobile capture.

### field_time — Field time capture
- **Identity:** business · routes≈38. `_timesheet`(reference, date, status, reverses_id, working_time_regime) · `_line`(resource_id, equipment_id, hours, cost_code, rsw_node_id, is_daywork, variation_id, daywork_sheet_id, started/ended_at, break_minutes, employer_kind, employer_subcontractor_id). **Key logic:** keeps hours operationally; **exports hours to corporate payroll** (no internal payroll batch). **Features:** [F1] timesheets (submit/approve/reverse) · [F2] lines (resource/equipment hours, RSW-coded) · [F3] daywork/variation tagging · [F4] subcontractor-employer split · [F5] payroll export. **Removal:** no field time.

### fieldreports — Field reports
- **Identity:** core · routes≈58. `_report`(report_date/type, weather, workforce, equipment_on_site, work_performed, delays(+hours), visitors, deliveries, safety_incidents, materials_used, photos, signature, status, approved_by) · `_workforce`(worker_type, company, headcount, hours, rsw_node_id) · `_equipment`(hours operational/standby/breakdown) · `_template`. **Features:** [F1] daily field reports · [F2] workforce log · [F3] equipment log · [F4] weather/delays/deliveries · [F5] templates · [F6] sign + approve. **Removal:** no field reporting; physical EVM loses an hours source.

### voice — Voice site notes
- **Identity:** core · no tables. **Features:** [F1] spoken/typed site-note → structured, human-confirmed draft. **Removal:** no voice capture.

### phonelog — Call/communication log
- **Identity:** core · ⟦structural⟧. `_call`(project_id, direction, party_contact_id, subject, notes, at, linked_entity). **Features:** [F1] call/communication log linked to records. **Removal:** no call log.

### site_prep — Pre-construction mobilisation
- **Identity:** business · routes≈20. `_plan`(target_start_date, status) · `_item`(category, title, status, responsible_party, due_date, is_gate). **Features:** [F1] site-prep plan · [F2] readiness items/gates. **Removal:** no mobilisation checklist.

### site_supervision — Supervision register
- **Identity:** core · routes≈38. `_visit`(planned/actual_date, visitor, discipline, status, summary, photos) · `_entry`(observation, category, links_to_change_ref, resolved_at). **Features:** [F1] supervision visits · [F2] observations (+link to change) · [F3] resolution. **Removal:** no supervision register.

### commissioning — Cx
- **Identity:** business · routes≈46. `_system`(system_type, tag, location, status, commissioned_at/by) · `_checklist`(+`_item`: status, verified_by/at) · `_issue`(severity, status, resolution). **Features:** [F1] systems · [F2] checklists + items · [F3] issues · [F4] sign-off. **Removal:** no Cx tracking.

### closeout — Handover & closeout
- **Identity:** core · routes≈26 · **Depends on:** [users, projects, documents]. `_package`(status, checklist_template, required/delivered_slot_count, completeness_pct) · `_slot`(slot_key, category, discipline, is_required, source_kind, generated_artifact) · `_binding`(slot_id, document_id, external_url, is_verified, suggested_by_ai, ai_confidence). **Features:** [F1] closeout packages (templated slots) · [F2] slot bindings · [F3] completeness tracking · [F4] AI slot suggestions (confirmed) · [F5] verification. **Removal:** no handover pack assembly.

### defects_liability — DLP / warranty register
- **Identity:** business · routes≈22. `_warranty`(reference, element_description, subcontractor_id, warranty_type, handover/start/end_date, warranty_months, dlp_end_date, external_contract_ref) · `_defect`(warranty_id, severity, raised/due/rectified_date, responsible_party, punchlist_id, ncr_id). **Features:** [F1] warranties (DLP windows) · [F2] defects (→ punchlist/NCR) · [F3] retention-release tie (physical). **Removal:** no post-handover warranty register.

### temporary_works — TW governance
- **Identity:** business · routes≈20. `_item`(tw_type, design_check_category, designer/checker/twc roles, required_load/strike_date, design_due_date, design/check_certificate_document_id, schedule_activity_id) · `_permit`(permit_type, status, valid_from/to, inspection_id, prereq_design_check_accepted, prereq_inspection_passed). **Features:** [F1] TW items (roles) · [F2] design-check categories · [F3] load/strike dates · [F4] permits (prereq-gated) · [F5] schedule links. **Removal:** no TW governance.

### timeline / deadlines
- **timeline** — core, thin view: [F1] lightweight project timeline.
- **deadlines** — core, routes≈4 · **Depends on:** [notifications]: [F1] deadline register · [F2] notification on due. **Removal:** no deadline alerts.

### prefab — Off-site / DfMA
- **Identity:** business · routes≈30. `_unit`(ref, unit_type, status, target_install_date, drawing_ref, asset_item_id, rsw_node_id) · `_production_event`(stage, from_stage, at). **Features:** [F1] prefab units (Asset/Item-linked) · [F2] production stage events. **Removal:** no offsite/DfMA tracking.

### resources — Resource planning
- **Identity:** business · prefix=`/resources` · routes≈108 · **Depends on:** [users, projects].
- **Entities:** `_resource`(code, name, resource_type, home_project_id, contact_id, default_cost_rate (mirror), capacity_percent, status) · `_skill`/`_resource_skill`(level, expires_at) · `_certification`(valid_until) · `_availability_window`(recurrence_rule) · `_assignment`(project/task/activity/work_order_id, start/end_at, allocation_percent, units, status) · `_resource_request`(required_skills, quantity, priority, fulfilled_assignment_id) · `_resource_link`.
- **Features:** [F1] resources (crews/plant/labour) · [F2] skills + levels · [F3] certifications · [F4] availability windows · [F5] assignments · [F6] requests + fulfilment · [F7] links. *(Hours flow out via `field_time` export.)* **Removal:** no resource planning.

### safety — Core safety
- **Identity:** core · routes≈34. `_incident`(incident_number/type, severity, injured_person_details, treatment_type, days_lost, root_cause, osha_recordable/case_number, days_away/restricted) · `oe_hse_corrective_action`(assigned_to, due_date, verified_by/at) · `_observation`(type, severity, likelihood, risk_score, immediate/corrective_action). **Features:** [F1] incidents (+OSHA) · [F2] corrective actions · [F3] safety observations (risk score). **Removal:** no safety tracking.

### hse_advanced — Full HSE suite
- **Identity:** business · routes≈162. `_incident_investigation` · `_jsa`(+`_jsa_template`) · `_ptw`(permit_type, jsa_id, prerequisites, closure_checklist) · `_toolbox_talk`(+`_attendance`, `_topic`) · `_ppe_issue`(valid_until, returned) · `_audit`(+`_audit_finding`) · `_capa`(five_whys, effectiveness_verified) · `_certification`. **Features:** [F1] incident investigations · [F2] JSAs + templates · [F3] permits-to-work · [F4] toolbox talks + attendance + topics · [F5] PPE issue/return · [F6] audits + findings · [F7] CAPA · [F8] certifications. **Removal:** no enterprise HSE; `safety` covers basics.

---

## 5 · Documents & CDE

### documents — Document management / CDE / **Virtual File System**
- **Identity:** core · prefix=`/documents` (VFS under `/documents/vfs/*`) · **Depends on:** [projects, file_versions, file_references, uploads, resumable_uploads, users, teams, signing] · **Dependents:** file_*, closeout, plan_room, workpacks, awp, AI index, every module that files a document.
- **Entities:** `_document`(name, category, file_size, mime_type, file_path [flat object-store UUID key], version, uploaded_by, tags, cde_state, suitability_code, revision_code, drawing_number, is_current_revision, parent_document_id, security_classification, discipline, asset_links, **folder_id→`_folder`**, **is_orphaned**, **source_module**) · `_photo`(gps, taken_at) · `_sheet`(page/sheet number, discipline, revision chain) · `_asset_link`(asset_node_id/asset_item_id, link_type, region_bbox) · **VFS:** `_folder`(parent_id, name, folder_type[`system_preset|user_personal|custom`], owner_user_id, versioning_enabled, path, is_immutable) · `_file_link`(document_id, target_type, target_id, relation, link_metadata) · `_share`(folder/document, user/role, permission[`view_download|edit_contribute`], external_token, password_hash, expires_at).
- **Key logic:** CDE states + suitability/revision codes; drawing sheets with revision chains; files via the storage service (never raw paths); the AI vector index embeds this row's metadata. **VFS (folded in):** a rename-safe logical folder tree decoupled from the flat object-store key (rename = DB-only); a **routing resolver** (`documents.route_and_store`) that files module uploads into the pre-set `/Site/Asset/RSW` scaffold with a standardised name; polymorphic `_file_link` entity associations (target delete → link removed + `is_orphaned=true`, never destroyed); the **orphan engine** (no link / target gone / floating) with reassignment; personal `/Users/{name}` lockers + role/external shares (external tokens via `portal`/`signing`); a per-folder **versioning toggle** that forks a `file_versions` revision (ON) or overwrites in place (OFF). Revisions/checksum stay in `file_versions`; entity-link mechanics build on `file_references`.
- **Verification:** `document.route`, `document.share`, `document.immutable`.
- **Features:** [F1] documents + categories · [F2] CDE states + suitability/revision codes · [F3] drawing sheets (revision chain) · [F4] photos (geo) · [F5] Asset/Item links · [F6] security classification/discipline · [F7] versioning pointer · **[F8] virtual folder tree (rename-safe) · [F9] pre-set structure generator · [F10] routing resolver · [F11] polymorphic entity links · [F12] orphan detection + reassignment · [F13] personal lockers + granular/external sharing · [F14] per-folder versioning toggle (over `file_versions`) · [F15] controlled-folder hooks into `file_approvals`/`signing`**. **Removal:** core — the `/files` hub; dropping just the VFS features (F8–F15) falls back to a flat file hub.

### file_* subsystem — each adds one capability to `/files`
All `core`, independently removable, depend on [projects, users, documents]:
- **file_versions** — `_file_version`(file_kind/id, version_number, canonical_name, previous_version_id, is_current, checksum): [F1] version history.
- **file_approvals** — `_file_stamp_template` · `_file_approval_workflow`(status, stamped_artifact_path) · `_file_approval_step`(approver, decision): [F1] multi-step approval + [F2] stamps.
- **file_comments** — `_file_comment`(parent_id, body, page_number, anchor_x/y, resolved) · `_mention`: [F1] threaded file comments + [F2] @mentions + [F3] anchored/resolve.
- **file_tags** — `_file_tag`(name, color, category) · `_assignment`: [F1] tags + [F2] assignment.
- **file_search** — `_file_search_index`(content_text, page_count, ocr_engine, language, indexed_at): [F1] full-text/OCR content index (Postgres tsvector) — **OCRs dockets/daywork/receipts** for the claims/search layer.
- **file_saved_views** — `_file_saved_view`(filter_json, is_pinned, is_shared): [F1] Smart Folders / saved filters.
- **file_favorites** — `_file_favorite`(file_kind/id, pinned): [F1] pins.
- **file_references** — `_file_reference`(target_type/id, relation) · `_file_naming_violation`(rule_set, violation_codes): [F1] file↔record links + [F2] naming-rule violations.
- **file_distribution** — `_file_distribution_list`(+member), `_subscription`(notify_on): [F1] distribution lists + [F2] subscriptions/notify + [F3] cross-project search.
- **file_transmittals** — `_file_transmittal`(number, reason_code, status, cover_sheet_path)(+item/recipient ack): [F1] formal file issue + [F2] recipients/ack.
- **file_trash** — `_file_trash`(original_kind/id, payload_json, retention_days, restore_token): [F1] soft-delete + [F2] restore + [F3] retention/purge.

### markups — Markups & annotations
- **Identity:** core · routes≈38. `_markup`(document_id, file_version_id, page, type, geometry, text, color, author_id, assignee_id, status, measurement_value/unit, stamp_template_id, linked_asset_item_id, layer) · `_scale_config`(pixels_per_unit, calibration_points) · `_stamp_template` · `_comment`. **Features:** [F1] markups/annotations · [F2] scale calibration + measurement · [F3] stamp templates · [F4] markup comments · [F5] Asset/Item link · [F6] assignee/status. **Removal:** no drawing markup.

### plan_room — Controlled current drawings
- **Identity:** business · routes≈6 · **Depends on:** [projects, users, documents]. `_plan_pin`(document_id, page, x, y, note, photo_ref, file_version_id). **Features:** [F1] plan-room pins on current drawings. **Removal:** no plan-room pinning.

### transmittals — Document-issue tracking
- **Identity:** core · routes≈20 · **Depends on:** [users, projects, contacts]. `_transmittal`(number, subject, sender_org_id, purpose_code, issued_date, response_due_date, status, is_locked)(+recipient: action_required/ack/response; +item: document_id, revision_id). **Features:** [F1] transmittals (purpose-coded) · [F2] recipients (+ack) · [F3] document items (+revision) · [F4] lock. **Removal:** no issue tracking (distinct from file_transmittals).

### correspondence — Project correspondence log
- **Identity:** core · routes≈14 · **Depends on:** [users, projects, contacts]. `_correspondence`(reference_number, direction, subject, from/to_contact_ids, date_sent/received, type, linked_document/transmittal/rfi_ids, status, response_required_by, external_contract_ref). **Features:** [F1] correspondence log (in/out) · [F2] contract-clause ref (text/external) · [F3] links · [F4] response tracking. **Removal:** no correspondence register.

---

## 6 · Collaboration

### collaboration / collaboration_locks
- **collaboration** — core, routes≈14 · **Depends on:** [users]. `_comment`(entity_type/id, author_id, text, comment_type, parent_comment_id, edited_at, is_deleted) · `_mention` · `_viewpoint`(entity_type/id, viewpoint_type, data, comment_id): [F1] threaded comments on any entity (entity-URI) · [F2] @mentions · [F3] viewpoints.
- **collaboration_locks** — core, routes≈12. `_collab_lock`(entity_type/id, user_id, locked_at, heartbeat_at, expires_at): [F1] heartbeat edit locks. **Removal:** no cross-entity commenting / edit-lock protection.

### meetings — Meetings & minutes
- **Identity:** core · routes≈58. `_meeting`(number/type, title, date, location, chairperson_id, attendees, agenda_items, action_items, minutes, status, series_id, recurrence_rule) · `_attendance`(checked_in_at, signature_image_path) · `_action_item`(owner_id, due_date, status, closed_in_meeting_id) · `_minutes`(content, issued/distributed). **Features:** [F1] meetings (+series/recurrence) · [F2] attendance (+signature) · [F3] agenda · [F4] action items (cross-meeting close) · [F5] minutes (issue/distribute). *(Feeds portal v3 visit scheduling.)* **Removal:** no meeting/minutes tracking.

### rfi — Requests for Information
- **Identity:** core · routes≈36 · **Depends on:** [users, projects]. `_rfi`(rfi_number, subject, question, raised_by, assigned_to, status, ball_in_court, official_response, responded_by/at, schedule_impact(+days), date_required, response_due_date, priority, discipline, linked_drawing_ids, change_order_id). **Features:** [F1] RFIs (Q→official response) · [F2] ball-in-court · [F3] schedule impact · [F4] drawing/change-order links · [F5] due dates/priority. **Removal:** no RFI register.

### submittals — Submittal register
- **Identity:** core · routes≈30 · **Depends on:** [users, projects]. `_submittal`(submittal_number, title, spec_section, submittal_type, status, ball_in_court, current_revision, submitted_by_org, reviewer_id, approver_id, date_submitted/required/returned, linked_rsw_node_ids/asset_item_ids). **Features:** [F1] submittals (spec-section) · [F2] review/approve cycle + ball-in-court · [F3] revisions · [F4] Asset/RSW links. **Removal:** no submittal management.

### interface_management — Multi-party interfaces
- **Identity:** business · routes≈20. `_interface`(owner/accepter_party+subcontractor_id, discipline_from/to, work_package_from/to, interface_type, status, need_by_date, agreed/closed_date, rfi_id, schedule_activity_id) · `_action`(action_party, due_date, status). **Features:** [F1] interface register (multi-party) · [F2] actions. **Removal:** no interface coordination.

---

## 7 · Integrations & ingestion

### integrations — Outbound webhooks, connectors & the P6/ERP ingestion surface
- **Identity:** core · routes≈26. `_webhook`(url, secret, events, failure_count) · `_delivery`(payload, status_code, duration_ms) · `_config`(integration_type, config, events) · staging: P6 raw/parsed, PO-line mirrors, safety KPI snapshots.
- **Ingestion surface:** `POST /integrations/p6/import`, `GET /integrations/p6/export`, `POST /integrations/purchasing/po-lines`, safety KPI sync — idempotent batch/webhook endpoints.
- **Features:** [F1] outbound webhooks (+delivery log) · [F2] chat connectors (Teams/Slack/Telegram) + calendar feeds · [F3] **P6 import/export surface** · [F4] **PO-line ingest** (for OS&D three-way match) · [F5] safety KPI sync. **Removal:** no external integrations.

### connectors — Data-source connectors
- **Identity:** core · routes≈8. `_source`(kind, name, root_path, enabled, last_synced_at, last_result). **Features:** [F1] data-source connectors. **Removal:** no connector sources.

### inbound_email / inbound_capture
- **inbound_email** — controls, no tables: [F1] inbound email intake. **inbound_capture** — integration, no tables: [F1] inbound capture gateway. **Removal:** no inbound ingestion.

### data_import — Bulk import wizard
- **Identity:** core · routes≈22. `_job`(target, filename, status, encoding, delimiter, mapping, summary, row_count) · `_record`(entity, record_id, action, row_no) · `_preset`(target, mapping). **Features:** [F1] CSV/XLSX import (map→validate→dry-run→commit) · [F2] per-row audit (undo) · [F3] mapping presets. **Removal:** no bulk import.

### pipelines — Data pipelines
- **Identity:** core · routes≈18. `_pipeline`(graph, policy, is_published, version) · `_run`(graph_snapshot, trigger, job_run_id) · `_node_state`(node_type, status, inputs, output, took_ms). **Features:** [F1] pipeline builder (node graph) · [F2] runs + per-node state. **Removal:** no data pipelines.

---

## 8 · AI & search

### erp_chat — Tool-calling assistant (operational/claims)
- **Identity:** core · routes≈16. `_session`(user_id, project_id, title) · `_message`(role, content, tool_calls, tool_results, renderer, tokens, cache_hit, latency_ms) · `_turn_feedback` · `_action`(action_type, status, payload, preview, confidence, rationale, target_entity, before_state, decided_by/at, applied_entity, result, reverted_by).
- **Key logic:** tool-calling agent; **mutations are proposed → human-confirmed → applied → revertible** (`_action`, `source="ai"`); operational tools: `get_open_constraints`, `get_po_delivery_status`, `check_ppc_reliability`, `summarize_claims_timeline`.
- **Features:** [F1] chat sessions/messages · [F2] tool-calling · [F3] rich renderers · [F4] propose→confirm→apply→revert actions · [F5] turn feedback · [F6] token/cache/latency metrics. **Removal:** no assistant.

### ai_agents / search / retrieval
- **ai_agents** — core, routes≈56. `_run`(agent_name, status, trigger_source, user_input, final_output, iterations, trust) · `_step`(role, content, token_count) · `_feedback` · `_custom`(display_name, system_prompt, category, guided, automation): [F1] agent runs (trust-scoped) · [F2] step trace · [F3] feedback · [F4] custom user-defined agents.
- **search** — core, no tables: [F1] unified cross-collection semantic search (vector + SQL two-track over all `oe_*` collections).
- **retrieval** — core, routes≈12. `_saved_search`(label, party, record_type, date range, entity, signature, use_count): [F1] retrieval/RAG layer + [F2] saved searches. **Removal:** chat/search lose RAG; no global semantic search.

---

## 9 · Defense & Claims

### variations — Field measurement & change capture (re-anchored)
- **Identity:** business · routes≈160 · **Depends on:** [projects, asset_management, progress] · **Dependents:** claims_evidence, portal(future co-sign), external commercial system.
- **Scope:** captures the **physical change on site** and exports it to the corporate commercial system for pricing & final account; the money half is **mirrored, not owned**.
- **Entities (12, re-anchored):** `_notice`(ref, title, status, raised_by, external_source/external_ref) · `_request`(classification, agreed_cost_impact (mirror), contract_clause_ref (text), rsw_subtree_ref/asset_item_refs) · `_asset_qty_trace`(change_kind, rsw_node_id/asset_item_id, qty_before/after) · `_order`(+`_cost_impact` mirror, `_schedule_impact` owned → links schedule) · `_site_measurement`(rsw_node_id/asset_item_id, measured_quantity, unit, owner_signature_ref, photos, geo — **owned**) · `_daywork_sheet`(+`_daywork_line`: labour/plant/material qty & hours owned; rates mirror) · `_disruption_claim`(baseline/impacted_productivity from costmodel, labour_hours_lost — owned) · `_eot_claim`(tia_delta_days, delay_analysis_id → schedule_advanced — owned) · `_final_account`(totals, retention — **read-only mirror**).
- **Endpoints:** `POST /variations/{id}/measure` (signed site measurement) · `POST /variations/{id}/export` (push qty+daywork+evidence to commercial) · `GET /variations/{id}/mirror` (pull priced values). Pricing endpoints are read-through to the mirror.
- **Verification:** `variation.measure` (measured qty references a live RSW/Item; signed by an authorised owner), `variation.export`.
- **Features:** [F1] variation notices (+external ref) · [F2] requests (Asset/RSW trace, clause text) · [F3] variation orders (schedule owned; cost mirror) · [F4] signed site measurements on RSW/Item · [F5] daywork sheets/lines · [F6] disruption claims (physical productivity) · [F7] EOT claims (TIA) · [F8] export to commercial + priced mirror. **Removal:** no field change capture; claims lose physical evidence.

### changeorders — Client change control
- **Identity:** core · routes≈38. `_order`(code, reason_category, status, schedule_impact_days, variation_type, cost_basis, approved_amount (mirror), time_impact/approved_time_days, ball_in_court, linked_po/rfi_ids) · `_item`(change_type, original/new_quantity, qty_delta) · `_approval`(step_order, approver, decision). **Features:** [F1] change orders · [F2] items (qty deltas) · [F3] multi-step approval · [F4] schedule+time impact · [F5] PO/RFI links. **Removal:** no change control.

### moc — Management of Change
- **Identity:** project_controls · routes≈26. `_entry`(code, change_category, risk_level, proposed/reviewed/decided/implemented_by+at, schedule_delta_days, status, variation/change_order links, ball_in_court) · `_impact`(impact_area, severity, schedule, mitigation). **Features:** [F1] MoC entries (propose→review→decide→implement) · [F2] impacts · [F3] variation/change-order links. **Removal:** no MoC.

### change_intelligence — Cross-data change detection
- **Identity:** controls · no tables (engine). **Features:** [F1] cross-data change detection/analysis (feeds claims binders). **Removal:** no change intelligence.

### claims_evidence — Claims/dispute evidence packs
- **Identity:** controls · no tables (assembler). **Features:** [F1] claims/dispute evidence-pack assembly (from productivity, delay windows, diaries, late-receipts, MoC). **Removal:** no evidence packs.

### reconciliation — Cross-record linking
- **Identity:** controls · routes≈6. `oe_record_link`(project_id, left_type/id, right_type/id, relation, confidence, status). **Features:** [F1] manual record links · [F2] candidate suggestion/scoring · [F3] evidence threads. **Removal:** no cross-module manual links.

### cost_recovery — Back-charges & apportionment (physical)
- **Identity:** controls · routes≈18. `_back_charge`(…) · `_apportionment`(…). **Key logic:** back-charges + apportionment + recovery ledger/performance (project + portfolio); amounts referenced via external mirror where monetary. **Features:** [F1] back-charges · [F2] apportionment · [F3] recovery ledger · [F4] recovery performance. **Removal:** no back-charge recovery.

---

## 10 · Controls & Insights

### costmodel (physical) — Physical control accounts & EVM
- **Identity:** core · prefix=`/costmodel` · **Depends on:** [projects, asset_management, progress, schedule] · **Dependents:** eac, project_controls, reporting, portal(read).
- **Entities:** `_control_account`(code, classification_standard, rsw_node_id/asset_subtree_root_id, responsible_id) · `_work_package`(control_account_id, code, unit, rsw_node_id/asset_item_id, planned/earned_quantity, crew_hours_budget) · `_physical_snapshot`(period, planned/earned/installed_quantity, spi, physical_cpi, forecast_quantity_at_completion) · `_labour_worker_day`(work_date, resource_id, source_module, hours) · `_productivity`(control_account_id, period, earned_quantity, crew_hours, unit_rate_achieved, target_rate, variance_pct).
- **Key logic:** **physical EVM only** — PV/EV/AC are planned/earned/installed **quantity**; SPI = earned/planned; **physical CPI** = earned-qty per crew-hour vs target. Control accounts roll up the Asset subtree. No cash flow, no money budget; `eac` forecasts **quantity/duration** at completion.
- **Verification:** `costmodel.snapshot` (earned ≤ installed ≤ planned per CA unless flagged; every CA resolves to a live Asset subtree).
- **Features:** [F1] physical control accounts (Asset-subtree) · [F2] work packages (RSW/Item, qty + crew-hours) · [F3] physical EV snapshots (SPI / physical-CPI) · [F4] productivity · [F5] labour worker-days · [F6] quantity-at-completion forecast. **Removal:** no physical EVM/productivity.

### eac (physical) — Rules/blocks engine for physical/quality forecasting
- **Identity:** core · prefix=`/eac` · **Depends on:** [users, projects] · part of the verification engine.
- **Entities (12):** `_ruleset`, `_rule`(+`_rule_version`), `_run`(+`_run_result_item`), `_global_variable`, `_parameter_aliases`(+synonyms/snapshots), `_block_graph`(+`_block`, `_block_connection`).
- **Key logic:** visual rules/block-graph engine evaluating **physical/quality predicates** (drop currency/tax); also backs `run_checks`. **Features:** [F1] rulesets + versioned rules · [F2] block-graph editor · [F3] parameter aliases · [F4] global variables · [F5] runs + result items · [F6] template rulesets. **Removal:** no rules-based forecasting/checks.

### full_evm (physical) — Earned Value (quantity/hours)
- **Identity:** enterprise · routes≈40 · **Depends on:** [eac, projects, progress]. `_forecast`(forecast_date, etc, eac, vac, tcpi, method, confidence, alert_status — all in **units/hours**) · `_baseline`(bac in units/hours, start/finish_date, approved_by/at) · `_baseline_period`(period_end, planned_value/quantity) · `_measure`(data_date, bac, pv, ev, ac as **quantities**, sv, cv, spi, cpi, percent_complete, eac_method). **Features:** [F1] baselines + periods · [F2] EV measures (quantity PV/EV/AC, SPI/physical-CPI) · [F3] forecasts (ETC/EAC/VAC in units) · [F4] variance alerts · [F5] baseline validation. **Removal:** no full physical EVM.

### project_controls — Executive controls board (physical)
- **Identity:** extension · routes≈4 · **Depends on:** [projects, users, bi_dashboards] · aggregator, no tables. **Features:** [F1] cross-module exec controls rollup — **physical** cost-proxy (productivity) + schedule + risk (no money EVM columns). **Removal:** no exec controls board.

### reporting — KPI snapshots & reports
- **Identity:** core · routes≈42. `_kpi_snapshot`(spi, physical_cpi, ppc, earned_quantity_pct, open_defects/observations/rfis/submittals, schedule_progress_pct, risk_score_avg) · `_template`(report_type, template_data, schedule_cron, recipients, is_scheduled, next_run_at) · `_generated`(template_id, format, storage_key, data_snapshot, published). **Features:** [F1] KPI snapshots (physical) · [F2] report templates (+scheduling) · [F3] generated reports (PDF/Excel/HTML) · [F4] publish. **Removal:** no reports.

### dashboard / dashboards / bi_dashboards
- **dashboard** — core, routes≈12. `_inbox_item_state`(user_id, item_id, source, state, findings): [F1] single rollup endpoint + [F2] per-user inbox item state.
- **dashboards** — core, routes≈54. `_snapshot`(parquet_dir, totals, summary_stats, source_files, parent_snapshot_id) · `_source_file` · `_preset`(kind, config_json, shared, sync_status): [F1] analytical snapshots (parquet) · [F2] source-file ingestion · [F3] presets.
- **bi_dashboards** — extension, routes≈68. `_kpi_definition`(formula_ref, source_modules, aggregation) · `_dashboard`(scope, role_ref, layout_json, cross_filter) · `_widget`(type, kpi_code, drill_path) · `_report_definition` · `_report_schedule` · `_alert_rule`(kpi_code, condition, threshold, severity, channels, throttle) · `_saved_filter` · `_kpi_value`: [F1] KPI definitions · [F2] dashboards + widgets (drill/cross-filter) · [F3] report definitions + schedules · [F4] alert rules · [F5] saved filters · [F6] KPI value store. **Removal:** no BI layer; `dashboards` gives basic.

### project_intelligence — Project insights
- **Identity:** core · no tables (engine). **Features:** [F1] project-level insights/analytics. **Removal:** no project insights.

### risk — Risk register
- **Identity:** core · routes≈30. `_register`(code, category, probability, impact_cost (mirror), impact_schedule_days, risk_score/tier, status, mitigation_strategy, contingency_plan, owner, escalation, last_simulation). **Features:** [F1] risk register · [F2] scoring/tiering · [F3] mitigation/contingency · [F4] escalation · [F5] simulation snapshot. **Removal:** no risk register.

---

## 11 · Ops & sustainability

### carbon — Carbon & whole-life cost
- **Identity:** business · routes≈122. `_epd_record`(gwp_a/b/c/d) · `_material_factor` · `_inventory` · `_embodied_entry`(carbon_kg, stage) · `_scope1/2/3_entry` · `_operational_entry`(annual_energy_kwh, grid) · `_lcc_entry`(capex/opex/replacement/eol PV, whole_life_cost) · `_target` · `_report`(framework) · `_grid_factor`. **Features:** [F1] EPD records + material factors · [F2] embodied carbon inventory · [F3] GHG scopes 1/2/3 · [F4] operational carbon · [F5] whole-life cost (LCC) · [F6] targets · [F7] framework reports · [F8] grid factors. **Removal:** no carbon/sustainability accounting.

### esg — ESG metrics
- **Identity:** business · routes≈20. `_entry`(metric_key, period, value, target, note). **Features:** [F1] ESG site-performance metrics vs targets. **Removal:** no ESG metrics.

### service — Field service & maintenance
- **Identity:** business · routes≈104. `_contract`(sla_definition_id, sla_tier, auto_renew) · `_asset`(asset_tag, warranty_until) · `_ticket`(priority, sla_due/response/resolution, status, sla_breached) · `_work_order`(technician_id, debrief_summary, customer_signature) · `_work_order_item` · `_debrief` · `_sla_definition`(response/resolution_time) · `_schedule`(frequency, next_due) · `_checklist` · `_recurring_schedule`(rrule). **Features:** [F1] service contracts (+SLA) · [F2] serviced assets (warranty) · [F3] tickets (SLA-tracked) · [F4] work orders (+items, debrief) · [F5] SLA definitions · [F6] preventive-maintenance schedules · [F7] checklists. **Removal:** no field-service/maintenance.

---

## 12 · Governance & workflow

### cases — Case playbooks
- **Identity:** core · routes≈20. `_user_case`(title, category, company_types, roles, stage, est_minutes, steps, source_playbook_id, is_shared) · `_pin`. **Features:** [F1] user cases/playbooks (seeds the per-persona walkthroughs) · [F2] pins. **Removal:** no case playbooks.

*(approval_routes and enterprise_workflows are specced under §1 Platform.)*

---

## 13 · Client portal

### portal — Client access, phased v1 → v2 → v3
- **Identity:** core · prefix=`/portal` · **Depends on:** [projects, asset_management, inspections, qms, ncr, signing, documents, review_authority, meetings] · scoped magic-link + per-resource access rules, per client project(s); every action routes through `run_checks` + audit.

**v1 — read-only**
- `_access_grant`(client_contact_id→contacts, project_id, scope[`assets|registers|documents`], asset_subtree_root_id, expires_at, magic_link_token). Endpoints: `GET /portal/assets`, `/portal/registers/{type}`, `/portal/documents` (filtered by grant).
- **Verification:** `portal.access` (reads filtered to granted project + subtree; no cross-tenant leakage → 404).
- **Features:** [F1] scoped magic-link access · [F2] Asset hierarchy viewer · [F3] registers read-only · [F4] document viewer (current revisions).

**v2 — co-sign reports & ITPs**
- `_cosign_request`(target_kind[`report|itp`], target_id, client_contact_id, status[`pending|signed|declined`], requested_by/at, due_at) · `_cosign_record`(request_id, signing_signature_id→signing, decision, signed_at, comment, declined_reason). Endpoints: `POST /portal/cosign/request`, `GET /portal/cosign/pending`, `POST …/{id}/sign`, `POST …/{id}/decline`.
- **State:** `pending→signed` (writes a `signing` signature, stamps the record) | `pending→declined` (reason required; blocks "client-accepted").
- **Verification (`report.cosign`/`itp.cosign`):** target must be internally complete/approved first; signer must be the granted client contact; ITP co-sign requires all hold/witness points released.
- **Features:** [F5] co-sign requests · [F6] client counter-signature via `signing` · [F7] decline with reason + notify · [F8] co-sign status stamped on the register.

**v3 — visit scheduler + client NCR**
- `_visit_booking`(purpose[`hold_point_witness|final_signoff`], review_gate_id→review_authority or rsw_node_id/asset_item_id, requested_by, proposed_slots, confirmed_slot, status[`requested|confirmed|completed|cancelled`], meeting_id→meetings, outcome_ref) · `_client_ncr_link`(ncr_id→ncr, client_contact_id, role[`raiser|signoff`], action[`raised|signed_off|rejected`], at, comment).
- Endpoints: `POST /portal/visits/request`, `GET /portal/visits`, `POST …/{id}/confirm`, `POST …/{id}/complete`, `POST /portal/ncr` (creates `ncr` `source='client'`), `POST /portal/ncr/{id}/signoff`.
- **State:** *visit* `requested→confirmed` (creates a `meetings` entry) `→completed` (releasing a hold gate is still gated by `review_authority`) `|cancelled`; *client NCR* `raised→[ncr lifecycle]→closure`, client sign-off gated by `ncr.signoff`.
- **Verification:** `portal.visit` (hold-point visit references a real open gate; completing it does not itself release it), `ncr.signoff` (sign-off only after internal closure; a rejection reopens + logs a finding).
- **Features:** [F9] visit scheduler (hold-point witness / final sign-off) · [F10] visits feed `review_authority` + `meetings` · [F11] client-raised NCRs · [F12] client NCR closure sign-off (gated) · [F13] client NCR rejection reopens.
- **Removal impact:** clients lose self-service visibility + co-sign/visit/NCR participation; v2/v3 degrade to v1 if `signing`/`review_authority`/`ncr` are absent. Co-sign/NCR actions never create a second source of truth — they write through `signing`/`ncr`.

---

---

## 14 · Work packs & AWP

> Full detail in `spec/MODULE-SPEC-10-workpacks-awp.md` (which also carries the VFS tables folded into `documents`, §1 there). Both are **orchestration layers over existing modules** — the field-pack execution flow and the AWP constraint engine, without re-implementing storage, ITP, welds, stock, e-sign or scheduling. *(The Virtual File System is part of `documents` — see §5.)*

### workpacks — Field/Inspection Work Packs (IWP) + ITP execution
- **Identity:** business · prefix=`/workpacks` · **Depends on:** [projects, asset_management, qms, wps, site_inventory, procurement, documents, markups, signing, ncr, notifications, jobs, reporting, construction_control, field_diary, validation]. **Reuses:** `qms` ITP templates/stages, `construction_control` hold gates, `wps` weld rules, `site_inventory`/`procurement` batches+MTCs, `documents` (drawings + VFS filing) + `markups` overlays, `signing` sign-off, `ncr` non-conformance, `field_diary` offline sync ledger, `notifications` alerts, `jobs`+ARQ compile, `reporting` instant certs.
- **Entities:** `oe_workpack`(work_pack_number, rsw_node_id, site_id, asset_id, component_asset_item_id, status[`draft|issued|in_progress|on_hold|completed|cancelled`], supervisor_id, qc_inspector_id, scope_of_work, safety_requirements jsonb, itp_template_id→qms, compiled_pdf_document_id→documents, folder_id→documents, awp_cwp_id) · `oe_workpack_execution_stage`(sequence, stage_name, acceptance_criteria, contractor/client/third_party_point[`H|W|S|R`], status, signed_by/at, client_signed_by/at, measurement_data jsonb, has_ncr, ncr_id, hold_gate_id) · `oe_workpack_drawing_link`(document_id→documents, drawing_number, sheet, revision, markup_layer_id) · `oe_workpack_material_allocation`(stock_item_id, product_name, batch_number, cert_document_id→documents, qty, uom) · `oe_workpack_tq_link`(tq_id→awp).
- **Key logic:** auto-compile clones `qms` ITP stages, pulls batches/certs + approved drawings, resolves the pre-set `documents` VFS folder; offline-first tablet sign-off via the `field_diary` ledger; supervisor→QC **hold-point gating** (arm `construction_control` gate → `notifications` → QC `inspect` writes a `signing` signature and releases the gate; weld stages defer to `wps` `weld.accept`); adaptive JSONB `measurement_data` validated by versioned JSONSchema (GIN-indexed); non-destructive `markups` overlay burned on export; ARQ QR-coded MDR compile; QR auto-filer returns signed scans into the `documents` VFS.
- **Verification:** `workpack.issue`, `workpack.stage.signoff`.
- **Features:** [F1] IWP scope/safety/header · [F2] ITP execution stages (from `qms`) · [F3] hold/witness/surveillance/review gating · [F4] drawings + `markups` overlay · [F5] batch/MTC allocation · [F6] adaptive JSONB measurement collector · [F7] offline-first sign-off · [F8] supervisor→QC gating + notifications · [F9] ARQ MDR/pack compile (QR, page X/Y) · [F10] QR auto-filer → `documents` VFS · [F11] instant single-stage cert via `reporting` · [F12] NCR via `ncr` · [F13] approved-TQ banner. **Removal:** no bundled field dossier/MDR; underlying registers still work standalone.

### awp — Advanced Work Packaging (EWP · PWP · CWP → IWP) + Technical Queries
- **Identity:** business · prefix=`/awp` · **Depends on:** [projects, asset_management, documents, schedule_advanced, procurement, supplier_catalogs, site_inventory, qms, workpacks, moc, validation, notifications]. **Reuses:** `documents` (approved drawings/CDE state + VFS filing), `procurement`/`site_inventory` (PO/batch/MTC status), `schedule_advanced` (look-ahead window), `qms` (master ITP), `moc` (TQ/concession lifecycle), `workpacks` (IWP generation).
- **Entities:** `oe_awp_ewp`(code, asset_subtree_root_id, discipline, status, drawing_cde_state, itp_template_id, bom_ref, spec_refs) · `oe_awp_pwp`(ewp_id, status, po_refs, laydown_zone, missing_items, critical_materials_received) · `oe_awp_cwp`(ewp_id, pwp_id, discipline, area, status[`blocked|hold|constraint_free|released`], schedule_window, readiness_report) · `oe_awp_cwp_iwp`(cwp_id, workpack_id, look_ahead_ref) · `oe_awp_technical_query`(tq_number, type, scope_level[`project|asset|work_pack|product_group`], scope_ref, status, moc_entry_id, concession_document_id→documents, from/to_product).
- **Key logic:** staggered gates — EWP sealed at Rev 0/approved; PWP complete when materials received with certs; CWP `constraint_free` only when drawings approved ∧ materials received ∧ no unresolved TQs; IWP generated 1–2 weeks ahead aligned to `schedule_advanced` look-ahead via `workpacks.create_automated_work_pack`; locked batches/certs + approved drawings **inherit automatically** into the IWP; approved TQs auto-stamp warning banners + enter the MDR by scope resolution.
- **Verification:** `cwp.release`, `iwp.generate`, `tq.apply`.
- **Features:** [F1] EWP · [F2] PWP (over `procurement`) · [F3] CWP · [F4] constraint engine (blocked/hold/constraint_free/released) · [F5] staggered auto-generation · [F6] CWP→IWP decomposition (look-ahead aligned) · [F7] automated batch/cert inheritance · [F8] Technical Queries/concessions (scope levels, `moc`-driven) · [F9] auto TQ banners + MDR inclusion. **Removal:** field packs can still be made ad-hoc in `workpacks`, but without engineering/procurement constraint gating or auto batch/TQ inheritance.

---

*End of module specifications. Everything above is in-scope; the anchor model and the external-mirror boundary are applied throughout. Companions: `DEVELOPMENT-PLAN.md`, `ADR-001-runtime-and-storage-architecture.md`, `spec/MODULE-SPEC-10-workpacks-awp.md`, `spec/MODULE-SPEC-11-inspection-requests.md` (+ `PROPOSAL-port-from-aip.md`).*

---

## 15 · Request-for-Inspection & ported Project-AIP capabilities

> Full detail in [`spec/MODULE-SPEC-11-inspection-requests.md`](spec/MODULE-SPEC-11-inspection-requests.md); rationale, dedup and the full Project-AIP gap list in [`PROPOSAL-port-from-aip.md`](PROPOSAL-port-from-aip.md). One new module plus capability extensions that **unify the two no-code designers into one engine** and add inspection record-integrity — reuse-first, house format.

- **inspection_requests** — Request for Inspection: seamless in-system request → notice → confirm → witness/waive/no-show/fail, an append-only hash-chained what/why/who event ledger, reason codes with cause-party attribution, a PDF Request/Confirmation/Outcome notice filed into the MDR, auto delay-events on client no-show/decline, one-click change-notice/EOT, and a client-vs-contractor delay dashboard. Replaces the antiquated "email a PDF RFI" step while still producing the PDF record.
- **forms (extension)** — merge to the Project-AIP designer: one shared field-type registry + shared TS/Python evaluator, template kinds (inspection/itp/rfi/permit/test_report), conditional + calculated fields, revision immutability (`schema_hash` + trigger), instrument-calibration field, test-fill/lint, section library, report-slot mapping.
- **item_types / asset_management (extension)** — the asset-schema half of the same engine: shared registry, auto-create ITP/inspection, schema versioning + migration preview, type inheritance, template-field write-back to asset attributes, starter packs.
- **inspections (extension)** — append-only hash-chained multi-party sign-off, idempotent offline responses, inline eligibility banner, competency-aware assignment, programmes/recurrence.
- **eligibility (new service)** — one "valid right now?" gate over `credentials`/`wps`/calibration/`compliance_docs`, consumed by `run_checks`.
- **rfi / submittals (extension)** — submittal-as-gate rule, inspection-paperwork submittal types, convert-RFI-to-variation, response-time analytics.
