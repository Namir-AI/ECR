"""Relational ECR package, tower, and cell-report identity models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME, MEDIUMTEXT
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
    page1: Mapped[EcrPage1Technical | None] = relationship(
        back_populates="report", uselist=False
    )
    page2: Mapped[EcrPage2Technical | None] = relationship(
        back_populates="report", uselist=False
    )
    page3: Mapped[EcrPage3 | None] = relationship(
        back_populates="report", uselist=False
    )
    fastener_rows: Mapped[list[EcrFastenerTorqueRow]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="(EcrFastenerTorqueRow.category, EcrFastenerTorqueRow.sequence_no)",
    )

    @property
    def display_identity(self) -> str:
        return f"{self.tower.display_name} / Cell-{self.cell_no}"


class EcrPage3(Base):
    """Completion text and private signature reference, nullable for Drafts."""

    __tablename__ = "ecr_page3"
    __table_args__ = (
        CheckConstraint(
            "(customer_signature_storage_key IS NULL AND customer_signed_at IS NULL) OR "
            "(customer_signature_storage_key IS NOT NULL AND customer_signed_at IS NOT NULL)",
            name="signature_timestamp_consistent",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ecr_reports.id", ondelete="RESTRICT"), unique=True
    )
    team_leader_report: Mapped[str | None] = mapped_column(
        Text().with_variant(MEDIUMTEXT(), "mysql")
    )
    customer_comment: Mapped[str | None] = mapped_column(
        Text().with_variant(MEDIUMTEXT(), "mysql")
    )
    customer_signature_storage_key: Mapped[str | None] = mapped_column(String(64))
    customer_signed_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )
    report: Mapped[EcrReport] = relationship(back_populates="page3")


class EcrPage1Technical(Base):
    """Nullable scalar capture for partial Page 1 Drafts; no engineering defaults."""

    __tablename__ = "ecr_page1_technical"
    __table_args__ = (
        CheckConstraint(
            "fan_no_of_blades IS NULL OR fan_no_of_blades > 0", name="blades_positive"
        ),
        CheckConstraint(
            "drive_shaft_oal IS NULL OR drive_shaft_oal_unit IS NOT NULL",
            name="oal_has_unit",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ecr_reports.id", ondelete="RESTRICT"), unique=True
    )
    motor_make: Mapped[str | None] = mapped_column(String(250))
    motor_serial_no: Mapped[str | None] = mapped_column(String(250), index=True)
    motor_hp: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    motor_frame: Mapped[str | None] = mapped_column(String(250))
    motor_insulation: Mapped[str | None] = mapped_column(String(250))
    motor_mounting: Mapped[str | None] = mapped_column(String(20))
    motor_speed: Mapped[str | None] = mapped_column(String(20))
    motor_rpm: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    motor_full_load_current: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    motor_current_drawn: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    fan_serial_no: Mapped[str | None] = mapped_column(String(250), index=True)
    fan_diameter_type: Mapped[str | None] = mapped_column(String(250))
    fan_hardware: Mapped[str | None] = mapped_column(String(20))
    fan_no_of_blades: Mapped[int | None] = mapped_column(BigInteger)
    fan_pitch_angle: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    fan_hub_cover: Mapped[str | None] = mapped_column(String(20))
    fan_cylinder_height: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    fan_cylinder_material: Mapped[str | None] = mapped_column(String(20))
    blade_tip_clearance: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    blade_tip_track_variation: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    drive_shaft_series: Mapped[str | None] = mapped_column(String(250))
    drive_shaft_class: Mapped[str | None] = mapped_column(String(20))
    drive_shaft_oal: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    drive_shaft_oal_unit: Mapped[str | None] = mapped_column(String(20))
    drive_shaft_serial_no: Mapped[str | None] = mapped_column(String(250), index=True)
    # Deliberately strings: final Series/Ratio lookup lists are owner-deferred.
    gearbox_series: Mapped[str | None] = mapped_column(String(250))
    gearbox_ratio: Mapped[str | None] = mapped_column(String(250))
    gearbox_serial_no: Mapped[str | None] = mapped_column(String(250), index=True)
    gearbox_model_no: Mapped[str | None] = mapped_column(String(250))
    fill_type: Mapped[str | None] = mapped_column(String(250))
    fill_material: Mapped[str | None] = mapped_column(String(20))
    report: Mapped[EcrReport] = relationship(back_populates="page1")
    blade_serials: Mapped[list[EcrFanBladeSerial]] = relationship(
        back_populates="technical",
        order_by="EcrFanBladeSerial.position",
        cascade="all, delete-orphan",
    )


class EcrFanBladeSerial(Base):
    """Genuinely repeating optional blade identifiers with stable display order."""

    __tablename__ = "ecr_fan_blade_serials"
    __table_args__ = (
        UniqueConstraint("technical_id", "position", name="uq_ecr_blade_position"),
        CheckConstraint("position > 0", name="position_positive"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    technical_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ecr_page1_technical.id", ondelete="RESTRICT")
    )
    position: Mapped[int] = mapped_column(BigInteger)
    serial_no: Mapped[str] = mapped_column(String(250))
    technical: Mapped[EcrPage1Technical] = relationship(back_populates="blade_serials")


class EcrPage2Technical(Base):
    """Page 2 nullable Draft capture without engineering defaults."""

    __tablename__ = "ecr_page2_technical"
    __table_args__ = (
        CheckConstraint("fc_valve_diameter >= 0", name="valve_diameter_nonnegative"),
        CheckConstraint("small_pulley_od >= 0", name="small_pulley_nonnegative"),
        CheckConstraint("large_pulley_od >= 0", name="large_pulley_nonnegative"),
        CheckConstraint("fc_valve_count > 0", name="valve_count_positive"),
        CheckConstraint("nozzle_count_per_cell > 0", name="nozzle_count_positive"),
        CheckConstraint("belts_used_count > 0", name="belt_count_positive"),
        *[
            CheckConstraint(f"{name} BETWEEN -1 AND 1", name=f"{name}_range")
            for name in (
                "radial_tir",
                "axial_tir",
                "de_top",
                "de_right",
                "de_bottom",
                "de_left",
                "nde_top",
                "nde_right",
                "nde_bottom",
                "nde_left",
            )
        ],
        CheckConstraint("de_nde_unit IN ('inches', 'mm')", name="de_nde_unit_choice"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ecr_reports.id", ondelete="RESTRICT"), unique=True
    )
    eliminator_type: Mapped[str | None] = mapped_column(String(250))
    fc_valve_diameter: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    fc_valve_count: Mapped[int | None] = mapped_column(BigInteger)
    nozzle_type: Mapped[str | None] = mapped_column(String(250))
    nozzle_count_per_cell: Mapped[int | None] = mapped_column(BigInteger)
    nozzle_part_no: Mapped[str | None] = mapped_column(String(250))
    bearing_housing_type: Mapped[str | None] = mapped_column(String(250))
    bearing_housing_serial_no: Mapped[str | None] = mapped_column(String(250))
    belt_type: Mapped[str | None] = mapped_column(String(250))
    belt_section_length: Mapped[str | None] = mapped_column(String(250))
    small_pulley_od: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    large_pulley_od: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    belts_used_count: Mapped[int | None] = mapped_column(BigInteger)
    uniform_belt_tension: Mapped[str | None] = mapped_column(String(20))
    pulley_construction: Mapped[str | None] = mapped_column(String(30))
    oil_type: Mapped[str | None] = mapped_column(String(250))
    oil_level_checked: Mapped[str | None] = mapped_column(String(20))
    oil_seal_leakage: Mapped[str | None] = mapped_column(String(20))
    general_tower_hardware: Mapped[str | None] = mapped_column(String(20))
    radial_tir: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    axial_tir: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    de_nde_unit: Mapped[str | None] = mapped_column(String(10))
    de_top: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    de_right: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    de_bottom: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    de_left: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    nde_top: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    nde_right: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    nde_bottom: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    nde_left: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    vibration_limit_switch: Mapped[bool | None] = mapped_column(Boolean)
    oil_level_switch: Mapped[bool | None] = mapped_column(Boolean)
    report: Mapped[EcrReport] = relationship(back_populates="page2")


class EcrFastenerTorqueRow(Base):
    __tablename__ = "ecr_fastener_torque_rows"
    __table_args__ = (
        UniqueConstraint(
            "report_id", "category", "sequence_no", name="uq_ecr_torque_order"
        ),
        CheckConstraint(
            "category IN ('FAN_HARDWARE', 'TOWER', 'MECH_HOLD_DOWN')",
            name="category_choice",
        ),
        CheckConstraint("sequence_no > 0", name="sequence_positive"),
        CheckConstraint("torque_unit IN ('ft-lbs', 'Nm')", name="torque_unit_choice"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ecr_reports.id", ondelete="RESTRICT")
    )
    category: Mapped[str] = mapped_column(String(20))
    sequence_no: Mapped[int] = mapped_column(BigInteger)
    diameter: Mapped[str | None] = mapped_column(String(250))
    torque: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    torque_unit: Mapped[str | None] = mapped_column(String(10))
    report: Mapped[EcrReport] = relationship(back_populates="fastener_rows")
