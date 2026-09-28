"""Shared landing and authenticated placeholder pages."""

from fastapi import APIRouter, Request, status
from starlette.responses import RedirectResponse, Response

from app.auth.dependencies import OptionalUser, PasswordReadyUser
from app.core.templates import render_template

router = APIRouter(tags=["pages"])


@router.get("/", include_in_schema=False)
def root(user: OptionalUser) -> RedirectResponse:
    destination = "/dashboard" if user is not None else "/auth/login"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/dashboard", name="dashboard")
def dashboard(request: Request, user: PasswordReadyUser) -> Response:
    """Authentication-only placeholder; ECR dashboard begins in Phase 3."""
    return render_template(
        request,
        "dashboard.html",
        {"current_user": user},
    )
