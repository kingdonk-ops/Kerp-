"""The single rule store and the findings log (T-0018, ADR 0004 §1, Amendment E).

* ``oe_validation_rule_set``: one named set of rules of one ``kind`` on one ``layer``.
  ``project_id`` is NULL on the ``system`` and ``tenant`` layers (CHECK ``layer_scope``).
* ``oe_validation_rule``: one rule of a set; ``version`` goes up on every change.
* ``oe_validation_finding``: one row per rule that did not pass in a ``run_checks`` run,
  with the ``rule_key`` and rule version that produced it (ADR 0004 §3).
* ``oe_validation_unlock_request``: the two-person unlock of a locked tenant-layer rule
  (ADR 0004 §5, Amendment B, TD-116; T-0034).
* ``oe_validation_override``: the per-record override of a blocked check (ADR 0004
  Amendment A; T-0035). Its shape is the table in ``docs/spec/modules/quality-and-inspection.md``
  §Per-record override, frozen at M1 (T-0131).

All use ``BaseRecordMixin`` and are under project RLS (migrations ``0005``, ``0014``, ``0022``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, BaseRecordMixin, JSONType

RULE_SET_TABLE: Final = "oe_validation_rule_set"
RULE_TABLE: Final = "oe_validation_rule"
FINDING_TABLE: Final = "oe_validation_finding"
UNLOCK_TABLE: Final = "oe_validation_unlock_request"
UNLOCK_PENDING_INDEX: Final = "uq_oe_validation_unlock_request_pending"
OVERRIDE_TABLE: Final = "oe_validation_override"
OVERRIDE_ACTIVE_INDEX: Final = "uq_oe_validation_override_active"

# ADR 0004 §1 + Amendment E. Frozen at M1 (T-0131); adding a value needs the freeze process.
RULE_KINDS: Final = ("check", "digest", "autolink", "notify", "p6_grammar", "p6_eligibility")
ENGINES: Final = ("cel", "regex")
LAYERS: Final = ("system", "tenant", "project")
SEVERITIES: Final = ("info", "warning", "error")
OUTCOMES: Final = ("fail", "error")
UNLOCK_STATUSES: Final = ("pending", "approved", "rejected", "cancelled")
# ADR 0004 Amendment A: ``pending`` -> ``approved`` | ``rejected`` | ``void``. Frozen at M1.
OVERRIDE_DECISIONS: Final = ("pending", "approved", "rejected", "void")
# Requests that still count: at most one per record and rule (``OVERRIDE_ACTIVE_INDEX``).
OVERRIDE_ACTIVE: Final = ("pending", "approved")
# Amendment A.4: the QA/QC Lead (PER-06), or the Project Manager (PER-02) when the requester
# holds PER-06.
OVERRIDE_APPROVER_PERSONAS: Final = ("PER-06", "PER-02")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class RuleSet(Base, BaseRecordMixin):
    """A named rule set, e.g. ``p6.import.commit`` (``check``) or ``handover.autolink``.

    ``version`` goes up whenever one of its rules changes, so a consumer can record which
    version of the set it ran with. Uniqueness treats a NULL ``project_id`` as one value
    on PostgreSQL (``NULLS NOT DISTINCT``); the service checks it on every dialect.
    """

    __tablename__ = RULE_SET_TABLE
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "layer",
            "key",
            name="uq_oe_validation_rule_set_key",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(_in("kind", RULE_KINDS), name="kind"),
        CheckConstraint(_in("layer", LAYERS), name="layer"),
        CheckConstraint("(layer = 'project') = (project_id IS NOT NULL)", name="layer_scope"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    key: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    layer: Mapped[str] = mapped_column(String(10), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_pack: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # The module whose seed files the set comes from (``rules/defaults/*.yaml``); NULL for a
    # set created through the API. Project copies are made per module (ADR 0004 §2.3).
    module: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # System layer: the seed file's ``version``. Tenant/project copies: the seed version
    # their source had when they were copied.
    seed_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Rule(Base, BaseRecordMixin):
    """One rule. ``params`` is KERP CEL JSON, available to CEL rules as ``params``.

    A deleted rule is soft-deleted and keeps its ``rule_key`` (ADR 0004 §3). A copied rule
    names the rule it was copied from (``origin_rule_key`` + ``origin_version``); a rule the
    project or tenant added has ``origin_rule_key`` NULL. A ``locked`` rule cannot be
    changed on the project layer; ``unlock_adr_reference`` records the ADR an approved
    unlock cited (ADR 0004 §5).
    """

    __tablename__ = RULE_TABLE
    __table_args__ = (
        UniqueConstraint("rule_set_id", "rule_key", name="uq_oe_validation_rule_key"),
        CheckConstraint(_in("engine", ENGINES), name="engine"),
        CheckConstraint(_in("severity", SEVERITIES), name="severity"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey(f"{RULE_SET_TABLE}.id"), nullable=False, index=True
    )
    rule_key: Mapped[str] = mapped_column(String(120), nullable=False)
    engine: Mapped[str] = mapped_column(String(10), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONType(), nullable=False, default=dict)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    origin_rule_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    origin_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    unlock_adr_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Finding(Base, BaseRecordMixin):
    """A rule that failed (``fail``) or could not be evaluated (``error``) in one run."""

    __tablename__ = FINDING_TABLE
    __table_args__ = (CheckConstraint(_in("outcome", OUTCOMES), name="outcome"),)

    run_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey(f"{RULE_SET_TABLE}.id"), nullable=False
    )
    rule_set_key: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_set_version: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey(f"{RULE_TABLE}.id"), nullable=False
    )
    rule_key: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 of the canonical JSON of the values the rule checked (T-0035, Amendment A.6):
    # an override of this finding is bound to it. NULL on findings logged before 0022.
    checked_values_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class UnlockRequest(Base, BaseRecordMixin):
    """A request to unlock one locked tenant-layer rule (ADR 0004 §5, Amendment B).

    A PER-13 Quality Director requests it with an ADR reference; a **different** PER-13 or
    PER-01 user approves or rejects it (TD-116 interim, until ``approval_routes``). It is
    bound to the rule version it was raised against: a rule changed meanwhile cannot be
    unlocked by it. Tenant-level row (``project_id`` NULL).
    """

    __tablename__ = UNLOCK_TABLE
    __table_args__ = (
        CheckConstraint(_in("status", UNLOCK_STATUSES), name="status"),
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint(
            "(status = 'pending') = (decided_by IS NULL AND decided_at IS NULL)", name="decision"
        ),
        CheckConstraint("status <> 'approved' OR decided_by <> requested_by", name="two_people"),
        # At most one pending request per rule (T-0216, migration 0020).
        Index(
            UNLOCK_PENDING_INDEX,
            "rule_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )

    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey(f"{RULE_SET_TABLE}.id"), nullable=False, index=True
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey(f"{RULE_TABLE}.id"), nullable=False, index=True
    )
    rule_key: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    adr_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    requested_by: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Override(Base, BaseRecordMixin):
    """A reasoned request to let one record pass one blocking ``check`` rule (Amendment A).

    Raised against the blocking finding of a ``run_checks`` run on the record
    (``record_type:record_id`` is the run's ``subject``) and bound to that rule as evaluated
    (``rule_set_key``, ``rule_key``, ``rule_version``) and to the hash of the values it
    checked (``checked_values_hash``). Routed to ``approver_persona`` (PER-06, or PER-02 when
    the requester holds PER-06); ``approver_user_id`` names the one approver when the
    request was routed to a single user, else any other holder of the persona may decide.
    The requester never decides their own request. A later evaluation of the record with a
    different hash (or rule version) sets ``decision = void`` and the block returns.
    Project-level row. Columns: quality-and-inspection.md §Per-record override (frozen at M1).
    """

    __tablename__ = OVERRIDE_TABLE
    __table_args__ = (
        CheckConstraint(_in("decision", OVERRIDE_DECISIONS), name="decision"),
        CheckConstraint(_in("approver_persona", OVERRIDE_APPROVER_PERSONAS), name="approver"),
        CheckConstraint("length(trim(reason)) > 0", name="reason_given"),
        CheckConstraint("length(checked_values_hash) = 64", name="hash_length"),
        CheckConstraint("(decision = 'pending') = (decided_at IS NULL)", name="decided"),
        CheckConstraint(
            "decision NOT IN ('approved', 'rejected') OR decided_by IS NOT NULL", name="decider"
        ),
        CheckConstraint(
            "decided_by IS NULL OR decided_by <> requested_by", name="never_self_decided"
        ),
        CheckConstraint(
            "approver_user_id IS NULL OR approver_user_id <> requested_by",
            name="never_self_routed",
        ),
        # At most one pending or approved override per record and rule.
        Index(
            OVERRIDE_ACTIVE_INDEX,
            "tenant_id",
            "project_id",
            "record_type",
            "record_id",
            "rule_set_key",
            "rule_key",
            unique=True,
            postgresql_where=text("decision IN ('pending', 'approved')"),
            sqlite_where=text("decision IN ('pending', 'approved')"),
        ),
    )

    # Always a project-level row (the record belongs to a project).
    project_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    record_type: Mapped[str] = mapped_column(String(60), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    rule_set_key: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_key: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey(f"{FINDING_TABLE}.id"), nullable=False, index=True
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    approver_persona: Mapped[str] = mapped_column(String(10), nullable=False)
    approver_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    decision: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_values_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    audit_chain_key: Mapped[str] = mapped_column(String(200), nullable=False)
