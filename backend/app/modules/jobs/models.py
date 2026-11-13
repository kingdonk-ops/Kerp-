"""The job record ``oe_jobs_job`` (T-0015, ADR 0007).

The row is the source of truth for a job; clients poll it through ``GET /jobs/{id}``. It
uses ``BaseRecordMixin`` and is under project RLS (migration ``0007``) with no exemption:
the worker binds the tenant and project from the SAQ job kwargs before it reads the row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, BaseRecordMixin, JSONType

TABLE: Final = "oe_jobs_job"
QUEUES: Final = ("interactive", "batch")
STATUSES: Final = ("queued", "running", "succeeded", "failed")
MAX_TEXT: Final = 500  # ``message`` and ``error`` are stored at most this long


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Job(Base, BaseRecordMixin):
    """One background job. ``saq_job_key`` is the SAQ job key (the row id as text)."""

    __tablename__ = TABLE
    __table_args__ = (
        CheckConstraint(_in("queue", QUEUES), name="queue"),
        CheckConstraint(_in("status", STATUSES), name="status"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress"),
    )

    kind: Mapped[str] = mapped_column(String(120), nullable=False)
    queue: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(String(MAX_TEXT), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType(), nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType(), nullable=True)
    error: Mapped[str | None] = mapped_column(String(MAX_TEXT), nullable=True)
    saq_job_key: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
