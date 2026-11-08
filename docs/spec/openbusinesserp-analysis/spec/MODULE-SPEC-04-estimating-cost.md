> ## ⚠ Field-Ops pivot status (authoritative: ../FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md)
> **REMOVED (all estimating/cost):** ~~boq~~ ~~takeoff~~ ~~dwg_takeoff~~ ~~rebar_schedule~~ ~~measurement~~ ~~waste_factors~~ ~~costs~~ ~~assemblies~~ ~~norm_expansion~~ ~~price_breakdown~~ ~~price_index~~ ~~cost_match~~ ~~cost_plan~~ ~~rom_estimate~~ ~~ai_estimator~~ ~~estimate_basis~~ ~~estimate_rollup~~ ~~cvr~~ ~~value~~ · **MODIFIED (physical-only):** costmodel, full_evm, eac, project_controls, resource_summary (from tasks) · **KEPT:** progress (re-anchored Asset/RSW), resources, prefab.

# OpenBusinessERP — Module Specifications · Batch 4: Estimating & Cost

> Editable specs (format in `MODULE-SPEC-00`). Entities = real `models.py` columns @ `a96d525`; very large tables show key fields + "…". The largest domain (26 modules).

**Modules (26):** `boq`, `takeoff`, `dwg_takeoff`, `rebar_schedule`, `measurement`, `waste_factors`, `costs`, `assemblies`, `norm_expansion`, `price_breakdown`, `price_index`, `cost_match`, `costmodel`, `cost_plan`, `rom_estimate`, `ai_estimator`, `estimate_basis`, `estimate_rollup`, `prefab`, `resources`, `resource_summary`, `progress`, `eac`, `full_evm`, `cvr`, `project_controls`, `value`.

---

### boq — Bill of Quantities (central measured-scope spine)
- **Identity:** core · prefix=`/boq` · routes≈220 · **Depends on:** [projects] · **Dependents:** costmodel, tendering, progress, estimate_*, postcalc, site_inventory, methodology, formwork
- **Entities:** `_boq`(project_id, name, status, estimate_type, is_locked, parent_estimate_id, approved_by/at, base_date, tax_date, variation_request_id) · `_position`(boq_id, parent_id, ordinal, description, unit, quantity, unit_rate, total, net_cost_rate, target_rate, sale_rate, classification, confidence, risk_dispersion, price_basis, cad_element_ids, cad_model_id, validation_status, wbs_id, cost_code_id, node_type, contractor_id, contract_id, funding_source_id, stage_id, link_group_id, cost_line_id, norm_id, norm_work_key, version, …) · `_markup`(markup_type, category, percentage, fixed_amount, apply_to, scope_position_id, overrides_id) · `_activity_log` · `_snapshot`(snapshot_data, total_value, position_count) · `_quantity_link`(position_id, model_id, element_stable_ids, quantity_field, aggregation, projection_mode, formula, last_applied_quantity)
- **Key logic:** hierarchical position tree w/ quantities+rates (net/target/sale); CAD/BIM quantity links (pull quantities from model elements via formula); markups; versioning + snapshots; lock/approve.
- **Features:** [F1] BOQ + hierarchical positions · [F2] net/target/sale rates · [F3] markups · [F4] CAD/BIM quantity links · [F5] classification/WBS/cost-code tagging · [F6] versioning + snapshots · [F7] lock/approval · [F8] activity log · [F9] norm linkage.
- **Removal impact:** core — tendering/costmodel/progress/estimating all read it.
- **Edit notes:**

### takeoff — PDF takeoff + AI takeoff
- **Identity:** extension · prefix=`/takeoff` · routes≈106 · **Depends on:** [projects, cad]
- **Entities:** `_cad_session`(filename, file_format, element_count, elements_data, columns_metadata, is_permanent, bim_model_id) · `_document`(filename, pages, content_type, status, extracted_text, page_data, analysis, page_scales) · `_measurement`(document_id, page, type, group_name, annotation, points, measurement_value/unit, depth, volume, perimeter, count_value, is_deduction, scale_pixels_per_unit, linked_boq_position_id, review_status) · `oe_ai_takeoff_run`(mode, provider, model_used, total_tokens, cost_usd_estimate, proposal_count, accepted_count, validation_report)
- **Key logic:** upload PDF → measure (area/length/count/volume, deductions) with page scale → link to BOQ positions; **AI takeoff run** proposes measurements (human-accept).
- **Features:** [F1] PDF upload + pages · [F2] manual measurement (area/length/count/volume) · [F3] deductions · [F4] scale calibration · [F5] BOQ linkage · [F6] AI-assisted takeoff runs · [F7] CAD session import.
- **Removal impact:** no PDF takeoff; manual BOQ remains.
- **Edit notes:**

