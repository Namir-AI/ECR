"""Phase 5 operations use the same report renderer and validated save path."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.responses import RedirectResponse

from app.auth.csrf import validate_csrf
from app.auth.dependencies import DatabaseSession, ManagementAdmin, SupervisorUser
from app.core.templates import render_template
from app.ecr import batch_c, page1, page2, page3
from app.ecr.models import EcrPackage, EcrReportStatus, EcrTower
from app.ecr.operations import (
    SEARCH_KEYS,
    create_cell,
    grouped_reports,
    search_reports,
    tower_access,
)
from app.ecr.routes import autosave, technical_context
from app.ecr.services import (
    EcrIdentityError,
    ExistingReportError,
    get_admin_visible_report,
    get_supervisor_report,
)
from app.ecr.workflow import transition

router = APIRouter(tags=["ecr-operations"])


async def admin_form_snapshot(request: Request):
    return await request.form()


@router.get("/reports/search", name="report_search")
def search(
    request: Request,
    db: DatabaseSession,
    admin: ManagementAdmin,
    search_by: str = Query(default="All"),
    value: str = Query(default=""),
):
    if search_by not in SEARCH_KEYS:
        raise HTTPException(422, "Select an approved search key.")
    reports, labels = search_reports(db, admin, search_by, value)
    if len(reports) == 1:
        return RedirectResponse(f"/reports/{reports[0].id}", status_code=303)
    return render_template(
        request,
        "ecr/search.html",
        {
            "current_user": admin,
            "reports": reports,
            "search_groups": grouped_reports(db, admin, reports),
            "matched_fields": labels,
            "search_keys": SEARCH_KEYS,
            "search_by": search_by,
            "search_value": value.strip(),
            "searched": bool(value.strip()),
        },
    )


@router.get("/reports/{report_id}/edit", name="admin_edit_report")
def admin_edit(
    request: Request, report_id: int, db: DatabaseSession, admin: ManagementAdmin
):
    report = get_admin_visible_report(db, report_id, admin)
    if report is None:
        raise HTTPException(404, "Report not found")
    if report.status is EcrReportStatus.APPROVED:
        raise HTTPException(409, "Approved reports are locked.")
    return render_template(
        request,
        "ecr/draft.html",
        {
            "current_user": admin,
            "report": report,
            "admin_edit": True,
            "series_editable": False,
            **technical_context(report, editable=True),
        },
    )


@router.post("/reports/{report_id}/save", name="admin_save_report")
def admin_save(
    request: Request,
    report_id: int,
    db: DatabaseSession,
    admin: ManagementAdmin,
    csrf_token: Annotated[str, Form()],
    technical_form: Annotated[dict | None, Depends(page1.page1_form_snapshot)],
    page2_form: Annotated[dict | None, Depends(page2.page2_form_snapshot)],
    batch_c_form: Annotated[dict | None, Depends(batch_c.form_snapshot)],
    page3_form: Annotated[dict | None, Depends(page3.form_snapshot)],
    form: Annotated[dict, Depends(admin_form_snapshot)],
    erection_start_date: Annotated[str, Form()] = "",
    erection_completion_date: Annotated[str, Form()] = "",
):
    validate_csrf(request, csrf_token, request.app.state.settings)
    protected = {
        "supervisor_user_id",
        "branch_id",
        "cooling_tower_serial_no",
        "tower_suffix",
        "cell_no",
        "cooling_tower_series",
        "customer",
        "customer_order_no",
        "model",
        "place_of_installation",
        "signature_png",
        "customer_signed_at",
        "customer_signature_storage_key",
    }
    if protected.intersection(form):
        raise HTTPException(
            422, "Ownership, shared identity and signature evidence are read-only."
        )
    return autosave(
        request,
        report_id,
        db,
        admin,
        csrf_token,
        technical_form,
        page2_form,
        batch_c_form,
        page3_form,
        None,
        erection_start_date,
        erection_completion_date,
    )


def workflow_action(request, report_id, db, actor, csrf_token, action, *, admin=False):
    validate_csrf(request, csrf_token, request.app.state.settings)
    try:
        report = (
            get_admin_visible_report(db, report_id, actor, lock=True)
            if admin
            else get_supervisor_report(db, report_id, actor.id, lock=True)
        )
        if report is None:
            raise HTTPException(404, "Report not found")
        errors = transition(db, report, actor, action)
        if errors:
            db.rollback()
            return render_template(
                request,
                "ecr/report_detail.html",
                {
                    "current_user": actor,
                    "report": report,
                    "validation_errors": errors,
                    "error": "The report is incomplete or invalid. Resolve the fields below before continuing.",
                    "back_url": "/dashboard",
                    **technical_context(report),
                },
                status_code=422,
            )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            503, "Unable to update the report. Reload and retry."
        ) from None
    url = f"/reports/{report_id}" if admin else f"/ecr/reports/{report_id}"
    if action == "back-to-edit":
        url += "/edit"
    return RedirectResponse(url, status_code=303)


@router.post("/ecr/reports/{report_id}/review")
def review(
    request: Request,
    report_id: int,
    db: DatabaseSession,
    supervisor: SupervisorUser,
    csrf_token: Annotated[str, Form()],
):
    return workflow_action(request, report_id, db, supervisor, csrf_token, "review")


@router.post("/ecr/reports/{report_id}/back-to-edit")
def back_to_edit(
    request: Request,
    report_id: int,
    db: DatabaseSession,
    supervisor: SupervisorUser,
    csrf_token: Annotated[str, Form()],
):
    return workflow_action(
        request, report_id, db, supervisor, csrf_token, "back-to-edit"
    )


@router.post("/ecr/reports/{report_id}/submit")
def submit(
    request: Request,
    report_id: int,
    db: DatabaseSession,
    supervisor: SupervisorUser,
    csrf_token: Annotated[str, Form()],
):
    return workflow_action(request, report_id, db, supervisor, csrf_token, "submit")


@router.post("/reports/{report_id}/approve")
def approve(
    request: Request,
    report_id: int,
    db: DatabaseSession,
    admin: ManagementAdmin,
    csrf_token: Annotated[str, Form()],
):
    return workflow_action(
        request, report_id, db, admin, csrf_token, "approve", admin=True
    )


@router.post("/ecr/towers/{tower_id}/cells", name="create_tower_cell")
def new_cell(
    request: Request,
    tower_id: int,
    db: DatabaseSession,
    supervisor: SupervisorUser,
    csrf_token: Annotated[str, Form()],
    cell_no: Annotated[int, Form(gt=0)],
):
    validate_csrf(request, csrf_token, request.app.state.settings)
    tower = tower_access(db, tower_id, supervisor, lock=True)
    if tower is None:
        raise HTTPException(404, "Tower not found")
    try:
        # Explicit confirmed number never changes under the user during a race.
        report = create_cell(db, tower, supervisor, cell_no)
        db.commit()
    except (ExistingReportError, EcrIdentityError) as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, "This cell already has a report. Reload the dashboard."
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Unable to create the report. Please retry.") from None
    return RedirectResponse(
        f"/ecr/reports/{report.id}/edit?notice=created", status_code=303
    )


@router.post("/ecr/packages/{package_id}/towers", name="add_package_tower")
def add_tower(
    request: Request,
    package_id: int,
    db: DatabaseSession,
    supervisor: SupervisorUser,
    csrf_token: Annotated[str, Form()],
):
    validate_csrf(request, csrf_token, request.app.state.settings)
    package = db.scalar(
        select(EcrPackage)
        .where(
            EcrPackage.id == package_id, EcrPackage.created_by_user_id == supervisor.id
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if package is None:
        raise HTTPException(404, "Package not found")
    if not package.multiple_towers:
        raise HTTPException(
            409, "A single-tower package cannot be converted from the dashboard."
        )
    suffixes = list(
        db.scalars(
            select(EcrTower.normalized_suffix_key)
            .where(EcrTower.package_id == package_id)
            .with_for_update()
        )
    )
    # Continue alphabetic progression; do not invent post-Z semantics or relabel.
    if any(len(s) != 1 for s in suffixes) or "Z" in suffixes:
        raise HTTPException(
            409,
            "Automatic tower suffix generation stops at Z. Please contact the Service Admin.",
        )
    suffix = chr(max([ord(s) for s in suffixes] or [ord("A") - 1]) + 1)
    try:
        tower = EcrTower(
            package_id=package_id, tower_suffix=suffix, normalized_suffix_key=suffix
        )
        db.add(tower)
        db.flush()
        report = create_cell(db, tower, supervisor, 1)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(409, "Unable to add the tower. Reload and retry.") from None
    return RedirectResponse(
        f"/ecr/reports/{report.id}/edit?notice=created", status_code=303
    )
