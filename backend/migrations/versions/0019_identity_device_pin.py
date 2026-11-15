"""Field PIN on the device record: ``oe_identity_device.pin_hash`` + ``pin_set_at`` (T-0038).

Diff §4.2 *Field PIN, hybrid* (TD-135, decision 8a): the PIN belongs to the enrolled device,
not to a server session, so its scrypt hash moves from the revocation store (``pin:<sid>``
in Redis, T-0009) to the device row. Additive: two nullable columns. Nothing is migrated:
the Redis entries were per session and die with their sessions (12 h); a device without a PIN
gets one at its next enrolment.

Downgrade drops both columns (every device PIN is forgotten).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_identity_device"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("pin_hash", sa.String(length=255), nullable=True))
    op.add_column(TABLE, sa.Column("pin_set_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # Plain ``DROP COLUMN`` (SQLite >= 3.35 too): a batch copy would lose the table's CHECKs.
    op.drop_column(TABLE, "pin_set_at")
    op.drop_column(TABLE, "pin_hash")
