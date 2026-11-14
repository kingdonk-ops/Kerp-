"""Internal staff flag and Entra link on ``auth_user`` (T-0049, ADR 0009).

Additive core-table change covered by ADR 0009: ``is_internal`` (not null, default false,
so every existing account starts external until its next sign-in recomputes it) and the
nullable Entra link ``entra_oid`` / ``entra_tid``, with a unique index on
``(tenant_id, entra_oid)``. No existing value changes; the table's RLS policy is untouched.

The downgrade drops the three columns and the index (the flag is derived, so nothing is
lost that the next sign-in would not recompute; Entra links made by T-0054 would be lost).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "auth_user"
INDEX = "uq_auth_user_tenant_entra_oid"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(TABLE, sa.Column("entra_oid", sa.String(length=64), nullable=True))
    op.add_column(TABLE, sa.Column("entra_tid", sa.String(length=64), nullable=True))
    op.create_index(INDEX, TABLE, ["tenant_id", "entra_oid"], unique=True)


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, "entra_tid")
    op.drop_column(TABLE, "entra_oid")
    op.drop_column(TABLE, "is_internal")
