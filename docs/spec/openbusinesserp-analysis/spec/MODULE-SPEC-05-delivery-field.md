> ## ⚠ Field-Ops pivot status (authoritative: ../FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md)
> **REMOVED:** ~~recurring~~ ~~formwork~~ ~~methodology~~ · **MODIFIED:** field_time (export hours to corporate payroll), assets → superseded by new **asset_management** · **KEPT:** all other delivery/field/quality/safety modules.

# OpenBusinessERP — Module Specifications · Batch 5: Project Delivery & Field

> Editable specs (format in `MODULE-SPEC-00`). Entities = real `models.py` columns @ `a96d525`; large tables show key fields + "…".

**Modules (25):** `schedule`, `schedule_advanced`, `timeline`, `deadlines`, `recurring`, `daily_diary`, `field_diary`, `field_time`, `fieldreports`, `site_prep`, `site_logistics`, `site_supervision`, `construction_control`, `commissioning`, `closeout`, `defects_liability`, `punchlist`, `ncr`, `inspections`, `safety`, `hse_advanced`, `temporary_works`, `formwork`, `equipment`, `assets`, `methodology`.

---

### schedule — 4D schedule (CPM)
- **Identity:** core · prefix=`/schedule` · routes≈110 · **Depends on:** [projects] · **Dependents:** progress, EVM, commissioning
- **Entities:** `_schedule`(name, schedule_type, start/end_date, data_date, status) · `_activity`(parent_id, wbs_code, start/end_date, duration_days, progress_pct, activity_type, dependencies, resources, boq_position_ids, early/late_start/finish, total/free_float, is_critical, constraint_type/date, bim_element_ids, cost_planned/actual, remaining_duration, budgeted/installed_units, calendar_id, revision, assignee_id, …) · `_work_order`(activity_id, assembly_id, boq_position_id, assigned_to, planned/actual_start/end, planned/actual_cost, status) · `_relationship`(predecessor/successor_id, relationship_type, lag_days) · `_baseline`(snapshot_data, is_active) · `_progress`(activity_id, progress_pct, actual_start/finish, submitted/approved_by) · `_eac_link`(task_id, rule_id, predicate_json) · `_progress_entry`(progress_percent, photo_attachment_ids, geolocation, device, actual_start/finish_date)
- **Key logic:** CPM (floats, critical path, constraints, calendars); activities↔BOQ/BIM; work orders; baselines; field progress (geo/photo); EAC rule linkage.
- **Features:** [F1] schedules + WBS activities · [F2] CPM (floats/critical/constraints) · [F3] relationships (lag) · [F4] calendars · [F5] baselines · [F6] work orders · [F7] progress updates (field, geo/photo) · [F8] BOQ/BIM links · [F9] EAC rule links · [F10] cost on activities.
- **Removal impact:** core delivery backbone; progress/EVM lose structure.
- **Edit notes:**

### schedule_advanced — Last Planner / Takt / delay analysis
- **Identity:** business · routes≈208 · **Depends on:** [users, projects]
- **Entities (18):** `_master_schedule`, `_phase_plan` (pull planning), `_look_ahead`, `_constraint` (make-ready), `_weekly_plan`(ppc_percent), `_commitment`(promised/actual_qty), `_rnc` (reasons-for-non-completion), `_baseline`(+`_baseline_delta`: schedule_variance_days), `_calendar`, `_weekly_commitment`(ppc), `_takt_schedule`(+`_takt_location`, `_takt_activity`: cycle/crew), `_delay_analysis`(method, windows, entitlement_days, eot_claim_id)(+`_delay_event`, `_fragnet`, `_delay_window`: employer/contractor/neutral/concurrent days)
- **Key logic:** Last Planner System (phase→lookahead→weekly→commitments→PPC→RNC); Takt planning; forensic **delay analysis** (windows, fragnets, apportionment → EOT claim).
- **Features:** [F1] master schedule · [F2] phase/pull planning · [F3] lookahead + constraints · [F4] weekly plans + commitments + PPC · [F5] RNC tracking · [F6] takt planning · [F7] baselines + variance deltas · [F8] delay analysis (windows/fragnets/apportionment) · [F9] calendars.
- **Removal impact:** no LPS/takt/delay-analysis; basic `schedule` remains.
- **Edit notes:**

