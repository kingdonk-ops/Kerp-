# uploads — presigned object-storage uploads (T-0016)

Files go straight between the client and S3/MinIO with presigned URLs (master spec §1
storage isolation, TD-004: MinIO in dev/test, AWS S3 in production). The API never handles
the bytes; it signs, checks and records. Helpers adapted from Inspection-ERP
`core/files/storage.py`; upload/download rewritten as presigned (ADR 0007).

| File | What |
|---|---|
| `storage.py` | `clean_filename`, `content_disposition`, served-type (H-10) and magic-byte allowlists, `Storage` Protocol, `LocalStorage` (traversal guard; tests), `S3Storage` (boto3, lazy), `get_storage`/`set_storage`; `ObjectInfo.storage_class` (S3 listing; `STANDARD` otherwise) and `ping()` on both backends (health probe, T-0219; `S3Storage` pings through its own probe client: 2 s connect and read timeouts, one attempt, `HEALTH_PROBE_TIMEOUT_S`) |
| `scan.py` | `Scanner` seam; `StubScanner` (always clean; dev and tests); `ClamdScanner` (ClamAV over the clamd socket, `INSTREAM`, `PING`; T-0218); `scanner_from_settings` / `get_scanner` (`KERP_UPLOAD_SCANNER`); `ping_scanner` (health probe, T-0219); `require_real_scanner`, the worker startup check that refuses the stub in production (T-0213) |
| `models.py` | `oe_uploads_file` (`BaseRecordMixin`, project RLS, migration `0009`; `scan_signature`, migration `0034`) |
| `service.py` | `presign_upload`, `complete_upload`, `get_file`, `download_request`, `verify_upload`, `sweep_orphans`, `schedule_sweep`; quarantine monitor: `quarantine_files`, `quarantine_counts`, `recheck_upload`, `queue_project_sweep` (T-0221); for the System console (T-0219): the stats provider `deployment_stats` (files per status, `available_bytes`), `object_store_totals` (objects and bytes per storage class of a tenant's key prefixes, run by the admin batch job), `ping_storage` (head bucket) and `ping_scanner` (clamd `PING`) |
| `jobs.py` | `uploads.verify` (interactive), `uploads.verify_large` (batch; both retried with back-off while the scanner is unavailable) and `uploads.sweep_orphans` (batch; queued hourly per project) |
| `setting_defs.py` | the module's runtime settings (category `uploads_media`, T-0230): `max_upload_size_mb`, `presigned_url_ttl_minutes` and the four image-derivative entries T-0247 reads; see `app/modules/settings/README.md` |
| `router.py`, `schemas.py` | `/api/v1/uploads/projects/{project_id}/files...`; the monitor's shapes (`Quarantine*`), served by `admin/system_uploads_router.py` |

## Flow

1. `POST .../files` `{module, filename, content_type, size_bytes, sha256}` (capability `C`):
   a `pending` row and a presigned `PUT` to `staging/{tenant}/{project}/{file_id}`. Content
   type and length are **signed**, so storage refuses any other size; the size limit
   is checked first (413). The limit and the URL lifetime are the runtime settings
   `max_upload_size_mb` and `presigned_url_ttl_minutes` (`service.upload_limits`, T-0230),
   never above the deployment's `KERP_UPLOAD_MAX_BYTES` and `KERP_UPLOAD_URL_SECONDS`. Only types in the magic-byte allowlist can be declared.
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

`rejection_reason` holds codes, not text, so the client translates them. An `infected` file
also stores the scanner's signature name in `scan_signature` (as clamd returns it, control
characters removed, at most 200 characters; T-0218). The bytes are deleted at rejection either
way (TD-397): nothing infected is kept at rest.

## Malware scan (T-0218, TD-211)

The scan runs in the worker on the quarantine copy, after the SHA-256 check. Which scanner is
deployment configuration: `KERP_UPLOAD_SCANNER=stub` (default; always clean, development and
tests) or `clamd`, with `KERP_CLAMD_ADDRESS` (`tcp://<host>:<port>` or `unix:///<path>`;
required, the settings refuse anything else). `ClamdScanner` uses only the standard library
`socket`:

* `zINSTREAM`, then the object streamed from storage in length-prefixed chunks of at most
  64 KiB and a zero-length terminator. Reply `stream: OK` is clean; `stream: <signature> FOUND`
  is `infected` with that signature; anything else (`... ERROR`, garbage) counts as unavailable.
* Size limit: an object larger than `KERP_UPLOAD_SCAN_MAX_BYTES` (100 MiB) is never sent, and
  clamd's own `INSTREAM size limit exceeded` is honoured too: the file is rejected with
  `verify_failed` at once (it is never published unscanned, and retrying cannot help; TD-452). Keep
  clamd's `StreamMaxLength` at least `KERP_UPLOAD_SCAN_MAX_BYTES`.
* Unavailable (connection refused, `KERP_CLAMD_TIMEOUT_SECONDS` (30) per socket operation,
  error reply): `ScannerUnavailableError`. The file stays `verifying`, its quarantine copy is
  kept, and the job raises the jobs module's `RetryLaterError`: SAQ runs it again after 30 s,
  doubling up to 30 min (with jitter), up to 48 attempts. The row is `queued` meanwhile. After
  `VERIFY_STUCK_AFTER` (6 h) the orphan sweep rejects the file with `verify_failed` and deletes
  the quarantine copy; a later retry finds the file decided and stops.
* `ping()` sends `zPING` and expects `PONG` (the System console health probe, T-0219;
  `ping_scanner()` answers `True`/`False`, or `None` for the stub).
