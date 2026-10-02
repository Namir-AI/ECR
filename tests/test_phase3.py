"""Phase 3 ECR identity, Draft, autosave, and historical-scope tests."""

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.ecr.models import EcrPackage, EcrReport, EcrReportStatus, EcrTower
from app.ecr.schemas import DraftCreateInput
from app.ecr.services import (
    EcrIdentityError,
    ExistingReportError,
    create_or_resume_draft,
)
from app.users.models import UserRole
from tests.conftest import csrf_from, login


def _serial(prefix: str = "26-2") -> str:
    return f"{prefix}-{uuid4().hex[:10].upper()}"


def _draft_input(
    serial_no: str,
    *,
    multiple_towers: bool = False,
    tower_suffix: str | None = None,
    declared_no_of_cells: int | None = None,
    cell_no: int = 1,
    customer: str = "Test Customer",
    customer_order_no: str | None = None,
    cooling_tower_series: str = "Series 10",
    erection_start_date: date | None = None,
    erection_completion_date: date = date(2026, 9, 30),
) -> DraftCreateInput:
    return DraftCreateInput(
        cooling_tower_serial_no=serial_no,
        customer=customer,
        customer_order_no=customer_order_no,
        cooling_tower_series=cooling_tower_series,
        model="Test Model",
        place_of_installation="Test Site",
        multiple_towers=multiple_towers,
        tower_suffix=tower_suffix,
        declared_no_of_cells=declared_no_of_cells,
        cell_no=cell_no,
        erection_start_date=erection_start_date,
        erection_completion_date=erection_completion_date,
    )


def _create_report(
    db_session: Session,
    supervisor,
    *,
    serial_no: str | None = None,
    **overrides,
) -> EcrReport:
    result = create_or_resume_draft(
        db_session,
        supervisor,
        _draft_input(serial_no or _serial(), **overrides),
    )
    db_session.commit()
    return result.report


def _creation_payload(csrf_token: str, serial_no: str, **overrides) -> dict[str, str]:
    payload = {
        "csrf_token": csrf_token,
        "cooling_tower_serial_no": serial_no,
        "customer": "Test Customer",
        "customer_order_no": "",
        "cooling_tower_series": "Series 10",
        "model": "Test Model",
        "place_of_installation": "Test Site",
        "multiple_towers": "false",
        "tower_suffix": "",
        "declared_no_of_cells": "",
        "cell_no": "1",
        "erection_start_date": "",
        "erection_completion_date": "2026-09-30",
    }
    payload.update({key: str(value) for key, value in overrides.items()})
    return payload


def test_package_creation_normalization_optional_order_and_required_fields(
    db_session: Session,
    user_factory,
) -> None:
    supervisor = user_factory(employee_id="P3-PACKAGE-CREATE")
    serial = f"  26-2 / {uuid4().hex[:6]}  "
    report = _create_report(db_session, supervisor, serial_no=serial)
    package = report.tower.package

    assert package.cooling_tower_serial_no == serial.strip()
    assert package.normalized_serial_no == serial.strip().casefold()
    assert package.customer_order_no is None
    assert package.created_by_user_id == supervisor.id
    with pytest.raises(ValidationError):
        _draft_input(_serial(), customer="   ")
    with pytest.raises(ValidationError):
        DraftCreateInput(
            **_draft_input(_serial()).model_dump(exclude={"erection_completion_date"})
        )


