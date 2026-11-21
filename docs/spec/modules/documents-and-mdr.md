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
1. subsystem scope query → **dependency-graph verification** (100 % ITPs passed, 0 open issues
   with `mdr_effect = blocks`, 100 % heat MTRs attached);
2. stream S3 inspection PDFs / MTRs / NDE plates;
3. transform: dynamic pagination, client cover sheets/title stamps, hierarchical bookmark
   tree, hyperlinked TOC;
4. atomic output: store compiled PDF, compute **SHA-256**, generate outgoing transmittal.

**Gating invariant:** a compile cannot run while `Σ open issues with mdr_effect = blocks > 0`
or `Σ unsigned ITP hold points > 0` (amended 2026-11-21; [issues.md](issues.md) §5–§6).
**MDR gate rule (planner default, TD-445):** `mdr_effect` alone governs the MDR gate: `mdr.compile` blocks while any issue in the dossier scope with `mdr_effect = blocks` is open, and nothing else about an issue (type, `punch_category`, visibility) enters it; this replaces the earlier "open Cat A punch" clause. Punch templates that use Cat A (all system-default punch templates) default to `mdr_effect = blocks`. An open Cat A punch always blocks work completion (`work_anchor.complete`) regardless of `mdr_effect`.
**Project-level issues in the dossier scope (user, 2026-11-21; issues.md §3.1, §6):** an audit
or H&S issue may be project-level (no anchor). With `mdr_effect = blocks`, an open project-level
issue without a location is in the scope of **every** MDR of the project; one with a location is
in the scope only of the MDRs whose dossier scope covers that location (the location is the scope
node, inside it or an ancestor of it; planner default). The same rule decides whether it is listed
in the NCR / concession register below. Informal inspections (quality-and-inspection.md
§Template settings) are never collected into a dossier.
The check is the `mdr.compile` rule set (CEL, ADR 0004
locked defaults) over the facts the `issues` service returns for the dossier scope; it is
separate from the work-completion gate (`work_anchor.complete`). Final dossiers and commercial Delay Notices are signed with
**pyHanko** (ETSI **PAdES-B-LTA** + **RFC 3161** timestamp).

**Signature evidence (enterprise roadmap, 2026-11-21).** The dossier prints, for every formal
sign-off it contains, the attestation block of `quality-and-inspection.md` §Signing attestation
(signer, persona, "on behalf of" for a delegate, intent statement and version, re-auth method,
time, device id, IP) through the shared `signing` renderer (T-0291); sign-offs without an
attestation (Pilot-0) say so.

### NCR / concession register in the MDR (user, 2026-11-21; E-05-05, from T-0314)
Every dossier carries an **NCR / concession register** section (its own Level 4 bookmark under
the discipline dossier, planner default) listing the scope's issues with `mdr_effect = blocks`
(all closed by then) or `register_only`: number, type, template name, category, origin,
severity / punch category, anchor in project terminology (or "Project" plus the location for a
project-level issue), disposition and `concession_ref`
(NCR), root-cause category, raised and closed dates, verifier and the `ncr_close_verify`
attestation block. Issues with `mdr_effect = excluded` and `void` issues are never listed.
Because the dossier goes to the client, **`internal` issues are never printed** (clients never
see internal issues, issues.md §5); an internal issue with `mdr_effect = blocks` still blocks the
compile while open (planner default). Rows and photos come from the issues service (`issues.service.mdr_register`, which marks every
row client-seen itself, issues.md §5); the MDR
engine reads no issues table.

### Material traceability in the MDR (user, 2026-11-21; E-05-05, from T-0184)
For every subsystem or asset where a material batch with an approved shelf-life extension was
used (`materials-and-traceability.md` §Shelf-life extension), the dossier attaches under the
**Material Test Reports** bookmark the original MTR/batch certificate **and** every approved
extension certificate, in chronological order (`extension_seq`). The material traceability index
lists per batch: original use-by date, each extension (sequence, previous and new date, issuer
kind, approver, approval date, document SHA-256) and the effective use-by date. Rejected and
pending extensions are not included. Every inspection or asset that references a
`substitution_id` (materials-and-traceability.md §Product substitutions; T-0185) brings the
approved substitution record, or for the `client_tq` route the linked TQ approval, and its
equivalency document into the dossier, listed on the material traceability index. Batch and extension data come from the materials service;
the MDR engine reads no materials table. Verified certificates only (`material_cert.verify`,
T-0265).

