"""Input and result of first-run provisioning (T-0023).

``FirstRunRequest`` is what the command line hands the service. It has no password field:
the first System admin's account is created without credentials and the person sets them
(SSO, or a one-time link; diff §4.1, §4.5), so no password ever reaches the command line, the
environment, the repository or a log.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.core.tenancy.models import DISPLAY_NAME_LENGTH
from app.core.tenancy.provision import DEFAULT_DISPLAY_NAME

# The same shape the admin API accepts for an account's e-mail (``admin.schemas.Email``).
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$"
    ),
]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
TenantName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DISPLAY_NAME_LENGTH)
]

DEMO_PROJECT_CODE: Final = "DEMO"
DEMO_PROJECT_NAME: Final = "Demo project (synthetic data)"


class FirstRunRequest(BaseModel):
    """What to provision. ``tenant_id`` unset: ``KERP_DEFAULT_TENANT_ID``, else a new id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    admin_email: Email
    admin_display_name: DisplayName = ""
    tenant_id: uuid.UUID | None = None
    tenant_display_name: TenantName = DEFAULT_DISPLAY_NAME
    demo: bool = False


@dataclass(frozen=True, slots=True)
class FirstRunResult:
    """What the run found or created. ``*_created`` is False when it existed already."""

    tenant_id: uuid.UUID
    tenant_created: bool
    settings_created: bool
    admin_user_id: uuid.UUID
    admin_created: bool
    admin_granted: bool
    admin_is_internal: bool
    demo_project_id: uuid.UUID | None = None
    demo_project_created: bool = False

    @property
    def changed(self) -> bool:
        return any(
            (
                self.tenant_created,
                self.settings_created,
                self.admin_created,
                self.admin_granted,
                self.demo_project_created,
            )
        )