### timeline — Project timeline view
- **Identity:** core · routes≈4 · **Depends on:** [users, projects] · (thin, view) **Features:** [F1] lightweight timeline view. **Removal impact:** minor.
- **Edit notes:**

### deadlines — Deadline register
- **Identity:** core · routes≈4 · **Depends on:** [users, projects, notifications] · **Features:** [F1] deadline register · [F2] notification on due. **Removal impact:** no deadline alerts.
- **Edit notes:**

### recurring — Recurring invoices
- **Identity:** core · routes≈22 · **Depends on:** [sales]
- **Entities:** `_invoice`(name, status, customer_id/name/email/address, currency, lines, net/gross_total, interval, interval_count, start/end_date, max_count, day_of_month, period_index, generated_count, next_run_date, auto_issue, auto_send, last_document_id) · `_run`(template_id, period_date, document_id, status, trigger)
- **Features:** [F1] recurring-invoice templates · [F2] schedule (interval/day-of-month/max) · [F3] auto-issue/auto-send · [F4] run log → sales invoices.
- **Removal impact:** no recurring billing.
- **Edit notes:**

### daily_diary — Full site diary
- **Identity:** business · routes≈108 · **Depends on:** [users, projects]
- **Entities:** `_diary`(diary_date, site_supervisor_id, weather_summary, labour/equipment_count, status, closed_at/by, owner/supervisor_signature_ref, pdf_export_ref) · `_weather`(temp/humidity/wind/precip, conditions, sunrise/set, geo) · `_entry`(entry_type, entry_time, title, source_module/ref, author_id, photo_ids) · `_photo`(taken_at, photographer_id, geo, file_url, is_drone) · `_video` · `_drone_survey`(ortho/dsm/point_cloud_url, elevation) · `_reality_capture`(capture_type, point_count, bbox, accuracy_mm, linked_bim_model_ref) · `_archive_signature`(signed_by, signature_payload, revision)
- **Features:** [F1] daily diary + close/sign · [F2] weather capture · [F3] timeline entries (cross-module) · [F4] photos (geo, drone) · [F5] video · [F6] drone surveys · [F7] reality capture (point clouds) · [F8] signed archive.
- **Removal impact:** no full diary; `field_diary` is the lighter sibling.
- **Edit notes:**

### field_diary — Field-worker diary MVP (mobile/low-trust)
- **Identity:** core · routes≈44 · **Depends on:** [projects, users]
- **Entities:** `_entry`(author_id, entry_date, weather, temperature_c, headcount, notes_md, field_source, status, submitted/approved, labour_published_at) · `_activity`(activity_type, hours, location, started/ended_at) · `_attachment`(storage_key) · `oe_field_module_grant`(user_id, module_key, granted/expires/revoked) · `_magic_link`(phone, token_hash, pin_hash, pin_attempts, expires/consumed) · `oe_field_sync_ledger`(client_op_id, op_kind, result_type/id) · `_session`(session_token_hash, pin_hash)
- **Key logic:** mobile field persona — magic-link/PIN auth, per-module grants, offline sync ledger (idempotent ops).
- **Features:** [F1] field diary entries (markdown, weather, headcount) · [F2] activities (hours) · [F3] attachments · [F4] magic-link + PIN auth · [F5] per-module field grants · [F6] offline sync ledger · [F7] approval → labour publish.
- **Removal impact:** no field-worker mobile capture.
- **Edit notes:**

### field_time — Field time capture
- **Identity:** business · routes≈38 · **Depends on:** []
- **Entities:** `_timesheet`(reference, date, status, submitted/approved_by, reverses_id, working_time_regime) · `_line`(resource_id, equipment_id, hours, cost_code, wbs, boq_position_id, is_daywork, variation_id, daywork_sheet_id, started/ended_at, break_minutes, employer_kind, employer_subcontractor_id)
- **Features:** [F1] timesheets (submit/approve/reverse) · [F2] lines (resource/equipment hours, cost-coded) · [F3] daywork/variation tagging · [F4] subcontractor-employer split.
- **Removal impact:** no field time → payroll.
- **Edit notes:**

