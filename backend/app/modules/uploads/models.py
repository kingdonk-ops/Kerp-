"""The uploaded-file record ``oe_uploads_file`` (T-0016, master spec §1 storage isolation).

One row per upload, always project-scoped, under the standard project RLS (migration
``0009``). The bytes live in object storage, never in the database:

* ``staging/{tenant}/{project}/{file_id}``: where the client's presigned ``PUT`` lands. The
  URL stays valid after ``complete``, so the client can still change these bytes.
* ``quarantine/{tenant}/{project}/{file_id}``: the verification job's first step copies
  staging here (no URL is ever issued for it) and deletes staging. Size, magic bytes,
  SHA-256 and the scan all run on this copy, and it is the copy that gets published.
* ``{tenant}/{project}/{module}/{sha256}``: the content-addressed object, copied from the
  quarantine copy that passed every check, and never overwritten once it exists. The
  client cannot write to either key, so these bytes are the ones that were verified.

Status: ``pending`` (URL issued) -> ``verifying`` (``complete`` passed size and magic
bytes; job queued) -> ``available`` | ``rejected``. A ``pending`` row never completed
becomes ``expired`` in the orphan sweep.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, BaseRecordMixin

TABLE: Final = "oe_uploads_file"
STATUSES: Final = ("pending", "verifying", "available", "rejected", "expired")
STAGING_PREFIX: Final = "staging"
QUARANTINE_PREFIX: Final = "quarantine"
MAX_REASON: Final = 200


def staging_prefix(tenant_id: uuid.UUID, project_id: uuid.UUID) -> str:
    return f"{STAGING_PREFIX}/{tenant_id}/{project_id}/"


def quarantine_prefix(tenant_id: uuid.UUID, project_id: uuid.UUID) -> str:
    return f"{QUARANTINE_PREFIX}/{tenant_id}/{project_id}/"


class UploadedFile(Base, BaseRecordMixin):
    """An uploaded file of one project; ``module`` is the owning module's namespace."""

    __tablename__ = TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NOT NULL", name="project_level"),
        CheckConstraint("status IN (" + ", ".join(f"'{s}'" for s in STATUSES) + ")", name="status"),
        CheckConstraint("size_bytes > 0", name="size"),
    )

    module: Mapped[str] = mapped_column(String(40), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(MAX_REASON), nullable=True)
    scan_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def staging_key(self) -> str:
        assert self.project_id is not None
        return f"{staging_prefix(self.tenant_id, self.project_id)}{self.id}"

    @property
    def quarantine_key(self) -> str:
        assert self.project_id is not None
        return f"{quarantine_prefix(self.tenant_id, self.project_id)}{self.id}"

    @property
    def content_key(self) -> str:
        return f"{self.tenant_id}/{self.project_id}/{self.module}/{self.sha256}"
