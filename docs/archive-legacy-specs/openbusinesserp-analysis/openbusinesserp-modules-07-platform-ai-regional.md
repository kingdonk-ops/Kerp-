# OpenBusinessERP — Per-Module Docs · Batch 7 (final): Platform, AI, Compliance & Regional

> Batch 7 of 7 — the remaining modules, completing coverage of **all 191** `app/modules/*`. Compact Four-Pillars entries grouped by theme. Grounded in `backend/app/modules/<name>/` (`category` + route counts from manifests/routers; purpose from each module docstring).

---

## A. AI & intelligence
- **`ai`** *(core, 28 r)* — AI Estimation service: the shared LLM-estimation surface.
- **`ai_agents`** *(core, 56 r)* — AI Agents framework (multi-step agent orchestration over ERP data).
- **`erp_chat`** *(core, 16 r)* — the in-app assistant (the floating chat dock / `/chat`) that answers over the ERP.
- **`retrieval`** *(core, 12 r)* — retrieval/RAG layer feeding chat & agents (pairs with `core/vector`).
- **`search`** *(core, 6 r)* — unified cross-collection **semantic** search.
- **`smart_views`** *(core, 22 r)* — rule-based, re-evaluating BIM viewer presets.
- **`saved_views`** *(core, 24 r)* — saved filters/views across modules.
- **`match_elements`** *(core, library)* — element-matching helpers (used by cost/BIM matching).
- **`change_intelligence`** *(controls, 34 r)* — detects/analyses change across project data.
- **`project_intelligence`** *(core, 26 r)* — project-level insights/analytics.
- **`voice`** *(core, 4 r)* — turn a spoken/typed site note into a structured, human-confirmed draft.
> **How/Interactions:** all are AI-augmented-human-confirmed (platform rule: AI mutations are drafts). They read across modules via services; `retrieval`+`search` sit on `core/vector`. **Failure:** disable → assistant/search degrade; data modules unaffected.

## B. Reporting, dashboards & portfolio
- **`reporting`** *(core, 42 r)* — reports & dashboards generation (branded output via `core/reporting`).
- **`dashboard`** *(core, 12 r)* — single rollup endpoint returning all dashboard sections.
- **`dashboards`** *(core, 54 r)* — analytical layer over snapshots (`Snapshot.tenant_id`).
- **`bi_dashboards`** *(extension, 68 r)* — BI dashboards & reporting (richer analytics, Wave 4).
- **`portfolio`** *(business, 20 r)* — multi-project portfolio rollups.
- **`teams`** *(core, 72 r)* — team visibility / project membership (the project-member access layer `dependencies.verify_project_access` consults).
- **`tasks`** *(core, 32 r)* — generic task tracking.

## C. Compliance, quality & governance
- **`compliance`** *(core, 14 r)* — compliance **DSL**: user-authored validation rules.
- **`compliance_ai`** *(core, 4 r)* — DSL engine + natural-language rule builder.
- **`compliance_docs`** *(core, 14 r)* — tracker for insurance/permits/bonds/certifications (expiry).
- **`qms`** *(extension, 118 r)* — full Quality Management System (ISO-style: procedures, audits, CAPA, NCR linkage).
- **`requirements`** *(core, 68 r)* — requirements & quality gates register.
- **`validation`** *(core, 32 r)* — the shared validation engine/rules surface (platform validation).
- **`risk`** *(core, 30 r)* — risk register (likelihood/impact, mitigation).
- **`cases`** *(core, 20 r)* — issue/case management.
- **`claims_evidence`** *(controls, 8 r)* — claims/dispute evidence-pack assembly.
- **`review_authority`** *(core, 30 r)* — external-review-authority (expertise/review cycle).
- **`moc`** *(project_controls, 26 r)* — Management of Change register.
- **`source_data`** *(core, 30 r)* — prerequisite-documents register (source data a task needs before it can start).
- **`authority_submission`** *(core, 24 r)* — XML document factory for authority submissions.
- **`record_publishing`** *(core, 8 r)* — one-tap publish-a-record-and-distribute.
- **`signing`** *(core, 22 r)* — e-signature workflow + attestation registry.
- **`phonelog`** *(core, 14 r)* — capture phone-call/voice-note verbal instructions as dispute-ready records.

## D. Commercial change & e-invoicing (remaining)
- **`changeorders`** *(core, 38 r)* — change orders (client-side change control).
- **`variations`** *(business, 160 r)* — Variations & Site Measurements (the large variation-valuation module).
- **`preliminaries`** *(core, 14 r)* — preliminaries / general conditions (feeds estimate rollup).
- **`einvoice`** *(core, 6 r)* — international electronic-invoicing (EN 16931 / UBL / CII / Peppol / ZUGFeRD generation).
- **`einvoice_clearance`** *(core, 32 r)* — e-invoice clearance (country clearance-model submission, e.g. real-time tax authority).
- **`service`** *(business, 104 r)* — Service & Maintenance (service jobs, maintenance contracts, scheduling).
- **`appointments`** *(business, 74 r)* — services, bookable resources, bookings, online booking + reminders.
- **`accommodation`** *(business, 38 r)* — worker camps / rental apartments / hotels management.
- **`property_dev`** *(business, 460 r)* — **Property Development & Buyer Portal** (the single largest module: units, sales, buyer portal, handover — a whole sub-product).
- **`portal`** *(business, 72 r)* — customer & partner portal (external-stakeholder access surface).
- **`webhook_leads`** *(business, 24 r)* — inbound web-form/webhook lead capture → CRM.
- **`forms`** *(business, 30 r)* — forms & checklists (configurable data-capture forms).
- **`carbon`** *(business, 122 r)* — carbon & sustainability accounting.
- **`esg`** *(business, 20 r)* — ESG site-performance metrics.
- **`interface_management`** *(business, 20 r)* — multi-package interface coordination register.
- **`enterprise_workflows`** *(enterprise, 22 r)* — configurable enterprise workflow automation.
- **`projects`** *(core, 122 r)* — **the project entity itself** (projects, ownership, membership) — foundational; nearly everything `depends=[projects]`.

