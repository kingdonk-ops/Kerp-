"""Runtime settings of the admin module (T-0230, diff §4.7), category ``identity``.

``impersonation_max_minutes`` (T-0277, ADR 0015 §2): the longest a System admin's
impersonation session may last; the admin session's own end caps it further. Read when an
impersonation starts (``impersonation.py``); a change applies to sessions started afterwards.
Not client-visible: only the start endpoint needs it.
"""

from __future__ import annotations

from typing import Final

from app.core.auth.impersonation import MAX_MINUTES_BOUNDS, MAX_MINUTES_DEFAULT, MAX_MINUTES_KEY
from app.modules.settings.registry import SettingDef, register

CATEGORY: Final = "identity"

IMPERSONATION_MAX_MINUTES: Final = SettingDef(
    key=MAX_MINUTES_KEY,
    category=CATEGORY,
    type="int",
    default=MAX_MINUTES_DEFAULT,
    minimum=MAX_MINUTES_BOUNDS[0],
    maximum=MAX_MINUTES_BOUNDS[1],
    client_visible=False,
    description="Longest a System admin's impersonation of another user may last, in minutes",
)

register(IMPERSONATION_MAX_MINUTES)
