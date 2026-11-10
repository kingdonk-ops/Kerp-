# app/core/audit — hash-chained, append-only audit ledger (T-0006)

Diff §4: "append-only hash-chained event ledgers (`seq`, `chain_key`, `prev_hash`,
`content_hash`, `payload`) with DB `REVOKE UPDATE, DELETE FROM app_role`". A rewrite, not a
port (ADR 0001). The spec names only the columns and the REVOKE; everything else below is
an implementer default, recorded in spec-gap **TD-057**.

It lives in `app/core/`, not `app/modules/`, because every module writes to it through
`service.py` and it has no routes of its own. `app/core/db.py` imports `models.py`, so the
table is on `Base.metadata` for Alembic and the contract tests.

| File | What |
|---|---|
| `models.py` | `AuditEntry` (`audit_ledger`, `BaseRecordMixin`), ORM append-only guard, `AppendOnlyError` |
| `hashing.py` | canonical JSON, `compute_content_hash`, `verify_entries`, `ChainReport` (pure, no DB) |
| `service.py` | `append`, `chain_entries`, `verify_chain` |
| `guard.py` | PostgreSQL REVOKE + trigger DDL (`ledger_guard_sql`) and `ledger_guard_problems` |

## Using it

```python
from app.core.audit.service import append, verify_chain

append(session, f"inspection:{inspection.id}", {"event": "signed_off", "result": "pass"},
       source="ui", actor_id=user_id)
report = verify_chain(session, f"inspection:{inspection.id}")   # report.ok, report.problems
```

`append` runs on the caller's session, so the entry commits or rolls back with the change it
records. The session must be bound (`bind_tenancy` / `unit_of_work`); tenant and project come
from that binding, never from arguments.

## Decisions (TD-057)

- **`chain_key`** names one chain and is unique per tenant: `(tenant_id, chain_key, seq)` is a
  unique constraint. Embed the entity id (`inspection:<uuid>`). A chain lives in one scope,
  the project (or tenant level) of its first entry. Appending from another scope raises
  `ChainScopeError` when the chain is visible (a tenant-level chain from a project session),
  or fails on the unique constraint when RLS hides it (another project's chain). It never
  forks.
- **Hash:** SHA-256, lower-case hex.
  `content_hash = sha256(b"kerp-audit-ledger\n" + canonical_json(body))`, where `body` is
  `{"v": 1, tenant_id, project_id, chain_key, seq, prev_hash, created_at, created_by,
  source, payload}`. UUIDs are strings, `created_at` is UTC ISO 8601 with microseconds
  (`2026-11-10T08:30:00.123456+00:00`). The mutable mixin columns are not hashed.
  `tests/test_audit_ledger.py::test_content_hash_known_answer` pins the format; changing it
  needs a new `v` and must keep verifying old entries.
- **Canonical JSON:** sorted keys, `","`/`":"` separators, UTF-8 (`ensure_ascii=False`). The
  payload must be an object of `str`, `int`, `bool`, `null`, lists and objects. **Floats and
  NUL characters are rejected**, because they do not survive a `JSONB` round trip unchanged;
  put measurements in as decimal strings. `append` stores the validated copy.
- **Genesis:** entry 1 has `prev_hash = "0" * 64`. `seq` runs 1, 2, 3, ... without gaps.
- **Verification** (`verify_entries`) walks the chain in `seq` order and reports, per entry:
  a `seq` other than previous + 1 (a deleted row, a reordering or a renumbering), a
  `prev_hash` that is not the previous entry's `content_hash`, a recomputed hash that does not
  match `content_hash` (an edited row), and `deleted_at` being set. In-database verification
  cannot detect the newest entries being removed, or a chain rewritten with recomputed hashes
  by someone with superuser access. Anchoring head hashes outside the database is **TD-058**.

## Append-only enforcement

| Layer | Where | Stops |
|---|---|---|
| ORM guard | `models.py` `before_flush`; `soft_delete()` raises | changing, deleting, soft-deleting or creating a deleted entry through a session (both dialects) |
| `REVOKE UPDATE, DELETE, TRUNCATE ... FROM PUBLIC, CURRENT_USER` | migration `0002` (`guard.py`) | the app role: `permission denied` |
| `BEFORE UPDATE OR DELETE` row trigger + `BEFORE TRUNCATE` statement trigger | migration `0002` (`guard.py`) | everyone except a superuser with `session_replication_role = replica`, or the owner after disabling the trigger |

**Why both REVOKE and triggers.** In CI the runtime role `kerp_app` also runs the migrations,
so it owns the table (TD-056). An owner's privileges can be revoked, and the REVOKE does apply
to it, but an owner can always grant them back. The triggers do not depend on privileges, so
they still hold when the owner has re-granted itself `UPDATE`
(`test_triggers_still_block_an_owner_that_grants_itself_update`). When production splits the
migration owner from the runtime role, the runtime role must get only `SELECT, INSERT`, and
`ledger_guard_problems(conn)` can be asserted at startup (**TD-059**).

`soft_delete` never applies to audit entries. Bulk ORM `update()`/`delete()` skip the ORM guard;
on PostgreSQL the database layers still refuse them, on SQLite nothing does.

## Concurrency

`append` takes `pg_advisory_xact_lock(hash(tenant_id, chain_key))` before it reads the tail, so
concurrent appends to one chain run one after another and link correctly. The unique
`(tenant_id, chain_key, seq)` constraint is the backstop: without the lock, a race fails rather
than forking. SQLite serialises writers itself.

## Tests

- `tests/test_audit_ledger.py`: SQLite and PostgreSQL, against the migrated schema. Append and
  linking, scope, canonical JSON, the known-answer hash, `verify_entries`, and tampering with
  stored rows (edited payload, deleted row, swapped `seq`, soft-delete). On PostgreSQL the
  tampering runs as the superuser (`KERP_TEST_OWNER_URL`) with triggers disabled.
- `tests/test_audit_ledger_pg.py`: PostgreSQL only, as `kerp_app` (skipped elsewhere, TD-052).
  It covers REVOKE (`UPDATE`/`DELETE`/`TRUNCATE` denied, `INSERT`/`SELECT` kept), the triggers
  against a re-granted owner, `ledger_guard_problems`, RLS project scoping, and 6 threads x 5
  concurrent appends to one chain.
