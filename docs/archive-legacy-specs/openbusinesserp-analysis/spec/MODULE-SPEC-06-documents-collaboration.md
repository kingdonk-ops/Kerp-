> ## ⚠ Field-Ops pivot status (authoritative: ../FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md)
> **REMOVED:** ~~cad~~ ~~bim_hub~~ · **KEPT:** documents (CDE), all file_*, rfi, submittals, transmittals, correspondence, meetings, markups, plan_room, collaboration(+locks), approval_routes.

# OpenBusinessERP — Module Specifications · Batch 6: Documents & Collaboration

> Editable specs (format in `MODULE-SPEC-00`). Entities = real `models.py` columns @ `a96d525`.

**Modules (24):** `documents` + 11 `file_*`, `rfi`, `submittals`, `transmittals`, `correspondence`, `meetings`, `markups`, `plan_room`, `collaboration`, `collaboration_locks`, `approval_routes`, `bim_hub`, `cad`.

---

### documents — Document Management / CDE
- **Identity:** core · prefix=`/documents` · routes≈84 · **Depends on:** [projects] · **Dependents:** file_*, closeout, plan_room, AI index
- **Entities:** `_document`(name, category, file_size, mime_type, file_path, version, uploaded_by, tags, cde_state, suitability_code, revision_code, drawing_number, is_current_revision, parent_document_id, security_classification, discipline, bim_links) · `_photo`(gps_lat/lon, taken_at, category) · `_sheet`(page_number, sheet_number/title, discipline, revision, is_current, previous_version_id) · `_bim_link`(bim_element_id, link_type, confidence, region_bbox)
- **Key logic:** CDE states + suitability/revision codes; drawing sheets with revision chains; BIM element links; files via storage service (never raw paths); **the AI vector index embeds this row's metadata** (see Part C5).
- **Features:** [F1] documents + categories · [F2] CDE states + suitability/revision codes · [F3] drawing sheets (revision chain) · [F4] photos (geo) · [F5] BIM links · [F6] security classification/discipline · [F7] versioning pointer.
- **Removal impact:** core — the `/files` hub; `file_*`, closeout, AI index all depend on it.
- **Edit notes:**

### file_* subsystem — each adds ONE capability to `/files`
All `category=core`, `Depends on:[projects, users]` (+documents where noted). Each is independently removable.
- **file_versions** — `_file_version`(file_kind, file_id, version_number, canonical_name, previous_version_id, is_current, superseded_by_id, checksum). [F1] version history per file.
- **file_approvals** — `_file_stamp_template`(svg_template) · `_file_approval_workflow`(file_version_snapshot, status, stamp_template_id, stamped_artifact_path) · `_file_approval_step`(approver_id, decision). [F1] multi-step approval + [F2] stamps.
- **file_comments** — `_file_comment`(parent_id, body, page_number, anchor_x/y, resolved) · `_file_comment_mention`. [F1] threaded file comments + [F2] @mentions + [F3] anchored/resolve.
- **file_tags** — `_file_tag`(name, color, category) · `_file_tag_assignment`. [F1] tags + [F2] assignment.
- **file_search** — `_file_search_index`(content_text, page_count, ocr_engine, language, indexed_at). [F1] full-text/OCR content index (Postgres tsvector). *(See Part C5.)*
- **file_saved_views** — `_file_saved_view`(filter_json, is_pinned, is_shared, use_count). [F1] Smart Folders / saved filters.
- **file_favorites** — `_file_favorite`(file_kind, file_id, pinned). [F1] pins.
- **file_references** — `_file_reference`(target_type/id, relation) · `_file_naming_violation`(rule_set, violation_codes, acknowledged). [F1] file↔record links + [F2] naming-rule violations.
- **file_distribution** — `_file_distribution_list`(+`_member`), `_file_distribution_subscription`(notify_on). [F1] distribution lists + [F2] subscriptions/notify + [F3] cross-project search.
- **file_transmittals** — `_file_transmittal`(number, reason_code, status, cover_sheet_path)(+`_item`: file_version_snapshot; `_recipient`: acknowledge_token). [F1] formal file issue + [F2] recipients/ack.
- **file_trash** — `_file_trash`(original_kind/id, payload_json, retention_days, restored/purged, restore_token). [F1] soft-delete + [F2] restore + [F3] retention/purge.
- **Removal impact (each):** drops just that `/files` capability; the document core keeps working.
- **Edit notes:**

