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

### Material traceability in the MDR (user, 2026-11-21; E-05-05, from T-0184)
For every subsystem or asset where a material batch with an approved shelf-life extension was
used (`materials-and-traceability.md` §Shelf-life extension), the dossier attaches under the
**Material Test Reports** bookmark the original MTR/batch certificate **and** every approved
extension certificate, in chronological order (`extension_seq`). The material traceability index
lists per batch: original use-by date, each extension (sequence, previous and new date, issuer
kind, approver, approval date, document SHA-256) and the effective use-by date. Rejected and
pending extensions are not included. Batch and extension data come from the materials service;
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

### Access: membership required (TD-396)
The archive holds every record, file and audit chain of a project, so it follows diff §4.1:
System admins read no project-scoped record without a membership. Every endpoint needs
`system_admin` (else 403); the ones that expose or copy the content of a **registry** archive
also need a membership of the requesting admin on the archived project (any persona; PER-09 is
the usual one), else **404** (cross-scope is 404, CLAUDE.md §6):

| Action | Membership needed |
|---|---|
| Export (T-0233) | live membership on the (live) project |
| Download (T-0237) | live project: live membership; deleted project: membership held when it was deleted |
| Restore from a registry `archive_id`, mode `deleted` **or** `new` (T-0239, T-0240) | same as download |
| Restore from an uploaded `.zip` (`file_id`), either mode | none: `system_admin` only, because the caller already holds the bytes (the upload is scanned like any other, T-0016) |
| Registry list and detail, storage-tier change, deep-archive restore request | none: tenant-level metadata (like the project list a System admin already sees, diff §4.1) and object moves that expose no content |

**Live membership.** A System admin without one grants themselves PER-09 through the existing
membership API (`PUT /api/v1/admin/projects/{p}/members/{user}`, T-0014), written to the audit
chain `project-members:<project>`; the archive endpoints never grant a membership implicitly.

**Membership held at deletion.** A deleted project has no live memberships (deleting revokes
them all, and the membership API answers 404 on a deleted project, T-0014), so nobody can
self-grant one. The discriminator is the **`revoked_memberships` payload of the project's
`delete` entry in the `project:<id>` audit chain**, written by `projects.service.delete_project`:
a sorted list of `"<user id>:<persona>"` for every grant that was live at the deletion
(`memberships.revoke_project` soft-deletes only live rows; team-derived grants cannot exist at
that point, they are unlinked first). Memberships removed earlier are soft-deleted too, but they
are not in that list, so they neither pass the gate nor come back on restore. The admin module
reads it through a new `admin.service.memberships_at_deletion(session, project_id) → dict[user
id, set[Grant]]`, built on `app/core/auth/memberships.py` (soft-deleted rows of the project,
filtered to the pairs of the latest `delete` entry, which the projects module exposes as
`projects.service.deletion_revoked_memberships(session, project_id)`; the archive module never
reads the ledger or another module's tables itself). A System admin who was not a member at deletion has no way in through the
registry; the only path left is an uploaded zip they already hold.

**Restore as new**: the restoring admin becomes PER-09 of the new project in the restoring
transaction, audited in `project-members:<new project>`. Planner default; the user may relax it
(TD-396).

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
  run under the exporting job's tenant and project RLS context, bound to the requesting admin,
  whose membership is checked when the export is requested and again when the job starts (no
  `is_system_admin` widening of project-scoped reads).
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
  API; export and download also need a membership, see Access above): project id, code and
  name, exported at/by, size, SHA-256, bucket and key, storage class
  `STANDARD | GLACIER_IR | DEEP_ARCHIVE`, restore status `available | restoring |
  restored_temp`, restore expiry, status `queued | building | ready | failed`, error code.
  Bucket and region come from deployment configuration (default `ap-southeast-2`, diff §4.4),
  never code.
- Storage tier change by S3 `CopyObject` onto itself with the new storage class; the registry is
  updated only after the copy succeeds. Moving to `DEEP_ARCHIVE` warns about the minimum storage
  duration and the restore time (hours).
- Download (membership required per §Access, live or at deletion; else 404): a short-lived presigned GET for
  `STANDARD`, `GLACIER_IR`, or a `restored_temp` copy; `DEEP_ARCHIVE` answers 409 until restored. **Restore from deep archive**
  (`RestoreObject`, tier `Bulk` or `Standard`, chosen days) is polled by an SAQ cron job
  (`HeadObject` restore header); when ready the requesting admin gets an in-app notification
  and an e-mail (§4.6); the registry shows the expiry of the temporary copy.
- Export completion and failure notify the requesting admin (in-app + e-mail). Archives of a
  project under **legal hold** cannot be deleted (diff §4.7); the app has no delete of archives
  in this epic.

### Restore (T-0239, T-0240)
- `POST /api/v1/admin/projects/restore` (`system_admin`) takes a registry archive id or a
  `.zip` uploaded through presigned uploads (T-0016, scanned like any upload). From an archive
  id, both modes need the membership of §Access (live project: live; deleted project: held at
  deletion), else 404 with nothing written; from an uploaded zip, `system_admin` only. Runs as a
  `batch` job.
  Before anything is written it checks every SHA-256 of the manifest, the schema versions (an
  archive from a newer schema is refused; older ones go through per-module upgrade steps) and
  verifies every audit chain; any failure writes nothing.
- **Restore deleted project:** a soft-deleted project's rows still exist. The restore verifies
  that the existing soft-deleted rows match the archive manifest (per-table counts and checksums,
  file SHA-256s) and then **un-deletes them in place** (ids unchanged); it never upserts from the
  archive. The memberships revoked by the deletion (exactly the `revoked_memberships` pairs of
  §Access, never earlier removals) come back with it, so the restoring admin's own membership,
  if any, is live again (audited in `project-members:<project>`). Any difference → 409
  `ARCHIVE_DIFF` with the differing tables, nothing written (use restore as new instead).
  Refused when the project is live (409).
- **Restore as new project:** new project id and code; every entity id remapped
  deterministically (UUIDv5 of the new project id and the old id), foreign keys inside the
  archive rewritten, ltree paths rebuilt from the remapped nodes, files copied to the new
  project's object keys and re-verified by SHA-256. The restoring admin becomes PER-09 of the
  new project (audited), otherwise nobody could open it. The archived audit chains are kept as
  read-only, verified evidence; new chains start with a genesis entry referencing the archive id
  and SHA-256 — no ledger row is ever rewritten (ADR; TD-401). Counts per table and file hashes
  are compared after the restore and reported.