## Related registers (epics)
`transmittals` (purpose-coded, recipients + ack), `correspondence`, `tq` (Technical Query — Request for Information)/`submittals`,
`meetings` (feeds portal v3 visit scheduling), `file_search` (tsvector OCR of dockets for the
claims/search layer).

## Project Export & Retention Archive (user, 2026-11-21; planner defaults)

A System admin exports a whole project into one self-contained archive that opens offline, can
be moved between storage tiers, and can be restored. Epic **E-01-02** (tasks T-0233–T-0242);
admin screens in diff §6.5; legal hold in diff §4.7. Open points: TD-401 (restore as new and
the audit ledger; no hard purge), TD-403 (providers of later modules), TD-405 (file layout).

### Access: system_admin, any project of the tenant (ADR 0016; TD-396 resolved)
Every archive endpoint needs `system_admin` (else **403**, PER-09 included). Since ADR 0016
(user, 2026-11-21: system_admin global visibility) a System admin exports, downloads and restores
(from a registry `archive_id` in either mode, or from an uploaded `.zip`) the archive of **any
project of the tenant**, live or deleted, without a membership; there is no membership gate.
Simplified by ADR 0016 (earlier rule: a membership, live or held at deletion, was required).
The remaining rules:

- An uploaded `.zip` (`file_id`) must be the caller's own upload (`uploaded_by` = caller; since
  ADR 0016 the upload RLS alone no longer limits a System admin), else 404; it is scanned like
  any other upload (T-0016).
