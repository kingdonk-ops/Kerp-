"""Migration ``0012``: the membership persona CHECK allows PER-16 Project Viewer (T-0076).

Runs the real Alembic migrations on SQLite and, in CI's PostgreSQL step, as the
``NOSUPERUSER NOBYPASSRLS`` role ``kerp_app`` (the table's RLS is forced).
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from app.core.db import GUID, Database, JSONType
from app.core.tenancy.context import TenancyContext
from tests.audit_support import ALEMBIC_INI
from tests.auth_support import ALICE, BOB, T1, revoke_project_viewers, seed_user
from tests.db_support import database_url, seed_tenants
from tests.tenancy_support import PA, PB

TENANT = TenancyContext(tenant_id=T1, is_system_admin=True)  # reads every membership (T-0014)


@pytest.fixture
def migrated(tmp_path: Path) -> Iterator[tuple[Config, Database]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=database_url(tmp_path), configure_logger=False)
    command.upgrade(cfg, "head")
    db = Database(database_url(tmp_path))
    try:
        seed_tenants(db, T1)
        yield cfg, db
    finally:
        revoke_project_viewers(db)  # the 0012 downgrade refuses while they are active
        db.dispose()
        command.downgrade(cfg, "base")


# The table's columns from 0006 on: the test runs below later revisions (e.g. 0025's source
# columns), so it does not go through the current ORM model.
_TABLE = sa.Table(
    "auth_project_membership",
    sa.MetaData(),
    sa.Column("id", GUID()),
    sa.Column("tenant_id", GUID()),
    sa.Column("project_id", GUID()),
    sa.Column("user_id", GUID()),
    sa.Column("member_project_id", GUID()),
    sa.Column("persona", sa.String(6)),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
    sa.Column("deleted_at", sa.DateTime(timezone=True)),
    sa.Column("custom_fields", JSONType()),
    sa.Column("source", sa.String(16)),
    sa.Column("revision", sa.Integer()),
)


def _member(db: Database, user: uuid.UUID, project: uuid.UUID, persona: str) -> uuid.UUID:
    row_id, now = uuid.uuid4(), datetime.now(UTC)
    with db.unit_of_work(TENANT) as s:
        s.execute(
            sa.insert(_TABLE).values(
                id=row_id,
                tenant_id=T1,
                user_id=user,
                member_project_id=project,
                persona=persona,
                created_at=now,
                updated_at=now,
                custom_fields={},
                source="system",
                revision=1,
            )
        )
    return row_id


def _personas(db: Database) -> set[str]:
    live = sa.select(_TABLE.c.persona).where(
        _TABLE.c.tenant_id == T1, _TABLE.c.deleted_at.is_(None)
    )
    with db.unit_of_work(TENANT) as s:
        return set(s.scalars(live))


def test_a_project_viewer_membership_inserts_after_the_upgrade(
    migrated: tuple[Config, Database],
) -> None:
    db = migrated[1]
    user = seed_user(db, T1, ALICE)
    _member(db, user, PA, "PER-16")
    _member(db, user, PB, "PER-15")
    assert _personas(db) == {"PER-15", "PER-16"}


def test_an_unknown_persona_is_still_rejected(migrated: tuple[Config, Database]) -> None:
    db = migrated[1]
    user = seed_user(db, T1, ALICE)
    with pytest.raises(IntegrityError):
        _member(db, user, PA, "PER-17")
    with pytest.raises(IntegrityError):
        _member(db, user, PA, "PER-00")


def test_downgrade_refuses_while_active_project_viewers_exist(
    migrated: tuple[Config, Database],
) -> None:
    cfg, db = migrated
    alice, bob = seed_user(db, T1, ALICE), seed_user(db, T1, BOB)
    viewer = _member(db, alice, PA, "PER-16")
    _member(db, bob, PA, "PER-03")
    with pytest.raises(RuntimeError, match="1 active PER-16"):
        command.downgrade(cfg, "0011")
    assert _personas(db) == {"PER-03", "PER-16"}  # nothing deleted

    # Once revoked (soft-deleted), the downgrade purges the revoked PER-16 row and restores
    # the PER-01..PER-15 CHECK; the other memberships are untouched.
    with db.unit_of_work(TENANT) as s:
        revoke = sa.update(_TABLE).where(_TABLE.c.id == viewer)
        assert s.execute(revoke.values(deleted_at=datetime.now(UTC))).rowcount == 1
    command.downgrade(cfg, "0011")
    assert _personas(db) == {"PER-03"}
    with db.unit_of_work(TENANT) as s:
        everything = sa.select(_TABLE.c.id).where(_TABLE.c.tenant_id == T1)
        assert viewer not in set(s.scalars(everything))  # purged, not only hidden
    with pytest.raises(IntegrityError):
        _member(db, alice, PA, "PER-16")
    command.upgrade(cfg, "head")
    _member(db, alice, PA, "PER-16")
