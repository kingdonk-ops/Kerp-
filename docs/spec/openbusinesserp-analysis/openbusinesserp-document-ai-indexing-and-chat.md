# OpenBusinessERP — Document Ingest, AI Indexing & the ERP Chat Trace

> How a document gets from upload to being answerable by the AI assistant. Two decoupled index pipelines (semantic vector + full-text), and the `erp_chat` tool-calling agent that queries them. Grounded in `backend/app/` of `kingdonk-ops/openbusinesserp` @ `a96d525`.

---

## Part A — Ingest & AI indexing (two separate pipelines)

### A1. Semantic / vector index — the "AI index" (`oe_documents` collection)
Powers semantic search, the "Similar items" panel, and the chat's `search_documents` tool.

- **Trigger — the in-process event bus.** `modules/documents/events.py` subscribes to `documents.document.created / updated / deleted`. Create/update → `core/vector_index.index_one`; delete → `delete_one`. **Best-effort**: its own short-lived session, errors logged and swallowed, never breaks CRUD (`events.py:41-95`).
- **What is embedded — metadata, not the file body.** `modules/documents/vector_adapter.py` builds the canonical text from **name · description · category · tags · drawing_number · discipline · file_name** (joined with `|`) and a light payload for hit rendering (`vector_adapter.py:44-88`). So *"structural rebar schedule level 02"* matches by meaning regardless of filename. **The extracted page/OCR text is NOT in this vector** — that lives in the full-text index (A2).
- **Embedding layer** (`core/vector_index.py`) — a **multi-collection** abstraction: every module ships a `vector_adapter.py` implementing the `EmbeddingAdapter` protocol (collection name, `to_text`, `to_payload`, `project_id_of`), so BOQ, documents, tasks, risks, BIM, RFIs, submittals, correspondence, validation and chat share one API. Reads: `search_collection` / `find_similar` (encode query → `vector_search_collection` → decode payload → `VectorHit`). Writes: `index_one / index_many / delete_one / reindex_collection`.
- **Vector store** (`core/vector.py`) — **LanceDB embedded by default** (in-process like SQLite, data at `~/.openestimator/data/vectors/`); **Qdrant** via `VECTOR_BACKEND=qdrant` for servers. Every row: `id, vector, text, tenant_id, project_id, module, payload(JSON)` — so hits are **tenant- and project-scoped by filter**. Embeddings via sentence-transformers (legacy default `all-MiniLM-L6-v2`/384-dim; active model multilingual from `Settings.embedding_model_name`; Qdrant snapshots 3072-dim).
- **Performance** (`core/embedding_pool.py`) — optional warm pool: pre-load/compile at startup (`OE_VECTOR_PRELOAD=1`), thread-pool `encode_texts_async` (`OE_VECTOR_POOL_WORKERS`), process-local per uvicorn worker. Model install in `core/embedding_installer.py`.
- **Fail-open everywhere** — if LanceDB/Qdrant/model is unavailable, every helper warns and returns empty/no-op; callers never wrap in try/except.

### A2. Full-text content index — `file_search` (text extraction + OCR)
The *other* pipeline — this is where the file's actual contents are processed.

- **Extraction** (`modules/file_search/extractors.py`, `extract_text(payload, mime)`): **PyMuPDF (`fitz`)** pulls embedded PDF text (cheap path); if a PDF has `< 32` chars of embedded text (a scan) it **falls back to Tesseract OCR**, rasterising each page; images (jpg/png/tiff) → OCR; text/JSON/XML → UTF-8 decode. Hardened: content capped (~1–10 MB), OCR capped at 150 DPI / 40 MP per page / 200 pages (memory safety); any missing library → engine `"none"`, never raises.
- **Store & search** (`modules/file_search/service.py`, `models.py`): extracted text → `oe_file_search_index.content_text` (+ `page_count`, `ocr_engine`, `language`, `indexed_at`); re-index overwrites in place. Search = **Postgres `to_tsvector('simple', content_text) @@ plainto_tsquery` + `ts_rank`** (generated `tsv_vector` column + GIN index), SQLite `LIKE` fallback for dev.

### A3. The key distinction
| | Vector / AI index (`oe_documents`) | Full-text index (`file_search`) |
|---|---|---|
| Content | Document **metadata** (name/desc/tags/drawing/discipline) | **Extracted body text + OCR** |
| Engine | sentence-transformers → LanceDB/Qdrant (ANN) | PyMuPDF + Tesseract → Postgres tsvector |
| Trigger | `documents.document.*` events | file-bytes indexer |
| Used by | semantic search, "Similar items", **chat tools** | `/files` keyword search |

**Implication:** semantic search over a PDF's *body* isn't wired today — bodies are keyword-searchable via `file_search`; semantics match on title/description/tags/drawing metadata. To add body-level semantics, feed `file_search`'s `content_text` (chunked) into a vector collection through the same `EmbeddingAdapter` path.

---

## Part B — The ERP Chat (`erp_chat`) trace

`erp_chat` is a **tool-calling agent with SSE streaming** (`modules/erp_chat/service.py`, 1718 lines). It reads live ERP data through tools and **changes nothing directly** — every mutation is a human-confirmed `propose_*` proposal.

