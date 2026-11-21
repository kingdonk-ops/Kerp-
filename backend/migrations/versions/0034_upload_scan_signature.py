"""Uploads: the malware scanner's signature name on ``oe_uploads_file`` (T-0218, diff §4.7).

Additive: one nullable ``scan_signature`` column (at most 200 characters, as returned by
``clamd``, e.g. ``Eicar-Signature``). Set only when the scan rejects a file as ``infected``;
``rejection_reason`` stays the code. No row changes and no policy changes: the table keeps
the RLS policies of migration ``0009``, so no frozen RLS SQL is needed here.

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_uploads_file"
COLUMN = "scan_signature"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
