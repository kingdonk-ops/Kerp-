# Options & Decisions Advisor — review

**TL;DR**
1. **Resolve TD-002 (one AIP engine) now — ADOPT it.** It is the single biggest fork; it is the "resolve the dedup in the rebuild" instruction made concrete, and it gates the shape of Phase 1 (`forms`/`item_types`/`inspections`/`eligibility`). Everything in the quality backbone hangs off this one choice.
2. **Seven decisions gate Phase 0/1 and must be made this week**: TD-002, TD-007 (tenancy), the **rule-evaluator engine** (undocumented — pick CEL), the **asset-tree storage** (undocumented — pick ltree), **approval_routes vs enterprise_workflows** (pick one), TD-009 (MVP cut line), and TD-003 (auth seam). The rest are safe to defer behind clean seams.
3. **Two load-bearing engine choices are missing from every spec**: the verification-rule DSL engine (the spec says "rules are data (DSL)" but never names one) and the tree storage model (`path`/`depth` columns imply materialized-path, but the AIP proposal assumes `ltree`). Pick both before T-0006/T-0101, or two agents will pick differently.
4. **Confirm the three "resolved" decisions** (D5 P6 one-way+export, D7 crm→contacts, D8 portal phasing) with a one-word owner sign-off so they stop being re-litigated. They are well-reasoned; leave them.
5. **Cut aggressively**: `module_builder` (TD-005), `enterprise_workflows`, `ai_gov`, and the SaaS `security` posture are dead weight for a single-company pilot — defer or delete, don't spec.

---

## 1. How to read the register

Every decision is a card: **Question · Options · Pros/Cons · Recommendation (+confidence) · Blocks/affects · Owner.** Two axes rank them:

- **Urgency** — `NOW` (gates Phase 0/1, a wrong default is expensive to unwind) · `SOON` (Phase 2–3) · `DEFER` (behind a seam, decide when the feature lands).
- **Blast radius** — how many modules/tables a reversal touches: `High` (core tables, every module), `Med` (a pillar), `Low` (one module).

"Owner" = only the repo owner can decide (business/cost/security posture). "Team" = an engineering call the planner/lead can make and record in an ADR.

---

## 2. Decision register

### A. The blocker

#### D-01 · TD-002 — Adopt the AIP one-engine proposal? · **NOW · High · Owner+Team**
**Question:** Collapse `forms` + `asset_management` field designer + the inspection logic spread across `inspections`/`qms`/`construction_control` into **one no-code engine** (shared field-type registry + shared TS/Python evaluator), plus the `eligibility` gate service and the new `inspection_requests` module (MODULE-SPEC-11)?

