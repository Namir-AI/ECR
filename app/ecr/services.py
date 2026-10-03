"""ECR identity, Draft creation, autosave, and scoped lookup services."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.ecr.models import (
    EcrPackage,
    EcrPage1Technical,
    EcrReport,
    EcrReportStatus,
    EcrTower,
)
from app.ecr.schemas import DraftAutosaveInput, DraftCreateInput, normalized_serial_key
from app.ecr.series import COOLING_TOWER_SERIES
from app.users.models import User, UserRole


class EcrIdentityError(ValueError):
    """Raised for safe, owner-visible ECR identity conflicts."""


class ExistingReportError(EcrIdentityError):
    """Raised when another report already owns a Tower + Cell identity."""


class DraftNotEditableError(EcrIdentityError):
    """Raised when an autosave targets a non-Draft report."""


@dataclass(frozen=True)
class DraftCreationResult:
    report: EcrReport
    resumed: bool


def _report_load_options():
    """Build eager-load options after the full application model registry is loaded."""
    return (
        joinedload(EcrReport.tower).joinedload(EcrTower.package),
        joinedload(EcrReport.supervisor),
        joinedload(EcrReport.branch),
        joinedload(EcrReport.page1).selectinload(EcrPage1Technical.blade_serials),
        joinedload(EcrReport.page2),
        joinedload(EcrReport.page3),
        selectinload(EcrReport.fastener_rows),
    )


def find_package_by_serial(
    db: Session, serial_no: str, *, lock: bool = False
) -> EcrPackage | None:
    statement = select(EcrPackage).where(
        EcrPackage.normalized_serial_no == normalized_serial_key(serial_no)
    )
    if lock:
        # Do not gap-lock a missing serial: concurrent first creation is handled
        # by the existing unique-constraint retry. Existing packages lock by PK.
        existing = db.scalar(statement)
        if existing is None:
            return None
        statement = (
            select(EcrPackage)
            .where(EcrPackage.id == existing.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    return db.scalar(statement)


def _validate_existing_package(package: EcrPackage, data: DraftCreateInput) -> None:
    submitted = {
        "customer": data.customer,
        "customer_order_no": data.customer_order_no,
        "cooling_tower_series": data.cooling_tower_series,
        "model": data.model,
        "place_of_installation": data.place_of_installation,
        "multiple_towers": data.multiple_towers,
    }
    existing = {field: getattr(package, field) for field in submitted}
    if submitted != existing:
        raise EcrIdentityError(
            "This Cooling Tower Serial No. already exists with different shared "
            "information. Existing package information was not changed."
        )


def _tower_identity(data: DraftCreateInput) -> tuple[str | None, str]:
    if data.multiple_towers:
        assert data.tower_suffix is not None
        return data.tower_suffix, data.tower_suffix
    return None, ""


def create_or_resume_draft(
    db: Session,
    supervisor: User,
    data: DraftCreateInput,
) -> DraftCreationResult:
    """Create the requested hierarchy or safely resume the owner's existing Draft."""
    if supervisor.role is not UserRole.SUPERVISOR:
        raise EcrIdentityError("Only a Supervisor can create an E&C Draft.")
    # Same package lock as Series edits: a concurrent second report cannot bypass
    # the sole-Draft restriction. Lock order is always package, then report.
    package = find_package_by_serial(db, data.cooling_tower_serial_no, lock=True)
    if package is None:
        if data.cooling_tower_series not in COOLING_TOWER_SERIES:
            raise EcrIdentityError("Select an approved Cooling Tower Series.")
        package = EcrPackage(
            cooling_tower_serial_no=data.cooling_tower_serial_no,
            normalized_serial_no=normalized_serial_key(data.cooling_tower_serial_no),
            customer=data.customer,
            customer_order_no=data.customer_order_no,
            cooling_tower_series=data.cooling_tower_series,
            model=data.model,
            place_of_installation=data.place_of_installation,
            multiple_towers=data.multiple_towers,
            created_by_user_id=supervisor.id,
        )
        db.add(package)
        db.flush()
    else:
        _validate_existing_package(package, data)

    tower_suffix, suffix_key = _tower_identity(data)
    tower = db.scalar(
        select(EcrTower).where(
            EcrTower.package_id == package.id,
            EcrTower.normalized_suffix_key == suffix_key,
        )
    )
    if tower is None:
        tower = EcrTower(
            package_id=package.id,
            tower_suffix=tower_suffix,
            normalized_suffix_key=suffix_key,
            declared_no_of_cells=data.declared_no_of_cells,
        )
        db.add(tower)
        db.flush()
    elif (
        data.declared_no_of_cells is not None
        and tower.declared_no_of_cells != data.declared_no_of_cells
    ):
        raise EcrIdentityError(
            "This tower already exists with a different declared No. of Cells. "
            "Existing tower information was not changed."
        )

    report = db.scalar(
        select(EcrReport)
        .options(*_report_load_options())
        .where(EcrReport.tower_id == tower.id, EcrReport.cell_no == data.cell_no)
    )
    if report is not None:
        if (
            report.supervisor_user_id == supervisor.id
            and report.status is EcrReportStatus.DRAFT
        ):
            return DraftCreationResult(report=report, resumed=True)
        raise ExistingReportError(
            "An E&C report already exists for this tower and cell. Please contact "
            "the Service Admin if access or correction is required."
        )

    report = EcrReport(
        tower_id=tower.id,
        cell_no=data.cell_no,
        supervisor_user_id=supervisor.id,
        branch_id=supervisor.branch_id,
        erection_start_date=data.erection_start_date,
        erection_completion_date=data.erection_completion_date,
        status=EcrReportStatus.DRAFT,
    )
    db.add(report)
    db.flush()
    return DraftCreationResult(report=report, resumed=False)


