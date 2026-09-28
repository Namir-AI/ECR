"""Admin-only user management routes."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from starlette.responses import RedirectResponse, Response

from app.auth.csrf import validate_csrf
from app.auth.dependencies import AdminUser, DatabaseSession
from app.auth.passwords import PasswordManager, PasswordPolicyError
from app.auth.sessions import revoke_all_user_sessions
from app.core.templates import render_template
from app.users.models import PasswordResetRequest, User
from app.users.schemas import AdminPasswordResetInput
from app.users.services import (
    UserActionError,
    activate_user,
    count_active_sessions,
    disable_user,
    reset_user_password,
)

router = APIRouter(prefix="/admin/users", tags=["admin-users"])
FormValue = Annotated[str, Form()]

_ACTION_NOTICES = {
    "activated": "has been activated.",
    "disabled": "has been disabled.",
    "enabled": "has been re-enabled.",
    "password-reset": "password has been reset.",
    "force-logout": "has been forced to log out.",
}


def _password_manager(request: Request) -> PasswordManager:
    return request.app.state.password_manager


def _get_user_or_404(db: DatabaseSession, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors(include_input=False, include_url=False)
    return errors[0]["msg"] if errors else "Invalid form submission."


def _action_message(notice: str | None, user: User) -> str | None:
    """Build a notice from a fixed action code and trusted database values."""
    action_text = _ACTION_NOTICES.get(notice or "")
    if action_text is None:
        return None
    return f"{user.full_name} ({user.employee_id}) {action_text}"


def _detail_redirect(user: User, notice: str) -> RedirectResponse:
    return RedirectResponse(
        f"/admin/users/{user.id}?notice={notice}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("", name="admin_user_list")
def user_list(request: Request, db: DatabaseSession, admin: AdminUser) -> Response:
    users = db.scalars(select(User).order_by(User.created_at.desc(), User.id.desc())).all()
    pending_requests = db.scalars(
        select(PasswordResetRequest)
        .where(PasswordResetRequest.resolved_at.is_(None))
        .order_by(PasswordResetRequest.requested_at.asc())
    ).all()
    requested_user_ids = {item.user_id for item in pending_requests}
    return render_template(
        request,
        "admin/users.html",
        {
            "current_user": admin,
            "users": users,
            "requested_user_ids": requested_user_ids,
        },
    )


@router.get("/{user_id}", name="admin_user_detail")
def user_detail(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: AdminUser,
) -> Response:
    user = _get_user_or_404(db, user_id)
    return render_template(
        request,
        "admin/user_detail.html",
        {
            "current_user": admin,
            "user": user,
            "active_session_count": count_active_sessions(db, user.id),
            "message": _action_message(request.query_params.get("notice"), user),
        },
    )


def _validate_action_csrf(request: Request, submitted_token: str) -> None:
    validate_csrf(request, submitted_token, request.app.state.settings)


@router.post("/{user_id}/activate", name="admin_activate_user")
def activate(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: AdminUser,
    csrf_token: FormValue,
) -> RedirectResponse:
    _validate_action_csrf(request, csrf_token)
    user = _get_user_or_404(db, user_id)
    activate_user(user)
    db.commit()
    return _detail_redirect(user, "activated")


@router.post("/{user_id}/disable", name="admin_disable_user")
def disable(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: AdminUser,
    csrf_token: FormValue,
) -> RedirectResponse:
    _validate_action_csrf(request, csrf_token)
    user = _get_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An Admin cannot disable the currently authenticated account.",
        )
    disable_user(db, user)
    db.commit()
    return _detail_redirect(user, "disabled")


@router.post("/{user_id}/enable", name="admin_enable_user")
def enable(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: AdminUser,
    csrf_token: FormValue,
) -> RedirectResponse:
    _validate_action_csrf(request, csrf_token)
    user = _get_user_or_404(db, user_id)
    activate_user(user)
    db.commit()
    return _detail_redirect(user, "enabled")


@router.get("/{user_id}/reset-password", name="admin_reset_password_form")
def reset_password_form(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: AdminUser,
) -> Response:
    user = _get_user_or_404(db, user_id)
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
    admin: AdminUser,
    csrf_token: FormValue,
    temporary_password: FormValue,
    confirm_password: FormValue,
) -> Response:
    _validate_action_csrf(request, csrf_token)
    user = _get_user_or_404(db, user_id)
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
        return render_template(
            request,
            "admin/reset_password.html",
            {"current_user": admin, "user": user, "error": _validation_message(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except (PasswordPolicyError, UserActionError) as exc:
        db.rollback()
        return render_template(
            request,
            "admin/reset_password.html",
            {"current_user": admin, "user": user, "error": str(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _detail_redirect(user, "password-reset")


@router.post("/{user_id}/force-logout", name="admin_force_logout")
def force_logout(
    request: Request,
    user_id: int,
    db: DatabaseSession,
    admin: AdminUser,
    csrf_token: FormValue,
) -> RedirectResponse:
    _validate_action_csrf(request, csrf_token)
    user = _get_user_or_404(db, user_id)
    revoke_all_user_sessions(db, user.id)
    db.commit()
    return _detail_redirect(user, "force-logout")
