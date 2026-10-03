"""Shared landing and authenticated placeholder pages."""

from fastapi import APIRouter, HTTPException, Query, Request, status
from starlette.responses import RedirectResponse, Response

from app.auth.dependencies import DatabaseSession, OptionalUser, PasswordReadyUser
from app.branches.services import list_branches
from app.core.templates import render_template
from app.ecr.models import EcrReportStatus
from app.ecr.operations import grouped_reports, visible_reports
from app.users.models import UserRole

router = APIRouter(tags=["pages"])


@router.get("/", include_in_schema=False)
def root(user: OptionalUser) -> RedirectResponse:
    destination = "/dashboard" if user is not None else "/auth/login"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/dashboard", name="dashboard")
def dashboard(
    request: Request,
    db: DatabaseSession,
    user: PasswordReadyUser,
    status_filter: str = Query(default=""),
    branch_id: int | None = Query(default=None),
    cell_page: int = Query(default=1, ge=1),
) -> Response:
    if status_filter and status_filter not in {s.value for s in EcrReportStatus}:
        raise HTTPException(422, "Select a valid report status.")
    reports = visible_reports(db, user, status=status_filter, branch_id=branch_id)
    return render_template(
        request,
        "ecr/operational_dashboard.html",
        {
            "current_user": user,
            "reports": reports,
            "groups": grouped_reports(db, user, reports, cell_page=cell_page),
            "status_filter": status_filter,
            "selected_branch_id": branch_id,
            "branches": list_branches(db) if user.role is UserRole.SUPERADMIN else [],
        },
    )
