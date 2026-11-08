# KERP spec review — coordinator's evaluation & decision menu

**Date:** 2026-11-08 · **Author:** coordinator (Claude), synthesising the nine advisor reports in `docs/reviews/01…09`.
**Purpose:** my recommendation on everything the advisors raised, the conflicts resolved, and a single decision menu for the owner to pick from. Once you mark your choices, the chosen items merge into one consolidated plan (`docs/reviews/PLAN.md`) ready to break into tasks with the `ecc` plugin.

> How to use this: read §1 for the headline, §2 for my per-advisor verdict, then **§4 is the decision menu** — that's the part you act on. Each decision has my recommendation and confidence; say yes/no/change per row.

---

## 1. Headline

The spec is **genuinely strong** — ahead of the construction-software market on verification-as-configuration, hash-chained records, offline witness scheduling and automatic client-vs-contractor delay attribution. The nine advisors converged hard on a small number of load-bearing decisions. **Five of them gate Phase-0/1 code and must be made before the backbone contract freezes.** Make those five now and the rest is executional.

**The single most important decision is TD-002** (adopt the Project-AIP "one-engine" model). Six of nine advisors independently said *adopt it, in full, now* — because the current spec runs three-to-four half-engines for inspection schema/forms/gates, and the cost of unifying them rises every phase. It reshapes Phase 1 and is the only true critical-path schedule risk.

**Strongest convergences (independent advisors agreeing):**
- **Adopt TD-002 one-engine** — architecture, options, data-model, feature-filler, delivery, tech-stack.
- **Rule DSL = CEL** (provable TS↔Python parity) — options, tech-stack, data-model.
- **Asset tree = Postgres `ltree`** (drop hand-rolled path/depth) — options, data-model.
- **Pick `approval_routes`, delete `enterprise_workflows`** — options, architecture.
- **One inspection record; `qms`=authoring, `construction_control`=gates; drop `construction_control._inspection`** — data-model, architecture.
- **Keep `tenant_id`+RLS (TD-007 = yes)** with explicit bypass gates — security, data-model, architecture.
- **MVP = Phase 0+1+2 + documents-lite + P6 round-trip** — delivery, options.

**The sharpest single-advisor flags worth elevating:**
- **Security:** the ledgers/`signing` are *tamper-evident, not court-defensible* — missing RFC 3161 trusted timestamps, PAdES-B-LTA, WORM storage, KMS. Since KERP's commercial value is records a tribunal accepts, this is a real gap. Also: drop HS256 shared-secret JWT for RS256/EdDSA+JWKS.
- **Tech stack:** ARQ is maintenance-only in 2026 — consider **SAQ** behind the `jobs` seam; and three library licence traps (libnest2d GPL, PyMuPDF AGPL, no OSS DWG reader).
- **Feature scout:** the one net-new surface no construction tool has — turn `asset_management`+`inspections` into an **asset-integrity engine** (corrosion/thickness loops, condition-based re-inspection). KERP's industrial/energy niche-definer.
- **Delivery:** three real tracker ordering bugs + a missing backbone-freeze gate.

---

## 2. Per-advisor verdict (what I'd adopt / modify / reject)

| Advisor | My verdict | Adopt | Modify / watch | Reject / defer |
|---|---|---|---|---|
| **01 Tech stack** | High value | Core stack confirmed; CEL evaluator; Dexie offline outbox; rectpack (2-D nest, MIT); GeoAlchemy2; named-library + licence register | **SAQ vs ARQ** → decision (D11); 4 vCPU/8 GB is the real floor from Phase 5 | — |
| **02 Security** | Critical | Keep RLS + 4 bypass gates; fix JWT crypto; harden magic-link/PIN + portal tokens; `ai_gov` 1-page register | **Evidentiary assurance level** → owner decision (D5); KMS/WORM ADR | SaaS-grade `security` module (single-company) |
| **03 Architecture** | High value | `core/` vs `modules/` split; scaffold generator in T-0009; 5 single-owner seams + import-lint; freeze top-3 fan-in contracts end-Phase-1; port as-built skeleton | `validation` = engine + thin register (resolve ambiguity) | drop as-built billing/entitlement gate |
| **04 Data model** | High value | `ltree` tree; one field-type registry table; append-only ledger patterns + "current value" views; index baseline; partition-ready PKs; P6 idempotency UNIQUE | item_types versioning/inheritance reconciliation with current dynamic schema | — |
| **05 Feature scout** | Selective | Instrument-cal gate finish; LPS board+PPC; weld traceability/weld-map; version-compare + records-completeness meter | **Condition-monitoring/integrity** = niche-definer but scope it (D6) | pay apps, takeoff→BOQ, CMMS valuation (break boundary) |
| **06 Options** | Backbone of this doc | The 20-card register; CEL/ltree/approval-engine as new TODOs | numbering reconciled below | — |
| **07 Feature filler** | High value | Expanded personas (incl. field-worker + client-portal presets); profile columns; access matrix; per-screen UX; 8 `cases` playbooks | Inspector Asset-`Site` scope = new plumbing (supported by anchor model, needs build) | — |
| **08 Pages & layout** | High value | 4-pillar IA; full sitemap; edge-screen specs (closes TD-008); Datum tokens + shadcn; dynamic-form renderer | Phase-1 priority screen set | — |
| **09 Delivery** | High value | Backbone-freeze gate (new T-0131); TD-002 5-step Phase-1 chain; 3 tracker fixes; worked Phase-3 breakdown; ecc feed | MVP cut line (D2); ~264–290 tasks total | — |

