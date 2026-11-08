# Module Specs · Batch 10 (NEW): documents (VFS folded in) · workpacks · awp

> Two new modules (`workpacks`, `awp`) plus a **capability extension to the existing `documents` module** — the Virtual File System (folder tree, routing, orphans, lockers, revisions) is now **part of `documents`, not a separate module.** Specified in the house format and **built on existing modules — reuse first.** Every table carries `BaseRecordMixin` (entity URI, `tenant_id`, hash, provenance, revision) + an RLS policy; heavy work runs in the ARQ worker (ADR-001), never in the web loop; AI-assisted steps carry `source="ai"` and need human confirmation. Grounded on the in-scope module set in `../MODULE-SPECS.md`.

**Reuse ethos:** the VFS is a *virtual tree over the bytes `documents` already stores* (+ `file_versions` revisions) — one module, no new store; `workpacks` *orchestrates* `qms`/`asset_management`/`wps`/`site_inventory`/`signing`/`markups`/`ncr`; `awp` is a *constraint/state machine* over `schedule_advanced`/`procurement`/`documents`, emitting `workpacks`. None of them re-implement storage, ITP, welds, stock, e-sign, inspection or scheduling — they call those modules' `service.py`.

---

## 1 · `documents` — VFS capability folded in (virtual tree, routing, orphans, lockers, revisions)

The Virtual File System is **not a new module** — it is a set of tables and services added to `documents`, which already owns the physical bytes (object-store key on `_document.file_path`), CDE states, revisions (via its dependent `file_versions`) and drawing sheets. Folding VFS in keeps one `/documents` surface and one `service.py`; no cross-module hop for file operations.

- **Identity:** core · prefix=`/documents` (VFS routes under `/documents/vfs/*`) · **Depends on:** [projects, file_versions, file_references, uploads, resumable_uploads, users, teams, signing] · **Dependents:** workpacks, awp, `site_inventory`, inspections, every module that files a document.
- **Purpose (VFS part):** give files a **logical** identity — folder tree, pre-set structures, entity links, revisions, lockers, orphans — decoupled from **physical** storage. Physical bytes stay in the object store under a flat UUID key; the `_document` row and the new VFS tables give them a rename-safe tree.

### What `documents` already provides (unchanged, reused by the VFS)
`_document`(name, category, file_path [object-store key], mime_type, version, uploaded_by, tags, cde_state, suitability_code, revision_code, drawing_number, is_current_revision, parent_document_id, security_classification, discipline, asset_links) · `_photo` · `_sheet`(page/sheet number, discipline, revision chain) · `_asset_link`. Revisions/checksum/rollback live in the dependent `file_versions` (`_file_version`: version_number, checksum, previous/superseded). The storage service and CDE/ISO-19650 container states are already here.

### New tables added to `documents` for the VFS
- `oe_documents_folder`(project_id, parent_id self-FK nullable, name, folder_type[`system_preset`|`user_personal`|`custom`], owner_user_id nullable, versioning_enabled bool, path materialised, sort_order, is_immutable bool) — the tree; `versioning_enabled` cascades to documents inside it.
- **`_document` gains:** `folder_id`(→`oe_documents_folder`, nullable), `is_orphaned`(bool), `source_module`(text). The logical "file" **is** a `_document` row — no separate file table.
- `oe_documents_file_link`(document_id→`_document`, target_type[`asset`|`asset_item`|`rsw`|`weld`|`stock_item`|`work_pack`|`material_batch`|…], target_id, relation, link_metadata jsonb) — polymorphic entity association, built on the `file_references` pattern. `ON DELETE` of the target removes the **link** and flips `_document.is_orphaned=true` — the file is never destroyed.
- `oe_documents_share`(folder_id|document_id, shared_with_user_id|shared_with_role, permission[`view_download`|`edit_contribute`], external_token nullable, password_hash nullable, expires_at) — locker/file sharing + time-limited external links (reuses `signing`/`portal` magic-link machinery for external tokens).

