# Tech Stack Advisor — review

**TL;DR**
1. The core stack (FastAPI modular monolith · Postgres 16 + PostGIS · Redis · Nginx · Vite/React 18/TS/Tailwind/TanStack Query · presigned object storage · no DuckDB) is **the right shape for this OLTP-plus-bursty-heavy-files workload**. Adopt it. Most of my concern is *below* ADR-001: the specific libraries it does not name.
2. **ARQ is the single biggest stack risk.** It is effectively maintenance-only in 2026 and its `cron`/priority story is thin for a single worker that must serve a 200-page MDR compile *and* interactive OCR. Pick **SAQ** (near-drop-in async-Redis successor, lower latency, built-in web UI) and keep the existing `jobs` module as the seam so the choice is reversible.
3. **The shared TS+Python rule evaluator (TD-002) needs a named language now, not at build time.** Choose **CEL** (testable cross-language parity against Google's conformance suite) over JSONLogic for engineering formulas — but budget for the native-dependency vs pure-Python trade-off and a JS CEL for the offline device. This decision gates Phase 1.
4. **Nesting, P6 parsing and DWG are licence/longevity traps** ADR-001 glosses over: the obvious nesting lib (`libnest2d`) is GPL, the main P6 parser (`PyP6Xer`) is a single-maintainer LGPL "WIP" on your #1 integration-risk path, and **native DWG parsing has no viable OSS library at all**. Each needs a named decision.
5. **The 2 vCPU / 4 GB target is honest only through Phase 2.** Once Phase 5 lands local embeddings + OCR + CAD/BIM in one ARQ worker, 4 vCPU / 8 GB is the real floor, and the single worker becomes a head-of-line-blocking bottleneck. Plan for an external embedding path or a second (priority) worker.

---

## 1. Verdict on the chosen stack

| Decision | Verdict | Where it's right / risky |
|---|---|---|
| **FastAPI modular monolith, 3 Uvicorn workers** | **Right** | Matches the as-built Inspection-ERP (arch-ref 01 §1.2) so the port (TD-001) is low-friction. Async SQLAlchemy 2.0 pairs cleanly with an async worker. Risk: `run_checks` and RLS re-bind per-transaction logic must survive the sync→async session move; the as-built uses sync `before_flush` listeners (arch-ref 01 §2.4) — porting those to async sessions is non-trivial and under-specified. |
| **Postgres 16 + PostGIS, no DuckDB** | **Right, and the best call in ADR-001** | The workload is OLTP (asset trees, inspections, sign-offs, ledgers). PostGIS genuinely earns its place (inspection pins, site maps, pipeline corridors — DEV-PLAN §2a). Dropping DuckDB removes the per-connection memory blow-up. Risk: the BI/EVM path (`bi_dashboards`, `full_evm`, `eac` physical) now *must* precompute into materialised tables in the worker — if any dashboard reaches for ad-hoc cross-module aggregation at request time it will violate both the no-DuckDB rule and the no-cross-module-table rule. Watch Phase 6. |
| **Redis 7 + one ARQ worker** | **Shape right, library wrong** | Isolating heavy work is correct. ARQ specifically is the risk — see §2 and §3. The `jobs` module seam (ADR-001 §4) is what saves you; keep it strict. |
| **Nginx HTTP/2 + Brotli** | **Right, cheap** | Correct for the UI-dense asset-tree/drawing screens. Also the natural place to reverse-proxy MinIO so presign has one hostname+cert (ADR-001 §3). |
| **Presigned direct-to-bucket uploads** | **Right — biggest stability win** | Correct that the API must never buffer 500 MB CAD/MDR files. The caveats ADR-001 lists (CORS, on-complete scan, orphan reaper, sha256 verify) are the real work; they are specced but not costed. |
| **Vite + React 18 + TS strict + Tailwind + TanStack Query** | **Right, and already proven** | Identical to the as-built (arch-ref 01 §1.2), so the frontend port is mechanical. See §4 for the one gap: offline **writes**. |
| **i18n `t()`** | **Over-scoped but harmless** | The brief says single-company (currency/date/locale/tax are Project Settings). You do *not* need an i18n subsystem for languages; you need a **terminology dictionary** (rename RSW / Punch→Snag) — the AIP `terms` idea (PROPOSAL §Optional). Keep `t()` as the resolution mechanism, drop the multi-locale ambition. |
| **2 vCPU / 4 GB viable** | **Optimistic past Phase 2** | See §6. |

**Net:** adopt the stack. The gaps are all one layer down, which is exactly what this review is for.

---

## 2. Capability → recommended library

Placement key: **web** = Uvicorn request path (keep tiny, lazy-import); **worker** = ARQ/SAQ job (heavy, lazy-import per ADR-001 §3A); **browser** = frontend.

| Capability | Recommended library | Why / maturity | Licence | Placement |
|---|---|---|---|---|
| **P6 `.xer` parse** | `PyP6Xer` (import `xerparser`) **behind the `integrations` seam**, OR parse XER directly (it is tab-delimited text) with a small in-house reader | Only real OSS XER parser, but single-maintainer, self-described "WIP". XER is a simple tabular format — owning a thin parser + a **fixture corpus of real-but-synthetic .xer files** de-risks your #1 integration risk (DEV-PLAN §9). | LGPL-2.1 (viral-ish on the lib, not your app) | **worker** |
| **P6 / Primavera XML parse** | `lxml` | Standard, fast, battle-tested. P6 XML is just XML; no bespoke lib needed. | BSD | **worker** |
| **Excel / CSV ingest** | `openpyxl` (read/write) + stdlib `csv`; `python-calamine` if read speed bites | `openpyxl` is the as-built choice (arch-ref 01 §1.2). Use the as-built `spreadsheet_safe` formula-injection guard on export (arch-ref 02). | MIT / stdlib | **worker** (large files), web (small) |
| **PDF generation (MDR / QR packs, reports)** | `reportlab` (vector/forms) + `PyMuPDF`/`fitz` (assemble/stamp/merge 200-page MDRs) + `pypdf` (light merge) | As-built uses reportlab+openpyxl (ADR 0006). PyMuPDF is the muscle for merging drawings + burning QR + stamping — exactly ADR-001 §4's job list. | reportlab BSD; **PyMuPDF AGPL** ⚠ | **worker** |
| **PDF AGPL hazard note** | — | **PyMuPDF is AGPL-3.0.** For a single-company internal deployment that is usually fine, but it *is* a copyleft obligation. If distribution is ever contemplated, use `pypdf` + `pdfium2` (BSD/Apache) for merge/render and keep reportlab for generation. **Flag for the owner.** | — | — |
| **OCR (dockets, daywork, receipts → FTS)** | **Tesseract** via `pytesseract` first; **docTR** or **PaddleOCR** if accuracy on photographed dockets is poor | Tesseract is light enough for 2–4 vCPU and simplest to ship. docTR/PaddleOCR are far better on skewed phone photos but pull torch/paddle (heavy — hurts the footprint target). Start Tesseract, keep the OCR call behind a service seam. | Apache-2.0 / Apache-2.0 / Apache-2.0 | **worker** |
| **CAD/BIM — IFC (BIM)** | `ifcopenshell` | The de-facto OSS IFC toolkit; geometry + property extraction. Heavy — lazy-import, worker only. | LGPL-3.0 | **worker** |
| **CAD/BIM — mesh / thumbnail** | `trimesh` (+ `pyrender`/`matplotlib` for raster thumbs) | As-built names trimesh (ADR-001 §3A). Good for STEP-ish/mesh preview. | MIT | **worker** |
| **CAD — DXF** | `ezdxf` | Mature, pure-Python DXF read/write + rendering backend for thumbnails. | MIT | **worker** |
| **CAD — native DWG** | **No viable OSS lib — gap.** Options: require DXF, or shell out to the **ODA File Converter** (free-to-use, proprietary EULA) DWG→DXF, then `ezdxf` | AutoCAD DWG has no production-grade OSS reader. This is a real **stack gap ADR-001 does not name** (§5). Decide: "we accept DXF only" or "we bundle ODA converter in the worker image." | proprietary (ODA) | **worker** |
| **1-D cutting optimisation (bars/rebar)** | **In-house** First-Fit-Decreasing / Best-Fit (≈100 LOC) | The `cutting` spec (MODULE-SPEC-08 §1) already says "1-D first-fit-decreasing / best-fit". This is trivial, deterministic, testable — **do not take a dependency.** Avoids the nesting-licence trap entirely for the common case. | — (yours) | **worker** |
| **2-D sheet/plate nesting** | `rectpack` (rectangular guillotine/shelf — MIT) for the specced "guillotine/shelf" case; avoid `nest2D`/`libnest2d` | MODULE-SPEC-08 §1 asks for "2-D guillotine/shelf nesting" — rectpack does exactly that and is MIT. **`libnest2d`/`nest2D` is GPL** and would contaminate a proprietary product; only reach for it if true irregular-polygon nesting becomes a requirement, and then isolate it as an out-of-process tool. | rectpack MIT | **worker** |
| **Geospatial** | **PostGIS** (via GeoAlchemy2 in SQLAlchemy); `shapely` for in-worker geometry ops | ADR-001 already adopts PostGIS. GeoAlchemy2 maps geometry columns in the ORM; shapely for any compute that shouldn't hit the DB. | PostGIS GPL (server, fine); GeoAlchemy2 MIT; shapely BSD | **DB / worker** |
| **Full-text search** | **Postgres `tsvector` + GIN** (native); consider `ParadeDB`/`pg_search` (BM25) only if ranking quality bites | Keeps the footprint single-datastore (no Elasticsearch). Matches the "no second store" ethos. BM25 via pg_search is a drop-in later if lexical ranking is weak. | PG native / pg_search (Elastic-License v2 ⚠) | **DB** |
| **Vector / embeddings search** | **pgvector** in the same Postgres; embeddings computed by `sentence-transformers` in the worker **or** an external embedding API | pgvector is the as-built documented path (arch-ref 01 §1.3) and keeps one datastore. The *embedding model* is the cost question — local `sentence-transformers` loads 0.4–2 GB and fights OCR/CAD for RAM on a 2–4 vCPU box; an external embedding API keeps the worker lean. **Decide per TD-004 hosting.** | pgvector PostgreSQL-licence; S-T Apache-2.0 | **DB + worker** |
| **Shared rule evaluator (CEL/JSONLogic, TS+Python parity, TD-002)** | **CEL** — `cel-expr-python` (official, Mar 2026) or `cel-python`/celpy (pure-Python) on the server; a JS CEL (`cel-js`) in the browser; one shared conformance-fixture suite | See §3 — this is the load-bearing choice. CEL has an **official cross-language conformance test suite**, which is the only way to *prove* the "same fixtures, TS+Python parity" the proposal demands (PROPOSAL §port, MODULE-SPEC-11 §2–3). JSONLogic (`json-logic-js` + `json-logic-py`) is simpler and genuinely cross-language but weak for engineering formulas (corrosion rate, DFT average) and has no conformance guarantee. | CEL impls Apache-2.0; JSONLogic MIT | **web + worker + browser** |
| **E-signature / PAdES** | `pyHanko` | MIT, actively released, supports PAdES B-B/B-T/B-LT/B-LTA. The right tool for cryptographically signing the MDR/notice PDFs. | MIT | **worker** |
| **Hash-chain record ledger** | **App-level** (`content_hash` = sha256 of canonical JSON, `prev_hash` link, `REVOKE UPDATE/DELETE`) — *not* pyHanko | The proposal (PROPOSAL §inspections, MODULE-SPEC-11) conflates two things: **PAdES** signs a *PDF document*; the **hash-chain** is an append-only *record* integrity chain. They are different mechanisms. The as-built already has the `BaseRecordMixin` sha-256 content hash (arch-ref 01 §3.3) — extend it with `prev_hash`, don't reach for a crypto lib. | — (yours) | **web/DB** |
| **Offline sync (IndexedDB layer)** | **Dexie.js** as the IndexedDB store + a typed **outbox** table; replay keyed on `client_op_id` | See §4. Dexie is the mature, typed IndexedDB wrapper; the "idempotent sync ledger (client op id → result)" in DEV-PLAN §1(9) is a transactional-outbox pattern. | Apache-2.0 | **browser** |
| **Background jobs** | **SAQ** (recommended) behind the `jobs` module; ARQ acceptable if the seam stays strict | §2/§3. | SAQ MIT; ARQ MIT | **worker** |
| **Auth / JWT** | `PyJWT` (HS256) + stdlib `hashlib.scrypt` (as-built); behind an `AuthProvider` seam (TD-003) | As-built (arch-ref 01 §3.1, ADR 0001). Consider `argon2-cffi` over scrypt if you want a modern memory-hard default, but scrypt is defensible. The seam is what lets TD-003 add Entra ID/SSO later without a rewrite. | PyJWT MIT; argon2 MIT | **web** |
| **Migrations** | **Alembic** | As-built; correct. Enforce "additive by default; destructive needs an ADR" (hard rule 8) in the CI gate. | MIT | **build/web** |
| **API client codegen** | `openapi-typescript` (as-built); `@hey-api/openapi-ts` is the evolved successor if you want typed clients not just types | As-built generates `src/api/schema.d.ts`, never hand-edited (hard rule 9). Keep it; wire `npm run gen:api` into the CI gate so drift fails the build. | MIT | **build/browser** |
| **Charting / BI** | **Recharts** (simple, React-native) or **Apache ECharts** (dense dashboards, big datasets) | ADR-001 names no charting lib — a gap for `dashboards`/`bi_dashboards`/`full_evm`. Recharts for standard KPI cards; ECharts when you need large time-series / Gantt-ish / heatmaps. Avoid a heavy commercial BI embed. | Recharts MIT; ECharts Apache-2.0 | **browser** |
| **Drawing markup / measurement** | `PDF.js` + a canvas overlay for view/markup; **PyMuPDF** to burn-in overlays in the worker | The `markups` module + `workpacks` drawing overlays (DEV-PLAN Phase 5/6b) need an on-screen annotation layer and a server-side flatten. No lib is named in ADR-001 — gap. PDF.js renders; store markups as vector JSON (rename-safe), flatten only for the MDR. | PDF.js Apache-2.0 | **browser + worker** |
| **QR / barcode generate** | `segno` (or `qrcode`) server-side | ADR-001 §4 names `qrcode`. `segno` is smaller/faster and does more symbologies. For the QR-coded MDR auto-filer (Phase 6b). | segno MIT | **worker** |
| **QR / barcode scan** | `html5-qrcode` or `zxing-js` in the browser | For the field QR auto-filer return path (scan signed pack → file into VFS). Not named anywhere. | Apache-2.0 | **browser** |

---

## 3. The two decisions ADR-001 defers that actually gate Phase 1

### 3.1 The async worker library (ARQ → SAQ)
ADR-001 §4 picks ARQ "because it shares our async SQLAlchemy engine" and notes "ARQ's `cron` is minimal vs Celery beat." In 2026 the stronger objection is **maintenance**: community comparisons now list ARQ as maintenance-only and point to **SAQ** as the async-Redis successor — same engine-sharing property, **sub-5 ms latency vs ARQ's 0.5 s poll**, a built-in web UI, and a heartbeat for abandoned jobs ([SAQ comparison](https://saq-py.readthedocs.io/en/stable/comparison.html), [Python job-queue survey](https://lab.abilian.com/Tech/Python/Useful%20Libraries/Job%20queues)). For a platform whose heavy jobs (MDR compile, OCR, P6 parse) are also the ones a user is *waiting on*, latency and a monitoring UI matter.

- **Recommendation:** adopt **SAQ** now. It costs nothing extra — ADR-001 already mandates the `jobs` module as the execution seam, so modules never import the backend directly. If you prefer to minimise change, keep ARQ but **treat the `jobs` seam as a hard rule** so a later swap is a one-file change. Do **not** go to Celery (heavier, the thing the pivot removed) or Dramatiq (LGPL-3 flags in some compliance reviews).
- Either way: a **single** worker is a head-of-line-blocking risk — a 200-page MDR compile will block an interactive OCR. Plan **two queues / priorities** (interactive vs batch) or a second worker container before Phase 5/6b. SAQ's priority support makes this easy; ARQ's does not.

### 3.2 The shared rule evaluator language (TD-002)
The AIP proposal's foundation is "one shared field-type registry + **one shared evaluator (TypeScript package with a Python port tested on the same fixtures)**" (PROPOSAL §port, step 1 of the dependency order; MODULE-SPEC-11 §2–3). This is also what `run_checks` conceptually needs for data-driven rule sets (DEV-PLAN §5). **The spec never names the expression language.** It must be chosen before Phase 1 because `forms`/`item_types`/`inspections`/`validation` all build on it.

Two credible families:

| | **CEL** (Common Expression Language) | **JSONLogic** |
|---|---|---|
| Cross-lang parity | **Official conformance suite**; Google shipped an official Python impl Mar 2026 ([announcement](https://opensource.googleblog.com/2026/03/announcing-cel-expr-python-the-common-expression-language-in-python-now-open-source.html)); JS/Go/C++ exist | `json-logic-js` + `json-logic-py` share a JSON format but **no conformance tests** — drift is on you |
| Engineering formulas | Good (arithmetic, comparisons, macros, custom functions for corrosion/DFT) | Weak/verbose; awkward for `visible_if`/calculated fields |
| Safety/sandboxing | Designed as a safe, non-Turing-complete eval | Safe by construction (data) |
| Cost | Official Python wraps **C++ (native dep)** — heavy in web+worker; pure-Python `celpy` avoids that but has **documented semantic divergences** (e.g. integer division truncation) you must pin in fixtures ([celpy](https://github.com/cloud-custodian/cel-python)) | Pure JS/Python, trivial to embed, tiny |

- **Recommendation:** choose **CEL** as the shared evaluator, because it is the only option where "TS + Python tested on the same fixtures" is *provable* against a maintained conformance suite — which is exactly the legal-defensibility argument the proposal makes. Accept the trade-off explicitly: use **pure-Python `celpy`** server-side to keep the worker image free of a C++ build, use a **JS CEL in the browser/offline device**, and **own a conformance-fixture file** (including the known integer-division edge) that both must pass in CI. If the owner judges the rule surface will stay simple (pure show/hide + required), JSONLogic is the cheaper, lighter answer — but it caps you below "real engineering inspections."
- This is a **P1 decision tied directly to TD-002** — resolving TD-002 "adopt AIP one-engine" without naming the evaluator leaves the highest-value change ungrounded.

---

## 4. Frontend data / offline architecture call

The spec is firm that offline edge capture (gate delivery, look-ahead board, daily workface log, diaries, inspections, workpack sign-off) is **Phase 1, not retrofitted** (TD-006, DEV-PLAN §9), via an "idempotent sync ledger (client op id → result) with conflict replay." TanStack Query alone does **not** give you this — it manages *server-state reads*, not a durable *write* queue.

**Recommended architecture:**
- **Reads offline:** TanStack Query + `@tanstack/query-persist-client` persisting the query cache into IndexedDB, so registers/hierarchy render offline. (Not localStorage — the asset tree + drawings exceed its quota.)
- **Writes offline (the real work):** a dedicated **transactional outbox** in IndexedDB via **Dexie.js** — a typed `outbox` table of `{client_op_id, endpoint, payload, status, result}`. Mutations enqueue locally, the UI updates optimistically, and a replay worker drains the outbox on reconnect (`navigator.onLine` + TanStack Query `onlineManager`). The server is **idempotent on `client_op_id`** (it stores op-id→result, exactly the as-built `core/sync` server-wins model, arch-ref 01 §1.3 / ADR 0007) so replays are safe and conflict resolution is explicit.
- **Why Dexie over raw IndexedDB or `idb`:** typed schema, transactions, versioned migrations, mature. `idb` (Jake Archibald) is a lighter alternative if you want minimal surface; either beats hand-rolling IndexedDB.
- **Watch-list:** TanStack **DB** (2025, reactive local collections) is tempting for this but is too new to bet the offline backbone on; revisit post-MVP. The `workpacks` tablet sign-off explicitly reuses the `field_diary` sync ledger (DEV-PLAN §6b, MODULE-SPEC-11 §reuse) — make the outbox **one shared mechanism**, not per-module, or you will build the sync ledger three times.

---

## 5. Stack gaps the module set implies that ADR-001 doesn't name

1. **DWG parsing** (CAD in `documents`/`markups`/`workpacks`) — no OSS lib; needs an explicit "DXF-only" or "bundle ODA converter" decision (§2).
2. **The expression language** for `run_checks` / shared evaluator (§3.2) — named nowhere.
3. **Charting** for `dashboards`/`bi_dashboards`/`full_evm` (§2).
4. **On-screen drawing markup/measurement** (`markups`) — PDF.js + overlay model unspecified (§2).
5. **QR *scanning*** (field auto-filer return path) — only QR *generation* is named (§2).
6. **Embedding model + host** for `search`/`retrieval` vector path — pgvector is named, the model that fills it is not, and it is the single heaviest RAM consumer (§2, §6).
7. **GeoAlchemy2** — PostGIS is adopted but the ORM mapping layer for geometry columns is unnamed; without it, spatial columns fall outside the `BaseRecordMixin`/RLS discipline.
8. **Async `before_flush` equivalents** — the as-built hash/revision, tenant-ref and project-scope guards are sync `before_flush` listeners (arch-ref 01 §2.4); porting them to async SQLAlchemy sessions is unspecified and load-bearing for hard rules 2–4.

---

## 6. Cost / ops footprint sanity check vs the 2–4 vCPU target

ADR-001 claims web ~150–200 MB, Postgres ~150–250 MB, "2 vCPU / 4 GB viable, 4 vCPU / 8 GB comfortable." **That is accurate for Phases 0–2** (platform + asset backbone + welding), where the heavy jobs are PDF/Excel and P6 parse — all modest.

It **stops being accurate at Phase 5/6**, where the *same single worker* must host, concurrently:
- OCR (Tesseract ~tens of MB; docTR/Paddle ~1–2 GB with torch),
- CAD/BIM (`ifcopenshell` + `trimesh` on a real IFC model: hundreds of MB, spiky),
- embeddings (local `sentence-transformers`: 0.4–2 GB resident),
- MDR compile (PyMuPDF on a 200-page pack: hundreds of MB spiky),
- BI rollups.

On 4 GB that OOMs; even on 8 GB a single worker serialises them (head-of-line blocking). **Recommendations:**
- Treat **4 vCPU / 8 GB as the real floor from Phase 5 on**, and say so in ADR-001 (it currently implies 2/4 is a steady-state option).
- **Offload embeddings to an external API** unless TD-004 picks a host with spare RAM — this single move keeps the worker lean and is the cheapest lever.
- Introduce **priority queues or a second worker** (interactive OCR/compile vs batch rollup/index) before Phase 6b — SAQ makes this trivial (another reason for §3.1).
- Keep the **lazy-import lint rule** (ADR-001 §3A) as a hard CI gate — it is doing real work holding idle footprint down; without it the worker's resident set balloons on import.
- Presigned uploads already keep the *web* tier flat under heavy files — that part of the sizing claim is solid.

---

## 7. Recommendations table

| # | Recommendation | Rationale | Effort | Priority | Affects |
|---|---|---|---|---|---|
| R1 | **Adopt SAQ** as the async worker backend (keep the `jobs` module as the seam) | ARQ is maintenance-only in 2026; SAQ is the async-Redis successor with lower latency, a web UI and priority queues | S | **P1** | ADR-001 §4; `jobs`; all worker jobs; Phases 0, 5, 6b |
| R2 | **Name CEL as the shared TS+Python rule evaluator**, with a shared conformance-fixture suite in CI | Only option that *proves* cross-language parity the AIP proposal requires; gates Phase 1 | M | **P1** | TD-002; `forms`/`item_types`/`inspections`/`validation`/`run_checks` |
| R3 | **Keep 1-D cutting in-house (FFD/BFD); use `rectpack` (MIT) for 2-D; ban `libnest2d`/`nest2D` (GPL)** | Avoids GPL contamination of a proprietary product; the specced algorithms are simple | S | **P1** | `cutting` (MODULE-SPEC-08 §1); Phase 3 |
| R4 | **Decide DWG strategy: "DXF-only via `ezdxf`" or "bundle ODA converter in the worker image"** | No OSS DWG reader exists; silence here blocks CAD thumbnails/markup | M | **P1** | `documents`, `markups`, `workpacks`; Phase 5/6b |
| R5 | **Offline writes = Dexie outbox keyed on `client_op_id`, replayed on reconnect; one shared ledger** | TanStack Query manages reads, not durable writes; the sync ledger must be one mechanism (DEV-PLAN §1.9) | M | **P1** | TD-006; `field_diary`, `inspections`, `workpacks`, all Tier-1 edge screens; Phase 1 |
| R6 | **Flag PyMuPDF AGPL; if distribution is ever possible, swap merge/render to `pypdf`+`pdfium2`** | AGPL is a real copyleft obligation on the MDR path | S | **P2** | `workpacks`, `reporting`, `documents`; Phase 5/6b |
| R7 | **Offload embeddings to an external API (or size the host for a local model)** | Local embedding models are the single heaviest worker RAM consumer vs the 2–4 vCPU target | M | **P2** | TD-004; `search`/`retrieval`; Phase 5; §6 |
| R8 | **Introduce priority queues / second worker (interactive vs batch) before Phase 6b** | One worker serialises MDR-compile behind interactive OCR | M | **P2** | `jobs`; Phases 5, 6b |
| R9 | **Name charting (Recharts default, ECharts for dense BI)** | ADR-001 names no charting lib for the dashboard modules | S | **P2** | `dashboards`/`bi_dashboards`/`full_evm`; Phase 6 |
| R10 | **Hash-chain = app-level `prev_hash` over the existing `BaseRecordMixin` hash; pyHanko only for PAdES PDF signing** | Spec conflates document-signing with record-chaining; they are different mechanisms | S | **P2** | MODULE-SPEC-11 (inspections integrity); `signing`; Phase 1 |
| R11 | **Own a synthetic `.xer`/XML fixture corpus; wrap `PyP6Xer` behind `integrations`; consider a thin in-house XER reader** | PyP6Xer is single-maintainer "WIP" on the #1 integration-risk path (DEV-PLAN §9) | M | **P2** | `asset_management`, `integrations`; Phase 1/4 |
| R12 | **Reduce i18n to a terminology dictionary** (`terms`), keep `t()` as the resolver, drop multi-locale | Single-company doctrine; renaming (RSW/Snag) is the real need, not translation | S | **P3** | hard rule 9; frontend shell; cross-cutting |
| R13 | **Add GeoAlchemy2 + shapely to the named stack; bring geometry columns under BaseRecordMixin/RLS** | PostGIS adopted without an ORM mapping layer named | S | **P3** | PostGIS usage; `asset_management` spatial; `markups` |
| R14 | **Keep `openapi-typescript` + `gen:api` as a CI gate; note `@hey-api/openapi-ts` as the upgrade path** | As-built; prevents client/schema drift (hard rule 9) | S | **P3** | frontend `api/`; CI |

---

## 8. Conflicts & risks

- **No-DuckDB vs rich BI (Phase 6).** With DuckDB gone, every dashboard aggregation must precompute into materialised tables in the worker. A dashboard that reaches for ad-hoc cross-module SQL at request time breaks *both* the no-DuckDB rule *and* the no-cross-module-table-access rule (hard rule 1). **Design the BI rollups as worker jobs writing summary tables from day one.**
- **Single ARQ/SAQ worker vs "API stays up for other users."** ADR-001's stability thesis is that the worker isolates OOM. True — but a single worker *serialises* heavy jobs, so one big MDR delays everyone's queued OCR. Isolation ≠ throughput. (R8.)
- **GPL/AGPL/Elastic-licence creep.** `libnest2d` (GPL), `PyMuPDF` (AGPL), `pg_search` (Elastic v2), `PyP6Xer`/`ifcopenshell` (LGPL). For a single-company internal deploy most are tolerable, but they should be an explicit, logged choice, not a default. (R3, R6.)
- **Async session port.** The as-built's tenancy/hash/scope guarantees ride on *sync* `before_flush` listeners; the chosen async stack must re-implement them on async sessions without losing the "no model can forget it" property (hard rules 2–4). Under-specified and high-blast-radius.
- **Evaluator semantic drift.** If CEL-pure-Python (celpy) and the JS CEL disagree on an edge (integer division, overflow) a rule can pass on the device and fail on the server — a legal-defensibility hole for signed inspection records. The shared fixture suite is the mitigation and is non-optional. (R2.)
- **Presigned-upload CORS/scan gap.** The security-critical pieces (on-complete AV scan, sha256 verify, orphan reaper) move *out* of the request into worker jobs; if any is skipped, an un-scanned "available" file is reachable. Specced (ADR-001 §3) but easy to under-build.

---

## 9. Open questions for the owner

1. **(→ TD-002) Which expression language** backs the shared evaluator and `run_checks` — **CEL** (provable parity, heavier/native option) or **JSONLogic** (lighter, weaker for engineering formulas)? *This blocks Phase 1.* (R2)
2. **(→ TD-004) Embedding/vector host:** local `sentence-transformers` in the worker, or an external embedding API? Drives whether 4 GB/8 GB holds. (R7)
3. **DWG support:** do you accept **DXF-only**, or must the platform read native AutoCAD DWG (→ bundle the proprietary ODA converter)? (R4) — *propose new TD.*
4. **Copyleft tolerance:** is AGPL (PyMuPDF) / GPL-adjacent tooling acceptable for an internal single-company deploy, or must the stack stay permissive-only (affects PDF, nesting, search choices)? (R3, R6) — *propose new TD.*
5. **(→ TD-003) Password hashing:** keep as-built **scrypt**, or move to **argon2** behind the `AuthProvider` seam while you're touching identity?
6. **Worker topology:** accept a single worker through MVP, or budget a second/priority worker for Phase 5/6b now? (R8)

## 10. Feeds-the-plan

Concrete items for the merged plan:
- **ADR-002 (new): async worker = SAQ** behind the `jobs` seam; two queues (interactive/batch); lazy-import lint gate retained. (R1, R8)
- **ADR-003 (new): shared expression language = CEL**; `celpy` server, JS CEL device, one conformance-fixture suite in CI; resolves the TD-002 evaluator gap. (R2)
- **Named library manifest** added to DEV-PLAN §2a (the §2 table above) so implementers don't re-pick per module — especially: `rectpack` (2-D nest, MIT), in-house 1-D nest, `ezdxf`+ODA (CAD), Tesseract→docTR (OCR), `ifcopenshell`/`trimesh` (BIM), pgvector + external embeddings, `pyHanko` (PAdES), Dexie (offline), Recharts/ECharts (BI), `segno`+`html5-qrcode` (QR), GeoAlchemy2/shapely (geo).
- **Offline outbox spec** (Dexie, `client_op_id` idempotency, server-wins replay) written once in Phase 1 and reused by `field_diary`/`inspections`/`workpacks`. (R5)
- **Licence register** in `docs/decisions/` listing every copyleft/Elastic-licensed dep and the explicit accept/avoid call. (R3, R6)
- **Sizing correction** in ADR-001: 4 vCPU / 8 GB is the floor from Phase 5; 2 vCPU / 4 GB is a Phase 0–2 statement. (§6)
- **CI gates** to add: `gen:api` drift check, lazy-import lint, evaluator conformance fixtures, Alembic additive-only check.

---

*Sources consulted: [SAQ vs ARQ](https://saq-py.readthedocs.io/en/stable/comparison.html) · [Python job-queue survey](https://lab.abilian.com/Tech/Python/Useful%20Libraries/Job%20queues) · [pyHanko](https://github.com/MatthiasValvekens/pyHanko) · [PyP6Xer](https://pypi.org/project/pyp6xer) · [CEL-expr-python announcement](https://opensource.googleblog.com/2026/03/announcing-cel-expr-python-the-common-expression-language-in-python-now-open-source.html) · [celpy](https://github.com/cloud-custodian/cel-python) · [nest2D/libnest2d licence](https://pypi.org/project/nest2D) · [deepnest/SVGnest](https://awesome.ecosyste.ms/projects/github.com%2Fdeepnest-next%2Fdeepnest).*
