"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import AppSettings, get_app_settings


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """Create the FastAPI application without opening external connections."""
    resolved_settings = settings or get_app_settings()
    application = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        debug=resolved_settings.app_debug,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    application.state.settings = resolved_settings
    application.include_router(api_router)
    return application


app = create_app()