### rfi — Requests for Information
- **Identity:** core · routes≈36 · **Depends on:** [users, projects]
- **Entities:** `_rfi`(rfi_number, subject, question, raised_by, assigned_to, status, ball_in_court, official_response, responded_by/at, cost_impact(+value), schedule_impact(+days), date_required, response_due_date, priority, discipline, linked_drawing_ids, change_order_id)
- **Features:** [F1] RFIs (Q→official response) · [F2] ball-in-court · [F3] cost/schedule impact · [F4] drawing/change-order links · [F5] due dates/priority.
- **Removal impact:** no RFI register.
- **Edit notes:**

### submittals — Submittal register
- **Identity:** core · routes≈30 · **Depends on:** [users, projects]
- **Entities:** `_submittal`(submittal_number, title, spec_section, submittal_type, status, ball_in_court, current_revision, submitted_by_org, reviewer_id, approver_id, date_submitted/required/returned, linked_boq_item_ids)
- **Features:** [F1] submittals (spec-section) · [F2] review/approve cycle + ball-in-court · [F3] revisions · [F4] BOQ links.
- **Removal impact:** no submittal management.
- **Edit notes:**

### transmittals — Document-issue tracking
- **Identity:** core · routes≈20 · **Depends on:** [users, projects, contacts]
- **Entities:** `_transmittal`(transmittal_number, subject, sender_org_id, purpose_code, issued_date, response_due_date, status, is_locked)(+`_recipient`: action_required, acknowledged_at, response; `_item`: document_id, revision_id)
- **Features:** [F1] transmittals (purpose-coded) · [F2] recipients (+ack/response) · [F3] document items (+revision) · [F4] lock.
- **Removal impact:** no issue tracking (distinct from file_transmittals, which is file-centric).
- **Edit notes:**

### correspondence — Project correspondence log
- **Identity:** core · routes≈14 · **Depends on:** [users, projects, contacts]
- **Entities:** `_correspondence`(reference_number, direction, subject, from_contact_id, to_contact_ids, date_sent/received, correspondence_type, linked_document/transmittal/rfi_ids, status, response_required_by, contract_clause_ref)
- **Features:** [F1] correspondence log (in/out) · [F2] contract-clause ref · [F3] links (doc/transmittal/RFI) · [F4] response tracking.
- **Removal impact:** no correspondence register.
- **Edit notes:**

### meetings — Meetings & minutes
- **Identity:** core · routes≈58 · **Depends on:** [users, projects]
- **Entities:** `_meeting`(meeting_number/type, title, date, location, chairperson_id, attendees, agenda_items, action_items, minutes, status, series_id, recurrence_rule, is_series_master) · `_attendance`(checked_in_at, signature_image_path) · `_action_item`(owner_id, due_date, status, closed_in_meeting_id) · `_minutes`(content, issued/distributed)
- **Features:** [F1] meetings (+series/recurrence) · [F2] attendance (+signature) · [F3] agenda · [F4] action items (cross-meeting close) · [F5] minutes (issue/distribute).
- **Removal impact:** no meeting/minutes tracking.
- **Edit notes:**

### markups — Markups & annotations
- **Identity:** core · routes≈38 · **Depends on:** [projects]
- **Entities:** `_markup`(document_id, file_version_id, page, type, geometry, text, color, line_width, author_id, assignee_id, status, measurement_value/unit, stamp_template_id, linked_boq_position_id, layer) · `_scale_config`(pixels_per_unit, calibration_points) · `_stamp_template` · `_comment`(markup_id, body)
- **Features:** [F1] markups/annotations (geometry) · [F2] scale calibration + measurement · [F3] stamp templates · [F4] markup comments · [F5] BOQ link · [F6] assignee/status.
- **Removal impact:** no drawing markup.
- **Edit notes:**

