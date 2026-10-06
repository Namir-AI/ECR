"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.auth.passwords import PasswordManager
from app.core.config import AppSettings, get_app_settings
from app.core.static_assets import static_bundle_version

STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"


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
    application.state.static_asset_version = static_bundle_version(STATIC_DIRECTORY)
    application.state.password_manager = PasswordManager(resolved_settings)
    application.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")
    application.include_router(api_router)
    return application


app = create_app()
