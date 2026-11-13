"""Request and response shapes of the jobs API (T-0015)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The payload names what to work on (ids, options), never the data itself (T-0060).
MAX_PAYLOAD_BYTES: Final = 64 * 1024


class JobCreate(BaseModel):
    """``POST /jobs``: a kind registered with ``api=True`` and its payload."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=3, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def _bounded(cls, payload: dict[str, Any]) -> dict[str, Any]:
        size = len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
        if size > MAX_PAYLOAD_BYTES:
            limit = MAX_PAYLOAD_BYTES // 1024
            raise ValueError(f"payload is {size} bytes as compact JSON; the limit is {limit} KiB")
        return payload


class JobRead(BaseModel):
    """``GET /jobs/{id}``: the job's state, as last written by the worker."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    queue: Literal["interactive", "batch"]
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: int
    message: str | None
    result: dict[str, Any] | None
    error: str | None
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