### dwg_takeoff — CAD/DWG vector takeoff
- **Identity:** extension · prefix=`/dwg-takeoff` · routes≈52 · **Depends on:** [projects]
- **Entities:** `_drawing`(name, file_format, status, discipline, sheet_number, scale_denominator/mode) · `_drawing_version`(layers, entities_key, entity_count, extents, units) · `_annotation`(annotation_type, geometry, measurement_value/unit, linked_boq_position_id/task_id/punch_item_id) · `_entity_group`(entity_ids)
- **Features:** [F1] DWG drawings + versions (layers/entities) · [F2] annotations/measurements · [F3] entity groups · [F4] BOQ/task/punch linkage · [F5] scale handling.
- **Removal impact:** no native CAD takeoff.
- **Edit notes:**

### rebar_schedule — Reinforcement scheduling
- **Identity:** business · routes≈30 · **Depends on:** [users, projects]
- **Entities:** `_import`(filename, record_count, total_weight_kg, validation_status) · `_shape`(line_no, super_group, drawing_ref, position, length_mm, quantity, weight_kg, diameter_mm, steel_grade, bending_roller_mm, mesh_type, geometry, block_layout, checksum_ok)
- **Features:** [F1] rebar schedule import · [F2] bar shapes/bending · [F3] weight rollup · [F4] mesh · [F5] validation.
- **Removal impact:** no rebar scheduling.
- **Edit notes:**

### measurement — Quantity-determination library
- **Identity:** core · library (no tables/router). **Features:** [F1] measurement helper functions shared by takeoff/BOQ. **Removal impact:** takeoff/BOQ lose shared calc helpers.
- **Edit notes:**

### waste_factors — Net-to-gross adjustment
- **Identity:** core · routes≈14 · **Depends on:** []
- **Entities:** `_factor`(category, label, factor, note)
- **Features:** [F1] waste-factor table · [F2] net→gross quantity adjustment.
- **Removal impact:** no waste adjustment; quantities taken net.
- **Edit notes:**

### costs — Cost Database
- **Identity:** core · prefix=`/costs` · routes≈123 · **Depends on:** [] · **Dependents:** assemblies, catalog, cost_match, price_index
- **Entities:** `_item`(code, description, descriptions[i18n], unit, rate, currency, source, classification, components, tags, region, mass_per_unit, price_as_of, catalog_id) · `_catalog`(name, currency, source) · `oe_regional_indices`(region_code, category, factor, effective_date) · `_cost_item_usage`(cost_item_id, project_id, used_at, unit_rate_at_use) · `oe_resource_price`(region, resource_key/code/name/type, unit_price, effective_date) · `oe_costs_base_state`(region, text_language, active_market, switching_to)
- **Key logic:** reference cost library w/ catalogs, regional indices, resource prices; usage tracking; active-market/region state.
- **Features:** [F1] cost items + catalogs · [F2] i18n descriptions · [F3] regional indices · [F4] resource prices · [F5] usage tracking · [F6] active-market/region switching.
- **Removal impact:** core for estimating — assemblies/price_index/cost_match depend on it. **(Data gap G8: the actual cost content is external.)**
- **Edit notes:**

