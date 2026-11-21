"""The read-only deployment configuration System admins see (T-0230, diff §4.7; TD-046, TD-398).

Shown, never edited at runtime: it comes from the environment (``app.core.config.Settings``)
and changes with a redeploy. ``FIELDS`` is an explicit allow-list of ``Settings`` fields;
``read`` refuses to run if one of them is a secret (``secret_fields``: every ``SecretStr``,
every field hidden from ``repr()``, connection URLs and key files, every secret-like name).
"""

from __future__ import annotations

from typing import Final

from app.core.auth.lockout import PIN_POLICY
from app.core.config import Settings
from app.modules.settings.registry import secret_like
from app.modules.settings.schemas import DeploymentConfigRead, LockoutScheduleRead

FIELDS: tuple[str, ...] = (
    "refresh_token_hours",
    "pin_idle_minutes",
    "aws_region",
    "s3_region",
    "mail_transport",
    "mail_from_address",
    "upload_scanner",
    "upload_max_bytes",
    "upload_url_seconds",
)
# Not configurable until the assistant exists (Phase 5).
AI_PROVIDER: Final = "none"


def secret_fields() -> frozenset[str]:
    """The ``Settings`` fields that must never leave the process."""
    return frozenset(
        name
        for name, field in Settings.model_fields.items()
        if "SecretStr" in str(field.annotation)
        or field.repr is False
        or secret_like(name)
        or name.endswith(("_url", "_file"))
    )


def read(config: Settings) -> DeploymentConfigRead:
    leaked = sorted(secret_fields().intersection(FIELDS))
    if leaked:
        raise RuntimeError(f"secret configuration can never be shown: {', '.join(leaked)}")
    values = {name: getattr(config, name) for name in FIELDS}
    return DeploymentConfigRead(
        refresh_token_hours=values["refresh_token_hours"],
        pin_idle_minutes=values["pin_idle_minutes"],
        pin_lockout=LockoutScheduleRead(
            free_attempts=PIN_POLICY.free_attempts,
            base_delay_seconds=int(PIN_POLICY.base_delay.total_seconds()),
            max_delay_seconds=int(PIN_POLICY.max_delay.total_seconds()),
            max_attempts=PIN_POLICY.max_attempts,
        ),
        region=values["aws_region"],
        storage_region=values["s3_region"],
        # Unset outside production means the log transport (production refuses to start).
        mail_transport=values["mail_transport"] or "log",
        mail_from_address=values["mail_from_address"],
        upload_scanner=values["upload_scanner"],
        upload_max_bytes=values["upload_max_bytes"],
        upload_url_seconds=values["upload_url_seconds"],
        ai_provider=AI_PROVIDER,
    )
