"""Top-level API router."""

from fastapi import APIRouter

from app.api import health, pages
from app.auth import routes as auth_routes
from app.users import routes as user_routes

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(pages.router)
api_router.include_router(auth_routes.router)
api_router.include_router(user_routes.router)
