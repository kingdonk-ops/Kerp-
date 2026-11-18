# Deploy: the tenants directory and provisioning order

Every table's `tenant_id` is a foreign key to the `tenants` directory table (master spec §1.1,
ADR 0011). A deployment has one hidden tenant (diff §4.1), `KERP_DEFAULT_TENANT_ID`.

## Order

1. **Migrate** as the schema owner (`KERP_MIGRATION_DATABASE_URL`): `alembic upgrade head`.
   Set `KERP_DEFAULT_TENANT_ID` in the migration environment too: migration `0026` then inserts
   its directory row along with one for every tenant id already stored.
2. **Provision** (optional, idempotent): `python -m app.core.tenancy.provision` creates the
   `KERP_DEFAULT_TENANT_ID` row if it is missing and does nothing otherwise. T-0023's first-run
   command writes the deployment tenant through the same path.
3. **Start** the API. Its start-up runs the same idempotent step before serving, so the
   deployment tenant's row exists before any tenant-scoped insert. On a database not yet
   migrated to `0026` it logs `tenant provisioning skipped` and creates nothing: migrate first.

## Upgrading a populated database to `0026`/`0027`

`0026` adds the foreign keys `NOT VALID` (brief locks, no scan); `0027` validates them
(`SHARE UPDATE EXCLUSIVE`: reads and writes continue). Each revision commits on its own. On a
large database run them as two steps, so the validation scan can be scheduled separately:

```bash
alembic upgrade 0026
alembic upgrade 0027   # then: alembic upgrade head
```

A `NOT VALID` key is already enforced for every row written after `0026`; `0026` backfilled a
directory row for every tenant id stored before it, so `0027` finds no violation.

## Rollback

`alembic downgrade 0025` drops every `tenant_id` foreign key and the `tenants` table; no other
data changes.
