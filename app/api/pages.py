"""Shared landing and authenticated placeholder pages."""

from fastapi import APIRouter, HTTPException, Query, Request, status
from starlette.responses import RedirectResponse, Response

from app.auth.dependencies import DatabaseSession, OptionalUser, PasswordReadyUser
from app.branches.services import list_branches
from app.core.templates import render_template
from app.ecr.models import EcrReportStatus
from app.ecr.operations import (
    dashboard_filter_number,
    erector_context,
    grouped_reports,
    visible_reports,
)
from app.ecr.services import EcrIdentityError
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
    branch_id: str = Query(default=""),
    cell_page: int = Query(default=1, ge=1),
    erector: str = Query(default=""),
    year: str = Query(default=""),
) -> Response:
    if status_filter and status_filter not in {s.value for s in EcrReportStatus}:
        raise HTTPException(422, "Select a valid report status.")
    try:
        branch_id = dashboard_filter_number(branch_id)
        year = dashboard_filter_number(year, maximum=9999)
    except EcrIdentityError as exc:
        raise HTTPException(422, str(exc)) from exc
    erector = erector.strip() if user.role is not UserRole.SUPERVISOR else ""
    year = year if user.role is not UserRole.SUPERVISOR else None
    reports = visible_reports(
        db, user, status=status_filter, branch_id=branch_id, erector=erector, year=year
    )
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
            **(
                erector_context(
                    db,
                    user,
                    status=status_filter,
                    branch_id=branch_id,
                    erector=erector,
                    year=year,
                )
                if user.role is not UserRole.SUPERVISOR
                else {}
            ),
        },
    )