| Option | Pros | Cons |
|---|---|---|
| **A. Adopt in full** (registry + evaluator + revision-immutability + `eligibility` + `inspection_requests`) | One place to build/test every template and asset schema; kills 3–4 half-engines; revision-immutability gives legally-defensible records (the owner's "inspection management system" selling point); directly executes the spec's own "resolve the dedup" instruction | Larger Phase-1 surface; introduces a **shared TS+Python evaluator** (two codebases, one fixture set) that must be kept in lockstep |
| **B. Adopt the dedup, skip the shared evaluator** — one engine, but Python-only; web calls the API to evaluate | Removes duplicate designers without the dual-language maintenance tax | Breaks offline capture — the device can't evaluate `visible_if`/calculated fields without network; offline is a Phase-1 hard requirement (TD-006), so this half-measure fails the core use case |
| **C. Reject — build the spec as written** (separate designers, inspection logic in 3 modules) | Smallest change to current task list | Ships the exact duplication the spec told us to remove; every new inspection kind touches 3 modules; this is technical debt by decision |

**Recommendation: A — Adopt in full. (confidence: high)** Sequence per PROPOSAL §4: shared registry+evaluator → `forms` → `item_types` → `inspections` record-integrity + `eligibility` → `inspection_requests`. Treat the dual-language evaluator as one package with a **shared fixture suite as the contract** (a CI gate that runs the same cases through both). If the owner wants to de-risk, ship the evaluator **TS-first, Python-generated-from-the-same-AST is a non-goal** — instead keep the rule grammar tiny (see D-03) so both ports are small.
**Blocks/affects:** `forms`, `item_types`/`asset_management`, `inspections`, `qms`, `construction_control`, `eligibility`, `inspection_requests`; E-01-01; T-0102/T-0109/T-0110; the whole Phase-1 task breakdown. **Nothing else in Phase 1 can be sized until this is decided.**

---

### B. Engine choices the specs never actually made (undocumented — raise as new TDs)

#### D-02 · **approval_routes vs enterprise_workflows** — pick one approval engine · **NOW · Med · Team** *(propose TD-011)*
Both modules are KEPT (FINAL-PLAN §1.3). MODULE-SPECS line 64 literally says *"Overlaps `approval_routes`; pick one engine in the rebuild."* Nobody picked.

| Option | Pros | Cons |
|---|---|---|
| **A. Keep `approval_routes`, drop `enterprise_workflows`** | Richer model (quorum, SLA hours, delegation, step-state); ~40 routes vs 22; already a `Dependents: many` cross-cutting service | Must migrate the 2 `enterprise_workflows` tables' intent into it |
| **B. Keep `enterprise_workflows`** | Slightly simpler table shape | Fewer features; loses delegation/SLA that field sign-offs need |
| **C. Keep both** | No deletion work | Two approval engines in a single-company app; agents will wire different modules to different engines; guaranteed inconsistency |

**Recommendation: A — one engine, `approval_routes`; delete `enterprise_workflows` from the register. (confidence: high)** This is the same "pick one" theme as TD-002 at platform level. **Affects:** platform layer, every module that needs a multi-step approval; do it before any module wires an approval. Record as an ADR (core-register change).

#### D-03 · **Verification rule DSL — which evaluator?** · **NOW · High · Team** *(propose TD-012, undocumented)*
DEVELOPMENT-PLAN §5 says `run_checks(context, rule_set)` is "built on `validation` + repurposed `eac` + the completion-gate pattern + `compliance`/`compliance_ai` DSL" and "rules are data (DSL)" — **four sources, no named engine.** This is the second "pick one" and it's invisible on the board.

| Option | Pros | Cons |
|---|---|---|
| **A. CEL (Common Expression Language), `cel-python` + a JS CEL impl** | Purpose-built for exactly this (Google's policy/rule language); sandboxed, side-effect-free, typed, terminating by design; mature spec; same grammar web + Python (solves TD-002's dual-evaluator problem for *rules*) | Smaller Python ecosystem than JSONLogic; one dependency to vet |
| **B. JSONLogic (`json-logic`/`json-logic-py`)** | Dead-simple JSON rules; trivially serialisable; tiny libs in both languages | Weak typing, no units/measurement semantics, clumsy for `corrosion_rate > tol` style engineering predicates; you outgrow it fast |
| **C. Home-grown restricted-formula interpreter** (AIP's "function allow-list") | Fits the calculated-field needs exactly; no external dep | You are now maintaining a language + its sandbox in two runtimes; high risk, high cost |

**Recommendation: A — CEL, one grammar across the stack. (confidence: med-high)** It collapses TD-002's biggest risk: the "shared TS/Python evaluator" becomes "two CEL runtimes over one rule grammar" instead of two bespoke interpreters. Keep KERP's measurement/unit helpers as CEL custom functions. **Affects:** `validation`, `eac`, `run_checks`, `eligibility`, every rule set in §5 (`weld.accept` … `tq.apply`), the forms calculated-fields. **Freeze before T-0006** (the `validation` engine shell in Phase 0). *Note: if the owner rejects a dual-runtime evaluator entirely, fall back to Python-only CEL + online-only calculated fields — but that re-opens offline (TD-006).*

#### D-04 · **Asset-tree storage — ltree vs materialized-path vs adjacency** · **NOW · High · Team** *(propose TD-013, undocumented)*
`oe_asset_node` carries `parent_id`, `path`, `depth` (adjacency + a path string), while the AIP proposal assumes **`ltree`** (`asset_path_scope ltree` for programme scoping). Two specs, two models, nobody chose. The tree is the critical path (DEVELOPMENT-PLAN §201: "freeze its contract before Phases 3–6").

| Option | Pros | Cons |
|---|---|---|
| **A. Postgres `ltree`** (native, GiST-indexed) | Native subtree queries (`path <@ ancestor`), recurrence `asset_path_scope` works out of the box, fast descendant aggregation (the Inspections tab "asset + descendants"); PostGIS box already ships GiST | Labels must be sanitised (ltree is restrictive on chars); re-parenting rewrites a subtree's paths |
| **B. Materialized path (text `path` + `depth`, as currently written)** | No extension; simple `LIKE 'root/%'` | Hand-rolled integrity; slower/awkward ancestor queries; re-implements what ltree gives free; the AIP recurrence scoping has to be rebuilt |
| **C. Pure adjacency (`parent_id` + recursive CTE)** | Simplest writes; trivial re-parent | Every "asset + all descendants" read is a recursive CTE — the hottest read path in the app, run on every asset-details screen |

**Recommendation: A — `ltree`, and delete the redundant `path`/`depth` text columns (keep `parent_id` for integrity + re-parenting). (confidence: high)** Postgres is already the DB of record and GiST is already in use for PostGIS; ltree is the cheapest correct answer and it is what the AIP recurrence model needs. **Affects:** `asset_management` (backbone), and transitively everything anchored to it. **Freeze in T-0101** (the hierarchy task), before Phase 2.

#### D-05 · **qms / construction_control / inspections ownership split** · **NOW · High · Team** (folds into TD-002)
Three modules own overlapping inspection halves; MODULE-SPECS line 104 says *"resolve the dedup … recommend `qms` as the ITP/audit layer, `construction_control` as the conformance/hold-gate engine."* PROPOSAL §1 goes further: **one inspection record** with `kind`, templates from `forms`, gates via `run_checks`.

| Option | Pros | Cons |
|---|---|---|
| **A. PROPOSAL model** — one inspection record (`kind`=inspection/itp/rfi); `qms`=audit register only; `construction_control`=conformance/hold-gate engine; thin `inspections` register **folds into the one record** | One record type to build, test, sign, sync offline; matches TD-002; removes the naming/overlap confusion | Requires TD-002 adopted; a real migration of three modules' intent |
| **B. MODULE-SPECS model** — keep 3 modules, just clarify boundaries (`qms`=ITP/audit, `construction_control`=gates, `inspections`=thin register) | Smaller refactor; no record unification | Still three inspection surfaces; offline sync + sign-off logic written three times; the overlap the spec flagged persists in softer form |

**Recommendation: A, contingent on D-01. (confidence: high)** If TD-002 is adopted (it should be), this resolves automatically: `qms` → audit/calibration register, `construction_control` → hold-gate/conformance engine behind `run_checks`, `inspections`/ITP become one `forms`-driven record. If TD-002 is rejected, fall back to B as the floor. **Affects:** `inspections`, `qms`, `construction_control`, `workpacks` (reuses qms ITP + cc gates), `portal`. **Decide with D-01.**

---

### C. Platform-posture decisions (mostly keep the applied defaults — confirm)

#### D-06 · TD-007 — Keep tenant_id + RLS in a single-company product? · **NOW · High · Owner**
| Option | Pros | Cons |
|---|---|---|
| **A. Keep `tenant_id` + RLS everywhere, one tenant per deploy** (applied default) | Cheap now, a hard rule already (`BaseRecordMixin`); the portal gives *clients* scoped access — effectively multi-party even if single-company; retrofitting RLS onto 94 modules later is a rewrite | Every query carries a tenant predicate; a little ceremony |
| **B. Strip tenancy, single-tenant schema** | Slightly simpler models | Throws away the only safe isolation boundary for the client portal; impossible to walk back |

**Recommendation: A — keep. (confidence: high)** The portal alone justifies RLS (client co-sign/NCR access must be row-scoped). This is a one-line owner confirmation. **Affects:** every table, `BaseRecordMixin`, T-0006.

#### D-07 · TD-003 — Identity: local JWT vs SSO · **NOW (seam) / DEFER (SSO) · Med · Owner**
| Option | Pros | Cons |
|---|---|---|
| **A. Local scrypt+JWT now, behind an `AuthProvider` seam; SSO (Entra ID) added later** (applied default) | Unblocks everything; staff-SSO + client email/password is the likely end state and the seam makes it additive | Owner must still eventually choose the IdP |
| **B. Build Entra-ID SSO up front** | No later auth work for staff | Blocks Phase 0 on a corporate-IT dependency (tenant app registration, consent) that isn't ready; clients still need local auth anyway |

**Recommendation: A — seam now, SSO deferred. (confidence: high)** The *only* thing the owner must decide now is "yes, build the seam" (cheap) and roughly whether staff will be Entra-ID (affects adapter design, not timing). **Affects:** `users`, T-0007, portal auth.

#### D-08 · TD-004 — Hosting: Coolify/MinIO vs AWS · **SOON · Med · Owner**
Storage is S3-compatible either way; only **deploy + email** differ (MinIO+SES-or-SMTP vs S3+SES). ADR-001 already assumes presigned-direct-to-bucket with a `/<bucket>/` Nginx reverse-proxy for on-prem MinIO.

| Option | Pros | Cons |
|---|---|---|
| **A. Coolify + MinIO on-prem/VPS** (ADR-001 default) | Cheapest; owner controls data (mining/IRAP-type clients often require on-prem); 2vCPU/4GB viable | You run MinIO CORS/TLS + backups; email needs an SMTP relay |
| **B. AWS (S3 + SES + ECS/Fargate)** | Managed storage/email/scale | Higher cost; data leaves the perimeter — may be a dealbreaker for the target clients |

**Recommendation: A — Coolify/MinIO, keep the adapter seam so AWS is a config swap. (confidence: med)** The storage adapter is S3-API either way, so this is low-risk to defer **up to the point T-0004 publishes images** — decide the *deploy + email* target before then (ADR-001 already flags this). **Affects:** `uploads`/`resumable_uploads` adapters, email/notifications adapter, T-0004/T-0015, deploy CI.

---

### D. Scope & sequencing

#### D-09 · TD-009 — MVP cut line · **NOW · High · Owner**
~94 modules; without a line, agents grind low-value modules before anything is pilot-usable.

| Option | Pros | Cons |
|---|---|---|
| **A. Cut at end of Phase 2 + `documents`** (backbone + welding + CDE) — the suggested line | Delivers the owner's core pitch (asset tree + ITP/NCR/weld quality + document filing) as a usable inspection system; smallest coherent pilot | No logistics/claims yet |
| **B. Extend to Phase 4** (add logistics + execution/edge screens) | A field crew can actually run a day on it | Months more before first feedback |
| **C. No line** | — | Death by 94 modules |

**Recommendation: A — Phase 2 + `documents` is the MVP. (confidence: high)** Add the three tier-1 **edge screens** (D-11) only if a field pilot is in scope; otherwise the backbone+quality+CDE is demonstrable to a client as "the inspection management system." **Affects:** phase plan, task prioritisation, TD-009. This is the owner's highest-leverage scoping call after TD-002.

#### D-10 · TD-006 — Offline sync in Phase 1 · **NOW · Med · Team/Owner**
Spec §9 requires offline from Phase 0/1; modules list put it in Phase 4. Re-sequenced to T-0125/T-0126.
**Recommendation: Confirm the move to Phase 1. (confidence: high)** Offline can't be retrofitted (it dictates the append-only response model, `client_id`/`sync_version`, and the CEL-on-device choice in D-03). It must be designed with the inspection record, not after. **Affects:** `inspections`, `field_diary`, `workpacks`, every edge screen; couples to D-01 and D-03.

#### D-11 · TD-008 — Tier-1 edge-screen UI specs · **SOON · Med · Team(planner)**
Gate Delivery, Look-Ahead Board, Daily Workface Log have no UI specs; architecture-suite Part 4 shows the as-built patterns to copy.
**Recommendation: Planner drafts the 3 UI specs before Phase 3/4 breakdown; not a Phase-0/1 blocker. (confidence: high)** Only needed if the pilot (D-09) includes field capture. **Affects:** `site_logistics`, `schedule_advanced`, `field_diary`, frontend.

---

### E. Already-resolved — confirm and stop re-litigating

#### D-12 · D5 — P6 one-way import+export vs bidirectional · **confirm · Owner**
Resolved: file import (.xer/XML) + export-back for re-import, **no live bidirectional sync** (FINAL-PLAN §9, MODULE-SPEC-08 §2.5). **Recommendation: confirm — correct.** Bidirectional sync with conflict rules is a large, fragile surface for little gain when KERP is deliberately *not* the master schedule. (confidence: high)

#### D-13 · D7 — crm folded into contacts · **confirm · Owner**
Resolved: folded; single people/company directory, no pipeline (FINAL-PLAN §9). **Recommendation: confirm — correct.** A field-ops platform has no sales pipeline; one directory is right. Delete any residual `crm` module reference from the register. (confidence: high)

#### D-14 · D8 — portal phasing · **confirm · Owner**
Resolved: v1 read-only → v2 co-sign reports/ITPs → v3 visit scheduler + client NCR (FINAL-PLAN §9, MODULE-SPECS §portal). **Recommendation: confirm — correct, keep v1 in the MVP read-only, v2/v3 in Phase 7.** (confidence: high)

#### D-15 · TD-001 — port Inspection-ERP core vs rewrite · **confirm · Owner**
Applied default: port-and-adapt the proven core (BaseRecordMixin, RLS, auth, audit, loader, validation). **Recommendation: confirm — port. (confidence: high)** The architecture-suite documents it file-by-file; rewriting tested doctrine is pure risk. Caveat: port *patterns*, re-verify against the hard rules (ADR-001 §5 lists the fixes).

---

### F. Cut / defer — don't spec these for the pilot

#### D-16 · TD-005 — `module_builder` fate · **DEFER/CUT · Low · Owner**
Runtime no-code modules; no buildable spec.
**Recommendation: Cut from scope; do not spec for the pilot. (confidence: high)** A single-company product does not need runtime module authoring; the `forms`/`item_types` engine (D-01) already gives no-code *configuration* where it matters. Delete T-0027 from the active plan or mark it `later`. **Affects:** removes a spec-gap and a dead task.

#### D-17 · `terms` terminology dictionary (optional) · **DEFER · Low · Team**
AIP has a layered label-override dictionary; KERP dropped i18n (single company) but still wants to rename RSW / Punch↔Snag↔Deficiency without code edits.
**Recommendation: Ship a *lightweight* key→label override table inside Project Settings, not a full `terms` module. (confidence: med)** Earns its place for the 20–30 renamable domain nouns; a full dictionary is over-build. Defer to when the first client asks to rename something. **Affects:** Project Settings, UI labels.

#### D-18 · `ai_gov` AI governance (optional) · **DEFER · Low · Owner**
A single AI gateway + data-flow register; KERP already has the "AI mutations are proposals / `source='ai'`" principle.
**Recommendation: Defer; keep only the data-flow *register* as a 1-page doc for client security reviews (mining/IRAP). (confidence: med)** Don't build a module; the hard rule (confirm-before-apply) is the actual control. Revisit if a client security review demands it. **Affects:** none in MVP.

#### D-19 · SaaS `security` programme · **CUT · Low · Owner**
PROPOSAL marks it "Skip (revisit if sold as SaaS)". **Recommendation: confirm cut.** (confidence: high)

#### D-20 · TD-010 — arch-suite reference re-sync · **DEFER · Low · Team**
Reference-only import from an unmerged branch. **Recommendation: leave; re-sync only if PR #22 lands materially different. Not a build gate.** (confidence: high)

---

## 3. One-page shortlist — decide NOW vs safe to defer

### The owner must decide now (these gate Phase 0/1)
| # | Decision | Recommended answer | Why it can't wait |
|---|---|---|---|
| **D-01 / TD-002** | Adopt the AIP one-engine proposal? | **Yes, in full** | Phase-1 tasks cannot be sized until this is settled |
| **D-09 / TD-009** | MVP cut line | **End of Phase 2 + `documents`** | Agents otherwise grind 94 modules with nothing pilot-usable |
| **D-06 / TD-007** | Keep tenant_id + RLS? | **Yes** | Core-table + RLS shape of every module (T-0006); impossible to retrofit |
| **D-07 / TD-003** | Build the `AuthProvider` seam (local now, SSO later)? + staff = Entra-ID? | **Seam yes; SSO deferred** | Phase-0 auth (T-0007); only the seam + rough IdP intent needed now |
| **D-15 / TD-001** | Port the proven core? | **Yes, port-and-adapt** | T-0005 is already proceeding on this default; confirm it |

### The team decides now (ADR, no owner needed — but needs a decision this week)
| # | Decision | Recommended answer |
|---|---|---|
| **D-03** (new TD-012) | Rule-evaluator engine | **CEL** (one grammar web+Python+device) |
| **D-04** (new TD-013) | Asset-tree storage | **Postgres `ltree`**, drop text `path`/`depth` |
| **D-02** (new TD-011) | Approval engine | **`approval_routes`**, delete `enterprise_workflows` |
| **D-05** | Inspection module split | **One record (follows D-01)**; qms=audit, cc=gates |
| **D-10 / TD-006** | Offline sync in Phase 1 | **Confirm the move** |

### Safe to defer (behind a seam) / just confirm
- **Confirm & move on:** D-12 (P6 one-way), D-13 (crm→contacts), D-14 (portal phasing) — all resolved and correct.
- **Decide by T-0004:** D-08 / TD-004 (hosting) — Coolify/MinIO, adapter-swappable.
- **Phase-3/4:** D-11 / TD-008 (edge-screen UI specs — planner drafts).
- **Cut/defer, don't spec:** D-16 (`module_builder`), D-17 (`terms` → lite), D-18 (`ai_gov` → register only), D-19 (`security`), D-20 (arch-suite re-sync).

---

## 4. Recommendations table (atomic, yes/no each)

| # | Recommendation | Rationale | Effort | Priority | Affects |
|---|---|---|---|---|---|
| R1 | Adopt TD-002 (AIP one-engine) in full, sequenced per PROPOSAL §4 | Kills 3–4 half-engines; executes the spec's own dedup instruction | L | P1 | forms, item_types, inspections, qms, construction_control, eligibility, inspection_requests; E-01-01; Phase 1 |
| R2 | Set MVP cut line = end of Phase 2 + `documents` (TD-009) | Smallest coherent, demonstrable "inspection management system" | S | P1 | phase plan, task priority |
| R3 | Keep tenant_id + RLS, one tenant/deploy (TD-007) | Portal needs row-scoping; un-retrofittable | S | P1 | all tables, T-0006 |
| R4 | Build AuthProvider seam, local JWT now, SSO later (TD-003) | Unblocks Phase 0 without IdP dependency | S | P1 | users, T-0007 |
| R5 | Confirm port-and-adapt the proven core (TD-001) | Tested doctrine; rewrite is pure risk | — | P1 | T-0005 |
| R6 | **Pick CEL as the verification/forms rule evaluator** (new TD-012) | One sandboxed grammar web+Python+device; de-risks TD-002's dual evaluator | M | P1 | validation, eac, run_checks, eligibility, forms; T-0006 |
| R7 | **Use Postgres `ltree` for the asset tree; drop text path/depth** (new TD-013) | Native subtree queries + AIP recurrence scoping; GiST already present | M | P1 | asset_management; T-0101 |
| R8 | **Keep `approval_routes`, delete `enterprise_workflows`** (new TD-011) | Spec says pick one; approval_routes is richer | S | P1 | platform, all approvals |
| R9 | Resolve inspection split via one record (qms=audit, cc=gates) | Follows R1; removes triple-write of sign-off/offline logic | M | P1 | inspections, qms, construction_control, workpacks |
| R10 | Confirm offline sync in Phase 1 (TD-006) | Dictates record model + on-device CEL; un-retrofittable | S | P1 | inspections, field_diary, edge screens |
| R11 | Choose Coolify/MinIO; keep S3 adapter swap; decide before T-0004 (TD-004) | Cheapest, on-prem-friendly for target clients | S | P2 | uploads, email, deploy |
| R12 | Planner drafts the 3 tier-1 edge-screen UI specs before Phase 3/4 (TD-008) | Only needed if pilot includes field capture | M | P2 | site_logistics, schedule_advanced, field_diary |
| R13 | Confirm D5/D7/D8 (P6 one-way, crm→contacts, portal phasing) | Resolved and correct; stop re-litigation | — | P2 | P6, contacts, portal |
| R14 | Cut `module_builder` from pilot scope (TD-005); mark T-0027 `later` | No single-company need; forms engine covers no-code config | S | P2 | removes dead task |
| R15 | Ship a lite key→label override in Project Settings, not a `terms` module | Rename RSW/Punch without code; full dictionary over-builds | S | P3 | Project Settings |
| R16 | Defer `ai_gov` to a 1-page data-flow register; `source="ai"` rule is the real control | Over-build for single-company | S | P3 | — |
| R17 | Confirm cut of SaaS `security` programme (D-19) and leave TD-010 as reference-only | Out of scope single-company | — | P3 | — |

---

## 5. Conflicts & risks

- **Two load-bearing engines are unspecified** (D-03 rule evaluator, D-04 tree storage). This is the biggest latent risk: `run_checks` and the asset tree are the critical path the plan says to *freeze first* (DEVELOPMENT-PLAN §201), yet neither names a concrete engine. If two agents implement T-0006 and T-0101 before these are fixed, they *will* diverge. **Raise TD-011/012/013 and resolve before Phase 0 code.**
- **TD-002's dual-language evaluator vs offline (TD-006)** is a real coupling: offline capture needs on-device evaluation, which forces the shared-evaluator decision and the CEL recommendation. Deciding TD-002 "yes" without deciding D-03 leaves the hardest part unspecified.
- **Register still lists both `enterprise_workflows` and `approval_routes`, and a `crm` reference** despite D7 folding it — the generated register (FINAL-PLAN §1.3) contradicts the resolved decisions. Clean the register when R8/R13 land, or agents will build the deleted modules.
- **No conflict with the hard rules** in any recommendation: CEL/ltree/approval_routes all live inside the modular-monolith + BaseRecordMixin + RLS doctrine; the anchor model (RSW/Item, no BOQ), the commercial boundary (money as read-only mirror), and single-company doctrine are all preserved. The one place to watch: `ltree` means deleting the hand-rolled `path`/`depth` columns — that's a **core-table change → needs an ADR** (hard rule 8).
- **Scope risk (TD-009) is the silent killer**: 94 modules with no cut line means the owner's core pitch (quality/inspections) may not be demonstrable for months. R2 is low-effort, high-leverage.

## 6. Open questions for the owner (only you can answer)
1. **TD-002:** Adopt the one-engine rebuild in full (R1)? *This is the gate — answer first.*
2. **TD-009:** Is the pilot "inspection management system" (Phase 2 + documents), or must it include field capture (→ Phase 4 + edge screens)? Changes R2 and whether TD-008 is urgent.
3. **TD-003:** Will staff authenticate via **Entra ID / Windows SSO** eventually (design the adapter for it), or is local auth the long-term plan?
4. **TD-004:** On-prem/VPS (Coolify/MinIO) or AWS? Driven by client data-residency requirements — do your target clients (mining/industrial) demand on-prem?
5. **TD-007:** Confirm keep multi-tenant plumbing (cheap insurance for the client portal)?
6. New **TD-011/012/013** (approval engine, rule evaluator, tree storage): these are engineering ADRs — do you want to ratify the recommendations (approval_routes / CEL / ltree) or leave them to the lead?

## 7. Feeds-the-plan (lift directly into the merged plan)
- **Decide-now block:** R1 (TD-002 adopt), R2 (MVP = Phase 2 + documents), R3 (keep RLS), R4 (auth seam), R5 (port core), R10 (offline in Phase 1).
- **Three new TODOs to raise immediately:** `TD-011` approval_routes vs enterprise_workflows → **approval_routes**; `TD-012` rule evaluator → **CEL**; `TD-013` asset-tree storage → **ltree** (+ ADR, core-table change). All three must be resolved before Phase-0 code (T-0006) and the tree task (T-0101).
- **Phase-0 freeze list:** before any Phase-0 code, lock: rule engine = CEL, tree = ltree, approval engine = approval_routes, tenancy = RLS-on, auth = AuthProvider seam. DEVELOPMENT-PLAN §201 already demands the tree/`run_checks` contract be frozen — name the engines in that freeze.
- **Register cleanup task:** on adopting R8/R13, delete `enterprise_workflows` and any `crm` reference from FINAL-PLAN §1.3 so generated boards match the resolved decisions.
- **Cut list for the pilot:** `module_builder` (later), full `terms` module (→ lite Project-Settings override), `ai_gov` (→ 1-page register), SaaS `security` (skip).
- **Confirm-and-close:** D5 (P6 one-way+export), D7 (crm→contacts), D8 (portal phasing), TD-001 (port) — mark these TODOs resolved once the owner signs off, to stop re-litigation.