### assemblies — Composite unit rates
- **Identity:** core · prefix=`/assemblies` · routes≈42 · **Depends on:** [costs, fx]
- **Entities:** `_assembly`(code, name, unit, category, classification, total_rate, currency, bid_factor, regional_factors, parameters, is_template, project_id) · `_component`(cost_item_id, catalog_resource_id, resource_type, factor, quantity, quantity_formula, unit, unit_cost, total) · `_template`(components, name_translations, is_builtin)
- **Key logic:** assembly = Σ components (resource × qty/formula × rate), FX/regional factored; templates (builtin + custom).
- **Features:** [F1] assemblies · [F2] components (formula-driven qty) · [F3] bid/regional factors · [F4] templates · [F5] FX.
- **Removal impact:** no composite rate build-up.
- **Edit notes:**

### norm_expansion — Production-norm engine
- **Identity:** core · routes≈20 · **Depends on:** []
- **Entities:** `_norm`(work_key, name, unit, category, labor_hours_per_unit, machine_hours_per_unit, is_active) · `_material`(norm_id, name, unit, qty_per_unit)
- **Features:** [F1] production norms (labour/machine hours/unit) · [F2] material consumption per unit · [F3] expansion to resource quantities.
- **Removal impact:** no norm-based expansion (postcalc feedback loop breaks).
- **Edit notes:**

### price_breakdown — Unit-price analysis library
- **Identity:** library (no tables/router). **Features:** [F1] structured unit-price breakdown/analysis helpers. **Removal impact:** no price-analysis view.
- **Edit notes:**

### price_index — Cost-index adjustment
- **Identity:** core · routes≈40 · **Depends on:** [costs]
- **Entities:** `_series`(name) · `_point`(series_id, period, factor) · `_location_factor`(region_code, factor) · `_resource_index`(region_code, quarter, resource_group, index_value) · `_overhead_norm`(work_type_code, nr_pct, sp_pct) · `_seed_marker`
- **Features:** [F1] index series/points · [F2] location factors · [F3] resource indices · [F4] overhead norms · [F5] base→current escalation.
- **Removal impact:** no escalation of historic rates.
- **Edit notes:**

### cost_match — Foreign bill → cost base (AI)
- **Identity:** core · routes≈26 · **Depends on:** [users, projects, costs, dashboards, ai]
- **Entities:** `_run`(name, source_label/locale, cost_source, region, catalog_id, status, item_count, candidate_limit) · `_result`(line_no, source_description/unit/quantity, tier, confidence, tie, hint_code, reason_codes, alternatives, suggested_cost_item_id/code/rate, decision_state) · `_decision`(decision, tier/confidence_at_decision, decided_cost_item_id/rate, decided_by, note)
- **Key logic:** map external bill lines → cost-base items (tiered confidence, alternatives, human decisions).
- **Features:** [F1] match runs · [F2] tiered AI matching + alternatives · [F3] decisions/audit.
- **Removal impact:** no foreign-bill→cost-base mapping.
- **Edit notes:**

### costmodel — 5D cost model
- **Identity:** core · prefix=`/costmodel` · routes≈66 · **Depends on:** [projects, boq] · **Dependents:** rom_estimate, postcalc
- **Entities:** `_snapshot`(period, planned/earned/actual, forecast_eac, spi, cpi) · `_budget_line`(boq_position_id, activity_id, cost_line_id, control_account_id, planned/committed/actual/forecast/earned_amount, overrun_alert_threshold_pct) · `_cash_flow`(period, planned/actual inflow/outflow, cumulative) · `_control_account`(code, classification_standard) · `_cost_line`(control_account_id, code, unit, boq_position_id, estimate_quantity/unit_rate/amount) · `_labour_worker_day`(work_date, resource_id, source_module, hours)
- **Key logic:** cost dimension over BOQ + time (control accounts, budget lines, cash flow, labour days); overrun alerts; EV snapshots.
- **Features:** [F1] control accounts · [F2] cost lines (BOQ-tied) · [F3] budget lines (planned/committed/actual/forecast/earned) · [F4] cash flow (planned vs actual) · [F5] overrun alerts · [F6] EV snapshots · [F7] labour worker-days.
- **Removal impact:** no 5D cost model; EVM loses a source.
- **Edit notes:**

### cost_plan — Elemental cost plan
- **Identity:** business · routes≈4 · **Depends on:** [boq, projects] · (thin; tables not confirmed) **Features:** [F1] elemental cost plan over BOQ. **Removal impact:** no early elemental plan.
- **Edit notes:**

