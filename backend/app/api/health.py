"""Liveness endpoint."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.config import Settings

router = APIRouter(tags=["platform"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


def app_settings(request: Request) -> Settings:
    """Settings bound to the running app by ``create_app``."""
    settings: Settings = request.app.state.settings
    return settings


@router.get("/health", response_model=HealthResponse)
def health(settings: Annotated[Settings, Depends(app_settings)]) -> HealthResponse:
    """Process is up and serving requests. Does not probe external dependencies."""
    return HealthResponse(status="ok", version=settings.version)
