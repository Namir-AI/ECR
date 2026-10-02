"""Relational ECR package, tower, and cell-report identity models."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now
from app.db.base import Base

if TYPE_CHECKING:
    from app.branches.models import Branch
    from app.users.models import User


class EcrReportStatus(StrEnum):
    """Approved ECR workflow states; Phase 3 creates only DRAFT."""

    DRAFT = "DRAFT"
    REVIEWED = "REVIEWED"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"


class EcrPackage(Base):
    """Shared identity and customer data for one Cooling Tower Serial No."""

    __tablename__ = "ecr_packages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cooling_tower_serial_no: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_serial_no: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )
    customer: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_order_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cooling_tower_series: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    place_of_installation: Mapped[str] = mapped_column(String(250), nullable=False)
    multiple_towers: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
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

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_user_id])
    towers: Mapped[list[EcrTower]] = relationship(back_populates="package")


class EcrTower(Base):
    """One logical tower within an ECR package."""

    __tablename__ = "ecr_towers"
    __table_args__ = (
        UniqueConstraint(
            "package_id",
            "normalized_suffix_key",
            name="uq_ecr_towers_package_suffix_key",
        ),
        CheckConstraint(
            "declared_no_of_cells IS NULL OR declared_no_of_cells > 0",
            name="declared_no_of_cells_positive",
        ),
        CheckConstraint(
            "(tower_suffix IS NULL AND normalized_suffix_key = '') OR "
            "(tower_suffix IS NOT NULL AND normalized_suffix_key <> '')",
            name="suffix_key_consistent",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ecr_packages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tower_suffix: Mapped[str | None] = mapped_column(String(20), nullable=True)
    normalized_suffix_key: Mapped[str] = mapped_column(String(20), nullable=False)
    declared_no_of_cells: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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

    package: Mapped[EcrPackage] = relationship(back_populates="towers")
    reports: Mapped[list[EcrReport]] = relationship(back_populates="tower")

    @property
    def display_name(self) -> str:
        suffix = f" {self.tower_suffix}" if self.tower_suffix else ""
        return f"{self.package.cooling_tower_serial_no}{suffix}"


class EcrReport(Base):
    """One cell-level Erection & Commissioning report."""

    __tablename__ = "ecr_reports"
    __table_args__ = (
        UniqueConstraint("tower_id", "cell_no", name="uq_ecr_reports_tower_cell"),
        CheckConstraint("cell_no > 0", name="cell_no_positive"),
        Index("ix_ecr_reports_supervisor_updated", "supervisor_user_id", "updated_at"),
        Index("ix_ecr_reports_branch_updated", "branch_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tower_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ecr_towers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    cell_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    supervisor_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    branch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    erection_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    erection_completion_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[EcrReportStatus] = mapped_column(
        Enum(
            EcrReportStatus,
            name="ecr_report_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        default=EcrReportStatus.DRAFT,
        index=True,
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

    tower: Mapped[EcrTower] = relationship(back_populates="reports")
    supervisor: Mapped[User] = relationship(foreign_keys=[supervisor_user_id])
    branch: Mapped[Branch] = relationship(foreign_keys=[branch_id])

    @property
    def display_identity(self) -> str:
        return f"{self.tower.display_name} / Cell-{self.cell_no}"
