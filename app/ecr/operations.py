"""Scoped grouped navigation, blank Cell creation and exact serial search."""

from collections import OrderedDict

from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload

from app.ecr.models import (
    EcrPackage,
    EcrPage1Technical,
    EcrReport,
    EcrReportStatus,
    EcrTower,
)
from app.ecr.schemas import normalized_serial_key
from app.ecr.services import EcrIdentityError, ExistingReportError, _report_load_options
from app.users.models import UserRole


def scope(statement, user):
    if user.role is UserRole.SUPERVISOR:
        return statement.where(EcrReport.supervisor_user_id == user.id)
    if user.role is UserRole.BRANCH_ADMIN:
        return statement.where(EcrReport.branch_id == user.branch_id)
    return statement


def visible_reports(db, user, *, status=None, branch_id=None):
    # Dashboard needs identity only, not all technical child records/signature data.
    query = scope(select(EcrReport), user).options(
        joinedload(EcrReport.tower).joinedload(EcrTower.package),
        joinedload(EcrReport.branch),
        joinedload(EcrReport.supervisor),
    )
    if status:
        query = query.where(EcrReport.status == status)
    if branch_id and user.role is UserRole.SUPERADMIN:
        query = query.where(EcrReport.branch_id == branch_id)
    return list(
        db.scalars(query.order_by(EcrReport.updated_at.desc(), EcrReport.id.desc()))
    )


def smallest_gap(numbers):
    result = 1
    for number in sorted(set(numbers)):
        if number == result:
            result += 1
        elif number > result:
            break
    return result


