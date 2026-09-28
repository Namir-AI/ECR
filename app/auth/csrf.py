"""CSRF protection for server-rendered cookie-authenticated forms."""

import hmac
import secrets

from fastapi import HTTPException, Request, Response, status

from app.core.config import AppSettings

CSRF_FIELD_NAME = "csrf_token"


def get_or_create_csrf_token(request: Request, settings: AppSettings) -> str:
    """Reuse a valid token cookie or create a new unpredictable token."""
    existing = request.cookies.get(settings.csrf_cookie_name)
    if existing and 32 <= len(existing) <= 128:
        return existing
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Response, token: str, settings: AppSettings) -> None:
    """Set a host-only CSRF cookie used with a matching hidden form value."""
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.session_secure_cookie,
        samesite="lax",
        path="/",
    )


def validate_csrf(request: Request, submitted_token: str | None, settings: AppSettings) -> None:
    """Reject state-changing requests without matching cookie/form tokens."""
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    if (
        not cookie_token
        or not submitted_token
        or not hmac.compare_digest(cookie_token, submitted_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token.",
        )
