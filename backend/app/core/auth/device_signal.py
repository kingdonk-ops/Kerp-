"""The ``DEVICE_REVOKED`` signal (T-0043, diff §4.3): no database, importable everywhere.

When a device is revoked, every later call from it (any authenticated request, refresh,
PIN unlock, sign-in, enrolment, sync) is answered ``401`` with ``code: DEVICE_REVOKED``.
The client then wipes its local store (records, outbox, media, keys) and confirms the wipe
(``POST /api/v1/devices/{device_id}/wipe-confirmed``). Any other ``401`` keeps the local
data: only a remote wipe deletes it.
"""

from __future__ import annotations

import uuid
from typing import Final

from fastapi import Request
from fastapi.responses import JSONResponse

# Every request from an enrolled field client names its device (diff §4.3).
DEVICE_HEADER: Final = "X-KERP-Device-Id"
DEVICE_REVOKED_CODE: Final = "DEVICE_REVOKED"
DEVICE_REVOKED_DETAIL: Final = "Device revoked"


class DeviceRevokedError(Exception):
    """The call comes from a revoked device (HTTP 401, ``code: DEVICE_REVOKED``)."""


class InvalidDeviceHeaderError(ValueError):
    """The device header is not a UUID (HTTP 400)."""


def parse_device_id(value: str | None) -> uuid.UUID | None:
    """The device id of a ``X-KERP-Device-Id`` header value (``None`` when absent)."""
    if value is None or not value.strip():
        return None
    try:
        return uuid.UUID(value.strip())
    except ValueError as exc:
        raise InvalidDeviceHeaderError("X-KERP-Device-Id must be a UUID") from exc


def request_device_id(request: Request) -> uuid.UUID | None:
    """The device a request says it comes from (a claim to check, never proof)."""
    return parse_device_id(request.headers.get(DEVICE_HEADER))


def device_revoked_response() -> JSONResponse:
    return JSONResponse(
        {"detail": DEVICE_REVOKED_DETAIL, "code": DEVICE_REVOKED_CODE},
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )


async def device_revoked_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return device_revoked_response()


async def invalid_device_header_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)
