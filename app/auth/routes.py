"""Server-rendered signup, login, recovery, password, and logout routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Form, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from starlette.responses import RedirectResponse, Response

from app.auth.csrf import validate_csrf
from app.auth.dependencies import AuthenticatedUser, DatabaseSession, OptionalUser
from app.auth.passwords import PasswordManager, PasswordPolicyError
from app.auth.sessions import (
    clear_session_cookie,
    create_session,
    revoke_session_by_token,
    set_session_cookie,
)
from app.branches.services import BranchSelectionError, list_active_branches
from app.core.templates import render_template
from app.users.schemas import LoginInput, PasswordChangeInput, SignupInput
from app.users.services import (
    UserActionError,
    UserConflictError,
    authenticate_user,
    change_user_password,
    create_supervisor,
    request_password_reset,
)

router = APIRouter(prefix="/auth", tags=["authentication"])
FormValue = Annotated[str, Form()]


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors(include_input=False, include_url=False)
    return errors[0]["msg"] if errors else "Invalid form submission."


def _password_manager(request: Request) -> PasswordManager:
    return request.app.state.password_manager


def _form_context(form: Any, *, error: str | None = None) -> dict[str, Any]:
    return {"form": form, "error": error}


@router.get("/signup", name="signup_form")
def signup_form(request: Request, db: DatabaseSession, user: OptionalUser) -> Response:
    if user is not None:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return render_template(
        request,
        "auth/signup.html",
        {**_form_context({}), "branches": list_active_branches(db)},
    )


@router.post("/signup", name="signup")
def signup(
    request: Request,
    db: DatabaseSession,
    csrf_token: FormValue,
    full_name: FormValue,
    employee_id: FormValue,
    mobile_number: FormValue,
    branch_id: FormValue,
    email: Annotated[str, Form()] = "",
    password: FormValue = "",
    confirm_password: FormValue = "",
) -> Response:
    settings = request.app.state.settings
    validate_csrf(request, csrf_token, settings)
    form_values = {
        "full_name": full_name,
        "employee_id": employee_id,
        "mobile_number": mobile_number,
        "email": email,
        "branch_id": branch_id,
    }
    try:
        data = SignupInput(
            **form_values,
            password=password,
            confirm_password=confirm_password,
        )
        create_supervisor(db, data, _password_manager(request))
        db.commit()
    except ValidationError as exc:
        return render_template(
            request,
            "auth/signup.html",
            {
                **_form_context(form_values, error=_validation_message(exc)),
                "branches": list_active_branches(db),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except (PasswordPolicyError, UserConflictError, BranchSelectionError) as exc:
        db.rollback()
        return render_template(
            request,
            "auth/signup.html",
            {
                **_form_context(form_values, error=str(exc)),
                "branches": list_active_branches(db),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except IntegrityError:
        db.rollback()
        return render_template(
            request,
            "auth/signup.html",
            {
                **_form_context(
                    form_values,
                    error="Employee ID, Mobile Number, or Email is already registered.",
                ),
                "branches": list_active_branches(db),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return RedirectResponse(
        "/auth/login?registered=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/login", name="login_form")
def login_form(request: Request, user: OptionalUser) -> Response:
    if user is not None:
        destination = "/auth/change-password" if user.must_change_password else "/dashboard"
        return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)
    return render_template(
        request,
        "auth/login.html",
        {"registered": request.query_params.get("registered") == "1", "form": {}},
    )


@router.post("/login", name="login")
def login(
    request: Request,
    db: DatabaseSession,
    csrf_token: FormValue,
    identifier: FormValue,
    password: FormValue,
) -> Response:
    settings = request.app.state.settings
    validate_csrf(request, csrf_token, settings)
    try:
        data = LoginInput(identifier=identifier, password=password)
    except ValidationError:
        data = None

    user = (
        authenticate_user(db, data.identifier, data.password, _password_manager(request))
        if data is not None
        else None
    )
    if user is None:
        db.rollback()
        return render_template(
            request,
            "auth/login.html",
            {
                "form": {"identifier": identifier},
                "error": "Invalid login credentials or account unavailable.",
                "registered": False,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    issued = create_session(
        db,
        user,
        settings,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    destination = "/auth/change-password" if user.must_change_password else "/dashboard"
    response = RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, issued.raw_token, settings)
    return response


@router.get("/forgot-password", name="forgot_password_form")
def forgot_password_form(request: Request) -> Response:
    return render_template(request, "auth/forgot_password.html", {"submitted": False})


@router.post("/forgot-password", name="forgot_password")
def forgot_password(
    request: Request,
    db: DatabaseSession,
    csrf_token: FormValue,
    identifier: FormValue,
) -> Response:
    settings = request.app.state.settings
    validate_csrf(request, csrf_token, settings)
    try:
        request_password_reset(db, identifier)
        db.commit()
    except ValueError:
        db.rollback()
    return render_template(
        request,
        "auth/forgot_password.html",
        {"submitted": True},
    )


@router.get("/change-password", name="change_password_form")
def change_password_form(request: Request, user: AuthenticatedUser) -> Response:
    return render_template(
        request,
        "auth/change_password.html",
        {"current_user": user, "required": user.must_change_password},
    )


@router.post("/change-password", name="change_password")
def change_password(
    request: Request,
    db: DatabaseSession,
    user: AuthenticatedUser,
    csrf_token: FormValue,
    current_password: FormValue,
    new_password: FormValue,
    confirm_password: FormValue,
) -> Response:
    settings = request.app.state.settings
    validate_csrf(request, csrf_token, settings)
    try:
        data = PasswordChangeInput(
            current_password=current_password,
            new_password=new_password,
            confirm_password=confirm_password,
        )
        change_user_password(
            db,
            user,
            data.current_password,
            data.new_password,
            _password_manager(request),
        )
        issued = create_session(
            db,
            user,
            settings,
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
    except ValidationError as exc:
        return render_template(
            request,
            "auth/change_password.html",
            {
                "current_user": user,
                "required": user.must_change_password,
                "error": _validation_message(exc),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except (PasswordPolicyError, UserActionError) as exc:
        db.rollback()
        return render_template(
            request,
            "auth/change_password.html",
            {
                "current_user": user,
                "required": user.must_change_password,
                "error": str(exc),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, issued.raw_token, settings)
    return response


@router.post("/logout", name="logout")
def logout(
    request: Request,
    db: DatabaseSession,
    csrf_token: FormValue,
) -> Response:
    settings = request.app.state.settings
    validate_csrf(request, csrf_token, settings)
    revoke_session_by_token(db, request.cookies.get(settings.session_cookie_name))
    db.commit()
    response = RedirectResponse("/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, settings)
    return response
