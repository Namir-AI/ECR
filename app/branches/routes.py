"""Superadmin-only branch management routes."""

from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.responses import RedirectResponse, Response

from app.auth.csrf import validate_csrf
from app.auth.dependencies import DatabaseSession, SuperadminUser
from app.branches.models import Branch
from app.branches.schemas import BranchCreateInput
from app.branches.services import BranchConflictError, create_branch
from app.core.templates import render_template

router = APIRouter(prefix="/admin/branches", tags=["branch-management"])
FormValue = Annotated[str, Form()]


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors(include_input=False, include_url=False)
    return errors[0]["msg"] if errors else "Invalid form submission."


@router.get("", name="branch_list")
def branch_list(
    request: Request,
    db: DatabaseSession,
    admin: SuperadminUser,
) -> Response:
    branches = list(db.scalars(select(Branch).order_by(Branch.name)))
    message = "Branch created successfully." if request.query_params.get("created") == "1" else None
    return render_template(
        request,
        "admin/branches.html",
        {"current_user": admin, "branches": branches, "form": {}, "message": message},
    )


@router.post("", name="branch_create")
def branch_create(
    request: Request,
    db: DatabaseSession,
    admin: SuperadminUser,
    csrf_token: FormValue,
    code: FormValue,
    name: FormValue,
) -> Response:
    validate_csrf(request, csrf_token, request.app.state.settings)
    form = {"code": code, "name": name}
    try:
        data = BranchCreateInput(**form)
        create_branch(db, data)
        db.commit()
    except ValidationError as exc:
        error = _validation_message(exc)
    except BranchConflictError as exc:
        db.rollback()
        error = str(exc)
    except IntegrityError:
        db.rollback()
        error = "A branch with that code or name already exists."
    else:
        return RedirectResponse("/admin/branches?created=1", status_code=303)
    branches = list(db.scalars(select(Branch).order_by(Branch.name)))
    return render_template(
        request,
        "admin/branches.html",
        {"current_user": admin, "branches": branches, "form": form, "error": error},
        status_code=status.HTTP_400_BAD_REQUEST,
    )