### Endpoints (added under `/documents/vfs`)
- Tree: `GET /documents/vfs/tree?project_id=` · `POST /documents/vfs/folders` · `PATCH /documents/vfs/folders/{id}` (rename — **DB-only, no physical move**) · `POST /documents/vfs/folders/{id}/move`.
- Files: `POST /documents/vfs/route-and-store` (the resolver, below) · `GET /documents/vfs/files?folder_id=&target_type=&target_id=` · `GET /documents/{id}/revisions` (existing, via `file_versions`) · `POST /documents/vfs/files/{id}/reassign` (attach a link + move into the pre-set path).
- Orphans: `GET /documents/vfs/orphaned?project_id=` (the set-difference query) · bulk reassign.
- Lockers/shares: `GET /documents/vfs/lockers/{user_id}` · `POST /documents/vfs/shares` · `GET /documents/vfs/shared/{token}` (external).
- Presets: `POST /documents/vfs/bootstrap?project_id=` (scaffold the pre-set tree).

### Key logic / state (VFS)
- **Rename-safe by construction:** folders and `_document` rows store logical names + `parent_id`/`folder_id`; the object-store key (`_document.file_path`) is a flat `/{project_id}/{uuid}`. Renaming a site/asset/RSW is a DB update — **never** a recursive FS/S3 copy.
- **Routing resolver** (`documents.route_and_store`): a module calls it with `{source_module, category, sub_category, product_name, serial_batch, file_type, target_entity_type, target_entity_id, file_stream|upload_session_id}`. It (1) evaluates routing rules (e.g. `materials → welding_rods → {product_name}`), (2) idempotently finds/creates the virtual folder path, (3) standardises the filename (`{product_name}_{serial_batch}_{YYYYMMDD}`), (4) registers the object-store key as a `_document` (+ `file_versions`), (5) creates the `oe_documents_file_link`. Modules **never write raw paths.**
- **Pre-set scaffold** on project create: `Sites/{Site}/{Asset}/{RSW}/{Photos,NDT_Reports,ITP_Signoffs}`, `Materials & Stock/{group}/{product}/…`, `Users/{First_Last} [Private]`, and the virtual `Orphaned Files` view. `system_preset` folders are `is_immutable=true`.
- **Versioning toggle** (per folder, overridable per document): ON → each same-name upload increments `file_versions.version_number` and repoints the current revision; OFF → overwrite the object + update checksum/size in place, keep no history. The ON path reuses `file_versions` entirely.
- **Orphan engine:** a document is orphaned when it has no `oe_documents_file_link`, or `is_orphaned=true` (target deleted), or `folder_id IS NULL`. Surfaced in an "Orphaned & Unassigned" smart filter with source-module/uploader/date cards and a quick reassign (pick entity type + autocomplete tag → attaches link + moves into the pre-set path). The **upload-orphan sweep** (presigned sessions that never completed, ADR-001 §3) runs as an ARQ job over `resumable_uploads._session`.
- **Lockers & sharing:** every project user gets `/Users/{First_Last}` (`user_personal`, owner-private); access check = owner or admin or an explicit `oe_documents_share`. External client/audit links are time-limited signed tokens (reuse `portal` magic-link).
- **Controlled-folder hooks:** a document landing in a controlled folder (e.g. `/…/ITP_Signoffs/`) triggers `file_approvals`/`signing` as before — the VFS just routes it there.

