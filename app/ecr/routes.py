"""Supervisor Draft and administrator report-visibility routes."""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.auth.csrf import validate_csrf
from app.auth.dependencies import (
    DatabaseSession,
    ManagementAdmin,
    PasswordReadyUser,
    SupervisorUser,
)
from app.branches.services import list_branches
from app.core.templates import render_template
from app.core.time import utc_now
from app.ecr import batch_c, page1, page2, page3
from app.ecr.models import EcrReportStatus
from app.ecr.operations import (
    dashboard_filter_number,
    erector_context,
    grouped_reports,
    visible_reports,
)
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
    update_series,
)
from app.ecr.workflow import audit_edits, report_snapshot
from app.storage import get_protected_storage
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
        "page3_values": page3.values(report),
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
    page3_form: Annotated[dict | None, Depends(page3.form_snapshot)],
    cooling_tower_series: Annotated[str | None, Depends(series_form_value)],
    erection_start_date: Annotated[str, Form()] = "",
    erection_completion_date: Annotated[str, Form()] = "",
) -> JSONResponse:
    validate_csrf(request, csrf_token, request.app.state.settings)
    admin_edit = supervisor.role in (UserRole.BRANCH_ADMIN, UserRole.SUPERADMIN)
    try:
        report = (
            get_admin_visible_report(db, report_id, supervisor, lock=True)
            if admin_edit
            else get_supervisor_report(db, report_id, supervisor.id, lock=True)
        )
    except SQLAlchemyError:
        db.rollback()
        return JSONResponse(
            {"ok": False, "message": "Unable to save — retrying"}, status_code=503
        )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )
    try:
        if admin_edit:
            if report.status is EcrReportStatus.APPROVED:
                raise DraftNotEditableError("Approved reports are locked.")
            if cooling_tower_series is not None:
                raise EcrIdentityError(
                    "Shared package fields cannot be changed by ordinary report editing."
                )
        before = report_snapshot(report) if admin_edit else None
        data = DraftAutosaveInput(
            erection_start_date=erection_start_date,
            erection_completion_date=erection_completion_date,
        )
        if admin_edit:
            report.erection_start_date = data.erection_start_date
            report.erection_completion_date = data.erection_completion_date
        else:
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
        if page3_form is not None:
            page3.save_text(db, report, page3.Page3DraftInput(**page3_form))
        if admin_edit:
            audit_edits(db, report, supervisor, before)
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
                **({"page3": page3.values(report)} if page3_form is not None else {}),
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
                "erection_completion_date": report.erection_completion_date.isoformat()
                if report.erection_completion_date
                else None,
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


