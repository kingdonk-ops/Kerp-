# uploads — presigned object-storage uploads (T-0016)

Files go straight between the client and S3/MinIO with presigned URLs (master spec §1
storage isolation, TD-004: MinIO in dev/test, AWS S3 in production). The API never handles
the bytes; it signs, checks and records. Helpers adapted from Inspection-ERP
`core/files/storage.py`; upload/download rewritten as presigned (ADR 0007).

| File | What |
|---|---|
| `storage.py` | `clean_filename`, `content_disposition`, served-type (H-10) and magic-byte allowlists, `Storage` Protocol, `LocalStorage` (traversal guard; tests), `S3Storage` (boto3, lazy), `get_storage`/`set_storage` |
| `scan.py` | `Scanner` seam; `StubScanner` says every object is clean (real AV scanner: TD-206) |
| `models.py` | `oe_uploads_file` (`BaseRecordMixin`, project RLS, migration `0009`) |
| `service.py` | `presign_upload`, `complete_upload`, `get_file`, `download_request`, `verify_upload`, `sweep_orphans`, `schedule_sweep` |
| `jobs.py` | `uploads.verify` (interactive), `uploads.sweep_orphans` (batch) |
| `router.py`, `schemas.py` | `/api/v1/uploads/projects/{project_id}/files...` |

## Flow

1. `POST .../files` `{module, filename, content_type, size_bytes, sha256}` (capability `C`):
   a `pending` row and a presigned `PUT` to `staging/{tenant}/{project}/{file_id}`. Content
   type and length are **signed**, so storage refuses any other size; `upload_max_bytes`
   is checked first (413). Only types in the magic-byte allowlist can be declared.
2. The client `PUT`s the bytes with the returned headers.
3. `POST .../files/{id}/complete` (`C`): `HEAD` the object (409 if absent, the row stays
   `pending`), compare the size, sniff the first 512 bytes against the declared type. A
   mismatch makes the file `rejected` (`size_mismatch` / `type_mismatch`) and deletes the
   object; otherwise the file is `verifying` and `uploads.verify` is queued after commit.
4. `uploads.verify` streams the object through SHA-256 (no transaction held open), scans
   it, copies it to the content key `{tenant}/{project}/{module}/{sha256}` and marks it
   `available`; or `rejected` (`sha256_mismatch`, `missing_object`, `infected`). The
   staging object is always deleted. Only verified bytes ever sit under a hash key.
5. `GET .../files/{id}` (`V`) polls; `GET .../files/{id}/download` (`V`) returns a
   presigned `GET` that forces the served type and `Content-Disposition` (H-10: HTML,
   SVG and anything unknown are `application/octet-stream` attachments).

`rejection_reason` holds codes, not text, so the client translates them.

## Orphan sweep

`sweep_orphans(db, tenancy, storage=...)` runs per project (`uploads.sweep_orphans`): a
`pending` file whose URL expired more than an hour ago becomes `expired`, and a staging
object no `pending`/`verifying` file owns is deleted once older than an hour.
`schedule_sweep(session)` queues it; a periodic schedule needs the scope list TD-155 asks
for (TD-207). A bucket lifecycle rule on `staging/` is the backstop (TD-208, with bucket
provisioning and CORS). No delete endpoint or content-object GC yet (TD-209).

## Configuration (`KERP_` env)

`STORAGE_BUCKET` (`kerp-vault`), `S3_ENDPOINT_URL` (MinIO; unset for AWS),
`S3_PUBLIC_ENDPOINT_URL` (what browsers reach, if different), `S3_REGION`,
`S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` (unset: the AWS credential chain),
`UPLOAD_MAX_BYTES` (100 MiB), `UPLOAD_URL_SECONDS` (900), `DOWNLOAD_URL_SECONDS` (300).

## Tests

`tests/test_uploads_storage.py` (helpers, `LocalStorage`, `S3Storage` via botocore's
`Stubber`; a real MinIO round trip when `KERP_TEST_S3_ENDPOINT` is set) and
`tests/test_uploads.py` (service, jobs, API on `LocalStorage`; SQLite and PostgreSQL).