* No error, log line or `repr` names the scanner's host, port or socket path.

`set_scanner` installs a scanner for the process (tests). In production the worker refuses to
start with the stub (T-0213) unless `KERP_UPLOAD_SCANNER_ALLOW_STUB=true`. The compose service
`clamd` (profile `worker`) runs ClamAV for development; the worker container is configured for it.

## Orphan sweep

`sweep_orphans(db, tenancy, storage=...)` runs per project (`uploads.sweep_orphans`): a
`pending` file whose URL expired more than an hour ago becomes `expired`, and a staging or
quarantine object no `pending`/`verifying` file owns is deleted once older than an hour. A
`verifying` file whose verify job `failed` (read with `jobs.service.job_status`) or that has been
`verifying` for more than `VERIFY_STUCK_AFTER` (6 h) is `rejected` with `verify_failed` (T-0215),
so its objects become orphans too. The worker's hourly cron queues the sweep in every live
project (`schedule_hourly_per_project` in `jobs.py`, T-0215); `schedule_sweep(session)` queues
one on demand. A bucket lifecycle rule on `staging/` is the backstop (TD-208, with bucket
provisioning and CORS). No delete endpoint or content-object GC yet (TD-209).

## Quarantine monitor (T-0221, diff §4.7)

The System console (`/api/v1/admin/system/uploads...`, `admin/system_uploads_router.py`,
`system_admin` only; 403 for everyone else, PER-09 included) calls this service:

* `quarantine_files` / `quarantine_counts`: `verifying`, `rejected` and `expired` uploads (rows,
  paged; counts per project, status and reason code) with `status`, `reason` and `project`
  filters, read on the caller's tenant-level request session under the normal project RLS of
  `oe_uploads_file`. A `system_admin` session sees every project of its tenant (ADR 0016,
  T-0274): no uploads-specific policy, no bypass role, no `SECURITY DEFINER` function. Rows
  carry metadata only (never a URL or storage key); `reason` codes are passed through (the
  media-probe codes of T-0169 appear once that lands); `scan_signature` for `infected`.
  `object_deleted` is true for `rejected` (bytes deleted at rejection, TD-397) and `expired`
  (the sweep that expires the row deletes its staging object). There is no purge.
* `recheck_upload`: a **stuck** file, i.e. one still `verifying` whose current verify job
  (read through `jobs.service.job_status`) is not `queued` or `running` (it `failed`, was
  aborted, or is unknown), and whose bytes still exist (staging, or the quarantine copy kept
  during a scanner outage), gets a new `uploads.verify` / `uploads.verify_large` job, created
  in the file's own project scope; it becomes the file's `job_id` and `updated_at` restarts
  the `VERIFY_STUCK_AFTER` clock. Otherwise 409 `NOT_VERIFYING`, `CHECK_RUNNING` or
  `OBJECT_GONE`. Two verify jobs of one file must not run at once: the row lock in
  `verify_upload` only makes one of them write the outcome, not the right one. Both use the
  same quarantine object and each deletes it before writing, so a job that finds it already
  gone rejects a clean file as `missing_object` and the other leaves an orphan content object.
  A job whose worker died stays `running`: abort it in the queue monitor first. That abort
  (and a queue-monitor retry of a replaced failed job) can still overlap a worker thread that
  is mid-check: TD-497 (fail-safe: nothing is published unscanned).
* `queue_project_sweep`: queues `uploads.sweep_orphans` for one live project now (housekeeping
  that returns no rows).

Re-check and sweep are written to the tenant-level console chain `admin-system:<tenant>`
(`upload_rechecked`, `upload_sweep_queued`) after the job commits, as the queue monitor does
(TD-455).

## Configuration (`KERP_` env)

`STORAGE_BUCKET` (`kerp-vault`), `S3_ENDPOINT_URL` (MinIO; unset for AWS),
`S3_PUBLIC_ENDPOINT_URL` (what browsers reach, if different), `S3_REGION` (per deployment,
never hard-coded: default `ap-southeast-2` for Australian deployments, TD-171 / diff §4.4;
MinIO ignores it),
`S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` (unset: the AWS credential chain),
`UPLOAD_MAX_BYTES` (100 MiB), `UPLOAD_URL_SECONDS` (900), `DOWNLOAD_URL_SECONDS` (300).
`UPLOAD_SCANNER` (`stub` | `clamd`), `CLAMD_ADDRESS`, `CLAMD_TIMEOUT_SECONDS` (30),
`UPLOAD_SCAN_MAX_BYTES` (100 MiB): the malware scanner (above, T-0218).
`UPLOAD_SCANNER_ALLOW_STUB` (false): with `KERP_ENVIRONMENT=production` the SAQ worker refuses
to start while the scanner is the always-clean `StubScanner` (worker startup check
`scan.require_real_scanner`, T-0213); configure `KERP_UPLOAD_SCANNER=clamd` or set this to
`true` to accept unscanned uploads explicitly.

## Tests

`tests/test_uploads_storage.py` (helpers, `LocalStorage`, `S3Storage` via botocore's
`Stubber`; a real MinIO round trip when `KERP_TEST_S3_ENDPOINT` is set) and
`tests/test_uploads.py` (service, jobs, API on `LocalStorage`; SQLite and PostgreSQL),
`tests/test_uploads_quarantine.py` (the System console monitor; SQLite and PostgreSQL as
`kerp_app`) and `tests/test_uploads_clamd.py` (`ClamdScanner` against a fake clamd socket server that reports a
synthetic marker string as a detection; no real malware or EICAR file in the repository).
