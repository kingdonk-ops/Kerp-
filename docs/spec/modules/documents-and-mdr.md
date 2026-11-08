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
`transmittals` (purpose-coded, recipients + ack), `correspondence`, `rfi`/`submittals`,
`meetings` (feeds portal v3 visit scheduling), `file_search` (tsvector OCR of dockets for the
claims/search layer).