def _visible_signature_report(db, report_id, user):
    report = (
        get_supervisor_report(db, report_id, user.id)
        if user.role is UserRole.SUPERVISOR
        else get_admin_visible_report(db, report_id, user)
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


def _editable_signature_report(db, report_id, supervisor):
    report = get_supervisor_report(db, report_id, supervisor.id, lock=True)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status is not EcrReportStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only Draft reports can be edited.")
    return report


def _signature_state(report):
    record = report.page3
    present = bool(record and record.customer_signature_storage_key)
    return {
        "present": present,
        "signed_at": record.customer_signed_at.isoformat() + "Z" if present else None,
        "url": f"/ecr/reports/{report.id}/signature" if present else None,
    }


@router.get("/ecr/reports/{report_id}/signature-state", name="ecr_signature_state")
def signature_state(report_id: int, db: DatabaseSession, user: PasswordReadyUser):
    report = _visible_signature_report(db, report_id, user)
    return JSONResponse(_signature_state(report), headers={"Cache-Control": "no-store"})


@router.get("/ecr/reports/{report_id}/signature", name="ecr_signature_read")
def signature_read(
    request: Request, report_id: int, db: DatabaseSession, user: PasswordReadyUser
):
    report = _visible_signature_report(db, report_id, user)
    if not report.page3 or not report.page3.customer_signature_storage_key:
        raise HTTPException(status_code=404, detail="No Customer Sign recorded")
    try:
        content = get_protected_storage(request).read(
            report.page3.customer_signature_storage_key
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Customer Sign unavailable") from None
    except (OSError, ValueError):
        raise HTTPException(
            status_code=503, detail="Customer Sign temporarily unavailable"
        ) from None
    return Response(
        content,
        media_type="image/png",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="customer-signature.png"',
        },
    )


def _remove_old_signature(store, key):
    if key:
        try:
            store.delete(key)
        except (OSError, ValueError):
            # DB reference was already revoked. Keep inaccessible data rather than
            # falsely report a failed save or risk destroying the new signature.
            logging.getLogger(__name__).warning("Private signature cleanup deferred")


@router.post("/ecr/reports/{report_id}/signature", name="ecr_signature_save")
async def signature_save(
    request: Request, report_id: int, db: DatabaseSession, supervisor: SupervisorUser
):
    validate_csrf(
        request, request.headers.get("X-CSRF-Token", ""), request.app.state.settings
    )
    report = _editable_signature_report(db, report_id, supervisor)
    if (
        request.headers.get("content-type", "").split(";")[0].strip()
        != "application/json"
    ):
        raise HTTPException(
            status_code=415,
            detail="Use the Customer Sign pad; file uploads are not supported.",
        )
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > page3.JSON_LIMIT:
            raise HTTPException(
                status_code=413, detail="Customer Sign is too large."
            )
    try:
        data = page3.SignatureInput.model_validate(json.loads(body))
        content = page3.validate_signature(data.signature_png)
    except (ValueError, UnicodeError, TypeError) as exc:
        message = (
            _validation_message(exc) if isinstance(exc, ValidationError) else str(exc)
        )
        return JSONResponse({"ok": False, "message": message}, status_code=422)
    old_key = report.page3.customer_signature_storage_key if report.page3 else None
    try:
        store = get_protected_storage(request)
        new_key = store.put(content)
        record = page3.ensure_page3(db, report)
        record.customer_signature_storage_key = new_key
        record.customer_signed_at = utc_now()
        report.updated_at = utc_now()
        db.commit()
    except (OSError, SQLAlchemyError):
        db.rollback()
        # An interrupted commit can be ambiguous; never delete the newly written
        # object here. It may be referenced by a committed row. Safe orphans can
        # be reconciled later; existing signed data is never destroyed on failure.
        return JSONResponse(
            {"ok": False, "message": "Unable to save sign. Please retry."},
            status_code=503,
        )
    _remove_old_signature(store, old_key)
    return JSONResponse(
        {"ok": True, "signature": _signature_state(report)},
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/ecr/reports/{report_id}/signature", name="ecr_signature_clear")
def signature_clear(
    request: Request, report_id: int, db: DatabaseSession, supervisor: SupervisorUser
):
    validate_csrf(
        request, request.headers.get("X-CSRF-Token", ""), request.app.state.settings
    )
    report = _editable_signature_report(db, report_id, supervisor)
    old_key = report.page3.customer_signature_storage_key if report.page3 else None
    try:
        store = get_protected_storage(request)
        if report.page3:
            report.page3.customer_signature_storage_key = None
            report.page3.customer_signed_at = None
            report.updated_at = utc_now()
        db.commit()
    except (OSError, SQLAlchemyError):
        db.rollback()
        return JSONResponse(
            {"ok": False, "message": "Unable to remove sign. Please retry."},
            status_code=503,
        )
    _remove_old_signature(store, old_key)
    return JSONResponse(
        {"ok": True, "signature": _signature_state(report)},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/reports", name="admin_report_list")
def admin_report_list(
    request: Request,
    db: DatabaseSession,
    admin: ManagementAdmin,
    branch_id: str = Query(default=""),
    status_filter: str = Query(default=""),
    erector: str = Query(default=""),
    year: str = Query(default=""),
) -> Response:
    try:
        branch_id = dashboard_filter_number(branch_id)
        year = dashboard_filter_number(year, maximum=9999)
    except EcrIdentityError as exc:
        raise HTTPException(422, str(exc)) from exc
    erector = erector.strip()
    selected_branch = branch_id if admin.role is UserRole.SUPERADMIN else None
    if status_filter and status_filter not in {s.value for s in EcrReportStatus}:
        raise HTTPException(422, "Select a valid report status.")
    reports = visible_reports(
        db,
        admin,
        branch_id=selected_branch,
        status=status_filter,
        erector=erector,
        year=year,
    )
    return render_template(
        request,
        "ecr/operational_dashboard.html",
        {
            "current_user": admin,
            "reports": reports,
            "groups": grouped_reports(db, admin, reports),
            "status_filter": status_filter,
            "branches": list_branches(db) if admin.role is UserRole.SUPERADMIN else [],
            "selected_branch_id": selected_branch,
            **erector_context(
                db,
                admin,
                status=status_filter,
                branch_id=selected_branch,
                erector=erector,
                year=year,
            ),
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