## E. Platform & integration services
- **`onboarding`** *(core, 8 r)* — non-blocking first-run provisioning (presets, starter data).
- **`module_builder`** *(core, 22 r)* — the runtime module-builder backend (documented in the registry/builder doc).
- **`notifications`** *(core, 30 r)* — in-app notifications with i18n keys + per-user preferences.
- **`i18n_foundation`** *(core, 46 r)* — the internationalisation foundation (44 languages, RTL, terminology).
- **`integrations`** *(core, 26 r)* — chat connectors (Teams/Slack/Telegram), webhooks, calendar feeds.
- **`connectors`** *(core, 8 r)* — connector registry/config.
- **`inbound_capture`** *(integration, 6 r)* — inbound capture gateway.
- **`inbound_email`** *(controls, 2 r)* — inbound email intake.
- **`data_import`** *(core, 22 r)* — CSV/XLSX import wizard (map → validate → dry-run → commit → undo).
- **`pipelines`** *(core, 18 r)* — pipeline builder (data/processing pipelines).
- **`uploads`** *(core, 2 r)* / **`resumable_uploads`** *(core, 12 r)* — upload intake + chunked/resumable uploads (GB-scale files).
- **`backup`** *(core, 6 r)* — export/import user-data backups.
- **`credentials`** *(core, 46 r)* — professional-credentials registry (people's licences/certs).
- **`global_presence`** *(core, 2 r)* — who's online and where (the presence WebSocket).
- **`jobs`** *(core, 8 r)* — background-job surface (over `core/job_runner`).
- **`project_route`** *(core, 20 r)* — work-type classifier gate ("delivery / permit route").
- **`client_errors`** *(infra, 2 r)* — client-side error sink (frontend error reporting).

## F. Regional / country packs
All `category=regional`. Most are thin activation shells (≈2 routes) that, when installed (via the `/modules` Packs tab), **switch on a bundle of modules, load demo data, apply branding, and add validation-rule packs / tax & document profiles** for that jurisdiction; a few carry their own tables/logic:
- **Thin packs (~2 r):** `uk_pack`, `dach_pack`, `latam_pack`, `asia_pac_pack`, `china_pack`, `india_pack`, `middle_east_pack`, `russia_pack`, `us_pack`.
- **Heavier packs (own routes/tables):** `mexico_pack` (20 r), `sa_pack` (South Africa, 12 r), `us_ca_pack` (6 r), `us_tx_pack` (6 r) — jurisdiction-specific tax/compliance specifics.
> **How/Interactions:** packs are applied through the registry's pack engine (`partnerPacks`, Batch "registry" doc) and compose other modules + `tax`/`einvoice`/`compliance` configuration. **Failure:** deactivating a pack restores the prior module set (the deactivate dialog lists `restored_modules`).

---

## Completion summary — all 191 modules documented

| Batch | Domain | Modules | File |
|---|---|---|---|
| 1 | Finance & Accounting | 15 | `…-01-finance.md` |
| 2 | Sales & Commercial | 10 | `…-02-sales-commercial.md` |
| 3 | Purchasing & Supply | 5 | `…-03-purchasing-supply.md` |
| 4 | Estimating & Cost | 26 | `…-04-estimating-cost.md` |
| 5 | Project Delivery & Field | 25 | `…-05-delivery-field.md` |
| 6 | Documents & Collaboration | 24 | `…-06-documents-collaboration.md` |
| 7 | Platform / AI / Compliance / Regional | 86 (incl. 13 regional packs) | this file |
| (deep-dives) | admin, users | 2 | earlier OBE docs |

That accounts for **191 module directories** under `backend/app/modules/` (the loader shows ~188 loadable; the difference is library-only modules such as `catalog`, `measurement`, `price_breakdown`, and always-on vs optional split). Every module directory now has at least a grounded Four-Pillars-compact entry; the architecturally load-bearing ones (`gl`, `boq`, `costs`, `schedule`, `documents`, `projects`, `contracts`, `property_dev`, `qms`, `admin`, `users`, module-builder) have fuller treatment in their batch or a dedicated doc.

### The full OpenBusinessERP documentation set (scratchpad)
1. `openbusinesserp-architecture.md` — stack, architecture, RBAC/security, module catalog
2. `openbusinesserp-frontend-ux.md` — layout, design tokens, menu, Settings, Team/permissions
3. `openbusinesserp-users-modules.md` — `/users` page + system/module settings
4. `openbusinesserp-modules-registry-builder.md` — `/modules` registry + module builder
5. `openbusinesserp-modules-01…07` — **per-module Four-Pillars for all 191 modules** (this batch set)

*Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525`.*
