# ADR 0010 — `device_id` in the audit ledger (ledger hash v2)

- **Status:** accepted (user decision TD-164 / 17a, 2026-11-13; diff §4.3 *Audit*, §4 ledger design)
- **Date:** 2026-11-15
- **Affects:** T-0043 (this change), the `audit_ledger` core table (migration `0016`),
  `app/core/audit` (`hashing.py`, `models.py`, `service.py`), every module that appends to the
  ledger (no call-site change needed)

## Context

Diff §4.3: "Ledger entries made through a device carry its `device_id`." The ledger
(`audit_ledger`, T-0006, TD-057) is append-only and hash-chained: `content_hash` is SHA-256
over a canonical body with a version field `v`, and the README already says "Changing the hash
format needs a new `v`". A `device_id` that is stored but not hashed could be rewritten by
someone with table access without breaking verification, so it has to be inside the hash.
Existing rows were hashed without it and must keep verifying. CLAUDE.md §6 requires an ADR for
core-table changes.

## Decision

1. **Two additive columns on `audit_ledger`** (migration `0016`):
   - `device_id` (UUID, nullable): the client-generated id of the enrolled field device
     (`oe_identity_device.device_id`) the entry was written through, else `NULL`. Not a foreign
     key: the ledger outlives devices and must never block or cascade.
   - `hash_version` (small int, not null, server default `1`): the `v` the entry was hashed
     with. Every row that exists when `0016` runs keeps `1`.
2. **Hash v2.** New entries are written with `hash_version = 2`. The v2 body is the v1 body
   plus `"device_id"` (string or `null`), with `"v": 2`:
   `{v, tenant_id, project_id, chain_key, seq, prev_hash, created_at, created_by, source,
   payload, device_id}`. v1 is unchanged (pinned by
   `test_audit_ledger.py::test_content_hash_known_answer`); v2 is pinned by
   `test_audit_ledger_device.py::test_v2_content_hash_known_answer`.
3. **Verification uses each entry's own version.** `compute_content_hash` hashes an entry in
   its `hash_version`; a chain may mix v1 and v2 entries (older v1 entries, newer v2 ones). An
   unknown version is reported as a problem, never silently accepted.
4. **CHECKs.** `hash_version IN (1, 2)` and `hash_version >= 2 OR device_id IS NULL` (a v1 row
   cannot carry a device the hash would not cover). Like the other ledger CHECKs, they hold
   even for a superuser with the append-only triggers disabled.
5. **Where the device comes from.** `audit.append(..., device_id=None)` takes an explicit
   device (e.g. a job replaying a device's queued work) and otherwise the request's device
   (`current_device_id`), which authentication sets only from the session's bound device or an
   enrolled device of the same user named in `X-KERP-Device-Id` (T-0043). Nothing else in a
   request can set it.

## Consequences

- Append-only enforcement is untouched: `ADD COLUMN` with a constant default rewrites no row
  and fires no row trigger on PostgreSQL; the REVOKEs and triggers of `0002` stay. SQLite
  copies the table (batch mode, frozen definition), where there is no guard to keep.
- The `0016` downgrade drops both columns: entries written as v2 lose `device_id` and
  `hash_version` and no longer verify. Downgrading past `0016` is therefore for development
  databases only; a production rollback keeps the columns (roll the code back, not the schema).
- Any later change to the hashed body needs `v = 3` and must keep v1 and v2 verifying.
- Anchoring head hashes outside the database (TD-058) is unaffected; anchors taken before and
  after `0016` both stay valid.