def test_duplicate_normalized_serial_is_prevented_by_mysql(
    db_session: Session,
    user_factory,
) -> None:
    supervisor = user_factory(employee_id="P3-PACKAGE-UNIQUE")
    report = _create_report(db_session, supervisor)
    package = report.tower.package
    duplicate = EcrPackage(
        cooling_tower_serial_no=package.cooling_tower_serial_no.lower(),
        normalized_serial_no=package.normalized_serial_no,
        customer="Other Customer",
        customer_order_no=None,
        cooling_tower_series="Other Series",
        model="Other Model",
        place_of_installation="Other Site",
        multiple_towers=False,
        created_by_user_id=supervisor.id,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_existing_package_and_tower_are_reused_without_shared_overwrite(
    db_session: Session,
    user_factory,
) -> None:
    supervisor = user_factory(employee_id="P3-PACKAGE-REUSE")
    serial = _serial()
    first = _create_report(db_session, supervisor, serial_no=serial, cell_no=1)
    second = _create_report(db_session, supervisor, serial_no=serial.lower(), cell_no=2)

    assert first.tower.package_id == second.tower.package_id
    assert first.tower_id == second.tower_id
    assert db_session.scalar(select(func.count()).select_from(EcrPackage)) >= 1
    with pytest.raises(EcrIdentityError):
        create_or_resume_draft(
            db_session,
            supervisor,
            _draft_input(serial, customer="Conflicting Customer", cell_no=3),
        )
    db_session.refresh(first.tower.package)
    assert first.tower.package.customer == "Test Customer"


def test_single_tower_identity_and_suffix_rules(
    db_session: Session,
    user_factory,
) -> None:
    supervisor = user_factory(employee_id="P3-SINGLE-TOWER")
    report = _create_report(db_session, supervisor)
    tower = report.tower
    assert tower.tower_suffix is None
    assert tower.normalized_suffix_key == ""

    duplicate = EcrTower(
        package_id=tower.package_id,
        tower_suffix=None,
        normalized_suffix_key="",
        declared_no_of_cells=None,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    with pytest.raises(ValidationError):
        _draft_input(_serial(), multiple_towers=False, tower_suffix="A")


@pytest.mark.parametrize("suffix", ["A", "B", "C", "Z", "AA"])
def test_multi_tower_suffixes_are_normalized_and_extendable(
    db_session: Session,
    user_factory,
    suffix: str,
) -> None:
    supervisor = user_factory()
    report = _create_report(
        db_session,
        supervisor,
        multiple_towers=True,
        tower_suffix=suffix.lower(),
    )
    assert report.tower.tower_suffix == suffix
    assert report.tower.normalized_suffix_key == suffix


def test_multi_tower_requires_suffix_rejects_unsuffixed_and_duplicate_suffix(
    db_session: Session,
    user_factory,
) -> None:
    with pytest.raises(ValidationError):
        _draft_input(_serial(), multiple_towers=True, tower_suffix=None)

    supervisor = user_factory(employee_id="P3-MULTI-UNIQUE")
    report = _create_report(
        db_session,
        supervisor,
        multiple_towers=True,
        tower_suffix="A",
    )
    duplicate = EcrTower(
        package_id=report.tower.package_id,
        tower_suffix="A",
        normalized_suffix_key="A",
        declared_no_of_cells=None,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize("cell_no", [1, 10, 12])
def test_cell_numbers_and_declared_cells_are_independent(
    db_session: Session,
    user_factory,
    cell_no: int,
) -> None:
    supervisor = user_factory()
    report = _create_report(
        db_session,
        supervisor,
        declared_no_of_cells=4,
        cell_no=cell_no,
    )
    assert report.cell_no == cell_no
    assert report.tower.declared_no_of_cells == 4
    assert report.erection_start_date is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("cell_no", 0), ("cell_no", -1), ("declared_no_of_cells", 0), ("declared_no_of_cells", -2)],
)
def test_non_positive_cell_values_are_rejected(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        _draft_input(_serial(), **{field: value})


def test_duplicate_report_policy_and_multiple_supervisors(
    db_session: Session,
    user_factory,
) -> None:
    first_supervisor = user_factory(employee_id="P3-DUPLICATE-A")
    second_supervisor = user_factory(employee_id="P3-DUPLICATE-B")
    serial = _serial()
    first = create_or_resume_draft(db_session, first_supervisor, _draft_input(serial, cell_no=1))
    db_session.commit()
    resumed = create_or_resume_draft(
        db_session,
        first_supervisor,
        _draft_input(serial.lower(), cell_no=1),
    )
    assert resumed.resumed is True
    assert resumed.report.id == first.report.id

    with pytest.raises(ExistingReportError):
        create_or_resume_draft(db_session, second_supervisor, _draft_input(serial, cell_no=1))

    different_cell = create_or_resume_draft(
        db_session,
        second_supervisor,
        _draft_input(serial, cell_no=2),
    )
    db_session.commit()
    assert different_cell.report.supervisor_user_id == second_supervisor.id
    assert different_cell.report.tower_id == first.report.tower_id


def test_mysql_prevents_duplicate_tower_cell_report(
    db_session: Session,
    user_factory,
) -> None:
    first_supervisor = user_factory(employee_id="P3-DB-REPORT-A")
    second_supervisor = user_factory(employee_id="P3-DB-REPORT-B")
    report = _create_report(db_session, first_supervisor)
    duplicate = EcrReport(
        tower_id=report.tower_id,
        cell_no=report.cell_no,
        supervisor_user_id=second_supervisor.id,
        branch_id=second_supervisor.branch_id,
        erection_start_date=None,
        erection_completion_date=date(2026, 10, 1),
        status=EcrReportStatus.DRAFT,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_authenticated_identity_and_branch_are_server_derived(
    client,
    db_session: Session,
    user_factory,
    branch_factory,
) -> None:
    delhi = branch_factory(code="DELHI")
    mumbai = branch_factory(code="MUMBAI")
    supervisor = user_factory(employee_id="P3-DERIVED-OWNER", branch=delhi)
    other = user_factory(employee_id="P3-CRAFTED-OWNER", branch=mumbai)
    login(client, supervisor.employee_id)
    start = client.get("/ecr/reports/new")
    response = client.post(
        "/ecr/reports",
        data=_creation_payload(
            csrf_from(start.text),
            _serial(),
            supervisor_user_id=other.id,
            branch_id=mumbai.id,
        ),
    )
    assert response.status_code == 303
    report = db_session.scalar(
        select(EcrReport).where(EcrReport.supervisor_user_id == supervisor.id)
    )
    assert report is not None
    assert report.supervisor_user_id == supervisor.id
    assert report.branch_id == delhi.id


def test_historical_branch_survives_supervisor_transfer_and_drives_scope(
    client,
    db_session: Session,
    user_factory,
    branch_factory,
) -> None:
    delhi = branch_factory(code="DELHI")
    mumbai = branch_factory(code="MUMBAI")
    supervisor = user_factory(employee_id="P3-TRANSFER-SUP", branch=delhi)
    report = _create_report(db_session, supervisor)
    serial = report.tower.package.cooling_tower_serial_no
    delhi_admin = user_factory(
        role=UserRole.BRANCH_ADMIN,
        employee_id="P3-DELHI-ADMIN",
        branch=delhi,
    )
    mumbai_admin = user_factory(
        role=UserRole.BRANCH_ADMIN,
        employee_id="P3-MUMBAI-ADMIN",
        branch=mumbai,
    )

    supervisor.branch_id = mumbai.id
    db_session.commit()
    db_session.refresh(report)
    assert report.branch_id == delhi.id

    login(client, delhi_admin.employee_id)
    assert serial in client.get("/reports").text
    assert client.get(f"/reports/{report.id}").status_code == 200
    client.cookies.clear()

    login(client, mumbai_admin.employee_id)
    assert serial not in client.get("/reports").text
    assert client.get(f"/reports/{report.id}").status_code == 404
    client.cookies.clear()

    login(client, supervisor.employee_id)
    dashboard = client.get("/dashboard")
    assert serial in dashboard.text
    assert delhi.name in dashboard.text


def test_supervisor_visibility_and_non_owner_autosave_are_scoped(
    client,
    db_session: Session,
    user_factory,
) -> None:
    owner = user_factory(employee_id="P3-OWNER")
    other = user_factory(employee_id="P3-NONOWNER")
    owner_report = _create_report(db_session, owner)
    other_report = _create_report(db_session, other)
    login(client, owner.employee_id)

    dashboard = client.get("/dashboard")
    assert owner_report.tower.package.cooling_tower_serial_no in dashboard.text
    assert other_report.tower.package.cooling_tower_serial_no not in dashboard.text
    assert client.get(f"/ecr/reports/{other_report.id}/edit").status_code == 404
    assert client.post(
        f"/ecr/reports/{other_report.id}/autosave",
        data={
            "csrf_token": csrf_from(dashboard.text),
            "erection_start_date": "",
            "erection_completion_date": "2026-10-01",
        },
    ).status_code == 404


def test_branch_admin_and_superadmin_report_visibility_is_read_only(
    client,
    db_session: Session,
    user_factory,
    branch_factory,
) -> None:
    delhi = branch_factory(code="DELHI")
    mumbai = branch_factory(code="MUMBAI")
    delhi_supervisor = user_factory(branch=delhi)
    mumbai_supervisor = user_factory(branch=mumbai)
    delhi_report = _create_report(db_session, delhi_supervisor)
    mumbai_report = _create_report(db_session, mumbai_supervisor)
    delhi_admin = user_factory(role=UserRole.BRANCH_ADMIN, branch=delhi)
    superadmin = user_factory(role=UserRole.SUPERADMIN)

    login(client, delhi_admin.employee_id)
    listing = client.get("/reports")
    assert delhi_report.tower.package.cooling_tower_serial_no in listing.text
    assert mumbai_report.tower.package.cooling_tower_serial_no not in listing.text
    assert client.get(f"/reports/{mumbai_report.id}").status_code == 404
    assert client.get("/ecr/reports/new").status_code == 403
    assert client.post(
        f"/ecr/reports/{delhi_report.id}/autosave",
        data={"csrf_token": csrf_from(listing.text)},
    ).status_code == 403
    client.cookies.clear()

    login(client, superadmin.employee_id)
    global_listing = client.get("/reports")
    assert delhi_report.tower.package.cooling_tower_serial_no in global_listing.text
    assert mumbai_report.tower.package.cooling_tower_serial_no in global_listing.text
    assert client.get(f"/reports/{delhi_report.id}").status_code == 200
    assert client.get(f"/reports/{mumbai_report.id}").status_code == 200
    assert client.get("/ecr/reports/new").status_code == 403
    assert not any(
        "POST" in (getattr(route, "methods", set()) or set())
        and getattr(route, "path", "").startswith("/reports/")
        for route in client.app.routes
    )


def test_autosave_persists_valid_dates_rejects_crafted_identity_and_invalid_data(
    client,
    db_session: Session,
    user_factory,
    branch_factory,
) -> None:
    delhi = branch_factory(code="DELHI")
    mumbai = branch_factory(code="MUMBAI")
    supervisor = user_factory(employee_id="P3-AUTOSAVE", branch=delhi)
    other = user_factory(branch=mumbai)
    report = _create_report(db_session, supervisor)
    login(client, supervisor.employee_id)
    edit = client.get(f"/ecr/reports/{report.id}/edit")
    token = csrf_from(edit.text)
    saved = client.post(
        f"/ecr/reports/{report.id}/autosave",
        data={
            "csrf_token": token,
            "erection_start_date": "2026-09-01",
            "erection_completion_date": "2026-10-01",
            "supervisor_user_id": other.id,
            "branch_id": mumbai.id,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["message"] == "Saved"
    db_session.refresh(report)
    assert report.erection_start_date == date(2026, 9, 1)
    assert report.erection_completion_date == date(2026, 10, 1)
    assert report.supervisor_user_id == supervisor.id
    assert report.branch_id == delhi.id
    reloaded = client.get(f"/ecr/reports/{report.id}/edit")
    assert 'value="2026-09-01"' in reloaded.text
    assert 'value="2026-10-01"' in reloaded.text

    invalid = client.post(
        f"/ecr/reports/{report.id}/autosave",
        data={
            "csrf_token": token,
            "erection_start_date": "",
            "erection_completion_date": "",
        },
    )
    assert invalid.status_code == 422
    db_session.refresh(report)
    assert report.erection_completion_date == date(2026, 10, 1)


def test_non_draft_autosave_is_rejected(
    client,
    db_session: Session,
    user_factory,
) -> None:
    supervisor = user_factory(employee_id="P3-LOCKED-DRAFT")
    report = _create_report(db_session, supervisor)
    report.status = EcrReportStatus.REVIEWED
    db_session.commit()
    login(client, supervisor.employee_id)
    dashboard = client.get("/dashboard")
    assert client.get(f"/ecr/reports/{report.id}/edit").status_code == 409
    response = client.post(
        f"/ecr/reports/{report.id}/autosave",
        data={
            "csrf_token": csrf_from(dashboard.text),
            "erection_start_date": "",
            "erection_completion_date": "2026-10-01",
        },
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    ("start_date", "completion_date"),
    [("02-09-2026", "2026-10-02"), ("", "02-10-2026"), ("2026-02-30", "2026-10-02")],
)
def test_autosave_rejects_visual_or_invalid_date_strings(
    client, db_session, user_factory, start_date, completion_date
) -> None:
    supervisor = user_factory()
    report = _create_report(db_session, supervisor)
    original_completion = report.erection_completion_date
    login(client, supervisor.employee_id)
    edit = client.get(f"/ecr/reports/{report.id}/edit")
    response = client.post(
        f"/ecr/reports/{report.id}/autosave",
        data={
            "csrf_token": csrf_from(edit.text),
            "erection_start_date": start_date,
            "erection_completion_date": completion_date,
        },
    )
    assert response.status_code == 422
    assert response.json()["ok"] is False
    db_session.refresh(report)
    assert report.erection_start_date is None
    assert report.erection_completion_date == original_completion


def test_autosave_rejects_bad_csrf_without_persistence(client, db_session, user_factory):
    supervisor = user_factory()
    report = _create_report(db_session, supervisor)
    original_completion = report.erection_completion_date
    login(client, supervisor.employee_id)
    response = client.post(
        f"/ecr/reports/{report.id}/autosave",
        data={
            "csrf_token": "wrong-token",
            "erection_start_date": "2026-09-02",
            "erection_completion_date": "2026-10-02",
        },
    )
    assert response.status_code == 403
    db_session.refresh(report)
    assert report.erection_start_date is None
    assert report.erection_completion_date == original_completion


def test_new_report_flow_resumes_owner_draft_and_shows_existing_package(
    client,
    db_session: Session,
    user_factory,
) -> None:
    supervisor = user_factory(employee_id="P3-WEB-RESUME")
    report = _create_report(db_session, supervisor)
    login(client, supervisor.employee_id)
    start = client.get("/ecr/reports/new")
    checked = client.post(
        "/ecr/reports/new/check",
        data={
            "csrf_token": csrf_from(start.text),
            "cooling_tower_serial_no": report.tower.package.cooling_tower_serial_no.lower(),
        },
    )
    assert checked.status_code == 200
    assert "Existing Package — shared information is read-only" in checked.text
    response = client.post(
        "/ecr/reports",
        data=_creation_payload(
            csrf_from(checked.text),
            report.tower.package.cooling_tower_serial_no,
        ),
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/{report.id}/edit?notice=resumed")

    other = user_factory(employee_id="P3-WEB-DUPLICATE-OTHER")
    client.cookies.clear()
    login(client, other.employee_id)
    other_start = client.get("/ecr/reports/new")
    blocked = client.post(
        "/ecr/reports",
        data=_creation_payload(
            csrf_from(other_start.text),
            report.tower.package.cooling_tower_serial_no,
        ),
    )
    assert blocked.status_code == 409
    assert "An E&amp;C report already exists for this tower and cell" in blocked.text
    assert "sql" not in blocked.text.lower()


def test_mysql_checks_reject_invalid_cell_and_declared_cell_values(
    db_session: Session,
    user_factory,
) -> None:
    supervisor = user_factory(employee_id="P3-DB-CHECKS")
    report = _create_report(db_session, supervisor)
    report.cell_no = 0
    with pytest.raises((IntegrityError, OperationalError)):
        db_session.commit()
    db_session.rollback()

    tower = db_session.get(EcrTower, report.tower_id)
    assert tower is not None
    tower.declared_no_of_cells = 0
    with pytest.raises((IntegrityError, OperationalError)):
        db_session.commit()
    db_session.rollback()


def test_phase3_dashboard_navigation_and_autosave_ui_contract(
    client,
    user_factory,
) -> None:
    supervisor = user_factory(employee_id="P3-UI-SUP")
    login(client, supervisor.employee_id)
    dashboard = client.get("/dashboard")
    assert "ERECTION &amp; COMMISSIONING" in dashboard.text
    assert "New Erection &amp; Commissioning Report" in dashboard.text
    assert "My Previous Reports" in dashboard.text
    assert '<svg class="nav-icon"' in dashboard.text
    new_report = client.get("/ecr/reports/new")
    assert 'class="nav-item is-active" href="/dashboard" aria-current="page"' in new_report.text

    autosave_script = client.get("/static/ecr_autosave.js")
    assert autosave_script.status_code == 200
    for message in ("Saving…", "Saved", "Unable to save — retrying"):
        assert message in autosave_script.text
    assert "window.setTimeout(save, 700)" in autosave_script.text

    client.cookies.clear()
    superadmin = user_factory(role=UserRole.SUPERADMIN, employee_id="P3-UI-SUPER")
    login(client, superadmin.employee_id)
    reports = client.get("/reports")
    assert reports.status_code == 200
    assert 'class="nav-item is-active" href="/reports" aria-current="page"' in reports.text
    assert "Reports across all historical branches" in reports.text