### Verification (via `run_checks`)
- `document.route` (resolved target entity exists and is in the caller's project; filename standardised), `document.share` (no personal-locker exposure without an explicit share; external token honours expiry/password), `document.immutable` (no writes into a `system_preset`/`is_immutable` folder except through the resolver).

### Features (VFS additions; existing documents features [F1–F7] unchanged)
[F8] virtual folder tree (rename-safe) · [F9] pre-set structure generator · [F10] routing resolver (module → folder + standard name) · [F11] polymorphic entity links · [F12] orphan detection + reassignment · [F13] personal lockers + granular sharing (roles, external tokens) · [F14] per-folder/document versioning toggle (over `file_versions`) · [F15] controlled-folder hooks into `file_approvals`/`signing`.
- **Removal impact (VFS features):** files fall back to the flat `documents`/`file_*` hub with no logical tree, routing or orphan recovery; the rest of `documents` is unaffected. (Removing `documents` itself remains catastrophic — it is the `/files` hub.)
- **Edit notes:** _`cde`-style ISO-19650 container states already live in `documents`; the VFS just arranges them into the `/Site/Asset/RSW` convention. Keep the resolver's routing rules as data (a `routing_rules` config) so new file categories don't need code. `file_versions`/`file_references`/`file_approvals` stay as independent `/files` capabilities; the VFS consumes them rather than absorbing them._

---

## 2 · `workpacks` — Inspection/Field Work Packs (IWP) + ITP execution

- **Identity:** business · prefix=`/workpacks` · **Depends on:** [projects, asset_management, qms, wps, site_inventory, procurement, documents, markups, signing, ncr, notifications, jobs, reporting, construction_control, field_diary, validation] · **Dependents:** awp (generates IWPs), portal (co-sign), reporting/MDR.
- **Purpose:** compile the self-contained field dossier for a scheduled job (scope, safety, drawings, ITP hold points, blank check-sheets, batch certs, TQs), run the live tablet sign-off with offline support, and compile the signed MDR/inspection PDFs — **reusing** the ITP, weld, stock, drawing, e-sign and NCR modules rather than re-implementing them. Files are filed through the `documents` VFS.

### Reuse map (critical — the user asked to reuse as much as possible)
| Work Pack concern | Reused module | How `workpacks` uses it |
|---|---|---|
| ITP master template + stages + hold/witness points | **`qms`** (`_itp_template`, `_itp_item`: control_point, acceptance_criteria, hold_witness_point, signatories) + **`construction_control`** (`_acceptance_criterion`, `_hold_gate`, `_handover_package`) | the IWP **clones** `qms` ITP stages into execution stages; hold points map to `construction_control._hold_gate` and block via `run_checks`. No new ITP authoring. |
| Scope (site/asset/RSW/component/weld) | **`asset_management`** (`oe_asset_node`, `oe_asset_item`) | the pack references `rsw_node_id`/`asset_item_id`; component tag = an Item. |
| Welds, welder qualification, NDE | **`wps`** (`oe_weld`, `oe_welder_qualification`, `oe_weld_nde_result`) | weld-stage sign-off defers to `wps` rules R-WPS-1..4; the pack just surfaces them. |
| Allocated materials + batch + MTC cert | **`site_inventory`** / **`procurement`** (goods receipts, batches) + **`documents`** VFS (cert files) | `WorkPackMaterialAllocation` points at a stock batch and its MTC `document`; cert auto-pulled, never re-uploaded. |
| Drawings / isometrics / markups + filing | **`documents`** (`_sheet`, revisions, VFS tree) + **`markups`** (`_markup`: geometry, layer, linked item) | drawing links point at `documents` current revisions; markup overlay is a `markups` layer (non-destructive — base stays immutable); compiled PDFs file through the VFS resolver. |
| E-signatures / stamps | **`signing`** (`_session`, `_signature`: content_hash, cert) | every stage/client sign-off writes a `signing` signature; PDF stamp burned by the compiler. |
| Non-conformance | **`ncr`** | a failed stage raises an `ncr` (`source` = field) and links it; does not duplicate NCR logic. |
| Offline capture + idempotent sync | **`field_diary`** pattern (`oe_field_sync_ledger`: client_op_id, op_kind, result) | the tablet queue replays through the same idempotent ledger; magic-link/PIN auth reused. |
| Hold-point ready alerts | **`notifications`** (push/in-app) | gating emits a notification to the assigned QC inspector. |
| Heavy PDF/QR compilation | **`jobs`** + ARQ worker (ADR-001) | compilation runs off the web loop; status polled via `jobs`. |
| Instant inspection certificate | **`reporting`** (`_template`, `_generated`) | single-stage cert is a `reporting` template rendered + filed via the `documents` VFS. |
| Verification gates | **`validation`/`eac`** → `run_checks` | all stage/issue gates are rule sets, not inline code. |

### Entities (new — the execution instance; templates reuse `qms`, files reuse `documents`)
- `oe_workpack`(project_id, work_pack_number [unique per tenant], rsw_node_id→asset, site_id, asset_id, component_asset_item_id nullable, title, status[`draft|issued|in_progress|on_hold|completed|cancelled`], supervisor_id, qc_inspector_id, scope_of_work, safety_requirements jsonb {ptw, loto, heights, confined_space, jha_ref}, itp_template_id→`qms._itp_template`, compiled_pdf_document_id→`documents`, folder_id→`documents` (the pre-set `/Site/Asset/RSW/`), awp_cwp_id nullable→`awp`).
- `oe_workpack_execution_stage`(workpack_id, sequence, stage_name, acceptance_criteria, contractor_point/client_point/third_party_point[`H|W|S|R`], required_form_code, status[`pending|ready_for_inspection|signed|ncr`], is_signed_off, signed_by_user_id, signed_at_utc, client_signed_by/at, measurement_data jsonb, has_ncr, ncr_id→`ncr`, hold_gate_id→`construction_control._hold_gate`) — **cloned** from `qms` ITP stages at issue.
- `oe_workpack_drawing_link`(workpack_id, document_id→`documents`, drawing_number, sheet_number, revision, markup_layer_id→`markups`).
- `oe_workpack_material_allocation`(workpack_id, stock_item_id→`site_inventory`, product_name, batch_number, cert_document_id→`documents`, allocated_quantity, uom).
- `oe_workpack_tq_link`(workpack_id, tq_id→`awp._technical_query`, applies) — surfaces an approved TQ banner (TQ itself lives in `awp`, §3).

### Endpoints
- Lifecycle: `POST /workpacks` (manual) · `POST /workpacks/auto` (compiler, below) · `GET /workpacks?project_id=&status=&rsw=` · `GET/PATCH /workpacks/{id}` · `POST /workpacks/{id}/issue` · `POST /workpacks/{id}/cancel`.
- Execution: `POST /workpacks/stages/{id}/complete` (supervisor) · `POST /workpacks/stages/{id}/inspect` (QC hold-point release) · `POST /workpacks/{id}/sync` (offline delta batch).
- Output: `POST /workpacks/{id}/compile` (enqueue ARQ MDR/pack compile) · `GET /workpacks/{id}/report?stage=` (instant single-stage cert) · `POST /workpacks/auto-file-scan` (QR auto-filer).

### Key logic / state
- **Automated compilation (`create_automated_work_pack`):** (1) resolve scope from `asset_management`; (2) **clone** `qms` ITP template stages → `oe_workpack_execution_stage`; (3) pull reserved batches + MTCs from `site_inventory`/`procurement`, link via `documents`; (4) link latest **approved** drawing revisions from `documents`; (5) `documents.get_or_create_rsw_folder(...)` for the pre-set path; (6) attach any approved TQs from `awp`. Pure orchestration — no new ITP/stock/drawing/storage tables.
- **Work-pack state machine:** `draft → issued → in_progress → (on_hold) → completed / cancelled`. `issued` requires `workpack.issue` checks (below).
- **Offline-first sign-off** (reusing `field_diary` ledger): the tablet commits a stage locally (IndexedDB), stamps UTC, attaches readings/photos, advances; on reconnect a delta (`STAGE_COMPLETED`/`MEASUREMENT_LOGGED`/`PHOTO_ATTACHED`) replays through `oe_field_sync_ledger` keyed on `client_op_id` (idempotent).
- **Supervisor → inspector handoff / gating:** on stage complete, evaluate the ITP matrix; if the next stage carries a **Hold (H)** or **Witness (W)** point, create/arm the `construction_control._hold_gate`, set next stage `ready_for_inspection`, and `notifications.notify_user(qc_inspector)`. The QC inspector's `inspect` call verifies readings, writes a `signing` signature, and **releases the hold gate** via `construction_control` (`run_checks: workpack.stage.signoff`) — unlocking the next trade stage. A weld stage additionally defers to `wps` `weld.accept` (R-WPS-4).
- **Adaptive measurement collector:** `measurement_data` is JSONB validated at the API boundary by **versioned Pydantic/JSONSchema** per stage type (ambient/dew-point ΔT≥3°C, SSPC-PA2 5-spot DFT matrix mean/min, weld visual+NDT ref, holiday-spark kV, adhesion MPa). Schemas are data; GIN-indexed (`jsonb_path_ops`). Same dynamic-schema discipline as `asset_management`.
- **Non-destructive markup:** base drawing in `documents` is immutable; inspector pins/defects are a `markups` overlay layer (coordinates, label, `linked_stage_id`, photo). The ARQ compiler **burns** the vector overlay onto a *copy* page stamped "As-Built Inspection Markup Copy".
- **QR auto-filer:** compiled pack cover carries `AIP-WP:{workpack_id}` QR. A returned scanned PDF → ARQ task renders page 1, decodes the QR (pyzbar), and files the signed PDF into the pack's pre-set `documents` folder; no QR → routed to the `documents` Orphaned Files view.
- **Instant inspection report:** `GET …/report?stage=` renders a single-stage certificate (ambient/DFT/weld) via `reporting`, pulling site metadata, in-place signatures, gauge serials, GPS/UTC photos, filed to `/…/Inspection_Reports/` via the `documents` VFS.

### Verification (via `run_checks`)
- `workpack.issue`: ITP template resolves; scope anchors to a live RSW/Item; **drawings are approved revisions** (no Rev A/draft); allocated batches have MTC certs; any applicable TQ is approved.
- `workpack.stage.signoff`: previous stage complete; measurement_data passes the stage JSONSchema (e.g. ΔT≥3°C; DFT within min/max); weld stages satisfy `wps` rules; a Hold point cannot be released by the same person who signed the contractor point where policy requires separation; a failed criterion forces an `ncr`.

### Features
[F1] work packs (IWP) scope/safety/header · [F2] ITP execution stages cloned from `qms` · [F3] hold/witness/surveillance/review points gated via `construction_control` · [F4] drawing links + non-destructive `markups` overlay · [F5] batch/MTC allocation from `site_inventory` · [F6] adaptive JSONB measurement collector (versioned schemas) · [F7] offline-first tablet sign-off (reusing `field_diary` ledger) · [F8] supervisor→QC gating + `notifications` · [F9] ARQ MDR/pack PDF compile (QR cover, page X/Y, cert append) · [F10] QR auto-filer → `documents` VFS · [F11] instant single-stage inspection certificate via `reporting` · [F12] NCR raising via `ncr` · [F13] approved-TQ warning banner (from `awp`).
- **Removal impact:** no compiled field dossiers or live ITP sign-off; the underlying `qms`/`wps`/`inspections` registers still work standalone but lose the bundled execution flow and the MDR pipeline.
- **Edit notes:** _Everything heavy is an ARQ job; the web API only enqueues. The template→instance split means editing an ITP in `qms` never mutates issued packs. If a client wants a different pack layout, it's a `reporting`/compiler template change, not schema._

---

## 3 · `awp` — Advanced Work Packaging (EWP · PWP · CWP → IWP) + Technical Queries

- **Identity:** business · prefix=`/awp` · **Depends on:** [projects, asset_management, documents, schedule_advanced, procurement, supplier_catalogs, site_inventory, qms, workpacks, moc, validation, notifications] · **Dependents:** workpacks (receives released scope).
- **Purpose:** implement the CII/COAA **AWP** sequence — Engineering (EWP) → Procurement (PWP) → Construction (CWP) → **Installation/field (IWP)** — as a **constraint engine and state machine** that only releases field work packs once engineering and procurement are constraint-free. Prevents premature inspections (draft drawings, missing certs, unresolved TQs). Mostly orchestration over existing modules.

### Reuse map
| AWP concern | Reused module | How |
|---|---|---|
| Scope hierarchy / area / asset | `asset_management` | EWP/CWP scope = an Asset subtree. |
| Drawings, isometrics, P&IDs, specs, ISO-19650 status, filing | `documents` (incl. VFS tree) | EWP references approved `documents` revisions; "Rev 0/Approved" read from CDE state. |
| ITP master template | `qms._itp_template` | EWP carries the master ITP ref; IWP clones it (via `workpacks`). |
| BOM / takeoff → PO → goods receipt → batch/MTC | `procurement`, `supplier_catalogs`, `site_inventory` | PWP is a view over POs + warehouse receipts + batch certs; "materials received" is a query, not new data. |
| Look-ahead / constraint-free scheduling / PPC | `schedule_advanced` (`_look_ahead`, `_constraint`, `_weekly_plan`) | CWP decomposes into IWPs aligned to the 1–2 week look-ahead window. |
| Change control for TQ/concession lifecycle | `moc` (`_entry`: propose→review→decide→implement) + `documents` | a TQ **is** an MoC-style concession with a scope map + concession sheet. |
| Field job pack generation | `workpacks` | `awp` calls `workpacks.create_automated_work_pack` on release. |
| Gates | `validation`/`eac` → `run_checks` | readiness is rule sets. |

### Entities (new — the packaging envelopes + TQ; underlying data stays in the reused modules)
- `oe_awp_ewp`(project_id, code, area/asset_subtree_root_id→asset, discipline, status[`in_design|issued|superseded`], drawing_cde_state, itp_template_id→`qms`, bom_ref, spec_refs jsonb, approved_at) — engineering envelope; drawings/specs are references, not copies.
- `oe_awp_pwp`(project_id, ewp_id, status[`sourcing|partially_received|complete`], po_refs jsonb→`procurement`, laydown_zone, missing_items jsonb, critical_materials_received bool) — procurement envelope as a materialised view + status cache over `procurement`/`site_inventory`.
- `oe_awp_cwp`(project_id, code, ewp_id, pwp_id, discipline, area, status[`blocked|hold|constraint_free|released`], schedule_window, jha_master_ref, readiness_report jsonb) — construction envelope; the thing the constraint engine evaluates.
- `oe_awp_cwp_iwp`(cwp_id, workpack_id→`workpacks`, look_ahead_ref→`schedule_advanced`, generated_at) — the decomposition link to field packs.
- `oe_awp_technical_query`(project_id, tq_number, title, type[`substitution|re_prep|geometry_change|spec_deviation|other`], scope_level[`project|asset|work_pack|product_group`], scope_ref (asset_id|work_pack_id|product_group|null=project), status[`raised|under_review|approved|rejected`], moc_entry_id→`moc`, concession_document_id→`documents`, approved_by_client, approved_at, from_product, to_product) — the TQ/concession, its lifecycle delegated to `moc`.

### Endpoints
- EWP/PWP/CWP CRUD + status transitions; `GET /awp/cwp/{id}/readiness` (runs the constraint engine); `POST /awp/cwp/{id}/release` (decompose → IWPs via `workpacks`).
- TQ: `POST /awp/tqs` · `GET /awp/tqs?scope_level=&scope_ref=` · `POST /awp/tqs/{id}/approve` · `GET /awp/tqs/applicable?asset_id=&product=&work_pack_id=` (resolves banners).

### Key logic / state (the constraint engine)
- **Staggered generation** — each envelope matures at its gate, not all at once:
  - **EWP** auto-generated/sealed when the model is frozen and drawings reach **Rev 0/Approved** in `documents` CDE and specs are published.
  - **PWP** reaches `complete` when the BOM is matched against POs/receipts in `procurement`/`site_inventory` and batch MTCs exist — locking batch numbers + certs to the scope.
  - **CWP** `constraint_free` only when `evaluate_cwp_readiness` passes: `ewp.drawings_approved` ∧ `pwp.critical_materials_received` ∧ **no unresolved TQs**. Otherwise `BLOCKED` (named reason) or `HOLD` (pending TQ).
  - **IWP** generated 1–2 weeks ahead when a field supervisor schedules the shift (aligned to `schedule_advanced` look-ahead) — `awp` calls `workpacks.create_automated_work_pack`, which inherits the locked batches + approved drawings + master ITP automatically.
- **Automated batch inheritance:** because PWP locked the batch/MTC to the scope, those exact batch numbers cascade into the IWP's ITP check-sheets (`workpacks` material allocation) with no manual entry.
- **Automated TQ stamping:** a TQ approved during procurement (e.g. paint substitution under `TQ-2026-088`) tags its scope (product group / asset / project); any IWP that resolves a tagged asset or material gets the high-contrast **"Specification Variance (Approved TQ)"** banner before work begins, and the concession sheet + cert are pulled into pack Section 6 and the MDR index. Resolution is the `GET /awp/tqs/applicable` query across scope levels.

### Verification (via `run_checks`)
- `cwp.release`: EWP drawings Rev 0/approved; PWP critical materials received with certs; no unresolved TQs in scope; schedule window set. Blocks release with a named reason (no premature field packs).
- `iwp.generate`: a releasing CWP is `constraint_free`; the look-ahead window exists; scope anchors to live RSWs.
- `tq.apply`: TQ `approved` (via `moc`) before its banner/concession is applied to any pack; scope resolution is deterministic (product_group ⊂ asset ⊂ project).

### Features
[F1] EWP envelopes (approved drawings/specs/ITP refs) · [F2] PWP envelopes (PO/receipt/batch/MTC status over `procurement`) · [F3] CWP envelopes (discipline/area scope) · [F4] constraint engine (BLOCKED/HOLD/constraint_free/released) · [F5] staggered auto-generation by gate · [F6] CWP→IWP decomposition into `workpacks` aligned to `schedule_advanced` look-ahead · [F7] automated batch/cert inheritance into field ITPs · [F8] Technical Queries / concessions (scope levels, `moc`-driven lifecycle) · [F9] automated TQ warning banners + MDR inclusion.
- **Removal impact:** field packs can still be created ad-hoc in `workpacks`, but without the engineering/procurement constraint gating — losing the protection against premature inspections and the automatic batch/TQ inheritance.
- **Edit notes:** _EWP/PWP/CWP are deliberately thin envelopes over existing data (drawings in `documents`, materials in `procurement`, schedule in `schedule_advanced`); the value is the **constraint engine** + the staggered gates, both expressed as `run_checks` rule sets so a client's extra gate is configuration. TQ reuses `moc` so the approval trail, impacts and audit are the same as any change._

---

## 4 · Interaction map (new work ↔ existing)

```
          file_versions / file_references / resumable_uploads / file_approvals
                       ▲ (revisions, links, presigned sessions, approvals)
                       │
   every module ──► [ documents + VFS ] ──► object store (flat UUID keys)
                       ▲   (tree, pre-set /Site/Asset/RSW folders, routing resolver,
                       │    orphan engine, lockers/shares, MTC certs, compiled PDFs)
                       │
   asset_management ─┐ │ ┌─ qms (ITP templates/stages, hold points)
   (scope, Items)    │ │ │─ construction_control (hold gates, handover)
   wps (welds/NDE)  ─┼─┴─┼─ site_inventory/procurement (batches, MTCs)
   markups (overlay)─┤   │─ signing (sign-off stamps)   · ncr (non-conformance)
                     ▼   ▼
                   [ workpacks ] ──► notifications (hold-point ready) · field_diary ledger (offline) · reporting (instant cert)
                     ▲        └────► jobs + ARQ worker (PDF/QR compile, OCR, scan auto-file)
                     │ release → create_automated_work_pack
                     │
   schedule_advanced │ (look-ahead window)       moc (TQ/concession lifecycle)
   (constraints/PPC) │                                   ▲
                     │                                   │
                   [ awp ] ──(EWP/PWP/CWP constraint engine)── documents (Rev 0) · procurement (received+certs)
```

**New engine rule sets registered** (plan §5): `document.route`, `document.share`, `document.immutable`, `workpack.issue`, `workpack.stage.signoff`, `cwp.release`, `iwp.generate`, `tq.apply`.

*Batch 10 complete. The VFS is part of `documents` (one module); `workpacks` and `awp` are the only new modules. No storage/ITP/weld/stock/e-sign/schedule logic re-implemented. Runtime/infra decisions in `../ADR-001-runtime-and-storage-architecture.md`.*
