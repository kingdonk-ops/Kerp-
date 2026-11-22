"""Re-binding a session to another scope for one block, in the same transaction (T-0349).

``bind_tenancy`` refuses to switch a session that holds objects (they were loaded under the
old scope). ``SessionRebinding`` flushes, detaches and re-attaches them around the block, so
a service can do one narrow piece of work in another scope without a second transaction:

* ``project_binding``: a tenant-level session bound to one project and back (a project's
  rule-set copies, the project administrators' notifications of an access request);
* a module's own target (``admin.invites``: the invitee's narrow system path).

Leaving the block always restores the caller's scope. The one exception-safe behaviour:

* a block that succeeded is flushed in the target scope; if that flush fails, the scope is
  still restored and the held objects re-attached before the error propagates;
* restoring a scope on a *failed* transaction may itself fail (the connection refuses every
  statement until rollback): the scope is then put back on the session only, which the
  rollback makes true. On a transaction that is still good, a failed restore propagates; it
  never leaves the connection in the target scope while the session claims the caller's.
"""

from __future__ import annotations

import dataclasses
import uuid
from types import TracebackType

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import (
    SESSION_INFO_KEY,
    bind_tenancy,
    session_tenancy,
    tenant_level_context,
)


class SessionRebinding:
    """Bind ``session`` to ``target`` for a ``with`` block, then back to the scope it had."""

    def __init__(self, session: Session, target: TenancyContext) -> None:
        self.session = session
        self.target = target
        self.restore = session_tenancy(session)
        self.held: list[object] = []

    def __enter__(self) -> None:
        self.session.flush()
        self.held = list(self.session.identity_map.values())
        self.session.expunge_all()
        bind_tenancy(self.session, self.target)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        failed = exc_type is not None
        try:
            if not failed:
                try:
                    self.session.flush()
                except BaseException:
                    failed = True
                    raise
        finally:  # a failing block or flush never leaves the session in the target scope
            self.session.expunge_all()
            try:
                bind_tenancy(self.session, self.restore)
            except SQLAlchemyError:
                if not failed:
                    raise
                # The transaction already failed and rolls back: restore the scope on the
                # session without touching the connection.
                self._restore_info()
            finally:
                self.session.add_all(self.held)

    def _restore_info(self) -> None:
        if self.restore is None:
            self.session.info.pop(SESSION_INFO_KEY, None)
        else:
            self.session.info[SESSION_INFO_KEY] = self.restore


def project_binding(session: Session, project_id: uuid.UUID) -> SessionRebinding:
    """Bind a tenant-level session to ``project_id`` for a block, then back.

    Everything else of the caller's context is kept, including its site filter: the binding
    never widens what the caller sees beyond the one project it names.
    """
    context = tenant_level_context(session, "a project binding")
    return SessionRebinding(session, dataclasses.replace(context, project_id=project_id))