### fieldreports — Field reports
- **Identity:** core · routes≈58 · **Depends on:** [projects]
- **Entities:** `_report`(report_date/type, weather fields, workforce, equipment_on_site, work_performed, delays, delay_hours, visitors, deliveries, safety_incidents, materials_used, photos, signature, status, approved_by) · `_workforce`(worker_type, company, headcount, hours_worked, overtime, wbs_id) · `_equipment`(hours_operational/standby/breakdown, operator_name) · `_template`(report_type, fields)
- **Features:** [F1] field reports (daily) · [F2] workforce log (hours) · [F3] equipment log · [F4] weather/delays/deliveries · [F5] templates · [F6] sign + approve.
- **Removal impact:** no field reporting; EVM/payroll lose an hours source.
- **Edit notes:**

### site_prep — Pre-construction mobilisation
- **Identity:** business · routes≈20 · **Depends on:** [projects]
- **Entities:** `_plan`(target_start_date, status) · `_item`(category, title, status, responsible_party, due_date, completed_date, is_gate)
- **Features:** [F1] site-prep plan · [F2] readiness items/gates.
- **Removal impact:** no mobilisation checklist.
- **Edit notes:**

### site_logistics — Logistics & delivery
- **Identity:** business · routes≈48 · **Depends on:** [projects, users, boq]
- **Entities:** `_gate`(open/close_time, capacity_per_slot) · `_laydown_zone`(capacity_desc) · `_delivery`(gate_id, supplier_name, vehicle_type, materials_desc, window_start/end, status, po_ref) · `_delivery_line`(boq_position_id, quantity, unit)
- **Features:** [F1] gates (slots/capacity) · [F2] laydown zones · [F3] deliveries (booking windows) · [F4] delivery lines (BOQ-linked).
- **Removal impact:** no logistics scheduling.
- **Edit notes:**

### site_supervision — Design-side supervision
- **Identity:** core · routes≈38 · **Depends on:** [users, projects]
- **Entities:** `_visit`(planned/actual_date, visitor, discipline, status, summary, photo_refs) · `_entry`(observation, category, structured_fields, links_to_change_ref, resolved_at)
- **Features:** [F1] supervision visits · [F2] observations (+link to change) · [F3] resolution.
- **Removal impact:** no supervision register.
- **Edit notes:**

### construction_control — Quality/conformance control
- **Identity:** core · routes≈100 · **Depends on:** [users, projects, ncr]
- **Entities (8):** `_acceptance_criterion`(standard_ref, method, acceptance_rule, nominal/tolerance) · `_inspection`(inspection_number, party_role, intervention_point, criterion_id, result, measured_value, raised_ncr_id) · `_element_ref`(bim_element_id, ifc_global_id, model_version) · `_material_record`(cert_type/number, dop_number, ce/ukca_marking, batch/heat/lot, criterion_id, po/gr links, raised_ncr_id) · `_test_result`(material_record_id, test_method, lab_accreditation, measured_value, result) · `_asbuilt_record`(capture_method, instrument_calibration_ref, accuracy, coordinate_system, deviation, valid_for_legal_record, signed) · `_hold_gate`(point_type, required_party_role, blocks_progress, released/waived) · `_handover_package`(completion_regime, gating_state, open_ncr_count, unreleased_hold_count, certificate_no, dossier_key)
- **Key logic:** ITP-style acceptance criteria → inspections/tests/material certs/as-builts, all raising NCRs; **hold gates** block progress until released; handover packages gate on open NCRs/holds.
- **Features:** [F1] acceptance criteria · [F2] inspections (party/intervention) · [F3] material records + certs (CE/UKCA/DoP) · [F4] test results (accredited labs) · [F5] as-built records (survey, legal validity) · [F6] hold/witness gates · [F7] handover packages + dossier · [F8] BIM element refs · [F9] NCR raising.
- **Removal impact:** no formal QA/conformance control.
- **Edit notes:**

### commissioning — Cx
- **Identity:** business · routes≈46 · **Depends on:** [projects, users]
- **Entities:** `_system`(system_type, tag, location, status, commissioned_at/by) · `_checklist`(kind) · `_checklist_item`(status, result_note, verified_by/at) · `_issue`(severity, status, resolution)
- **Features:** [F1] systems · [F2] checklists + items (verify) · [F3] issues · [F4] commissioning sign-off.
- **Removal impact:** no Cx tracking.
- **Edit notes:**

