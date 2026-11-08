# ADR-001 — Runtime & storage architecture (review of the production recommendations)

> **Status:** Accepted (with adaptations). **Scope:** the "site spec ideas" (build pipeline, runtime sizing, DB layer, file ingestion, dynamic inspections, async worker, frontend/network). **Context:** 30–80 active field/office users, heavy files (CAD/BIM, 200-page MDRs), spatial drawings, dynamic inspection records. This ADR records what we adopt, adapt, defer or reject, and the resulting changes to `DEVELOPMENT-PLAN.md`.

The review is favourable: the recommendations are well-aimed at *this* workload (OLTP + bursty heavy jobs), and several of them are things the clean plan already implied (offline-first, physical-only data, removal of the estimating/BOQ analytics that justified the heavy DB). Most are **adopted**. A handful need a caveat so we don't trade one failure mode for another.

---

## 1. Decisions at a glance

| # | Recommendation | Decision | Why / caveat |
|---|---|---|---|
| 1 | **Prebuild images in CI (GHCR), deploy `pull && up`**; swap as fallback; `--only-binary`; multi-stage Docker cache | **Adopt** | Removes host compilation entirely — the real cause of the 504s. Swap is a seatbelt, not the plan. |
| 2A | **ARQ + Redis async worker** for CAD/PDF/BIM/OCR instead of in-process `BackgroundTasks` | **Adopt** | Isolates OOM to the worker; the API stays up for the other users. ARQ (async, shares our SQLAlchemy async engine) over Celery. |
| 2B | **2–4 Uvicorn workers**; 4 vCPU / 8 GB (tight: 2/4 + swap) | **Adopt** (3 workers) | Standard. With lazy imports (below) idle footprint is small enough that 3 workers fit 8 GB comfortably. |
| 2C | pgduckdb tuning (`duckdb.memory_limit`) **or** drop DuckDB | **Adopt the stronger form — drop DuckDB** | Our workload is ~100% OLTP; the BOQ/estimating analytics that needed DuckDB were removed in the pivot. See §2. |
| 3A | **Lazy-import heavy libs** (`fitz`, `trimesh`, `cv2`, `pyarrow`) inside handlers/tasks | **Adopt** | 30–50% idle-footprint cut; instant startup. Enforce with an import-lint rule. |
| 3B | **Stream uploads to disk** (`shutil.copyfileobj`, 1 MB chunks), not `await file.read()` | **Adopt** — superseded by presigned where possible | For any upload that still transits the API, stream it. But prefer §4 (presigned) so the API never touches the bytes. |
| 3C | Disable unused modules (`enabled=False`) | **Already done** | The field-ops scope already drops ~98 modules; keep the loader lean. |
| — | **Postgres 16 + PostGIS**, JSONB + GIN, B-tree/GiST | **Adopt** | PostGIS earns its place here (inspection pins, site maps, pipeline corridors). |
| — | **Presigned direct-to-object-store uploads** (S3/R2/MinIO) | **Adopt** | Biggest single stability win for heavy files; the API never buffers 500 MB. Caveat in §3. |
| — | **Dynamic inspections = relational columns + JSONB + versioned JSONSchema** | **Adopt** | Matches the dynamic-schema pattern `asset_management` already uses. |
| — | **Nginx HTTP/2 + static Brotli/gzip** (`vite-plugin-compression`) | **Adopt** | Cheap; helps the UI-dense asset-tree/drawing screens. |

**Net effect on sizing:** web container ~150–200 MB, Postgres ~150–250 MB (no DuckDB spikes), heavy work isolated in one ARQ container. A **2 vCPU / 4 GB** box becomes viable; **4 vCPU / 8 GB** is the comfortable target. This matches the recommendation.

---

## 2. Drop pgduckdb — the one structural change

**Decision:** swap `pgduckdb/pgduckdb:16` for `postgres:16` + the `postgis` extension. No DuckDB in the request path.

**Pros (for this platform):**
- The analytics that justified DuckDB (millions of BOQ/regional cost lines, Parquet cost catalogues) are **out of scope** — estimating/BOQ/cost DBs were removed in the field-ops pivot.
- Our hot paths are relational asset trees, inspection forms, sign-offs, audit logs, transmittals — classic OLTP that B-tree/GIN/GiST serve in a fraction of the RAM, with no per-connection engine contexts to blow up.
- PostGIS gives native geo for the many spatial features (inspection pins, site layout maps, corridors) — a real capability gain, not just a swap.
- Predictable memory: removes the "every connection allocates a DuckDB instance" failure mode on a modest VM.

**Cons / what we lose, and the mitigation:**
- **In-DB columnar analytics** for the BI layer (`dashboards` uses parquet snapshots; `bi_dashboards` has a KPI engine). *Mitigation:* field-ops BI is lighter and periodic — compute it in the **ARQ worker** on a schedule and persist to summary/materialised tables (`dashboards._snapshot`, `bi_dashboards._kpi_value`), or read parquet with `pyarrow`/`duckdb` **inside the worker process** (not inside Postgres). This keeps analytics possible without putting DuckDB in every DB connection.
- **Ad-hoc cross-table aggregation** that DuckDB made trivial → use Postgres materialised views + scheduled refresh. Adequate at this scale.

**Verdict:** net positive. The only thing to watch is the BI/reporting path; it moves to the worker, which we are adding anyway.

