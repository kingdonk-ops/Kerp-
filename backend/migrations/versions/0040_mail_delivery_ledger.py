"""Mail: the delivery ledger (T-0226, diff §4.7 Delivery ledger, ADR 0020).

Additive on the core table ``mail_send_log`` (migration ``0024``):

* nullable ``recipient_user_id`` (the user the e-mail went to, when the sender knows one) and
  ``broadcast_id`` (the broadcast it belongs to, T-0228); both plain ids without a foreign key,
  like ``created_by``; each indexed;
* an index on ``template_key`` and one on ``(tenant_id, created_at)`` for the stats and the
  searchable history;
* the status check gains ``delivered`` (an SES ``Delivery`` notification):
  ``queued | sent | delivered | bounced | complained | failed | suppressed``.

``project_id`` is not added: every send-log row already has it (``BaseRecordMixin``; the
sending session's project, ``NULL`` for tenant-level sends). No row changes and no policy
changes, so this revision carries no ``FROZEN_RLS_DDL``.

On PostgreSQL it is ``ADD COLUMN`` plus a re-created check. SQLite cannot alter a check, so
batch mode copies the table; it gets the frozen definition below (no reflection, so offline
``--sql`` works and later model changes stay out). The downgrade turns ``delivered`` rows back
into ``sent`` (what they were before the notification) before the old check returns.

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "mail_send_log"
CHECK = f"ck_{TABLE}_status"
OLD_STATUSES = ("queued", "sent", "bounced", "complained", "failed", "suppressed")
NEW_STATUSES = ("queued", "sent", "delivered", "bounced", "complained", "failed", "suppressed")
LEDGER_COLUMNS = ("recipient_user_id", "broadcast_id")
COLUMN_INDEXES = ("recipient_user_id", "broadcast_id", "template_key")
CREATED_INDEX = f"ix_{TABLE}_tenant_created"


_UNDELIVER = (
    f"UPDATE {TABLE} SET status = 'sent', updated_at = CURRENT_TIMESTAMP, "
    "revision = revision + 1 WHERE status = 'delivered'"
)


def _status_sql(statuses: tuple[str, ...]) -> str:
    return f"status IN ({', '.join(repr(s) for s in statuses)})"


def _ledger_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("recipient_user_id", GUID(), nullable=True),
        sa.Column("broadcast_id", GUID(), nullable=True),
    ]


def _frozen_send_log(*, with_ledger: bool) -> sa.Table:
    """``mail_send_log`` as of revision 0039 (SQLite), or as of this one."""
    ledger: list[sa.SchemaItem] = []
    statuses = OLD_STATUSES
    if with_ledger:
        statuses = NEW_STATUSES
        ledger = [
            *_ledger_columns(),
            *(sa.Index(f"ix_{TABLE}_{column}", column) for column in COLUMN_INDEXES),
            sa.Index(CREATED_INDEX, "tenant_id", "created_at"),
        ]
    return sa.Table(
        TABLE,
        sa.MetaData(),
        sa.Column("template_key", sa.String(length=100), nullable=False),
        sa.Column("recipient_hash", sa.String(length=64), nullable=False),
        sa.Column("recipient_masked", sa.String(length=260), nullable=False),
        sa.Column("locale", sa.String(length=35), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", GUID(), nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.Column("project_id", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("custom_fields", JSONType(), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint(_status_sql(statuses), name=CHECK),
        sa.PrimaryKeyConstraint("id", name=f"pk_{TABLE}"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{TABLE}_tenant_id_tenants"
        ),
        *(
            sa.Index(f"ix_{TABLE}_{column}", column)
            for column in ("tenant_id", "project_id", "recipient_hash", "status")
        ),
        sa.Index(f"ix_{TABLE}_provider_message_id", "provider_message_id"),
        *ledger,
    )


def _create_indexes() -> None:
    for column in COLUMN_INDEXES:
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])
    op.create_index(op.f(CREATED_INDEX), TABLE, ["tenant_id", "created_at"])


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        for column in _ledger_columns():
            op.add_column(TABLE, column)
        op.drop_constraint(op.f(CHECK), TABLE, type_="check")
        op.create_check_constraint(op.f(CHECK), TABLE, _status_sql(NEW_STATUSES))
        _create_indexes()
        return
    frozen = _frozen_send_log(with_ledger=False)
    with op.batch_alter_table(TABLE, copy_from=frozen, recreate="always") as batch:
        for column in _ledger_columns():
            batch.add_column(column)
        batch.drop_constraint(op.f(CHECK), type_="check")
        batch.create_check_constraint(op.f(CHECK), sa.text(_status_sql(NEW_STATUSES)))
    _create_indexes()


def downgrade() -> None:
    postgres = op.get_context().dialect.name == "postgresql"
    # The owner rewrites every tenant's rows: RLS is forced on the table, so lift it meanwhile
    # (like 0020).
    if postgres:
        op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(_UNDELIVER)
    if postgres:
        op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
        op.drop_index(op.f(CREATED_INDEX), TABLE)
        for column in reversed(COLUMN_INDEXES):
            op.drop_index(op.f(f"ix_{TABLE}_{column}"), TABLE)
        op.drop_constraint(op.f(CHECK), TABLE, type_="check")
        op.create_check_constraint(op.f(CHECK), TABLE, _status_sql(OLD_STATUSES))
        for name in reversed(LEDGER_COLUMNS):
            op.drop_column(TABLE, name)
        return
    frozen = _frozen_send_log(with_ledger=True)
    with op.batch_alter_table(TABLE, copy_from=frozen, recreate="always") as batch:
        batch.drop_index(op.f(CREATED_INDEX))
        for column in reversed(COLUMN_INDEXES):
            batch.drop_index(op.f(f"ix_{TABLE}_{column}"))
        batch.drop_constraint(op.f(CHECK), type_="check")
        batch.create_check_constraint(op.f(CHECK), sa.text(_status_sql(OLD_STATUSES)))
        for name in reversed(LEDGER_COLUMNS):
            batch.drop_column(name)