### closeout — Handover & closeout
- **Identity:** core · routes≈26 · **Depends on:** [users, projects, documents]
- **Entities:** `_package`(project_type, status, checklist_template, required/delivered_slot_count, completeness_pct, package_key) · `_slot`(slot_key, category, discipline, is_required, source_kind, generated_artifact) · `_binding`(slot_id, document_id, external_url, is_verified, suggested_by_ai, ai_confidence)
- **Features:** [F1] closeout packages (templated slots) · [F2] slot bindings (docs/artifacts) · [F3] completeness tracking · [F4] AI slot suggestions · [F5] verification.
- **Removal impact:** no handover pack assembly.
- **Edit notes:**

### defects_liability — DLP / warranty register
- **Identity:** business · routes≈22 · **Depends on:** [projects]
- **Entities:** `_warranty`(reference, element_description, subcontractor_id, warranty_type, handover/start/end_date, warranty_months, limitation_regime, dlp_end_date, retention_release_date, contract_id) · `_defect`(warranty_id, severity, raised/due/rectified_date, responsible_party, punchlist_id, ncr_id)
- **Features:** [F1] warranties (DLP windows) · [F2] defects (→ punchlist/NCR) · [F3] retention-release tie.
- **Removal impact:** no post-handover warranty register.
- **Edit notes:**

### punchlist — Punch/snag
- **Identity:** core · routes≈30 · **Depends on:** [projects]
- **Entities:** `_item`(contract_id, document_id, page, location_x/y, title, priority, status, assigned_to, due_date, category, trade, photos, resolution_notes, resolved/verified_at, reopen_history, rework_cost, clash_result_id, geo_lat/lon)
- **Features:** [F1] punch items (drawing pin + geo) · [F2] assignment/priority/due · [F3] photos · [F4] resolve/verify + reopen history · [F5] rework cost · [F6] clash linkage.
- **Removal impact:** no snag tracking.
- **Edit notes:**

### ncr — Non-Conformance Reports
- **Identity:** core · routes≈14 · **Depends on:** [users, projects] · **Dependents:** construction_control
- **Entities:** `_ncr`(ncr_number, ncr_type, severity, root_cause(+category), corrective/preventive_action, status, cost_impact, schedule_impact_days, location(+geo), linked_inspection_id, change_order_id, clash_result_id)
- **Features:** [F1] NCRs (type/severity) · [F2] root cause + corrective/preventive · [F3] cost/schedule impact · [F4] links (inspection/change/clash).
- **Removal impact:** construction_control & others lose NCR raising.
- **Edit notes:**

### inspections — QA/QC inspections
- **Identity:** core · routes≈18 · **Depends on:** [users, projects]
- **Entities:** `_inspection`(inspection_number/type, title, location, wbs_id, inspector_id, inspection_date, status, result, checklist_data, geo_lat/lon)
- **Features:** [F1] inspections (checklist, result) · [F2] geo-stamp · [F3] WBS tie.
- **Removal impact:** no QA inspection records (lighter than construction_control).
- **Edit notes:**

### safety — Core safety
- **Identity:** core · routes≈34 · **Depends on:** [users, projects]
- **Entities:** `_incident`(incident_number, incident_type, severity, injured_person_details, treatment_type, days_lost, root_cause, osha_recordable/case_number, days_away/restricted) · `oe_hse_corrective_action`(assigned_to, due_date, verified_by/at) · `_observation`(observation_type, severity, likelihood, risk_score, immediate/corrective_action)
- **Features:** [F1] incidents (+OSHA) · [F2] corrective actions (verify) · [F3] safety observations (risk score).
- **Removal impact:** no safety tracking; `hse_advanced` is the enterprise sibling.
- **Edit notes:**

