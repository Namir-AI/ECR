"""Shared landing and authenticated placeholder pages."""

from fastapi import APIRouter, Request, status
from starlette.responses import RedirectResponse, Response

from app.auth.dependencies import DatabaseSession, OptionalUser, PasswordReadyUser
from app.core.templates import render_template
from app.ecr.services import list_supervisor_reports
from app.users.models import UserRole

router = APIRouter(tags=["pages"])


@router.get("/", include_in_schema=False)
def root(user: OptionalUser) -> RedirectResponse:
    destination = "/dashboard" if user is not None else "/auth/login"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/dashboard", name="dashboard")
def dashboard(request: Request, db: DatabaseSession, user: PasswordReadyUser) -> Response:
    if user.role is UserRole.SUPERVISOR:
        return render_template(
            request,
            "ecr/supervisor_dashboard.html",
            {
                "current_user": user,
                "reports": list_supervisor_reports(db, user.id),
            },
        )
    return render_template(
        request,
        "dashboard.html",
        {"current_user": user},
    )