### plan_room — Controlled current drawings
- **Identity:** business · routes≈6 · **Depends on:** [projects, users, documents]
- **Entities:** `_plan_pin`(document_id, page, x, y, note, photo_ref, file_version_id)
- **Features:** [F1] plan-room pins on current drawings.
- **Removal impact:** no plan-room pinning.
- **Edit notes:**

### collaboration — Comments/viewpoints on any entity
- **Identity:** core · routes≈14 · **Depends on:** [users]
- **Entities:** `_comment`(entity_type, entity_id, author_id, text, comment_type, parent_comment_id, edited_at, is_deleted) · `_mention`(mentioned_user_id, mention_type) · `_viewpoint`(entity_type/id, viewpoint_type, data, comment_id)
- **Features:** [F1] threaded comments on **any** entity (entity-URI) · [F2] @mentions · [F3] viewpoints (BIM/doc camera state).
- **Removal impact:** no generic cross-entity commenting.
- **Edit notes:**

### collaboration_locks — Edit locks
- **Identity:** core · routes≈12 · **Depends on:** [users]
- **Entities:** `_collab_lock`(org_id, entity_type, entity_id, user_id, locked_at, heartbeat_at, expires_at)
- **Features:** [F1] heartbeat edit locks (concurrent-edit guard).
- **Removal impact:** no edit-lock protection.
- **Edit notes:**

### approval_routes — Reusable multi-step approvals
- **Identity:** core · routes≈40 · **Depends on:** [projects, users] · **Dependents:** many (contracts, construction_control, etc.)
- **Entities:** `_route`(target_kind, system_key) · `_step`(approver_role/user_id, mode, required_approver_count, sla_hours) · `_instance`(target_kind/id, current_step_ordinal, status, current_assignee) · `_step_state`(approver_user_id, decision, comment) · `_delegation`(delegator/delegate, starts/ends_at)
- **Features:** [F1] configurable routes (role/user steps, quorum, SLA) · [F2] instances (per target) · [F3] step decisions · [F4] delegations.
- **Removal impact:** modules needing approvals lose the shared engine.
- **Edit notes:**

### bim_hub — BIM model hub
- **Identity:** core · prefix=`/bim` · **Depends on:** [users, projects, boq]
- **Entities (8):** `_bim_model`(discipline, model_format, version, element/storey_count, bounding_box, parent_model_id) · `_bim_element`(stable_id, element_type, storey, properties, quantities, geometry_hash, mesh_ref, lod_variants, is_tracked_asset) · `_bim_boq_link`(boq_position_id, bim_element_id, link_type, confidence, rule_id) · `_bim_quantity_map`(element_type_filter, property_filter, quantity_source, multiplier, waste_factor_pct, boq_target) · `_bim_model_diff` · `_bim_element_group`(is_dynamic, filter_criteria) · `_bim_federation`(+`_federation_model`: color_hint, z_order)
- **Features:** [F1] BIM models (+versions, parent) · [F2] elements (properties/quantities/geometry) · [F3] element↔BOQ links · [F4] quantity maps (→4D/5D) · [F5] model diffs · [F6] element groups (static/dynamic) · [F7] federations.
- **Removal impact:** no BIM; takeoff/quantity-links lose a source.
- **Edit notes:**

### cad — CAD/DWG handling (library)
- **Identity:** extension · **Depends on:** [projects] · (no tables; backs takeoff/dwg_takeoff/CAD explorer) **Features:** [F1] CAD/DWG parse/convert services. **Removal impact:** drawing-based takeoff breaks.
- **Edit notes:**

---

*Batch 6 of 7 complete. Next (final): Batch 7 (Platform, AI, Compliance & Regional — 85 modules). Grounded @ `a96d525`.*
