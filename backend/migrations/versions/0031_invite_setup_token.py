"""Invite ``setup_token`` (T-0059, diff §4.1 External invitees create their own password).

Additive: ``oe_admin_project_invite.setup_token_hash`` (nullable): the SHA-256 of the second
secret that only the invite e-mail carries. Together with the ``invite_token`` it lets a
brand-new invitee set a first password while accepting the invite; the token itself is never
stored. ``NULL`` for invites created before this revision (they are accepted after a normal
sign-in, or re-sent) and once an invite is accepted or revoked. The table's RLS policies
cover the new column as they are.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_admin_project_invite"
COLUMN = "setup_token_hash"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