### rom_estimate — Conceptual/ROM estimate
- **Identity:** core · routes≈14 · **Depends on:** [projects, costmodel]
- **Entities:** `_estimate`(name, building_type, quality, region, currency, gross_floor_area, gfa_unit, total_cost, estimate_class, accuracy_low/high_pct+amount, breakdown)
- **Features:** [F1] ROM estimates (GFA × rate) · [F2] estimate class + accuracy band · [F3] breakdown.
- **Removal impact:** no conceptual estimating.
- **Edit notes:**

### ai_estimator — AI Estimate Builder
- **Identity:** core · prefix=`/ai-estimator` · routes≈66 · **Depends on:** []
- **Entities:** `_run`(agent_name, status, current_stage, checkpoints, source_inputs, detected_source, suggested_config, catalogue_id, region, currency, group_by, provider, model_used, total_tokens, cost_usd_estimate, grand_total, completeness_score, boq_id) · `_group`(group_key, signature, element_ids, quantities, chosen_unit, trade, chosen_code, unit_rate, confidence_band, resources, candidates, match_method, status, boq_position_id, confirmed_by/at) · `_step`(stage, role, content, token_count) · `_intake`(mode, raw_request, detected_type, params, round_idx, questions, transcript, phase, packages)
- **Key logic:** staged AI agent run → groups elements → proposes coded rates w/ confidence/candidates → human-confirm → BOQ; intake Q&A.
- **Features:** [F1] AI estimate runs (staged, checkpointed) · [F2] element grouping · [F3] rate candidates + confidence · [F4] intake Q&A · [F5] human-confirm → BOQ · [F6] token/cost tracking.
- **Removal impact:** no AI estimating; manual/ROM remain.
- **Edit notes:**

### estimate_basis — Basis of Estimate
- **Identity:** business · routes≈12 · **Depends on:** [boq, projects, allowances, preliminaries, costs]
- **Entities:** `_document`(boq_id, title, status, inclusions, exclusions, assumptions, coverage, currency, financials, provenance, pricing_date, estimate_class, accuracy_low/high_pct, market_conditions, contingency_rationale, budget_target)
- **Features:** [F1] basis-of-estimate doc (inclusions/exclusions/assumptions) · [F2] estimate class + accuracy · [F3] contingency rationale · [F4] budget target.
- **Removal impact:** no documented estimate basis.
- **Edit notes:**

### estimate_rollup — Headline rollup
- **Identity:** business · routes≈2 · **Depends on:** [boq, projects, preliminaries, allowances] · (engine, no tables) **Features:** [F1] roll BOQ + preliminaries + allowances → headline total. **Removal impact:** manual totalling.
- **Edit notes:**

### prefab — Off-site / DfMA
- **Identity:** business · routes≈30 · **Depends on:** [users, projects]
- **Entities:** `_unit`(ref, unit_type, status, target_install_date, drawing_ref, bim_element_ids, boq_position_id, assembly_id) · `_production_event`(stage, from_stage, at, note)
- **Features:** [F1] prefab units (BIM/BOQ/assembly linked) · [F2] production stage events.
- **Removal impact:** no offsite/DfMA tracking.
- **Edit notes:**

### resources — Graphical resource planning
- **Identity:** business · prefix=`/resources` · routes≈108 · **Depends on:** [users, projects] · **Dependents:** payroll, certified_payroll
- **Entities:** `_resource`(code, name, resource_type, home_project_id, contact_id, default_cost_rate, capacity_percent, status) · `_skill` · `_resource_skill`(level, acquired/expires_at) · `_certification`(cert_type, valid_until, status) · `_availability_window`(window_type, start/end_at, recurrence_rule) · `_assignment`(project/task/activity/work_order_id, start/end_at, allocation_percent, units, cost_rate, status) · `_resource_request`(required_skills, quantity, priority, status, fulfilled_assignment_id) · `_resource_link`(primary/secondary_resource_id, link_type)
- **Features:** [F1] resources (crews/plant/labour) · [F2] skills + levels · [F3] certifications · [F4] availability windows (recurring) · [F5] assignments (allocation %) · [F6] resource requests + fulfilment · [F7] resource links.
- **Removal impact:** no resource planning; payroll/certified_payroll lose hours source.
- **Edit notes:**

