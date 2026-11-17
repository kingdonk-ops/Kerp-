"""Liveness endpoint (plus configuration warnings, e.g. the Entra ``acct`` claim, T-0054)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.auth import sso
from app.core.auth.keys import AuthConfigError
from app.core.auth.revocation import RevocationUnavailableError
from app.core.auth.runtime import app_auth_runtime
from app.core.config import Settings

router = APIRouter(tags=["platform"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    warnings: list[str] | None = Field(
        default=None,
        description=(
            "Configuration warnings, absent when there are none. `sso_acct_claim_missing`: an "
            "Entra ID SSO sign-in was refused in the last 24 h because the ID token had no "
            "`acct` claim (enable the optional claims `email`, `acct`, `xms_edov`)"
        ),
    )


def app_settings(request: Request) -> Settings:
    """Settings bound to the running app by ``create_app``."""
    settings: Settings = request.app.state.settings
    return settings


def _warnings(request: Request, settings: Settings) -> list[str]:
    if not settings.entra_enabled:
        return []
    try:
        # Only with SSO enabled; a store or key problem is not this endpoint's to report.
        store = app_auth_runtime(request.app).store
        missing = sso.acct_missing_recently(store)
    except (AuthConfigError, RevocationUnavailableError):
        return []
    return [sso.ACCT_MISSING_WARNING] if missing else []


@router.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
def health(
    request: Request, settings: Annotated[Settings, Depends(app_settings)]
) -> HealthResponse:
    """Process is up and serving requests. Does not probe external dependencies; with Entra
    ID SSO enabled it reads one marker from the auth store (``warnings``)."""
    found = _warnings(request, settings)
    return HealthResponse(status="ok", version=settings.version, warnings=found or None)