- **Memberships revived by a restore of a deleted project** are exactly those revoked by the
  deletion: the **`revoked_memberships` payload of the project's `delete` entry in the
  `project:<id>` audit chain**, written by `projects.service.delete_project` (a sorted list of
  `"<user id>:<persona>"` for every grant live at the deletion; team-derived grants cannot exist
  at that point, they are unlinked first). Memberships removed earlier are soft-deleted too but
  are not in that list, so they never come back. The admin module reads it through
  `admin.service.memberships_at_deletion(session, project_id) → dict[user id, set[Grant]]`,
  built on `app/core/auth/memberships.py` (soft-deleted rows of the project with
  `source_type = 'direct'`, filtered to the pairs of the latest `delete` entry and to the rows
  that deletion soft-deleted: `deleted_at` and `updated_by` matching the deletion's time and
  actor, which the projects service returns with the pairs; if the stamps cannot be matched
  exactly, the `delete` payload additively records source and site id per grant instead). The
  pair alone is not precise, because the membership key also has `source_type`, `source_id` and
  `site_node_id`: an earlier team-link row or a PER-07 grant on another site removed earlier must
  never come back (PR #137 review round 3). The `delete` entry is exposed by the projects module
  as `projects.service.deletion_revoked_memberships(session, project_id)`; the archive module
  never reads the ledger or another module's tables itself.

**Restore as new**: the restoring admin becomes PER-09 of the new project in the restoring
transaction, audited in `project-members:<new project>`, so the project has a project
administrator from the start (every System admin can open it anyway, ADR 0016).

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
    for anchored evidence (photos as the full-resolution original plus, where an annotation
    exists, a copy rendered from the original with the annotation burned in; derivatives
    (`quality-and-inspection.md` §Image derivatives) are not exported and are regenerated on restore;
    inspection PDFs of T-0158), and `files/{quals,equipment,materials,mdr,reports}/` for
    register files (TD-405: the breakdown is configurable, ADR 0003, so the tree folders follow
    the project's levels instead of a fixed asset/work-anchor pair).
- **Each module exports itself** through an export provider in its `service.py` (rows, restore
  lines, files); the archive module never reads another module's tables (CLAUDE.md §6). Reads
  run under the exporting job's tenant and project RLS context, bound to the requesting admin
  with `is_system_admin`, which the job binds only after re-checking at start that the admin
  still holds `system_admin` (ADR 0016).
- The offline viewer is a **packaged static bundle** built with the frontend and shipped inside
  the backend image; the build job copies it into the zip and the manifest records the KERP
  version it belongs to (T-0236).
- Synthetic fixtures only in tests; no real client data.

### Offline viewer (T-0236)
- Opens from `file://` or any static HTTP server with **no network access**: no CDN, no remote
  fonts, no analytics; a Content-Security-Policy meta tag allows only local resources.
- Renders the hierarchy tree, Item register, inspections with answers, comments and review
  history, NCRs/punch, and audit hashes with their verification result; opens photos and PDFs
  through relative `./files/...` links. Strings through the same i18n catalogue (bundled).

### Registry, tiers and download (T-0233, T-0237, T-0238)
- `oe_archive_project_archive` (`BaseRecordMixin`, tenant RLS; only `system_admin` through the
  API, any project of the tenant, see Access above): project id, code and
  name, exported at/by, size, SHA-256, bucket and key, storage class
  `STANDARD | GLACIER_IR | DEEP_ARCHIVE`, restore status `available | restoring |
  restored_temp`, restore expiry, status `queued | building | ready | failed`, error code.
  Bucket and region come from deployment configuration (default `ap-southeast-2`, diff §4.4),
  never code.
- Storage tier change by S3 `CopyObject` onto itself with the new storage class; the registry is
  updated only after the copy succeeds. Moving to `DEEP_ARCHIVE` warns about the minimum storage
  duration and the restore time (hours).
- Download (`system_admin`, any project of the tenant, live or deleted): a short-lived presigned GET for
  `STANDARD`, `GLACIER_IR`, or a `restored_temp` copy; `DEEP_ARCHIVE` answers 409 until restored. **Restore from deep archive**
  (`RestoreObject`, tier `Bulk` or `Standard`, chosen days) is polled by an SAQ cron job
  (`HeadObject` restore header); when ready the requesting admin gets an in-app notification
  and an e-mail (§4.6); the registry shows the expiry of the temporary copy.
- Export completion and failure notify the requesting admin (in-app + e-mail). Archives of a
  project under **legal hold** cannot be deleted (diff §4.7); the app has no delete of archives
  in this epic.

### Restore (T-0239, T-0240)
- `POST /api/v1/admin/projects/restore` (`system_admin`) takes a registry archive id or a
  `.zip` uploaded through presigned uploads (T-0016, scanned like any upload; the caller's own
  upload, §Access). Either source, either mode, any project of the tenant (ADR 0016). Runs as a
  `batch` job.
  Before anything is written it checks every SHA-256 of the manifest, the schema versions (an
  archive from a newer schema is refused; older ones go through per-module upgrade steps) and
  verifies every audit chain; any failure writes nothing.
- **Restore deleted project:** a soft-deleted project's rows still exist. The restore verifies
  that the existing soft-deleted rows match the archive manifest (per-table counts and checksums,
  file SHA-256s) and then **un-deletes them in place** (ids unchanged); it never upserts from the
  archive. The memberships revoked by the deletion (exactly the `revoked_memberships` pairs of
  §Access, never earlier removals) come back with it (audited in `project-members:<project>`). Any difference → 409
  `ARCHIVE_DIFF` with the differing tables, nothing written (use restore as new instead).
  Refused when the project is live (409).
- **Restore as new project:** new project id and code; every entity id remapped
  deterministically (UUIDv5 of the new project id and the old id), foreign keys inside the
  archive rewritten, ltree paths rebuilt from the remapped nodes, files copied to the new
  project's object keys and re-verified by SHA-256. The restoring admin becomes PER-09 of the
  new project (audited), so it has a project administrator from the start. The archived audit chains are kept as
  read-only, verified evidence; new chains start with a genesis entry referencing the archive id
  and SHA-256 — no ledger row is ever rewritten (ADR; TD-401). Counts per table and file hashes
  are compared after the restore and reported.