### resource_summary — Demand rollup
- **Identity:** core · routes≈10 · **Depends on:** [boq]
- **Entities:** `_snapshot`(generated_at, currency, total_cost, line_count, payload)
- **Features:** [F1] resource-demand rollup snapshot from BOQ.
- **Removal impact:** no resource summary view.
- **Edit notes:**

### progress — Site-execution measurement
- **Identity:** core · routes≈18 · **Depends on:** [projects, boq] · **Dependents:** eac, full_evm, cvr, site_inventory
- **Entities:** `_entry`(boq_position_id, period_label, percent_complete, recorded_by/at, geo_lat/lon, photos) · `_plan`(period_label, planned_pct)
- **Features:** [F1] progress entries (% per BOQ position, geo+photo) · [F2] planned-vs-actual plan.
- **Removal impact:** no progress measurement; EVM/CVR lose earned input.
- **Edit notes:**

### eac — EAC platform (rules/blocks engine)
- **Identity:** core · prefix=`/eac` · routes≈58 · **Depends on:** [users, projects]
- **Entities (12):** `_ruleset`, `_rule`(+`_rule_version`), `_run`(+`_run_result_item`), `_global_variable`, `_parameter_aliases`(+`_alias_synonyms`, `_alias_snapshots`), `_block_graph`(+`_block`, `_block_connection`) — a visual rules/block-graph engine evaluating element attributes to forecast cost.
- **Features:** [F1] rulesets + versioned rules · [F2] block-graph visual editor · [F3] parameter aliases/synonyms · [F4] global variables · [F5] runs + result items · [F6] marketplace/template rulesets.
- **Removal impact:** no rules-based EAC; full_evm loses a dependency.
- **Edit notes:**

### full_evm — Earned Value Management
- **Identity:** enterprise · routes≈40 · **Depends on:** [finance, eac, projects]
- **Entities:** `oe_evm_forecast`(forecast_date, etc_, eac, vac, tcpi, forecast_method, confidence range, alert_status) · `_baseline`(bac, start/finish_date, approved_by/at, validation) · `_baseline_period`(period_end, planned_value/quantity) · `_measure`(data_date, bac, pv, ev, ac, sv, cv, spi, cpi, percent_complete/spent, eac_method, eac_variants, tcpi_bac/eac)
- **Features:** [F1] baselines + periods · [F2] EV measures (PV/EV/AC, SPI/CPI) · [F3] forecasts (ETC/EAC/VAC/TCPI, methods) · [F4] variance alerts · [F5] baseline validation.
- **Removal impact:** no full EVM; `eac`/`costmodel` give lighter forecasting.
- **Edit notes:**

### cvr — Cost-Value Reconciliation & cashflow
- **Identity:** business · routes≈54 · **Depends on:** [users, projects]
- **Entities:** `_report`(period, title, status, currency) · `_line`(cost_code, wbs_id, cost_to_date, value_to_date, accruals, forecast_cost/value) · `_cashflow_point`(period, cash_in/out) · `_payment_application`(period, application_number, gross/retention/net, progress_claim_id)
- **Features:** [F1] CVR reports + lines (cost vs value) · [F2] accruals + forecast · [F3] cashflow points · [F4] payment applications.
- **Removal impact:** no CVR commercial reporting.
- **Edit notes:**

### project_controls — Executive controls dashboard
- **Identity:** extension · routes≈4 · **Depends on:** [projects, users, bi_dashboards] · (aggregator, no tables) **Features:** [F1] cross-module exec controls rollup (cost/schedule/risk). **Removal impact:** no exec controls dashboard.
- **Edit notes:**

### value — Value Realized (controls)
- **Identity:** controls · routes≈16
- **Entities:** `oe_value_time_factor`(module, action, minutes)
- **Features:** [F1] value/time-saving factors per module-action · [F2] realised-value tracking.
- **Removal impact:** no value-realised metric.
- **Edit notes:**

---

*Batch 4 of 7 complete. Next: Batch 5 (Project Delivery & Field). Grounded @ `a96d525`.*