def get_supervisor_report(
    db: Session, report_id: int, supervisor_id: int, *, lock: bool = False
) -> EcrReport | None:
    if lock:
        package_id = db.scalar(
            select(EcrTower.package_id)
            .join(EcrReport)
            .where(
                EcrReport.id == report_id, EcrReport.supervisor_user_id == supervisor_id
            )
        )
        if package_id is None:
            return None
        db.scalar(
            select(EcrPackage)
            .where(EcrPackage.id == package_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    statement = (
        select(EcrReport)
        .options(*_report_load_options())
        .where(
            EcrReport.id == report_id,
            EcrReport.supervisor_user_id == supervisor_id,
        )
    )
    return db.scalar(
        statement.with_for_update().execution_options(populate_existing=True)
        if lock
        else statement
    )


def can_edit_series(
    db: Session, report: EcrReport, user: User, *, lock: bool = False
) -> bool:
    package = report.tower.package
    if (
        user.role is not UserRole.SUPERVISOR
        or package.created_by_user_id != user.id
        or report.supervisor_user_id != user.id
        or report.status is not EcrReportStatus.DRAFT
    ):
        return False
    query = select(EcrReport.id).join(EcrTower).where(EcrTower.package_id == package.id)
    # A locking/current read avoids an earlier MySQL REPEATABLE READ snapshot.
    ids = list(db.scalars(query.with_for_update() if lock else query))
    return ids == [report.id]


def update_series(
    db: Session, report: EcrReport, user: User, value: str | None
) -> None:
    if value is None or value == report.tower.package.cooling_tower_series:
        return
    if not can_edit_series(db, report, user, lock=True):
        raise DraftNotEditableError(
            "Cooling Tower Series is read-only for this package. Reload the report."
        )
    if value not in COOLING_TOWER_SERIES:
        raise EcrIdentityError("Select an approved Cooling Tower Series.")
    report.tower.package.cooling_tower_series = value


def list_supervisor_reports(db: Session, supervisor_id: int) -> list[EcrReport]:
    return list(
        db.scalars(
            select(EcrReport)
            .options(*_report_load_options())
            .where(EcrReport.supervisor_user_id == supervisor_id)
            .order_by(EcrReport.updated_at.desc(), EcrReport.id.desc())
        )
    )


def list_admin_reports(
    db: Session,
    admin: User,
    *,
    branch_id: int | None = None,
) -> list[EcrReport]:
    statement = select(EcrReport).options(*_report_load_options())
    if admin.role is UserRole.BRANCH_ADMIN:
        statement = statement.where(EcrReport.branch_id == admin.branch_id)
    elif branch_id is not None:
        statement = statement.where(EcrReport.branch_id == branch_id)
    return list(
        db.scalars(statement.order_by(EcrReport.updated_at.desc(), EcrReport.id.desc()))
    )


def get_admin_visible_report(
    db: Session, report_id: int, admin: User
) -> EcrReport | None:
    statement = (
        select(EcrReport)
        .options(*_report_load_options())
        .where(EcrReport.id == report_id)
    )
    if admin.role is UserRole.BRANCH_ADMIN:
        statement = statement.where(EcrReport.branch_id == admin.branch_id)
    return db.scalar(statement)


def autosave_draft(report: EcrReport, data: DraftAutosaveInput) -> None:
    if report.status is not EcrReportStatus.DRAFT:
        raise DraftNotEditableError("Only Draft reports can be edited.")
    report.erection_start_date = data.erection_start_date
    report.erection_completion_date = data.erection_completion_date