### hse_advanced — Full HSE suite
- **Identity:** business · routes≈162 · **Depends on:** [users, projects]
- **Entities (12):** `_incident_investigation`(method, findings, recommendations) · `_jsa`(hazards, required_ppe, risk_score)(+`_jsa_template`) · `_ptw`(permit_type, jsa_id, prerequisites, closure_checklist) · `_toolbox_talk`(+`_toolbox_attendance`, `_toolbox_topic`) · `_ppe_issue`(ppe_type, serial, valid_until, returned) · `_audit`(+`_audit_finding`) · `_capa`(source, five_whys, effectiveness_verified) · `_certification`
- **Features:** [F1] incident investigations · [F2] JSAs + templates · [F3] permits-to-work (prereqs/closure) · [F4] toolbox talks + attendance + topic library · [F5] PPE issue/return · [F6] audits + findings · [F7] CAPA (5-whys, effectiveness) · [F8] certifications.
- **Removal impact:** no enterprise HSE; `safety` covers basics.
- **Edit notes:**

### temporary_works — TW governance
- **Identity:** business · routes≈20 · **Depends on:** [projects]
- **Entities:** `_item`(tw_type, design_check_category, designer/checker/twc_name+user_id, required_load/strike_date, design_due_date, formwork_assignment_id, design/check_certificate_document_id, schedule_activity_id) · `_permit`(permit_type, status, valid_from/to, inspection_id, prereq_design_check_accepted, prereq_inspection_passed)
- **Features:** [F1] TW items (designer/checker/TWC roles) · [F2] design-check categories · [F3] load/strike dates · [F4] permits (prereq-gated) · [F5] formwork/schedule links.
- **Removal impact:** no TW governance register.
- **Edit notes:**

### formwork — Formwork planning & cost
- **Identity:** estimation · routes≈50 · **Depends on:** [projects, boq]
- **Entities:** `_system`(system_type, supplier, reuses_max, unit_rate, erect_strike_rate, strip_time_days, cycle_days) · `_assignment`(boq_position_id, formwork_system_id, reuse_count, waste_pct, computed_unit/material/labour_cost) · `_schedule_line`(assignment_id, pour_no, pour_date, level_label)
- **Features:** [F1] formwork systems (reuse/cycle) · [F2] priced assignments (BOQ) · [F3] pour-cycle schedule.
- **Removal impact:** no formwork planning/cost.
- **Edit notes:**

### equipment — Equipment & fleet
- **Identity:** business · routes≈96 · **Depends on:** [users, projects]
- **Entities (10):** `_type`(service intervals) · `_equipment`(serial, ownership, status, hour_meter, odometer, depreciation) · `_telemetry`(fuel/meter/geo/engine) · `_maintenance_schedule`(trigger, next_due) · `_work_order`(technician, cost) · `_inspection`(valid_until, certificate) · `_rental`(internal rate/day/hour, billing) · `_fuel_log` · `_parts_log` · `_damage_report`
- **Features:** [F1] equipment register + types · [F2] telemetry · [F3] maintenance schedules + work orders · [F4] inspections/certs · [F5] internal rental billing · [F6] fuel/parts logs · [F7] damage reports · [F8] depreciation.
- **Removal impact:** no fleet management.
- **Edit notes:**

### assets — Asset operations
- **Identity:** core · routes≈10 · **Depends on:** [users, projects] · (tables not detected — thin/registry) **Features:** [F1] owned-asset lifecycle register. **Removal impact:** no asset register.
- **Edit notes:**

### methodology — Estimating/analytic methodology
- **Identity:** core · routes≈42 · **Depends on:** [projects, boq]
- **Entities:** `oe_methodology`(slug, scope, country_code, industry, currency, decimals, hierarchy_levels, dimension_scheme, column_preset, base_mapping, composites, cascade_steps, vat_rate, is_builtin/editable) · `oe_analytic_dimension`(key, label, kind, is_required)(+`_value`, `oe_position_dimension_value`) · `oe_funding_source`(code, name)
- **Key logic:** defines the analytic/costing methodology (dimensions, hierarchy, cascade, VAT) a project's BOQ follows; funding sources.
- **Features:** [F1] methodology definitions (builtin + custom) · [F2] analytic dimensions + values · [F3] position dimension tagging · [F4] funding sources · [F5] VAT/decimals/cascade config.
- **Removal impact:** BOQ loses its analytic/dimension framework.
- **Edit notes:**

---

*Batch 5 of 7 complete. Next: Batch 6 (Documents & Collaboration). Grounded @ `a96d525`.*