---

## 3. Presigned direct-to-object-store uploads — adopt, with a boundary note

**Decision:** `POST /uploads/presign` → browser PUTs directly to the bucket → `POST /uploads/complete` registers metadata. The API and Nginx never stream the bytes. This is the primary file path; the chunked-to-disk fallback (3B) is only for environments without a reachable bucket.

**This is already modelled** in `resumable_uploads` (`_session`: storage_key, chunk_size, received_chunks, status, document_id) — extend it with a presign step rather than inventing a new module.

**Caveats to design in:**
- **Browser → bucket reachability:** on-prem MinIO must be exposed to clients with correct **CORS** and TLS; presign only works if the browser can reach the bucket host. For a single-company on-prem install behind one Nginx, terminate a `/<bucket>/` location on the same Nginx (reverse-proxy to MinIO) so there's one hostname and cert.
- **Virus/content scanning & quota** can no longer sit in the upload request — move them to an **on-complete ARQ task** (scan → mark `clean`/`quarantined` → only then flip the file `available`).
- **Orphan risk:** a presigned upload that never calls `/complete` leaves a bucket blob with no DB row. Reap with a scheduled worker sweep over `resumable_uploads._session` older than the presign TTL (this is exactly the `documents` VFS orphan engine).
- **Integrity:** require the client to send the `sha256` on `/complete`; the worker verifies it against the stored object before registering the revision (feeds `file_versions.checksum`).

---

## 4. Async worker — adopt ARQ, with the job taxonomy

**Decision:** one `redis:7-alpine` (~15–25 MB) + one **ARQ worker container**. All CPU/RAM-heavy work runs there, never in Uvicorn:
- Work Pack / MDR PDF compilation (PyMuPDF + reportlab + qrcode).
- CAD/BIM parse/convert/thumbnail (trimesh/opencv), drawing-markup burn-in.
- Document OCR → full-text index (`file_search`).
- Vector/embedding indexing for search (`search`/`retrieval`).
- Scheduled BI/KPI rollups (replacing in-DB DuckDB analytics, §2).
- Upload-complete scanning + checksum verification (§3).
- P6 `.xer`/XML import parsing and export file generation (`asset_management`/`schedule`).

The platform **already has a `jobs` module** over `core/job_runner`; ARQ is the execution backend behind it. Job status/polling continues to surface through `jobs`, so modules enqueue through one service and the UI has one place to watch progress.

**Caveat:** ARQ's scheduler (`cron`) is minimal vs Celery beat. For the handful of periodic jobs (BI rollups, orphan sweep, credential/continuity-expiry notifications) ARQ cron is fine; if scheduling needs grow, the existing scheduled-task mechanism covers it without adding Celery.

---

## 5. Things to correct in the sample code (when it's built)

The attached SQLAlchemy/endpoint samples are directionally right but must conform to the platform contract before they ship:
- **Every table gets `BaseRecordMixin`** (typed entity URI, `tenant_id`, hash, provenance, revision) **+ an RLS policy**. The `WorkPack`/`ItpTemplate` samples omit it — add it. `work_pack_number` is unique **per tenant**, not globally.
- `class WorkPackStatus(str, Enum)` then stored in a `String(20)` column with an `Enum` member default is inconsistent — use `enum.Enum` + SQLAlchemy `Enum(...)` (or a `String` + `CHECK`), consistently. Same for `InspectionPointType`.
- `updated_at_utc` with only `onupdate=` is `NULL` on insert — set `default=` too (or inherit from `BaseRecordMixin`).
- No raw filesystem paths and no cross-module table reads — go through the owning module's `service.py` and the VFS/storage service (CLAUDE.md hard rules).
- AI-assisted steps (TQ drafting, auto-filing suggestions) carry `source="ai"` and need human confirmation before they apply.

These are implementation notes, not blockers — the schema shape (template → execution-instance split, JSONB `measurement_data`, VFS links) is sound and is what the module specs below adopt.

---

## 6. Resulting changes to the plan
1. **Runtime topology** (new, in `DEVELOPMENT-PLAN.md` §2a): Nginx (HTTP/2, static Brotli) → 3 Uvicorn workers; Redis + **one ARQ worker**; Postgres 16 + PostGIS; object store (MinIO/S3/R2) with presigned uploads. Images prebuilt in CI.
2. **No DuckDB** anywhere in the request path; BI/analytics move to the ARQ worker + materialised tables.
3. **Lazy imports** for heavy libs enforced by lint; **presigned uploads** as the default file path (extends `resumable_uploads`).
4. **Two new modules** registered: `workpacks`, `awp` (see `spec/MODULE-SPEC-10-workpacks-awp.md`), built on existing modules. The **Virtual File System is folded into `documents`** (new `oe_documents_folder`/`_file_link`/`_share` tables + routing/orphan/locker services), not a separate module.
5. **New engine rule sets** (plan §5): `document.route`, `document.share`, `document.immutable`, `workpack.issue`, `workpack.stage.signoff`, `cwp.release`, `iwp.generate`, `tq.apply`.
6. **New concept — Technical Query / concession (TQ)** with scope levels (project/asset/work-pack/product), reusing `moc` for the change-control lifecycle.

*Companion specs: `spec/MODULE-SPEC-10-workpacks-awp.md` (VFS tables are in its §1, on `documents`).*
