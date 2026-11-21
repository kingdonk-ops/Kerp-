# Module detail — Document control, CDE/VFS & automated MDR (`documents`)

Covers MOD-11. Merged from legacy `documents` (+ folded VFS) with the 3D-CAD cull applied.

## CDE + Virtual File System
- CDE states + suitability/revision codes; drawing sheets with revision chains; files always
  go through the **storage service** (presigned uploads), never raw paths.
- **VFS:** a rename-safe logical folder tree decoupled from the flat object-store key
  (rename = DB-only).
- **Routing resolver** `documents.route_and_store` files module uploads into the pre-set
  `/Site/Asset/RSW` scaffold under a standardised name.
- Polymorphic `_file_link` associations (target delete → link removed + `is_orphaned=true`,
  file never destroyed); **orphan engine** (no link / target gone / floating) with
  reassignment.
- Personal `/Users/{name}` lockers + role/external shares (external tokens via
  `portal`/`signing`); per-folder **versioning toggle** (fork a `file_versions` revision ON,
  overwrite in place OFF).

## Drawing viewer & markups (3D CAD culled)
Standard **PDF/drawing plan viewer** + vector **markup** overlays (`markups`). No WebGL
IFC/BIM renderer, no native `.dwg` parser, no 3D takeoff. A **pluggable background parser
adapter** `app/core/integrations/parsers.py` handles neutral formats (`.dxf`, `.pdf`, `.xer`).

## Automated MDR / turnover collation engine
Batch pipeline (**SAQ `batch`** queue, heavy libs lazy):
1. subsystem scope query → **dependency-graph verification** (100 % ITPs passed, 0 open Cat A
   punches, 100 % heat MTRs attached);
2. stream S3 inspection PDFs / MTRs / NDE plates;
3. transform: dynamic pagination, client cover sheets/title stamps, hierarchical bookmark
   tree, hyperlinked TOC;
4. atomic output: store compiled PDF, compute **SHA-256**, generate outgoing transmittal.

**Gating invariant:** a compile cannot run while `Σ open Cat A punches > 0` or
`Σ unsigned ITP hold points > 0`. Final dossiers and commercial Delay Notices are signed with
**pyHanko** (ETSI **PAdES-B-LTA** + **RFC 3161** timestamp).

## Related registers (epics)
`transmittals` (purpose-coded, recipients + ack), `correspondence`, `tq` (Technical Query — Request for Information)/`submittals`,
`meetings` (feeds portal v3 visit scheduling), `file_search` (tsvector OCR of dockets for the
claims/search layer).

## Project Export & Retention Archive (user, 2026-11-21; planner defaults)

A System admin exports a whole project into one self-contained archive that opens offline, can
be moved between storage tiers, and can be restored. Epic **E-01-02** (tasks T-0233–T-0242);
admin screens in diff §6.5; legal hold in diff §4.7. Open points: TD-401 (restore as new and
the audit ledger; no hard purge), TD-403 (providers of later modules), TD-405 (file layout).

