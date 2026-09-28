"""Opaque server-side session creation, validation, and revocation."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload
from starlette.responses import Response

from app.auth.models import UserSession
from app.core.config import AppSettings
from app.core.time import utc_now
from app.users.models import User, UserStatus


def digest_session_token(raw_token: str) -> str:
    """Return the one-way digest stored in MySQL."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IssuedSession:
    """A newly issued raw browser token and its persisted session row."""

    raw_token: str
    record: UserSession


def create_session(
    db: Session,
    user: User,
    settings: AppSettings,
    *,
    user_agent: str | None = None,
) -> IssuedSession:
    """Create a persistent session while storing only the token digest."""
    raw_token = secrets.token_urlsafe(32)
    now = utc_now()
    record = UserSession(
        user_id=user.id,
        token_digest=digest_session_token(raw_token),
        created_at=now,
        last_used_at=now,
        expires_at=now + timedelta(days=settings.session_ttl_days),
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(record)
    db.flush()
    return IssuedSession(raw_token=raw_token, record=record)


def find_valid_session(
    db: Session,
    raw_token: str,
    settings: AppSettings,
) -> UserSession | None:
    """Resolve and conditionally touch a valid active-user session."""
    token_digest = digest_session_token(raw_token)
    record = db.scalar(
        select(UserSession)
        .options(joinedload(UserSession.user))
        .where(UserSession.token_digest == token_digest)
    )
    if record is None or record.revoked_at is not None:
        return None

    now = utc_now()
    if record.expires_at <= now:
        record.revoked_at = now
        return None
    if record.user.status is not UserStatus.ACTIVE:
        record.revoked_at = now
        return None

    touch_after = timedelta(seconds=settings.session_touch_interval_seconds)
    if record.last_used_at + touch_after <= now:
        record.last_used_at = now
    return record


def revoke_session_by_token(db: Session, raw_token: str | None) -> None:
    """Revoke the current session when its raw cookie token is available."""
    if not raw_token:
        return
    now = utc_now()
    db.execute(
        update(UserSession)
        .where(
            UserSession.token_digest == digest_session_token(raw_token),
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


def revoke_all_user_sessions(db: Session, user_id: int) -> int:
    """Revoke all currently live sessions belonging to a user."""
    result = db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
    return int(result.rowcount or 0)


def set_session_cookie(
    response: Response,
    raw_token: str,
    settings: AppSettings,
) -> None:
    """Send an opaque session token in a secure host-only browser cookie."""
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.session_secure_cookie,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: AppSettings) -> None:
    """Expire the browser session cookie during logout or invalidation."""
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.session_secure_cookie,
        samesite="lax",
        path="/",
    )
