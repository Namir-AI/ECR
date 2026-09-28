"""User and internal password-recovery persistence models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now
from app.db.base import Base

if TYPE_CHECKING:
    from app.auth.models import UserSession


class UserRole(StrEnum):
    """Roles approved for the current application phase."""

    SUPERVISOR = "SUPERVISOR"
    ADMIN = "ADMIN"


class UserStatus(StrEnum):
    """Account states used by authentication and administration."""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class User(Base):
    """Application user without any recoverable plaintext password."""

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_role_status", "role", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    employee_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    mobile_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        default=UserRole.SUPERVISOR,
    )
    status: Mapped[UserStatus] = mapped_column(
        Enum(
            UserStatus,
            name="user_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        default=UserStatus.PENDING,
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    password_reset_requests: Mapped[list[PasswordResetRequest]] = relationship(
        foreign_keys="PasswordResetRequest.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class PasswordResetRequest(Base):
    """Internal recovery request visible only to authorized administrators."""

    __tablename__ = "password_reset_requests"
    __table_args__ = (
        Index("ix_password_reset_requests_pending", "resolved_at", "requested_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped[User] = relationship(
        foreign_keys=[user_id],
        back_populates="password_reset_requests",
    )
    resolved_by: Mapped[User | None] = relationship(
        foreign_keys=[resolved_by_user_id],
    )
