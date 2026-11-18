"""Validate the ``tenant_id`` foreign keys, step 2 of 2 (T-0079, ADR 0011).

PostgreSQL only: ``VALIDATE CONSTRAINT`` on every ``NOT VALID`` foreign key to ``tenants``
that ``0026`` added. It scans existing rows under a ``SHARE UPDATE EXCLUSIVE`` lock, so reads
and writes go on meanwhile, and ``env.py`` runs it in its own transaction (one per revision),
apart from ``0026``'s brief schema locks. Large deployments may run ``alembic upgrade 0026``
and ``alembic upgrade 0027`` as two separate steps (``docs/deploy.md``). SQLite has no
``NOT VALID``; nothing to do there.

The downgrade is a no-op: a validated constraint is the same schema object; ``0026``'s
downgrade drops it.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# One statement, so the offline SQL (``--sql``) validates the same constraints.
_VALIDATE = """
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT conrelid::regclass AS tbl, conname FROM pg_constraint
    WHERE contype = 'f' AND confrelid = 'tenants'::regclass AND NOT convalidated
    ORDER BY conrelid::regclass::text
  LOOP
    EXECUTE format('ALTER TABLE %s VALIDATE CONSTRAINT %I', r.tbl, r.conname);
  END LOOP;
END
$$
"""


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute(_VALIDATE)


def downgrade() -> None:
    """Nothing to undo: validation changes no schema object."""
