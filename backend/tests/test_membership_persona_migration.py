"""Migration ``0012``: the membership persona CHECK allows PER-16 Project Viewer (T-0076).

Runs the real Alembic migrations on SQLite and, in CI's PostgreSQL step, as the
``NOSUPERUSER NOBYPASSRLS`` role ``kerp_app`` (the table's RLS is forced).
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.auth.models import ProjectMembership
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from tests.audit_support import ALEMBIC_INI
from tests.auth_support import ALICE, BOB, T1, revoke_project_viewers, seed_user
from tests.db_support import database_url
from tests.tenancy_support import PA, PB

TENANT = TenancyContext(tenant_id=T1)


@pytest.fixture
def migrated(tmp_path: Path) -> Iterator[tuple[Config, Database]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=database_url(tmp_path), configure_logger=False)
    command.upgrade(cfg, "head")
    db = Database(database_url(tmp_path))
    try:
        yield cfg, db
    finally:
        revoke_project_viewers(db)  # the 0012 downgrade refuses while they are active
        db.dispose()
        command.downgrade(cfg, "base")


def _member(db: Database, user: uuid.UUID, project: uuid.UUID, persona: str) -> uuid.UUID:
    with db.unit_of_work(TENANT) as s:
        row = ProjectMembership(
            tenant_id=T1, user_id=user, member_project_id=project, persona=persona
        )
        s.add(row)
        s.flush()
        return row.id


def _personas(db: Database) -> set[str]:
    with db.unit_of_work(TENANT) as s:
        return set(s.scalars(select(ProjectMembership.persona)))


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
        row = s.get(ProjectMembership, viewer)
        assert row is not None
        row.deleted_at = datetime.now(UTC)
    command.downgrade(cfg, "0011")
    assert _personas(db) == {"PER-03"}
    with db.unit_of_work(TENANT) as s:
        everything = select(ProjectMembership.id).execution_options(include_deleted=True)
        assert viewer not in set(s.scalars(everything))  # purged, not only hidden
    with pytest.raises(IntegrityError):
        _member(db, alice, PA, "PER-16")
    command.upgrade(cfg, "head")
    _member(db, alice, PA, "PER-16")