def tower_access(db, tower_id, supervisor, *, lock=False):
    query = (
        select(EcrTower)
        .options(joinedload(EcrTower.package))
        .where(EcrTower.id == tower_id)
    )
    tower = db.scalar(query)
    if tower is None:
        return None
    # Creator or a Supervisor already owning a report under this package may
    # navigate its towers. Never grant access based only on a guessed tower ID.
    owned = db.scalar(
        select(EcrReport.id)
        .join(EcrTower)
        .where(
            EcrTower.package_id == tower.package_id,
            EcrReport.supervisor_user_id == supervisor.id,
        )
        .limit(1)
    )
    if tower.package.created_by_user_id != supervisor.id and owned is None:
        return None
    if lock:
        db.scalar(
            select(EcrPackage)
            .where(EcrPackage.id == tower.package_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        tower = db.scalar(
            query.with_for_update().execution_options(populate_existing=True)
        )
    return tower


def grouped_reports(db, user, reports, *, cell_page=1):
    packages = OrderedDict()
    for report in reports:
        package = report.tower.package
        group = packages.setdefault(
            package.id, {"package": package, "branches": set(), "towers": OrderedDict()}
        )
        group["branches"].add(report.branch.name)
        tower_group = group["towers"].setdefault(
            report.tower_id, {"tower": report.tower, "reports": {}}
        )
        tower_group["reports"][report.cell_no] = report
    if user.role is UserRole.SUPERVISOR and packages:
        towers = list(
            db.scalars(
                select(EcrTower)
                .options(joinedload(EcrTower.package))
                .where(EcrTower.package_id.in_(packages))
            )
        )
        ids = [t.id for t in towers]
        # Only occupancy numbers are loaded for inaccessible reports, never owner
        # names, status, technical data or branch. They block duplicate creation.
        occupied = defaultdict_set(
            db.execute(
                select(EcrReport.tower_id, EcrReport.cell_no).where(
                    EcrReport.tower_id.in_(ids)
                )
            )
        )
        for tower in towers:
            group = packages[tower.package_id]["towers"].setdefault(
                tower.id, {"tower": tower, "reports": {}}
            )
            group["occupied"] = occupied.get(tower.id, set())
            group["next_cell"] = smallest_gap(group["occupied"])
    for package in packages.values():
        package["branches"] = sorted(package["branches"])
        for group in package["towers"].values():
            tower = group["tower"]
            numbers = set(group["reports"])
            if user.role is UserRole.SUPERVISOR and tower.declared_no_of_cells:
                # Page virtual rows without allocating an owner-entered BIGINT
                # range. This is presentation pagination, never a Cell No. cap.
                pages = (tower.declared_no_of_cells + 49) // 50
                current = min(cell_page, pages)
                start = (current - 1) * 50 + 1
                end = min(current * 50, tower.declared_no_of_cells)
                numbers.update(range(start, end + 1))
                group["cell_page"] = current
                group["cell_pages"] = pages
            group["cells"] = [
                (n, group["reports"].get(n), n in group.get("occupied", set()))
                for n in sorted(numbers)
            ]
        package["towers"] = sorted(
            package["towers"].values(), key=lambda g: g["tower"].normalized_suffix_key
        )
    return list(packages.values())


def defaultdict_set(rows):
    result = {}
    for tower_id, number in rows:
        result.setdefault(tower_id, set()).add(number)
    return result


def create_cell(db, tower, user, cell_no=None):
    # Package lock serializes creation with shared-Series edits and other creates.
    numbers = list(
        db.scalars(
            select(EcrReport.cell_no)
            .where(EcrReport.tower_id == tower.id)
            .with_for_update()
        )
    )
    number = smallest_gap(numbers) if cell_no is None else cell_no
    if number < 1 or number > 2**63 - 1:
        raise EcrIdentityError(
            "Cell No. must be a positive integer within storage capacity."
        )
    existing = db.scalar(
        select(EcrReport)
        .where(EcrReport.tower_id == tower.id, EcrReport.cell_no == number)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing:
        if (
            existing.supervisor_user_id == user.id
            and existing.status is EcrReportStatus.DRAFT
        ):
            return existing
        raise ExistingReportError(
            "An E&C report already exists for this tower and cell. Please contact the Service Admin if access or correction is required."
        )
    report = EcrReport(
        tower_id=tower.id,
        cell_no=number,
        supervisor_user_id=user.id,
        branch_id=user.branch_id,
        status=EcrReportStatus.DRAFT,
        erection_completion_date=None,
    )
    db.add(report)
    db.flush()
    return report


SEARCH_KEYS = (
    "All",
    "Cooling Tower Serial No.",
    "Motor Serial No.",
    "Gearbox Serial No.",
    "Drive Shaft Serial No.",
)


def search_reports(db, user, key, value):
    value = value.strip()
    if not value:
        return [], {}
    if key not in SEARCH_KEYS:
        raise EcrIdentityError("Select an approved search key.")
    query = scope(
        select(EcrReport).join(EcrTower).join(EcrPackage).outerjoin(EcrPage1Technical),
        user,
    )
    matches = []
    if key in ("All", "Cooling Tower Serial No."):
        normalized = normalized_serial_key(value)
        # Prefer exact base serial. Serial formats legitimately include spaces.
        base = db.scalar(
            select(EcrPackage.id).where(EcrPackage.normalized_serial_no == normalized)
        )
        if base is not None:
            matches.append(("Cooling Tower Serial No.", EcrPackage.id == base))
        elif " " in value:
            serial, suffix = value.rsplit(" ", 1)
            matches.append(
                (
                    "Cooling Tower Serial No.",
                    (EcrPackage.normalized_serial_no == normalized_serial_key(serial))
                    & (EcrTower.normalized_suffix_key == suffix.upper()),
                )
            )
    for label, column in (
        ("Motor Serial No.", EcrPage1Technical.motor_serial_no),
        ("Gearbox Serial No.", EcrPage1Technical.gearbox_serial_no),
        ("Drive Shaft Serial No.", EcrPage1Technical.drive_shaft_serial_no),
    ):
        if key in ("All", label):
            # MySQL's existing case-insensitive collation uses serial indexes.
            matches.append((label, column == value))
    if not matches:
        return [], {}
    reports = list(
        db.scalars(
            query.where(or_(*(c for _, c in matches)))
            .options(*_report_load_options())
            .order_by(EcrPackage.id, EcrTower.normalized_suffix_key, EcrReport.cell_no)
        )
    )
    labels = {}
    for report in reports:
        found = []
        if key in ("All", "Cooling Tower Serial No.") and value.casefold() in (
            report.tower.package.cooling_tower_serial_no.casefold(),
            report.tower.display_name.casefold(),
        ):
            found.append("Cooling Tower Serial No.")
        for label, name in (
            ("Motor Serial No.", "motor_serial_no"),
            ("Gearbox Serial No.", "gearbox_serial_no"),
            ("Drive Shaft Serial No.", "drive_shaft_serial_no"),
        ):
            stored = getattr(report.page1, name, None)
            if (
                key in ("All", label)
                and stored
                and stored.casefold() == value.casefold()
            ):
                found.append(label)
        labels[report.id] = ", ".join(found)
    # Keep exact identity matching even when a DB collation is accent-insensitive.
    return [report for report in reports if labels[report.id]], labels
