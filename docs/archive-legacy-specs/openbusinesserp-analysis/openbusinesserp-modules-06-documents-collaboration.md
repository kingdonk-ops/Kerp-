# OpenBusinessERP — Per-Module Docs · Batch 6: Documents & Collaboration

> Batch 6 of 7. Four Pillars per module (compact). Grounded in `backend/app/modules/<name>/`.

**Modules (24):** `documents` + the `file_*` subsystem (11), `rfi`, `submittals`, `transmittals`, `correspondence`, `meetings`, `markups`, `plan_room`, `collaboration`, `collaboration_locks`, `approval_routes`, `bim_hub`, `cad`.

---

### Document management core
- **`documents`** — *core, depends[projects], 84 routes.* **What:** the Document Management core / CDE (the `/files` surface). **Does:** folders, files, metadata, download. **How:** files go through the core storage service (never raw paths); the `file_*` modules below each add one capability on top. Core — the hub the whole file subsystem orbits.

### The `file_*` subsystem (each adds one capability to `/files`)
All `category=core`, depend on `[projects, users]` (and `documents` where noted):
- **`file_versions`** (8 r) — version history per file.
- **`file_approvals`** (18 r) — approval workflows + stamps (Wave W8).
- **`file_comments`** (12 r) — per-file comment threads.
- **`file_tags`** (16 r) — taggable files (W4).
- **`file_search`** (8 r, depends documents) — full-text search over files (W3).
- **`file_saved_views`** (12 r) — Smart Folders / saved filters for `/files`.
- **`file_favorites`** (6 r) — pins/favourites.
- **`file_references`** (16 r) — link files to other records.
- **`file_distribution`** (20 r, depends documents) — cross-project search + distribution lists.
- **`file_transmittals`** (18 r) — formal file-issue transmittals (W7).
- **`file_trash`** (12 r) — recycle bin / soft-delete + restore.

> **How, collectively:** each is a thin, independently-gated capability so the document core stays small and features can be enabled per deployment; they share the `documents` file ids. **Failure:** disabling one removes just that capability (e.g. no versions, or no trash), leaving the rest of `/files` working.

### Controlled-document workflows (construction correspondence)
- **`rfi`** — *core, 36 routes.* Requests for Information (raise → respond → close, with ball-in-court).
- **`submittals`** — *core, 30 routes.* Submittal register (shop drawings/material approvals, review cycles).
- **`transmittals`** — *core, depends[contacts], 20 routes.* Document-issue tracking with recipients (who got what, when).
- **`correspondence`** — *core, depends[contacts], 14 routes.* General project correspondence log.
- **`meetings`** — *core, 58 routes.* Meetings & minutes (agenda, attendees, action items).
- **`markups`** — *core, depends[projects], 38 routes.* Markups & annotations on drawings/documents.

### Collaboration & approvals
- **`collaboration`** — *core, depends[users], 14 routes.* Threaded comments + viewpoints attachable to **any** entity (generic, entity-URI based).
- **`collaboration_locks`** — *core, depends[users], 12 routes.* Edit locks — "layer 1 of the real-time collaboration plan" (prevent concurrent-edit clobber).
- **`approval_routes`** — *core, depends[projects, users], 40 routes.* Configurable multi-step approval routes reusable across modules.
- **`plan_room`** — *business, depends[projects, users, documents], 6 routes.* Plan room — a controlled set of current drawings for distribution/viewing.

### BIM & CAD
- **`bim_hub`** — *core, depends[users, projects, boq].* BIM model hub (host/view models, link elements to BOQ for 4D/5D). Model/viewer-heavy; thin REST surface.
- **`cad`** — *extension, depends[projects].* CAD/DWG handling that backs `takeoff`/`dwg_takeoff` and the CAD explorer. Underpins drawing-based takeoff (Batch 4).

---

### Batch-6 map
```
documents (/files, CDE)
  └─ file_versions · file_approvals(+stamps) · file_comments · file_tags · file_search
     file_saved_views · file_favorites · file_references · file_distribution
     file_transmittals · file_trash        (each = one capability, independently gated)
rfi / submittals / transmittals / correspondence / meetings / markups  ──controlled docs──▶ projects
collaboration (any entity) · collaboration_locks (edit locks) · approval_routes (reusable approvals)
bim_hub ──elements──▶ boq (4D/5D) ; cad ──drawings──▶ takeoff / dwg_takeoff / plan_room / markups
```
`documents` is the file hub; the `file_*` capabilities and the controlled-document registers gate independently. `approval_routes` and `collaboration` are cross-cutting services other modules reuse.

*Batch 6 of 7. Next (final): **Country packs, AI & Platform/Integration** — regional packs (uk/dach/latam/asia_pac/china/india/mexico/middle_east/russia/sa/us/us_ca/us_tx), AI (ai, ai_agents, erp_chat, change_intelligence, project_intelligence, retrieval, smart_views, saved_views, search), and platform/integration (connectors, integrations, webhook_leads, inbound_email, inbound_capture, phonelog, notifications, onboarding, backup, credentials, data_import, pipelines, client_errors, portal, voice, enterprise_workflows, interface_management, project_route, module_builder, admin, i18n_foundation, einvoice, qms, carbon/esg, compliance*, bi_dashboards/dashboards, cases, correspondence, risk, etc.). Grounded @ `a96d525`.*
