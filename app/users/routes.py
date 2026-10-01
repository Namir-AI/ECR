"""Role-aware, branch-scoped user management routes."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from starlette.responses import RedirectResponse, Response

from app.auth.csrf import validate_csrf
from app.auth.dependencies import DatabaseSession, ManagementAdmin, SuperadminUser
from app.auth.passwords import PasswordManager, PasswordPolicyError
from app.auth.sessions import revoke_all_user_sessions
from app.branches.models import Branch
from app.branches.services import BranchSelectionError, list_active_branches, list_branches
from app.core.templates import render_template
from app.users.models import PasswordResetRequest, User, UserRole, UserStatus
from app.users.schemas import AdminPasswordResetInput, BranchAdminCreateInput
from app.users.services import (
    UserActionError,
    UserConflictError,
    activate_user,
    count_active_sessions,
    create_branch_admin,
    disable_user,
    reassign_user_branch,
    reset_user_password,
)

router = APIRouter(prefix="/admin/users", tags=["user-management"])
FormValue = Annotated[str, Form()]
FormId = Annotated[int, Form()]

_ACTION_NOTICES = {
    "activated": "has been activated.",
    "disabled": "has been disabled.",
    "enabled": "has been re-enabled.",
    "password-reset": "password has been reset.",
    "force-logout": "has been forced to log out.",
    "branch-updated": "branch assignment has been updated.",
    "branch-admin-created": "Branch Admin account has been created.",
}


def _password_manager(request: Request) -> PasswordManager:
    return request.app.state.password_manager


def _scoped_user_statement(admin: User):
    statement = select(User).options(joinedload(User.branch))
    if admin.role is UserRole.BRANCH_ADMIN:
        statement = statement.where(
            User.role == UserRole.SUPERVISOR,
            User.branch_id == admin.branch_id,
        )
    return statement


def _get_scoped_user_or_404(db: DatabaseSession, admin: User, user_id: int) -> User:
    user = db.scalar(_scoped_user_statement(admin).where(User.id == user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors(include_input=False, include_url=False)
    return errors[0]["msg"] if errors else "Invalid form submission."


def _action_message(notice: str | None, user: User) -> str | None:
    action_text = _ACTION_NOTICES.get(notice or "")
    if action_text is None:
        return None
    return f"{user.full_name} ({user.employee_id}) {action_text}"


def _detail_redirect(user: User, notice: str) -> RedirectResponse:
    return RedirectResponse(
        f"/admin/users/{user.id}?notice={notice}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _validate_action_csrf(request: Request, submitted_token: str) -> None:
    validate_csrf(request, submitted_token, request.app.state.settings)


@router.get("", name="admin_user_list")
def user_list(
    request: Request,
    db: DatabaseSession,
    admin: ManagementAdmin,
    branch_id: int | None = Query(default=None),
    role: str | None = Query(default=None),
    account_status: str | None = Query(default=None, alias="status"),
) -> Response:
    statement = _scoped_user_statement(admin)
    if admin.role is UserRole.SUPERADMIN:
        if branch_id is not None:
            statement = statement.where(User.branch_id == branch_id)
        if role in {item.value for item in UserRole}:
            statement = statement.where(User.role == UserRole(role))
        if account_status in {item.value for item in UserStatus}:
            statement = statement.where(User.status == UserStatus(account_status))
    users = list(db.scalars(statement.order_by(User.created_at.desc(), User.id.desc())))
    visible_user_ids = [user.id for user in users]
    requested_user_ids: set[int] = set()
    if visible_user_ids:
        requested_user_ids = set(
            db.scalars(
                select(PasswordResetRequest.user_id).where(
                    PasswordResetRequest.resolved_at.is_(None),
                    PasswordResetRequest.user_id.in_(visible_user_ids),
                )
            )
        )
    return render_template(
        request,
        "admin/users.html",
        {
            "current_user": admin,
            "users": users,
            "requested_user_ids": requested_user_ids,
            "branches": list_branches(db) if admin.role is UserRole.SUPERADMIN else [],
            "filters": {
                "branch_id": branch_id,
                "role": role or "",
                "status": account_status or "",
            },
        },
    )


@router.get("/create-branch-admin", name="create_branch_admin_form")
def branch_admin_form(
    request: Request,
    db: DatabaseSession,
    admin: SuperadminUser,
) -> Response:
    return render_template(
        request,
        "admin/create_branch_admin.html",
        {"current_user": admin, "branches": list_active_branches(db), "form": {}},
    )


@router.post("/create-branch-admin", name="create_branch_admin_action")
def branch_admin_create(
    request: Request,
    db: DatabaseSession,
    admin: SuperadminUser,
    csrf_token: FormValue,
    full_name: FormValue,
    employee_id: FormValue,
    mobile_number: FormValue,
    branch_id: FormId,
    email: Annotated[str, Form()] = "",
    temporary_password: FormValue = "",
    confirm_password: FormValue = "",
) -> Response:
    _validate_action_csrf(request, csrf_token)
    form_values = {
        "full_name": full_name,
        "employee_id": employee_id,
        "mobile_number": mobile_number,
        "email": email,
        "branch_id": branch_id,
    }
    try:
        data = BranchAdminCreateInput(
            **form_values,
            password=temporary_password,
            confirm_password=confirm_password,
        )
        user = create_branch_admin(db, data, _password_manager(request))
        db.commit()
    except ValidationError as exc:
        error = _validation_message(exc)
    except (PasswordPolicyError, UserConflictError, BranchSelectionError) as exc:
        db.rollback()
        error = str(exc)
    except IntegrityError:
        db.rollback()
        error = "Employee ID, Mobile Number, or Email is already registered."
    else:
        return _detail_redirect(user, "branch-admin-created")
    return render_template(
        request,
        "admin/create_branch_admin.html",
        {
            "current_user": admin,
            "branches": list_active_branches(db),
            "form": form_values,
            "error": error,
        },
        status_code=status.HTTP_400_BAD_REQUEST,
    )


@router.get("/{user_id}", name="admin_user_detail")
def user_detail(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: ManagementAdmin,
) -> Response:
    user = _get_scoped_user_or_404(db, admin, user_id)
    reset_requested = db.scalar(
        select(PasswordResetRequest.id).where(
            PasswordResetRequest.user_id == user.id,
            PasswordResetRequest.resolved_at.is_(None),
        ).limit(1)
    ) is not None
    return render_template(
        request,
        "admin/user_detail.html",
        {
            "current_user": admin,
            "user": user,
            "branches": list_active_branches(db) if admin.role is UserRole.SUPERADMIN else [],
            "active_session_count": count_active_sessions(db, user.id),
            "reset_requested": reset_requested,
            "message": _action_message(request.query_params.get("notice"), user),
        },
    )


@router.post("/{user_id}/branch", name="admin_reassign_branch")
def reassign_branch(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: SuperadminUser,
    csrf_token: FormValue,
    branch_id: FormId,
) -> Response:
    _validate_action_csrf(request, csrf_token)
    user = _get_scoped_user_or_404(db, admin, user_id)
    try:
        reassign_user_branch(db, user, branch_id)
        db.commit()
    except (BranchSelectionError, UserActionError) as exc:
        db.rollback()
        return render_template(
            request,
            "admin/user_detail.html",
            {
                "current_user": admin,
                "user": user,
                "branches": list_active_branches(db),
                "active_session_count": count_active_sessions(db, user.id),
                "reset_requested": False,
                "error": str(exc),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _detail_redirect(user, "branch-updated")


@router.post("/{user_id}/activate", name="admin_activate_user")
def activate(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: ManagementAdmin,
    csrf_token: FormValue,
) -> RedirectResponse:
    _validate_action_csrf(request, csrf_token)
    user = _get_scoped_user_or_404(db, admin, user_id)
    activate_user(user)
    db.commit()
    return _detail_redirect(user, "activated")


@router.post("/{user_id}/disable", name="admin_disable_user")
def disable(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: ManagementAdmin,
    csrf_token: FormValue,
) -> RedirectResponse:
    _validate_action_csrf(request, csrf_token)
    user = _get_scoped_user_or_404(db, admin, user_id)
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The currently authenticated Superadmin cannot disable this account.",
        )
    disable_user(db, user)
    db.commit()
    return _detail_redirect(user, "disabled")


@router.post("/{user_id}/enable", name="admin_enable_user")
def enable(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: ManagementAdmin,
    csrf_token: FormValue,
) -> RedirectResponse:
    _validate_action_csrf(request, csrf_token)
    user = _get_scoped_user_or_404(db, admin, user_id)
    activate_user(user)
    db.commit()
    return _detail_redirect(user, "enabled")


@router.get("/{user_id}/reset-password", name="admin_reset_password_form")
def reset_password_form(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: ManagementAdmin,
) -> Response:
    user = _get_scoped_user_or_404(db, admin, user_id)
    return render_template(
        request,
        "admin/reset_password.html",
        {"current_user": admin, "user": user},
    )


@router.post("/{user_id}/reset-password", name="admin_reset_password")
def reset_password(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: ManagementAdmin,
    csrf_token: FormValue,
    temporary_password: FormValue,
    confirm_password: FormValue,
) -> Response:
    _validate_action_csrf(request, csrf_token)
    user = _get_scoped_user_or_404(db, admin, user_id)
    try:
        data = AdminPasswordResetInput(
            temporary_password=temporary_password,
            confirm_password=confirm_password,
        )
        reset_user_password(
            db,
            user,
            data.temporary_password,
            _password_manager(request),
            resolved_by=admin,
        )
        db.commit()
    except ValidationError as exc:
        error = _validation_message(exc)
    except (PasswordPolicyError, UserActionError) as exc:
        db.rollback()
        error = str(exc)
    else:
        return _detail_redirect(user, "password-reset")
    return render_template(
        request,
        "admin/reset_password.html",
        {"current_user": admin, "user": user, "error": error},
        status_code=status.HTTP_400_BAD_REQUEST,
    )


@router.post("/{user_id}/force-logout", name="admin_force_logout")
def force_logout(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: ManagementAdmin,
    csrf_token: FormValue,
) -> RedirectResponse:
    _validate_action_csrf(request, csrf_token)
    user = _get_scoped_user_or_404(db, admin, user_id)
    revoke_all_user_sessions(db, user.id)
    db.commit()
    return _detail_redirect(user, "force-logout")