### B1. Request → agent loop
1. **Entry:** `ChatSessionStreamer.stream_response(user_id, request)` (`service.py:514`) — an async generator yielding SSE frames: `session_id`, `tool_start`, `tool_result`, `text`, `done`, with periodic keep-alive comment frames (the reply is not token-streamed, so comments keep the connection alive while a provider call is in flight).
2. **Prompt assembly** (`prompts.py`): `SYSTEM_PROMPT` (tool-capable) or `SYSTEM_PROMPT_NO_TOOLS` + a per-request **context block** (`build_context_block`) naming the active project, currency, interface language, and regional methodology (VOB/HOAI for DACH, NRM/RICS for UK, …). The no-tools prompt forbids fabricating project data.
3. **Provider call** (`call_ai`): **Anthropic / OpenAI native tool-calling**; other OpenAI-shaped providers get the tool schema too (widened per-provider on evidence — issue #424); self-hosted `ollama`/`vllm` tool mode is configurable (`ai_client.tool_calling_mode`); everything else falls back to plain text. Per-provider timeout via `chat_ai_timeout`.
4. **Tool loop:** `_extract_tool_calls(provider, result)` → run each handler → append `tool_result` → call the model again, up to `MAX_TOOL_ROUNDS` (infinite-loop guard). Tool results are truncated to `MAX_TOOL_RESULT_CHARS` (8000).

### B2. The tools (`tools.py`, 1405 lines)
- **Structured-data tools** (direct SQL/service reads): `get_all_projects`, `get_project_summary`, `get_boq_items`, `get_schedule`, `get_validation_results`, `get_risk_register`, `get_cost_model`, `compare_projects`, `run_validation`, `search_cwicr_database`.
- **Semantic-memory tools (vector-backed)** — the bridge to Part A: `search_documents`, `search_boq_positions`, `search_tasks`, `search_risks`, `search_bim_elements`, `search_rfis`, `search_submittals`, `search_correspondence` (each: free-text `query` + optional `project_id`; omit → cross-project "lessons learned").
- **Permission-gated:** `check_tool_permission` + `_require_project_access` / `_user_is_manager_or_above` enforce the caller's RBAC per tool; a tool the role can't run is refused, not executed.

### B3. How `search_documents` reaches the `oe_documents` vectors (the full loop)
```
model emits tool_call search_documents{query, project_id?}
  → handle_search_documents (tools.py:1234)
    → _generic_collection_search(COLLECTION_DOCUMENTS, …) (tools.py:1142)
      → modules.search.service.unified_search_service(...)         # two-track
          ├─ VECTOR track: core.vector_index.search_collection
          │     → core.vector.encode_texts_async(query)            # embedding_pool
          │     → core.vector.vector_search_collection             # ANN over LanceDB/Qdrant
          │        filtered by tenant_id + project_id              # the vectors A1 wrote at ingest
          └─ SQL track: ILIKE substring over the same collection's rows
        → merge + rank → hits
    → tool_result frame streamed to the client, fed back to the model
  → model composes the answer from the hits (never fabricates) → text frames → done
```
So the ingest side (A1: `documents.document.*` → `vector_adapter` → `index_one` → LanceDB/Qdrant) and the read side (B3: `search_documents` → `unified_search_service` → `search_collection` → same store) are the two ends of one semantic memory, joined by the `oe_documents` collection and scoped by tenant/project on both write and read.

### B4. Mutations are proposals, not writes
A change is never a direct tool: the model calls a `propose_*` tool from `actions/registry`, which **stores a proposal** (`ChatActionService` / `propose_tool_result`); `_commit_turn` commits it (in a savepoint) **before** its `tool_result` frame so the UI renders a confirmation **card** the human approves — the platform's "AI-augmented, human-confirmed" rule. `MAX_TOOL_ROUNDS` bounds the loop.

### B5. Chat self-memory
The chat also indexes **its own messages** into a `COLLECTION_CHAT` vector collection (`erp_chat/events.py` + `vector_adapter.py`), and `router.py` exposes a `find_similar` over `COLLECTION_CHAT` (surface similar past conversations) — the same `EmbeddingAdapter` pattern as documents.

---

## One-line summary
Upload → `documents.document.created` event → **metadata embedded** into the `oe_documents` vector store (LanceDB/Qdrant, tenant/project-scoped), while `file_search` separately extracts **full text/OCR** into a Postgres tsvector index. The **erp_chat agent** answers by calling the `search_documents` tool, which runs a two-track (vector + SQL) `unified_search_service` over that same `oe_documents` collection, feeds the scoped hits back to the LLM, streams the reply over SSE, and routes any change through a human-confirmed `propose_*` card.

*Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525`. Files: `core/vector.py`, `core/vector_index.py`, `core/embedding_pool.py`, `modules/documents/{vector_adapter,events}.py`, `modules/file_search/{extractors,service,models}.py`, `modules/erp_chat/{service,tools,prompts,events,vector_adapter}.py`, `modules/search/service.py`.*
