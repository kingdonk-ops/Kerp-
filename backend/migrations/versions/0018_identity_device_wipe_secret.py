"""Per-device wipe secret: ``oe_identity_device.wipe_secret_hash`` (T-0080, diff §4.3).

Additive: one nullable column holding the SHA-256 (hex) of the wipe secret enrolment issues
once (never the secret). A device enrolled before this revision has no secret, so it cannot
confirm its wipe (``POST /api/v1/devices/{device_id}/wipe-confirmed`` answers it with the
uniform refusal); acceptable because no device is in production before the pilot.

Downgrade drops the column (every issued secret stops working).

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_identity_device"
COLUMN = "wipe_secret_hash"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.String(length=64), nullable=True))


def downgrade() -> None:
    # Plain ``DROP COLUMN`` (SQLite >= 3.35 too): a batch copy would lose the table's CHECKs.
    op.drop_column(TABLE, COLUMN)
