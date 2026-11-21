"""Request and response shapes of the uploads API (T-0016)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.modules.uploads.storage import clean_filename, is_upload_type

FileStatus = Literal["pending", "verifying", "available", "rejected", "expired"]


def _upload_type(value: str) -> str:
    if not is_upload_type(value):
        raise ValueError(f"{value!r} is not an allowed upload type")
    return value.split(";", 1)[0].strip().lower()


ModuleName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,39}$")]
Filename = Annotated[
    str, StringConstraints(min_length=1, max_length=1024), AfterValidator(clean_filename)
]
ContentType = Annotated[str, StringConstraints(max_length=127), AfterValidator(_upload_type)]
Sha256 = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[0-9a-fA-F]{64}$")
]


class UploadCreate(BaseModel):
    """``POST /uploads/projects/{project_id}/files``: what the client is about to upload.

    ``sha256`` is the client's hex digest of the bytes; the server recomputes it before the
    file becomes available. ``size_bytes`` is signed into the upload URL.
    """

    model_config = ConfigDict(extra="forbid")

    module: ModuleName
    filename: Filename
    content_type: ContentType
    size_bytes: int = Field(gt=0)
    sha256: Sha256


class FileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    module: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: FileStatus
    rejection_reason: str | None
    job_id: uuid.UUID | None
    expires_at: datetime
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PresignedRead(BaseModel):
    """A request the client sends straight to object storage before ``expires_at``."""

    model_config = ConfigDict(from_attributes=True)

    url: str
    method: Literal["PUT", "GET"]
    headers: dict[str, str]
    expires_at: datetime


class UploadTicket(BaseModel):
    """The new file record and the presigned ``PUT`` for its bytes."""

    file: FileRead
    upload: PresignedRead


# --- quarantine monitor (T-0221): System console, ``/api/v1/admin/system/uploads`` ------------

MonitoredStatus = Literal["verifying", "rejected", "expired"]


class QuarantineFileRead(BaseModel):
    """An upload the monitor shows. ``reason`` is a code the client translates (media-probe
    codes of T-0169 included); ``scan_signature`` is set for ``infected``. ``object_deleted``:
    the bytes are gone (rejected at once, TD-397; expired by the sweep); there is no purge."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    project_code: str | None
    module: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: MonitoredStatus
    reason: str | None
    scan_signature: str | None
    uploader_id: uuid.UUID | None
    object_deleted: bool
    created_at: datetime
    updated_at: datetime


class QuarantinePage(BaseModel):
    """``GET /admin/system/uploads``: one page, most recently changed first."""

    items: list[QuarantineFileRead]
    total: int
    limit: int
    offset: int


class QuarantineCountRead(BaseModel):
    """``GET /admin/system/uploads/counts``: uploads of one project per status and reason."""

    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    project_code: str | None
    status: MonitoredStatus
    reason: str | None
    count: int


class QuarantineJobRead(BaseModel):
    """A queued re-check or sweep: the job to poll (``/api/v1/jobs/{id}``)."""

    job_id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    status: Literal["queued"]
