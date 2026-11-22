"""Session re-binding (``core/tenancy/rebind.py``) and the tenant-level guard (T-0349).

One implementation replaced three copies that had diverged: the access-request notifications
swallowed a failed restore even on a good transaction, the validation copy left the session
in the target scope when the closing flush failed, and only the invite path restored the
scope after a failed flush. The kept behaviour is the safer of each; these tests pin it.

Runs on SQLite and on PostgreSQL (the scratch tables are under RLS there).
"""

import dataclasses
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.db import Database
from app.core.tenancy import rebind
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rebind import SessionRebinding, project_binding
from app.core.tenancy.rls import SESSION_INFO_KEY, session_tenancy, tenant_level_context
from tests.db_support import Widget
from tests.tenancy_support import CTX_A, CTX_T1, PA, PB, T1, seeded_database


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from seeded_database(tmp_path)


def _library(s: Session) -> Widget:
    return s.scalars(select(Widget).where(Widget.name == "t1-library")).one()


def _names(s: Session) -> set[str]:
    return set(s.scalars(select(Widget.name)))


# --- tenant_level_context --------------------------------------------------------------------


def test_tenant_level_context_returns_a_tenant_level_binding(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        assert tenant_level_context(s, "the test") == CTX_T1


@pytest.mark.parametrize("context", [None, CTX_A], ids=["unbound", "project-bound"])
def test_tenant_level_context_refuses_any_other_session(
    db: Database, context: TenancyContext | None
) -> None:
    for s in db.session(context):
        with pytest.raises(RuntimeError) as refused:
            tenant_level_context(s, "the queue monitor")
        assert str(refused.value) == "the queue monitor needs a tenant-level bound session"


# --- project_binding ---------------------------------------------------------------------------


def test_a_project_binding_sees_the_project_and_comes_back_with_the_callers_objects(
    db: Database,
) -> None:
    with db.unit_of_work(CTX_T1) as s:
        library = _library(s)
        with project_binding(s, PA):
            assert session_tenancy(s) == dataclasses.replace(CTX_T1, project_id=PA)
            assert {"a1", "a2"} <= _names(s)
            s.add(Widget(tenant_id=T1, project_id=PA, name="written-in-a"))
        assert session_tenancy(s) == CTX_T1
        assert library in s  # re-attached
        assert _names(s) == {"t1-library"}
    with db.unit_of_work(CTX_A) as s:
        assert "written-in-a" in _names(s)  # flushed in the project scope, same transaction


def test_a_project_binding_keeps_the_callers_site_filter() -> None:
    """Never widened: the access-request copy used to drop ``site_node_ids`` (T-0349)."""
    site = uuid.uuid4()
    caller = TenancyContext(tenant_id=T1, site_node_ids=frozenset({site}))
    session = Session()
    session.info[SESSION_INFO_KEY] = caller
    binding = project_binding(session, PB)
    assert binding.target == dataclasses.replace(caller, project_id=PB)
    assert binding.target.site_node_ids == frozenset({site})


def test_a_project_binding_needs_a_tenant_level_session(db: Database) -> None:
    with db.unit_of_work(CTX_A) as s, pytest.raises(RuntimeError, match="tenant-level"):
        project_binding(s, PB)


# --- leaving the block -------------------------------------------------------------------------


def test_a_failing_block_restores_the_scope_and_propagates(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        library = _library(s)
        with pytest.raises(LookupError, match="synthetic"), project_binding(s, PA):
            raise LookupError("synthetic failure inside the block")
        assert session_tenancy(s) == CTX_T1
        assert library in s


def test_a_failing_closing_flush_still_restores_the_scope(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The validation copy left the session in the project scope here (T-0349)."""

    def boom() -> None:
        raise RuntimeError("synthetic flush failure")

    with db.unit_of_work(CTX_T1) as s:
        library = _library(s)
        binding = project_binding(s, PA)
        binding.__enter__()
        monkeypatch.setattr(s, "flush", boom)
        with pytest.raises(RuntimeError, match="synthetic flush failure"):
            binding.__exit__(None, None, None)
        monkeypatch.undo()
        assert session_tenancy(s) == CTX_T1
        assert library in s


def _restore_fails(monkeypatch: pytest.MonkeyPatch) -> list[TenancyContext | None]:
    """Make the restoring ``bind_tenancy`` call (the second one) fail like a dead connection."""
    calls: list[TenancyContext | None] = []
    real = rebind.bind_tenancy

    def flaky(session: Session, context: TenancyContext | None) -> None:
        calls.append(context)
        if len(calls) == 2:
            raise OperationalError("SET", {}, Exception("synthetic: connection gone"))
        real(session, context)

    monkeypatch.setattr(rebind, "bind_tenancy", flaky)
    return calls


def test_a_failed_restore_on_a_good_transaction_propagates(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The access-request copy swallowed this, leaving the connection in the project scope
    while the session claimed the caller's; now the unit of work fails and rolls back."""
    calls = _restore_fails(monkeypatch)
    held: list[bool] = []

    def bind_and_leave() -> None:
        with db.unit_of_work(CTX_T1) as s:
            library = _library(s)
            try:
                with project_binding(s, PA):
                    pass
            finally:
                held.append(library in s)  # re-attached whatever happens

    with pytest.raises(OperationalError):
        bind_and_leave()
    assert calls == [dataclasses.replace(CTX_T1, project_id=PA), CTX_T1]
    assert held == [True]


def test_a_failed_restore_on_a_failed_transaction_restores_the_session_only(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _restore_fails(monkeypatch)
    with db.unit_of_work(CTX_T1) as s:
        library = _library(s)
        with pytest.raises(LookupError), project_binding(s, PA):
            raise LookupError("synthetic failure inside the block")
        assert len(calls) == 2
        assert session_tenancy(s) == CTX_T1  # the rollback makes the connection agree
        assert library in s


def test_rebinding_to_any_target_restores_the_original_scope(db: Database) -> None:
    elevated = dataclasses.replace(CTX_T1, user_id=uuid.uuid4(), is_system_admin=True)
    with db.unit_of_work(CTX_T1) as s:
        with SessionRebinding(s, elevated):
            assert session_tenancy(s) == elevated
            assert {"a1", "b1", "t1-library"} <= _names(s)  # system admin: every project
        assert session_tenancy(s) == CTX_T1
        assert _names(s) == {"t1-library"}


def test_an_unbound_session_is_unbound_again(db: Database) -> None:
    for s in db.session(None):
        with SessionRebinding(s, CTX_T1):
            assert session_tenancy(s) == CTX_T1
        assert session_tenancy(s) is None


def test_the_helper_is_the_one_used_everywhere() -> None:
    """No module keeps a private copy (T-0349)."""
    from app.modules.access_requests import service as access_requests
    from app.modules.admin import invites
    from app.modules.validation import service as validation

    assert issubclass(invites._SystemPath, SessionRebinding)
    for module in (access_requests, validation):
        assert not hasattr(module, "_ProjectBinding")
        assert module.project_binding is project_binding