Net: **nothing to fully reject except the three boundary-breaking features and the SaaS security posture.** The rest is adopt-or-sequence.

---

## 3. Conflicts between advisors — resolved

1. **SAQ (tech-stack) vs ARQ (ADR-001).** Both sit behind the `jobs` module seam, so this is low-regret and reversible. If ARQ really is maintenance-only, pick SAQ now while the seam makes it free; otherwise ADR-001 stands. → **Decision D11**, my lean: SAQ behind the seam, documented in a new ADR. Not a blocker either way.
2. **MVP cut line: "end Phase 2 + documents" (options) vs "Phase 0+1+2 + documents-lite + P6 round-trip" (delivery).** Same intent; delivery is more precise and correctly notes inspection PDFs/MDRs need *somewhere to live* (documents-lite). → adopt delivery's version.
3. **`validation` as a Phase-0 module vs the `run_checks` substrate (architecture flagged ambiguous).** Resolve to a `core/verification/` engine (the `run_checks` kernel + rule-sets-as-data) **plus** a thin `validation` register module for findings. Data-model and architecture agree. → fold into the plan.
4. **New-TODO numbering collision** (feature-scout's TD-011 vs options' TD-011/012/013 vs security's assurance TODO). Reconciled into a clean block below.

No irreconcilable conflicts. The advisors are unusually aligned.

---

## 4. DECISION MENU  ← act on this

Mark each: **✅ adopt as recommended · ✍️ change (note it) · ❌ drop**. My recommendation + confidence is given. Grouped by who decides and how urgent.

### 4A · Owner decisions — needed **before Phase 0/1 code** (these gate everything)

| # | Decision | Options | My recommendation (confidence) | Affects |
|---|---|---|---|---|
| **D1** | **TD-002 — adopt the AIP one-engine model?** (unify `forms`+`item_types` on one field-type registry + shared evaluator; `eligibility` service; new `inspection_requests`; one inspection record) | (a) adopt in full · (b) adopt partially · (c) keep current multi-module split | **Adopt in full (High)** — 6/9 advisors; biggest structural win; cost rises every phase | Phase 1 shape, TD-002, T-0102/0109/0110, E-01-01 |
| **D2** | **TD-009 — MVP / first-pilot cut line** | (a) Phase 0+1+2 + documents-lite + P6 round-trip (~85–95 tasks) · (b) end-of-Phase-2 only · (c) further | **(a) (High)** — this *is* your "inspection management system"; (b) leaves inspection PDFs/MDRs homeless | scope, sequencing |
| **D3** | **TD-007 — keep multi-tenant `tenant_id`+RLS** in a single-company product? | (a) keep RLS + bypass gates · (b) drop to single-tenant | **(a) keep (High)** — cheap now, defensive, enables future multi-client; add 4 bypass gates | every table, T-0006 |
| **D4** | **TD-003 — identity** | (a) local JWT behind `AuthProvider` now, Entra ID SSO later · (b) SSO now · (c) local only | **(a) (High)** — *and fix the crypto regardless* (see D13) | T-0007, portal, field_diary |
| **D5** | **NEW TD-015 — evidentiary assurance level** (your records in a tribunal) | (a) court-defensible for the legal subset (RFC 3161 timestamps + PAdES-B-LTA + WORM + KMS) · (b) tamper-evident only (today's hash chains) | **(a) for the legal subset (High)** — decide before Phase 1 freezes the ledger/sign-off contract | inspections, inspection_requests, signing, audit |
| **D6** | **NEW TD-016 — asset-integrity / condition-monitoring scope** (corrosion/thickness CMLs, remaining-life→NCR, condition-based re-inspection) — the niche-definer | (a) in-scope, schema reserved now, build post-MVP · (b) in-scope now · (c) out | **(a) (Medium-High)** — reserve the schema in the one-engine so it's cheap later; build after MVP | asset_management, inspections, feature set |

### 4B · Team ADRs — decide **this week**, before Phase-0 code (I recommend; tell me to proceed or override)

| # | Decision | My recommendation (confidence) | Note |
|---|---|---|---|
| **D7** | Verification rule DSL | **CEL** (High) — only option with a conformance suite to *prove* TS↔Python parity | new **TD-011**; ADR |
| **D8** | Asset-tree storage | **Postgres `ltree`**, drop hand-rolled path/depth (High) | new **TD-012**; ADR (core-table change per hard-rule 8) |
| **D9** | Approval engine | **`approval_routes`**, delete `enterprise_workflows` (High) | new **TD-013**; spec already says "pick one" |
| **D10** | Inspection ownership split | **One inspection record**; `qms`=authoring/audit, `construction_control`=conformance/hold-gates; **drop `construction_control._inspection`** (High) | follows D1 |
| **D11** | Background-job backend | **SAQ behind the `jobs` seam** (Medium) — or stay ARQ; low-regret | new **TD-014**; ADR |
| **D12** | Offline sync (TD-006) | **Confirm in Phase 1** — Dexie IndexedDB outbox keyed on `client_op_id`, one shared ledger for field_diary/inspections/workpacks (High) | T-0125/0126 |
| **D13** | JWT crypto hardening | **RS256/EdDSA + JWKS, short access + rotating refresh + server-side `jti` revocation** (High) | part of T-0007; independent of D4 |

### 4C · Confirm-and-close (already resolved; just sign off to stop re-litigation)

- **D5-P6** one-way import + export (no live bidirectional) · **D7-crm** folded into `contacts` · **D8-portal** phased v1→v2→v3 · **TD-001** port-and-adapt the proven Inspection-ERP core.

### 4D · Safe to defer

- **TD-004 hosting** (Coolify/MinIO vs AWS) — decide by T-0004 · **TD-005 `module_builder`** — cut from pilot, revisit later · **TD-008 edge-screen UI** — now largely drafted by advisor 08; fold those in · **`terms`** → lite Project-Settings label override (not a module) · **`ai_gov`** → 1-page AI data-flow register (do it; cheap) · SaaS **`security`** module → skip.

### 4E · Feature additions (feature scout) — pick which to bring in

| # | Feature | Module(s) | Effort/Prio | My rec |
|---|---|---|---|---|
| F-a | Instrument-calibration gate + traceability stamp (finish what's half-specced) | forms F10 / eligibility | M / P1 | **Adopt** (part of one-engine) |
| F-b | LPS constraint board + PPC/variance-reason dashboard | schedule_advanced / bi_dashboards | M+S / P1 | **Adopt** (Phase 4; closes TD-008) |
| F-c | Weld batch/heat-input traceability + visual weld-map on isometrics | wps / markups | S–M / P1 | **Adopt** (Phase 2) |
| F-d | Drawing revision version-compare + records-completeness meter | markups / claims_evidence | M / P2 | **Adopt** (Phase 5/6) |
| F-e | Condition-monitoring loops (see D6) | asset_management / inspections | L / P1 | **Adopt but schedule post-MVP** |
| F-x | Pay applications · takeoff→BOQ · CMMS spare-parts valuation | — | — | **Reject** — break money/anchor boundary |

### 4F · Tracker fixes & plan mechanics (do regardless of the above)

- Add **backbone-freeze gate** T-0131 at end of Phase 1 (nothing currently asserts the §9 freeze).
- Move `resource_summary` (E-03-07) to **Phase 4** (depends on tasks/look-ahead).
- Pull `procurement` to the **front of Phase 3**, split the 3-module epic E-03-05; fix `site_inventory` (E-03-03) deps (needs procurement + progress).
- Split the overloaded **T-0124** (5 rule sets in one task — over M budget).
- Add a **module scaffold generator** to T-0009 (7 files + manifest) + manifest-contract test.
- Clean **FINAL-PLAN §1.3** (still lists `enterprise_workflows` + a `crm` reference despite D7/D9).
- Add **Inspector Asset-`Site` scope** to the RBAC build (new plumbing; anchor model supports it).

---

## 5. New TODOs to raise (reconciled numbering)

| New ID | Kind | Title | Decision |
|---|---|---|---|
| TD-011 | decision | Verification rule DSL = CEL (TS+Python parity) | D7 |
| TD-012 | decision | Asset-tree storage = Postgres ltree (ADR; core-table change) | D8 |
| TD-013 | decision | Approval engine = approval_routes; retire enterprise_workflows | D9 |
| TD-014 | decision | Background-job backend = SAQ vs ARQ behind `jobs` seam | D11 |
| TD-015 | decision | Evidentiary assurance level (RFC3161/PAdES-LTA/WORM/KMS) | D5 |
| TD-016 | decision | Asset-integrity / condition-monitoring scope line | D6 |

(If you adopt the recommendations, I'll create these via `scripts/tracker.py new-todo` as part of the merge, and mark TD-001/003/007 + D5-P6/D7/D8 resolved.)

---

## 6. What merges into the consolidated plan (once you pick)

The chosen items fold into **`docs/reviews/PLAN.md`** structured as: (1) confirmed scope & doctrine; (2) resolved decisions + new ADR stubs; (3) the one-engine Phase-1 chain; (4) the corrected phase/epic sequence + backbone-freeze gate + MVP cut line; (5) a named-library + licence register; (6) the evidentiary/security hardening tasks; (7) the feature-scout adds slotted into phases; (8) persona/access-matrix + page catalogue as build inputs; (9) the ecc `epic-decompose` feed using the worked Phase-3 breakdown as the format exemplar. Then the backend/frontend scaffold + Phase 0 is ready to run through `/kerp-run` or the ecc pipeline.

---

*Full detail behind every line here is in `docs/reviews/01…09`. This evaluation is the coordinator's own synthesis; the owner's picks in §4 govern the merge.*
