"""Supervisor Draft and administrator report-visibility routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.auth.csrf import validate_csrf
from app.auth.dependencies import DatabaseSession, ManagementAdmin, SupervisorUser
from app.branches.services import list_branches
from app.core.templates import render_template
from app.ecr.schemas import DraftAutosaveInput, DraftCreateInput, SerialLookupInput
from app.ecr.models import EcrReportStatus
from app.ecr.services import (
    DraftNotEditableError,
    EcrIdentityError,
    ExistingReportError,
    autosave_draft,
    create_or_resume_draft,
    find_package_by_serial,
    get_admin_visible_report,
    get_supervisor_report,
    list_admin_reports,
)
from app.users.models import UserRole

router = APIRouter(tags=["ecr"])
FormValue = Annotated[str, Form()]


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors(include_input=False, include_url=False)
    return errors[0]["msg"] if errors else "Invalid form submission."


def _creation_context(
    request: Request,
    supervisor: Any,
    db: DatabaseSession,
    form: dict[str, Any],
    *,
    error: str | None = None,
) -> dict[str, Any]:
    package = None
    serial_no = str(form.get("cooling_tower_serial_no") or "").strip()
    if serial_no:
        try:
            package = find_package_by_serial(db, serial_no)
        except ValueError:
            package = None
    return {
        "current_user": supervisor,
        "package": package,
        "form": form,
        "error": error,
        "request": request,
    }


@router.get("/ecr/reports/new", name="ecr_new_report_start")
def new_report_start(request: Request, supervisor: SupervisorUser) -> Response:
    return render_template(
        request,
        "ecr/new_report_start.html",
        {"current_user": supervisor, "form": {}},
    )


@router.post("/ecr/reports/new/check", name="ecr_check_package")
def check_package(
    request: Request,
    db: DatabaseSession,
    supervisor: SupervisorUser,
    csrf_token: FormValue,
    cooling_tower_serial_no: FormValue,
) -> Response:
    validate_csrf(request, csrf_token, request.app.state.settings)
    try:
        lookup = SerialLookupInput(cooling_tower_serial_no=cooling_tower_serial_no)
    except ValidationError as exc:
        return render_template(
            request,
            "ecr/new_report_start.html",
            {
                "current_user": supervisor,
                "form": {"cooling_tower_serial_no": cooling_tower_serial_no},
                "error": _validation_message(exc),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    package = find_package_by_serial(db, lookup.cooling_tower_serial_no)
    return render_template(
        request,
        "ecr/new_report.html",
        {
            "current_user": supervisor,
            "package": package,
            "form": {"cooling_tower_serial_no": lookup.cooling_tower_serial_no},
        },
    )


@router.post("/ecr/reports", name="ecr_create_report")
def create_report(
    request: Request,
    db: DatabaseSession,
    supervisor: SupervisorUser,
    csrf_token: FormValue,
    cooling_tower_serial_no: FormValue,
    customer: Annotated[str, Form()] = "",
    customer_order_no: Annotated[str, Form()] = "",
    cooling_tower_series: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
    place_of_installation: Annotated[str, Form()] = "",
    multiple_towers: Annotated[str, Form()] = "",
    tower_suffix: Annotated[str, Form()] = "",
    declared_no_of_cells: Annotated[str, Form()] = "",
    cell_no: Annotated[str, Form()] = "",
    erection_start_date: Annotated[str, Form()] = "",
    erection_completion_date: Annotated[str, Form()] = "",
) -> Response:
    validate_csrf(request, csrf_token, request.app.state.settings)
    form = {
        "cooling_tower_serial_no": cooling_tower_serial_no,
        "customer": customer,
        "customer_order_no": customer_order_no,
        "cooling_tower_series": cooling_tower_series,
        "model": model,
        "place_of_installation": place_of_installation,
        "multiple_towers": multiple_towers,
        "tower_suffix": tower_suffix,
        "declared_no_of_cells": declared_no_of_cells,
        "cell_no": cell_no,
        "erection_start_date": erection_start_date,
        "erection_completion_date": erection_completion_date,
    }
    try:
        data = DraftCreateInput(**form)
    except ValidationError as exc:
        return render_template(
            request,
            "ecr/new_report.html",
            _creation_context(
                request,
                supervisor,
                db,
                form,
                error=_validation_message(exc),
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    result = None
    for attempt in range(2):
        try:
            result = create_or_resume_draft(db, supervisor, data)
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            if attempt == 1:
                return render_template(
                    request,
                    "ecr/new_report.html",
                    _creation_context(
                        request,
                        supervisor,
                        db,
                        form,
                        error=(
                            "The report identity changed while it was being created. "
                            "Please retry."
                        ),
                    ),
                    status_code=status.HTTP_409_CONFLICT,
                )
        except ExistingReportError as exc:
            db.rollback()
            return render_template(
                request,
                "ecr/new_report.html",
                _creation_context(request, supervisor, db, form, error=str(exc)),
                status_code=status.HTTP_409_CONFLICT,
            )
        except EcrIdentityError as exc:
            db.rollback()
            return render_template(
                request,
                "ecr/new_report.html",
                _creation_context(request, supervisor, db, form, error=str(exc)),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    assert result is not None
    notice = "resumed" if result.resumed else "created"
    return RedirectResponse(
        f"/ecr/reports/{result.report.id}/edit?notice={notice}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/ecr/reports/{report_id}/edit", name="ecr_edit_draft")
def edit_draft(
    request: Request,
    report_id: int,
    db: DatabaseSession,
    supervisor: SupervisorUser,
) -> Response:
    report = get_supervisor_report(db, report_id, supervisor.id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    if report.status is not EcrReportStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only Draft reports can be edited.",
        )
    notice = request.query_params.get("notice")
    message = {
        "created": "Draft created successfully.",
        "resumed": "Your existing Draft has been resumed.",
    }.get(notice)
    return render_template(
        request,
        "ecr/draft.html",
        {"current_user": supervisor, "report": report, "message": message},
    )


@router.post("/ecr/reports/{report_id}/autosave", name="ecr_autosave_draft")
def autosave(
    request: Request,
    report_id: int,
    db: DatabaseSession,
    supervisor: SupervisorUser,
    csrf_token: FormValue,
    erection_start_date: Annotated[str, Form()] = "",
    erection_completion_date: Annotated[str, Form()] = "",
) -> JSONResponse:
    validate_csrf(request, csrf_token, request.app.state.settings)
    report = get_supervisor_report(db, report_id, supervisor.id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    try:
        data = DraftAutosaveInput(
            erection_start_date=erection_start_date,
            erection_completion_date=erection_completion_date,
        )
        autosave_draft(report, data)
        db.commit()
    except ValidationError as exc:
        db.rollback()
        return JSONResponse(
            {"ok": False, "message": _validation_message(exc)},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except DraftNotEditableError as exc:
        db.rollback()
        return JSONResponse(
            {"ok": False, "message": str(exc)},
            status_code=status.HTTP_409_CONFLICT,
        )
    except SQLAlchemyError:
        db.rollback()
        return JSONResponse(
            {"ok": False, "message": "Unable to save — retrying"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return JSONResponse(
        {
            "ok": True,
            "message": "Saved",
            "updated_at": report.updated_at.isoformat(),
            "values": {
                "erection_start_date": (
                    report.erection_start_date.isoformat()
                    if report.erection_start_date else None
                ),
                "erection_completion_date": report.erection_completion_date.isoformat(),
            },
        }
    )


@router.get("/reports", name="admin_report_list")
def admin_report_list(
    request: Request,
    db: DatabaseSession,
    admin: ManagementAdmin,
    branch_id: int | None = Query(default=None),
) -> Response:
    selected_branch = branch_id if admin.role is UserRole.SUPERADMIN else None
    reports = list_admin_reports(db, admin, branch_id=selected_branch)
    return render_template(
        request,
        "ecr/admin_reports.html",
        {
            "current_user": admin,
            "reports": reports,
            "branches": list_branches(db) if admin.role is UserRole.SUPERADMIN else [],
            "selected_branch_id": selected_branch,
        },
    )


@router.get("/reports/{report_id}", name="admin_report_detail")
def admin_report_detail(
    request: Request,
    report_id: int,
    db: DatabaseSession,
    admin: ManagementAdmin,
) -> Response:
    report = get_admin_visible_report(db, report_id, admin)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return render_template(
        request,
        "ecr/report_detail.html",
        {"current_user": admin, "report": report},
    )