### Package (T-0233, T-0234, T-0235)
- Built by a **`batch` SAQ job**, heavy libraries imported lazily; streamed to the object store
  as a multipart upload, never assembled in memory. Name
  `<project_code>_archive_<YYYY-MM-DD>_<short_id>.zip`, one top folder of the same name:
  - `index.html` + `viewer-assets/` — the offline viewer;
  - `data/project_data.json` — the human-readable graph (project, hierarchy levels and nodes,
    Items, inspections with answers, comments and review history, signatures, and what later
    modules contribute: NCRs, punch, equipment, credentials, materials, MDR), plus
    `data/project_data.js` with the same content assigned to a global, because browsers refuse
    `fetch()` of local files under `file://`;
  - `data/db_restore_manifest.jsonl.gz` — the lossless, schema-versioned row dump (one line per
    row: module, table, schema version, row) with per-table counts and checksums;
  - `data/audit/` — the project's ledger chains, exactly as stored, with their verification
    report;
  - `data/archive_manifest.json` — metadata (KERP version, schema versions, exported at/by,
    project code and name), the file inventory with SHA-256 and size of every file, and the
    folder-name mapping;
  - `files/tree/<node code per level from the root>/[<item code>/]{reports,photos,attachments}/`
    for anchored evidence (photos as original plus annotated with the annotation burned in,
    inspection PDFs of T-0158), and `files/{quals,equipment,materials,mdr,reports}/` for
    register files (TD-405: the breakdown is configurable, ADR 0003, so the tree folders follow
    the project's levels instead of a fixed asset/work-anchor pair).
- **Each module exports itself** through an export provider in its `service.py` (rows, restore
  lines, files); the archive module never reads another module's tables (CLAUDE.md §6). Reads
  run under the exporting job's tenant and project RLS context.
- Synthetic fixtures only in tests; no real client data.

### Offline viewer (T-0236)
- Opens from `file://` or any static HTTP server with **no network access**: no CDN, no remote
  fonts, no analytics; a Content-Security-Policy meta tag allows only local resources.
- Renders the hierarchy tree, Item register, inspections with answers, comments and review
  history, NCRs/punch, and audit hashes with their verification result; opens photos and PDFs
  through relative `./files/...` links. Strings through the same i18n catalogue (bundled).

### Registry, tiers and download (T-0233, T-0237, T-0238)
- `oe_archive_project_archive` (`BaseRecordMixin`, tenant RLS; only `system_admin` through the
  API): project id, code and name, exported at/by, size, SHA-256, bucket and key, storage class
  `STANDARD | GLACIER_IR | DEEP_ARCHIVE`, restore status `available | restoring |
  restored_temp`, restore expiry, status `queued | building | ready | failed`, error code.
  Bucket and region come from deployment configuration (default `ap-southeast-2`, diff §4.4),
  never code.
- Storage tier change by S3 `CopyObject` onto itself with the new storage class; the registry is
  updated only after the copy succeeds. Moving to `DEEP_ARCHIVE` warns about the minimum storage
  duration and the restore time (hours).
- Download: a short-lived presigned GET for `STANDARD`, `GLACIER_IR`, or a `restored_temp`
  copy; `DEEP_ARCHIVE` answers 409 until restored. **Restore from deep archive**
  (`RestoreObject`, tier `Bulk` or `Standard`, chosen days) is polled by an SAQ cron job
  (`HeadObject` restore header); when ready the requesting admin gets an in-app notification
  and an e-mail (§4.6); the registry shows the expiry of the temporary copy.
- Export completion and failure notify the requesting admin (in-app + e-mail). Archives of a
  project under **legal hold** cannot be deleted (diff §4.7); the app has no delete of archives
  in this epic.

### Restore (T-0239, T-0240)
- `POST /api/v1/admin/projects/restore` (`system_admin`) takes an archive id or a `.zip`
  uploaded through presigned uploads (T-0016, scanned like any upload). Runs as a `batch` job.
  Before anything is written it checks every SHA-256 of the manifest, the schema versions (an
  archive from a newer schema is refused; older ones go through per-module upgrade steps) and
  verifies every audit chain; any failure writes nothing.
- **Restore deleted project:** a soft-deleted project with the same id is brought back with its
  rows (ids unchanged); refused when the project is live.
- **Restore as new project:** new project id and code; every entity id remapped
  deterministically (UUIDv5 of the new project id and the old id), foreign keys inside the
  archive rewritten, ltree paths rebuilt from the remapped nodes, files copied to the new
  project's object keys and re-verified by SHA-256. The archived audit chains are kept as
  read-only, verified evidence; new chains start with a genesis entry referencing the archive id
  and SHA-256 — no ledger row is ever rewritten (ADR; TD-401). Counts per table and file hashes
  are compared after the restore and reported.
