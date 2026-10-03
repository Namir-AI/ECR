"""Supervisor Draft and administrator report-visibility routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.auth.csrf import validate_csrf
from app.auth.dependencies import DatabaseSession, ManagementAdmin, SupervisorUser
from app.branches.services import list_branches
from app.core.templates import render_template
from app.ecr import batch_c, page1, page2
from app.ecr.models import EcrReportStatus
from app.ecr.page1 import (
    FINAL_REQUIRED_FIELDS,
    SECTIONS,
    Page1DraftInput,
    page1_form_snapshot,
    page1_values,
    save_page1,
)
from app.ecr.schemas import DraftAutosaveInput, DraftCreateInput, SerialLookupInput
from app.ecr.series import (
    COOLING_TOWER_SERIES,
    active_fields,
    active_sections,
    applicable_sections,
)
from app.ecr.services import (
    DraftNotEditableError,
    EcrIdentityError,
    ExistingReportError,
    autosave_draft,
    can_edit_series,
    create_or_resume_draft,
    find_package_by_serial,
    get_admin_visible_report,
    get_supervisor_report,
    list_admin_reports,
    update_series,
)
from app.users.models import UserRole

router = APIRouter(tags=["ecr"])
FormValue = Annotated[str, Form()]


async def series_form_value(request: Request) -> str | None:
    form = await request.form()
    return str(form["cooling_tower_series"]) if "cooling_tower_series" in form else None


def technical_context(report, *, editable=False):
    series = report.tower.package.cooling_tower_series
    return {
        "series_options": COOLING_TOWER_SERIES,
        "series_rules": {
            value: sorted(applicable_sections(value)) for value in COOLING_TOWER_SERIES
        },
        "series_known": series in COOLING_TOWER_SERIES,
        "applicable_sections": applicable_sections(series),
        "page1_sections": SECTIONS if editable else active_sections(SECTIONS, series),
        "page1_required_fields": page1.required_fields(series),
        "page1_final_required_fields": FINAL_REQUIRED_FIELDS,
        "page1_values": page1_values(report),
        "page2_sections": page2.SECTIONS
        if editable
        else active_sections(page2.SECTIONS, series),
        "page2_required_fields": page2.required_fields(series),
        "page2_final_required_fields": {
            name
            for name, field in page2.Page2DraftInput.model_fields.items()
            if field.json_schema_extra["final_required"]
        },
        "page2_values": page2.page2_values(report),
        "batch_c_values": batch_c.values(report),
        "torque_categories": batch_c.CATEGORIES,
        "torque_required_categories": batch_c.FINAL_REQUIRED_CATEGORIES,
        "reading_positions": batch_c.POSITIONS,
    }


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
        "series_options": COOLING_TOWER_SERIES,
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
            "series_options": COOLING_TOWER_SERIES,
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
        SerialLookupInput(cooling_tower_serial_no=cooling_tower_serial_no)
        package = find_package_by_serial(db, cooling_tower_serial_no)
        data = DraftCreateInput.model_validate(
            form,
            context={
                "existing_series": package.cooling_tower_series if package else None,
            },
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )
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
        {
            "current_user": supervisor,
            "report": report,
            "message": message,
            **technical_context(report, editable=True),
            "series_editable": can_edit_series(db, report, supervisor),
        },
    )


@router.get("/ecr/reports/{report_id}", name="supervisor_report_detail")
def supervisor_report_detail(
    request: Request, report_id: int, db: DatabaseSession, supervisor: SupervisorUser
) -> Response:
    report = get_supervisor_report(db, report_id, supervisor.id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return render_template(
        request,
        "ecr/report_detail.html",
        {
            "current_user": supervisor,
            "report": report,
            "back_url": "/dashboard",
            **technical_context(report),
        },
    )


@router.get("/ecr/reports/{report_id}/series-state", name="ecr_series_state")
def series_state(
    report_id: int, db: DatabaseSession, supervisor: SupervisorUser
) -> JSONResponse:
    """Reconcile a rejected/uncertain save without changing any report data."""
    report = get_supervisor_report(db, report_id, supervisor.id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return JSONResponse(
        {
            "series": report.tower.package.cooling_tower_series,
            "editable": can_edit_series(db, report, supervisor),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/ecr/reports/{report_id}/autosave", name="ecr_autosave_draft")
def autosave(
    request: Request,
    report_id: int,
    db: DatabaseSession,
    supervisor: SupervisorUser,
    csrf_token: FormValue,
    technical_form: Annotated[dict | None, Depends(page1_form_snapshot)],
    page2_form: Annotated[dict | None, Depends(page2.page2_form_snapshot)],
    batch_c_form: Annotated[dict | None, Depends(batch_c.form_snapshot)],
    cooling_tower_series: Annotated[str | None, Depends(series_form_value)],
    erection_start_date: Annotated[str, Form()] = "",
    erection_completion_date: Annotated[str, Form()] = "",
) -> JSONResponse:
    validate_csrf(request, csrf_token, request.app.state.settings)
    report = get_supervisor_report(db, report_id, supervisor.id, lock=True)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )
    try:
        data = DraftAutosaveInput(
            erection_start_date=erection_start_date,
            erection_completion_date=erection_completion_date,
        )
        autosave_draft(report, data)
        update_series(db, report, supervisor, cooling_tower_series)
        series = report.tower.package.cooling_tower_series
        technical = (
            Page1DraftInput(
                **{
                    name: value
                    for name, value in technical_form.items()
                    if name in active_fields(SECTIONS, series)
                    or name == "blade_serials"
                }
            )
            if technical_form is not None
            else None
        )
        batch_ab = (
            page2.Page2DraftInput(
                **{
                    name: value
                    for name, value in page2_form.items()
                    if name in active_fields(page2.SECTIONS, series)
                }
            )
            if page2_form is not None
            else None
        )
        batch_c_data = (
            batch_c.BatchCDraftInput(**batch_c_form)
            if batch_c_form is not None
            else None
        )
        if technical is not None:
            save_page1(db, report, technical)
        if batch_ab is not None:
            page2.save_page2(db, report, batch_ab)
        if batch_c_data is not None:
            batch_c.save(db, report, batch_c_data)
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
    except EcrIdentityError as exc:
        db.rollback()
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=422)
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
                **(
                    {"batch_c": batch_c.values(report)}
                    if batch_c_form is not None
                    else {}
                ),
                "erection_start_date": (
                    report.erection_start_date.isoformat()
                    if report.erection_start_date
                    else None
                ),
                "erection_completion_date": report.erection_completion_date.isoformat(),
                **(
                    {
                        "page1": {
                            name: value
                            for name, value in page1_values(report).items()
                            if name in active_fields(SECTIONS, series)
                            or name == "blade_serials"
                        }
                    }
                    if technical_form is not None
                    else {}
                ),
                **(
                    {
                        "page2": {
                            name: value
                            for name, value in page2.page2_values(report).items()
                            if name in active_fields(page2.SECTIONS, series)
                        }
                    }
                    if page2_form is not None
                    else {}
                ),
                **(
                    {"cooling_tower_series": report.tower.package.cooling_tower_series}
                    if cooling_tower_series is not None
                    else {}
                ),
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )
    return render_template(
        request,
        "ecr/report_detail.html",
        {
            "current_user": admin,
            "report": report,
            **technical_context(report),
        },
    )
