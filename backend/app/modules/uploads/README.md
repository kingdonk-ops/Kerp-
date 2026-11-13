# uploads — presigned object-storage uploads (T-0016)

Files go straight between the client and S3/MinIO with presigned URLs (master spec §1
storage isolation, TD-004: MinIO in dev/test, AWS S3 in production). The API never handles
the bytes; it signs, checks and records. Helpers adapted from Inspection-ERP
`core/files/storage.py`; upload/download rewritten as presigned (ADR 0007).

| File | What |
|---|---|
| `storage.py` | `clean_filename`, `content_disposition`, served-type (H-10) and magic-byte allowlists, `Storage` Protocol, `LocalStorage` (traversal guard; tests), `S3Storage` (boto3, lazy), `get_storage`/`set_storage` |
| `scan.py` | `Scanner` seam; `StubScanner` says every object is clean (real AV scanner: TD-211) |
| `models.py` | `oe_uploads_file` (`BaseRecordMixin`, project RLS, migration `0009`) |
| `service.py` | `presign_upload`, `complete_upload`, `get_file`, `download_request`, `verify_upload`, `sweep_orphans`, `schedule_sweep` |
| `jobs.py` | `uploads.verify` (interactive), `uploads.verify_large` and `uploads.sweep_orphans` (batch) |
| `router.py`, `schemas.py` | `/api/v1/uploads/projects/{project_id}/files...` |

## Flow

1. `POST .../files` `{module, filename, content_type, size_bytes, sha256}` (capability `C`):
   a `pending` row and a presigned `PUT` to `staging/{tenant}/{project}/{file_id}`. Content
   type and length are **signed**, so storage refuses any other size; `upload_max_bytes`
   is checked first (413). Only types in the magic-byte allowlist can be declared.
2. The client `PUT`s the bytes with the returned headers.
3. `POST .../files/{id}/complete` (`C`, and only the uploader, else 403): `HEAD` the
   object (409 if absent, the row stays `pending`), compare the size, sniff the first 512
   bytes against the declared type. This is early feedback only. A mismatch makes the file
   `rejected` (`size_mismatch` / `type_mismatch`) and deletes the object; otherwise the
   file is `verifying` and `uploads.verify` is queued after commit (`uploads.verify_large`
   on the batch queue above 50 MiB).
4. The presigned `PUT` is still valid after `complete`, so the job never trusts staging:
   it first copies staging to `quarantine/{tenant}/{project}/{file_id}` (no URL is ever
   issued for that key) and deletes staging. Size, magic bytes, SHA-256 (streamed, no
   transaction held open) and the scan all run on the quarantine copy, and that same copy
   is copied to the content key `{tenant}/{project}/{module}/{sha256}`. An existing content
   key is never overwritten (it already holds those bytes). The file becomes `available`,
   or `rejected` (`size_mismatch`, `type_mismatch`, `sha256_mismatch`, `missing_object`,
   `infected`). Staging and quarantine are deleted in every outcome, errors included. A
   re-`PUT` racing the job therefore either lands before the quarantine copy (and is
   checked in full) or after it (and is an orphan for the sweep); it never reaches a
   content key.
5. `GET .../files/{id}` (`V`) polls; `GET .../files/{id}/download` (`V`) returns a
   presigned `GET` that forces the served type and `Content-Disposition` (H-10: HTML,
   SVG and anything unknown are `application/octet-stream` attachments).

`rejection_reason` holds codes, not text, so the client translates them.

## Orphan sweep

`sweep_orphans(db, tenancy, storage=...)` runs per project (`uploads.sweep_orphans`): a
`pending` file whose URL expired more than an hour ago becomes `expired`, and a staging or
quarantine object no `pending`/`verifying` file owns is deleted once older than an hour.
`schedule_sweep(session)` queues it; a periodic schedule needs the scope list TD-155 asks
for (TD-212). A bucket lifecycle rule on `staging/` is the backstop (TD-208, with bucket
provisioning and CORS). No delete endpoint or content-object GC yet (TD-209).

## Configuration (`KERP_` env)

`STORAGE_BUCKET` (`kerp-vault`), `S3_ENDPOINT_URL` (MinIO; unset for AWS),
`S3_PUBLIC_ENDPOINT_URL` (what browsers reach, if different), `S3_REGION` (per deployment,
never hard-coded: default `ap-southeast-2` for Australian deployments, TD-171 / diff §4.4;
MinIO ignores it),
`S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` (unset: the AWS credential chain),
`UPLOAD_MAX_BYTES` (100 MiB), `UPLOAD_URL_SECONDS` (900), `DOWNLOAD_URL_SECONDS` (300).

## Tests

`tests/test_uploads_storage.py` (helpers, `LocalStorage`, `S3Storage` via botocore's
`Stubber`; a real MinIO round trip when `KERP_TEST_S3_ENDPOINT` is set) and
`tests/test_uploads.py` (service, jobs, API on `LocalStorage`; SQLite and PostgreSQL).
