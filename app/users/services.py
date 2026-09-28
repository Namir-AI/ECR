"""Reusable user, authentication, and Admin account services."""

from dataclasses import dataclass

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.auth.models import UserSession
from app.auth.passwords import PasswordManager
from app.auth.sessions import revoke_all_user_sessions
from app.core.time import utc_now
from app.users.models import PasswordResetRequest, User, UserRole, UserStatus
from app.users.schemas import (
    SignupInput,
    normalize_employee_id,
    normalize_full_name,
    normalize_mobile_number,
)


class UserConflictError(ValueError):
    """Raised for owner-visible uniqueness conflicts."""


class UserActionError(ValueError):
    """Raised when an account transition is not allowed."""


@dataclass(frozen=True)
class AdminBootstrapInput:
    """Validated values needed by the initial Admin command."""

    full_name: str
    employee_id: str
    mobile_number: str
    email: str | None
    password: str


def _existing_unique_field(
    db: Session,
    *,
    employee_id: str,
    mobile_number: str,
    email: str | None,
) -> str | None:
    conditions = [
        User.employee_id == employee_id,
        User.mobile_number == mobile_number,
    ]
    if email is not None:
        conditions.append(User.email == email)

    existing = db.scalars(select(User).where(or_(*conditions))).all()
    for user in existing:
        if user.employee_id == employee_id:
            return "Employee ID"
        if user.mobile_number == mobile_number:
            return "Mobile Number"
        if email is not None and user.email == email:
            return "Email"
    return None


def create_supervisor(
    db: Session,
    data: SignupInput,
    password_manager: PasswordManager,
) -> User:
    """Create a pending supervisor after enforcing normalized uniqueness."""
    conflict = _existing_unique_field(
        db,
        employee_id=data.employee_id,
        mobile_number=data.mobile_number,
        email=data.email,
    )
    if conflict:
        raise UserConflictError(f"{conflict} is already registered.")

    user = User(
        full_name=data.full_name,
        employee_id=data.employee_id,
        mobile_number=data.mobile_number,
        email=data.email,
        password_hash=password_manager.hash(data.password),
        role=UserRole.SUPERVISOR,
        status=UserStatus.PENDING,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    return user


def find_user_by_identifier(db: Session, identifier: str) -> User | None:
    """Find a user by normalized Employee ID or Mobile Number."""
    employee_id = normalize_employee_id(identifier)
    try:
        mobile_number = normalize_mobile_number(identifier)
    except ValueError:
        mobile_number = ""
    return db.scalar(
        select(User).where(
            or_(
                User.employee_id == employee_id,
                User.mobile_number == mobile_number,
            )
        )
    )


def authenticate_user(
    db: Session,
    identifier: str,
    password: str,
    password_manager: PasswordManager,
) -> User | None:
    """Authenticate without revealing account existence or inactive status."""
    user = find_user_by_identifier(db, identifier)
    if user is None:
        password_manager.verify_dummy(password)
        return None

    password_valid = password_manager.verify(user.password_hash, password)
    if not password_valid or user.status is not UserStatus.ACTIVE:
        return None

    if password_manager.needs_rehash(user.password_hash):
        user.password_hash = password_manager.hash(password)
    user.last_login_at = utc_now()
    return user


def activate_user(user: User) -> None:
    """Activate a pending or disabled user."""
    user.status = UserStatus.ACTIVE


def disable_user(db: Session, user: User) -> None:
    """Disable an account and invalidate every session."""
    user.status = UserStatus.DISABLED
    revoke_all_user_sessions(db, user.id)


def reset_user_password(
    db: Session,
    user: User,
    temporary_password: str,
    password_manager: PasswordManager,
    *,
    resolved_by: User,
) -> None:
    """Replace the password, force a subsequent change, and revoke sessions."""
    user.password_hash = password_manager.hash(temporary_password)
    user.must_change_password = True
    revoke_all_user_sessions(db, user.id)
    db.execute(
        update(PasswordResetRequest)
        .where(
            PasswordResetRequest.user_id == user.id,
            PasswordResetRequest.resolved_at.is_(None),
        )
        .values(resolved_at=utc_now(), resolved_by_user_id=resolved_by.id)
    )


def recover_admin_password(
    db: Session,
    employee_id: str,
    new_password: str,
    password_manager: PasswordManager,
) -> User:
    """Reset an Admin password from trusted server-side recovery tooling."""
    normalized_employee_id = normalize_employee_id(employee_id)
    user = db.scalar(select(User).where(User.employee_id == normalized_employee_id))
    if user is None:
        raise UserActionError("No user exists with that Employee ID.")
    if user.role is not UserRole.ADMIN:
        raise UserActionError("The selected user is not an Admin.")

    user.password_hash = password_manager.hash(new_password)
    user.must_change_password = False
    revoke_all_user_sessions(db, user.id)
    db.execute(
        update(PasswordResetRequest)
        .where(
            PasswordResetRequest.user_id == user.id,
            PasswordResetRequest.resolved_at.is_(None),
        )
        .values(resolved_at=utc_now(), resolved_by_user_id=user.id)
    )
    return user


def change_user_password(
    db: Session,
    user: User,
    current_password: str,
    new_password: str,
    password_manager: PasswordManager,
) -> None:
    """Change an authenticated password and revoke all prior sessions."""
    if not password_manager.verify(user.password_hash, current_password):
        raise UserActionError("Current password is incorrect.")
    if password_manager.verify(user.password_hash, new_password):
        raise UserActionError("New password must be different from the current password.")

    user.password_hash = password_manager.hash(new_password)
    user.must_change_password = False
    revoke_all_user_sessions(db, user.id)


def request_password_reset(db: Session, identifier: str) -> None:
    """Record an internal request only when the supplied account exists."""
    user = find_user_by_identifier(db, identifier)
    if user is None:
        return

    existing = db.scalar(
        select(PasswordResetRequest).where(
            PasswordResetRequest.user_id == user.id,
            PasswordResetRequest.resolved_at.is_(None),
        )
    )
    if existing is None:
        db.add(PasswordResetRequest(user_id=user.id))
    else:
        existing.requested_at = utc_now()


def create_initial_admin(
    db: Session,
    data: AdminBootstrapInput,
    password_manager: PasswordManager,
) -> User:
    """Create the first active Admin and reject repeated bootstrapping."""
    existing_admin = db.scalar(select(User.id).where(User.role == UserRole.ADMIN).limit(1))
    if existing_admin is not None:
        raise UserActionError("An Admin account already exists.")

    full_name = normalize_full_name(data.full_name)
    employee_id = normalize_employee_id(data.employee_id)
    mobile_number = normalize_mobile_number(data.mobile_number)
    email = data.email.strip().lower() if data.email else None
    conflict = _existing_unique_field(
        db,
        employee_id=employee_id,
        mobile_number=mobile_number,
        email=email,
    )
    if conflict:
        raise UserConflictError(f"{conflict} is already registered.")

    admin = User(
        full_name=full_name,
        employee_id=employee_id,
        mobile_number=mobile_number,
        email=email,
        password_hash=password_manager.hash(data.password),
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        must_change_password=False,
    )
    db.add(admin)
    db.flush()
    return admin


def count_active_sessions(db: Session, user_id: int) -> int:
    """Return a user's currently unrevoked, unexpired session count."""
    now = utc_now()
    return int(
        db.scalar(
            select(func.count())
            .select_from(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
        )
        or 0
    )
