"""Reusable server-side authentication and role authorization dependencies."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.models import UserSession
from app.auth.sessions import find_valid_session
from app.db.session import get_db_session
from app.users.models import User, UserRole

DatabaseSession = Annotated[Session, Depends(get_db_session)]


def get_optional_user(request: Request, db: DatabaseSession) -> User | None:
    """Resolve an active session without requiring authentication."""
    settings = request.app.state.settings
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        return None

    auth_session = find_valid_session(db, raw_token, settings)
    if db.dirty:
        db.commit()
    if auth_session is None:
        return None

    request.state.auth_session = auth_session
    return auth_session.user


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def require_authenticated_user(user: OptionalUser) -> User:
    """Require a currently active, authenticated user."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/auth/login"},
        )
    return user


AuthenticatedUser = Annotated[User, Depends(require_authenticated_user)]


def require_password_ready_user(user: AuthenticatedUser) -> User:
    """Block normal application access until a required password change."""
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/auth/change-password"},
        )
    return user


PasswordReadyUser = Annotated[User, Depends(require_password_ready_user)]


def require_superadmin(user: PasswordReadyUser) -> User:
    """Require global Superadmin authority."""
    if user.role is not UserRole.SUPERADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user


SuperadminUser = Annotated[User, Depends(require_superadmin)]


def require_branch_admin_or_superadmin(user: PasswordReadyUser) -> User:
    """Require an administrator with either global or branch scope."""
    if user.role not in {UserRole.SUPERADMIN, UserRole.BRANCH_ADMIN}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user


ManagementAdmin = Annotated[User, Depends(require_branch_admin_or_superadmin)]


def require_supervisor(user: PasswordReadyUser) -> User:
    """Require the Supervisor role for future supervisor-only routes."""
    if user.role is not UserRole.SUPERVISOR:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user


SupervisorUser = Annotated[User, Depends(require_supervisor)]


def get_authenticated_session(request: Request) -> UserSession | None:
    """Return the resolved session attached by authentication dependencies."""
    return getattr(request.state, "auth_session", None)
