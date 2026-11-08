"""Application factory.

Run with: ``uvicorn app.main:create_app --factory``.
"""

from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI app. Uses environment settings unless ``settings`` is given."""
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        debug=settings.debug,
    )
    app.state.settings = settings

    app.include_router(health_router)
    # Domain modules (app/modules/<name>/) are mounted by the module loader (later task).

    return app
