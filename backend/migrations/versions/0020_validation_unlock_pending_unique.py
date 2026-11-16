"""At most one pending unlock request per rule: partial unique index (T-0216).

``request_unlock`` checked for a pending request and then inserted, with nothing in the
database to back the check, so two concurrent requests could leave two ``pending`` rows for
one rule. Additive: a partial unique index on ``rule_id`` where ``status = 'pending'``.

Existing duplicates are resolved first: per rule the oldest pending request (``created_at``,
then ``id``) stays pending; every other one becomes ``cancelled``, decided by its own
requester at migration time (a withdrawal, which the ``decision`` CHECK allows), and its id
is logged. A migration does not append to the audit ledger; the log line is the record.

On PostgreSQL the table's row-level security is forced, so a migration role that is not a
superuser (``kerp_app`` in CI) would see no rows: the data step runs with RLS lifted for the
table owner inside the migration's transaction (``NO FORCE`` ... ``FORCE``), as in 0012.

Downgrade drops the index (cancelled requests stay cancelled).

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-16
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_validation_unlock_request"
INDEX = "uq_oe_validation_unlock_request_pending"
PENDING = "status = 'pending'"
# Every pending request but the oldest of its rule.
_DUPLICATES = (
    f"SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY rule_id "
    f"ORDER BY created_at, id) AS n FROM {TABLE} WHERE {PENDING}) ranked WHERE n > 1"
)
_CANCEL = (
    f"UPDATE {TABLE} SET status = 'cancelled', decided_by = requested_by, "
    "decided_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP, revision = revision + 1 "
    f"WHERE {PENDING} AND id IN ({_DUPLICATES})"
)

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    context = op.get_context()
    postgres = context.dialect.name == "postgresql"
    if postgres:
        op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    if not context.as_sql:
        ids = [str(i) for i in op.get_bind().execute(sa.text(_DUPLICATES)).scalars()]
        if ids:
            log.warning(
                "0020: cancelled %d duplicate pending unlock request(s): %s",
                len(ids),
                ", ".join(ids),
            )
    op.execute(_CANCEL)
    if postgres:
        op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.create_index(
        INDEX,
        TABLE,
        ["rule_id"],
        unique=True,
        postgresql_where=sa.text(PENDING),
        sqlite_where=sa.text(PENDING),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
