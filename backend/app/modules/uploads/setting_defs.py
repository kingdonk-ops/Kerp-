"""Runtime settings of the uploads module (T-0230, diff §4.7), category ``uploads_media``.

``max_upload_size_mb`` and ``presigned_url_ttl_minutes`` are read by ``service.upload_limits``
and capped by the deployment configuration (``KERP_UPLOAD_MAX_BYTES``,
``KERP_UPLOAD_URL_SECONDS``): a System admin can lower them at runtime, never raise them above
what the deployment allows. The four image-derivative entries are read by T-0247.
"""

from __future__ import annotations

from typing import Final

from app.modules.settings.registry import DeploymentCap, SettingDef, register

CATEGORY: Final = "uploads_media"
MIB: Final = 1024 * 1024
# ``Settings.upload_max_bytes`` allows up to 5 GiB.
MAX_UPLOAD_SIZE_MB_LIMIT: Final = 5 * 1024

MAX_UPLOAD_SIZE_MB: Final = SettingDef(
    key="max_upload_size_mb",
    category=CATEGORY,
    type="int",
    default=100,
    minimum=1,
    maximum=MAX_UPLOAD_SIZE_MB_LIMIT,
    client_visible=True,
    # Rounded up, so a deployment limit below 1 MiB still leaves the minimum valid; the exact
    # byte limit is applied by ``upload_limits``.
    cap=DeploymentCap(env="KERP_UPLOAD_MAX_BYTES", limit=lambda s: -(-s.upload_max_bytes // MIB)),
    description="Largest file a user may upload, in MiB",
)
PRESIGNED_URL_TTL_MINUTES: Final = SettingDef(
    key="presigned_url_ttl_minutes",
    category=CATEGORY,
    type="int",
    default=15,
    minimum=1,
    maximum=60,
    cap=DeploymentCap(env="KERP_UPLOAD_URL_SECONDS", limit=lambda s: s.upload_url_seconds // 60),
    description="Minutes a presigned upload URL stays valid",
)
IMAGE_DERIVATIVES: Final = (
    SettingDef(
        key="tablet_proxy_max_px",
        category=CATEGORY,
        type="int",
        default=1280,
        minimum=640,
        maximum=2560,
        description="Longest edge in pixels of the image proxy sent to tablets",
    ),
    SettingDef(
        key="tablet_proxy_quality",
        category=CATEGORY,
        type="int",
        default=72,
        minimum=40,
        maximum=95,
        description="Compression quality of the tablet image proxy",
    ),
    SettingDef(
        key="report_image_max_px",
        category=CATEGORY,
        type="int",
        default=1024,
        minimum=480,
        maximum=2048,
        description="Longest edge in pixels of an image embedded in a report",
    ),
    SettingDef(
        key="report_image_quality",
        category=CATEGORY,
        type="int",
        default=68,
        minimum=40,
        maximum=95,
        description="Compression quality of an image embedded in a report",
    ),
)

register(MAX_UPLOAD_SIZE_MB, PRESIGNED_URL_TTL_MINUTES, *IMAGE_DERIVATIVES)
