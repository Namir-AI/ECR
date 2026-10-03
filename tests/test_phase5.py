"""Operational workflow, final validation, scoped search, creation and audit."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select

from app.audit.models import ReportAuditEvent
from app.ecr import batch_c, page1, page2, page3
from app.ecr.models import EcrReport, EcrReportStatus, EcrTower
from app.ecr.operations import (
    grouped_reports,
    search_reports,
    smallest_gap,
    visible_reports,
)
from app.ecr.series import active_fields
from app.ecr.workflow import final_errors
from app.users.models import UserRole
from tests.conftest import csrf_from, login
from tests.test_page1 import SAMPLES as PAGE1
from tests.test_page2 import SAMPLES as PAGE2
from tests.test_phase3 import _create_report


def complete_report(db, report):
    series = report.tower.package.cooling_tower_series
    values = {
        k: v for k, v in PAGE1.items() if k in active_fields(page1.SECTIONS, series)
    }
    values["motor_current_drawn"] = "0"
    # Permanently optional fields deliberately absent.
    for name in (
        "motor_frame",
        "motor_insulation",
        "motor_mounting",
        "drive_shaft_oal",
        "drive_shaft_oal_unit",
        "gearbox_model_no",
        "fill_type",
    ):
        values.pop(name, None)
    page1.save_page1(db, report, page1.Page1DraftInput(**values))
    values = {
        k: v for k, v in PAGE2.items() if k in active_fields(page2.SECTIONS, series)
    }
    page2.save_page2(db, report, page2.Page2DraftInput(**values))
    batch_c.save(
        db,
        report,
        batch_c.BatchCDraftInput(
            de_nde_unit="mm",
            **{
                f"{end}_{position}": "0.00"
                for end in ("de", "nde")
                for position in batch_c.POSITIONS
            },
            vibration_limit_switch="No",
            oil_level_switch="No",
            fastener_rows=[
                {
                    "category": "FAN_HARDWARE",
                    "diameter": "M10",
                    "torque": "0",
                    "torque_unit": "Nm",
                }
            ],
        ),
    )
    page3.save_text(
        db,
        report,
        page3.Page3DraftInput(team_leader_report="Completed\nSecond paragraph"),
    )
    report.erection_completion_date = date(2026, 10, 1)
    db.commit()
    return report


@pytest.fixture
def operational_report(client, db_session, user_factory):
    owner = user_factory()
    report = complete_report(db_session, _create_report(db_session, owner))
    login(client, owner.employee_id)
    token = csrf_from(client.get("/dashboard").text)
    return owner, report, token


@pytest.mark.parametrize("series", ["AQ-3800", "CF-I", "Series 10", "RXF"])
def test_final_conditional_requiredness_zero_optional_signature(
    db_session, user_factory, series
):
    report = complete_report(
        db_session,
        _create_report(db_session, user_factory(), cooling_tower_series=series),
    )
    assert final_errors(report) == {}
    assert report.page1.motor_current_drawn == Decimal(0)
    assert report.page3.customer_signature_storage_key is None
    report.page3.team_leader_report = None
    assert "Completion Details" in final_errors(report)
    report.page3.team_leader_report = "Complete"
    report.page2.de_top = None
    assert "DE / NDE" in final_errors(report)
    report.page2.de_top = Decimal(0)
    report.fastener_rows.clear()
    assert "Fastener Torque" in final_errors(report)


@pytest.mark.parametrize("series", ["AQ-3800", "CF-I", "Series 10"])
def test_required_fields_applicable_not_hidden(db_session, user_factory, series):
    report = complete_report(
        db_session,
        _create_report(db_session, user_factory(), cooling_tower_series=series),
    )
    report.page1.drive_shaft_serial_no = None
    report.page1.gearbox_serial_no = None
    report.page2.bearing_housing_serial_no = None
    report.page2.belt_type = None
    errors = final_errors(report)
    assert ("Drive Shafts" in errors) == (series == "Series 10")
    assert ("Gearboxes" in errors) == (series == "Series 10")
    assert ("Bearing Housing" in errors) == (series == "AQ-3800")
    assert ("Belt & Pulleys" in errors) == (series == "AQ-3800")


def test_workflow_review_back_submit_approval_audit(
    client, db_session, user_factory, operational_report
):
    _owner, report, token = operational_report
    for action, state in [
        ("review", "REVIEWED"),
        ("back-to-edit", "DRAFT"),
        ("review", "REVIEWED"),
        ("submit", "SUBMITTED"),
    ]:
        response = client.post(
            f"/ecr/reports/{report.id}/{action}", data={"csrf_token": token}
        )
        assert response.status_code == 303, response.text
        db_session.refresh(report)
        assert report.status.value == state
        if action == "back-to-edit":
            assert report.reviewed_at is None
    assert report.submitted_at is not None and report.reviewed_at is not None
    assert client.get(f"/ecr/reports/{report.id}/edit").status_code == 409
    assert (
        client.post(
            f"/ecr/reports/{report.id}/autosave", data={"csrf_token": token}
        ).status_code
        == 409
    )
    admin = user_factory(role=UserRole.BRANCH_ADMIN, branch=report.branch)
    client.cookies.clear()
    login(client, admin.employee_id)
    token = csrf_from(client.get("/dashboard").text)
    response = client.post(f"/reports/{report.id}/approve", data={"csrf_token": token})
    assert response.status_code == 303, response.text
    db_session.refresh(report)
    assert report.status is EcrReportStatus.APPROVED
    assert report.approved_by_user_id == admin.id and report.approved_at
    assert client.get(f"/reports/{report.id}/edit").status_code == 409
    assert (
        client.post(
            f"/reports/{report.id}/save", data={"csrf_token": token}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/reports/{report.id}/approve", data={"csrf_token": token}
        ).status_code
        == 409
    )
    events = list(
        db_session.scalars(
            select(ReportAuditEvent)
            .where(ReportAuditEvent.report_id == report.id)
            .order_by(ReportAuditEvent.id)
        )
    )
    assert [e.action for e in events] == [
        "REVIEW",
        "BACK_TO_EDIT",
        "REVIEW",
        "SUBMIT",
        "APPROVE",
    ]
    assert events[-1].actor_user_id == admin.id


def test_incomplete_review_structured_errors_no_transition(
    client, db_session, user_factory
):
    owner = user_factory()
    report = _create_report(db_session, owner)
    report.erection_completion_date = None
    db_session.commit()
    login(client, owner.employee_id)
    token = csrf_from(client.get("/dashboard").text)
    response = client.post(
        f"/ecr/reports/{report.id}/review", data={"csrf_token": token}
    )
    assert response.status_code == 422
    for text in (
        "Motor",
        "Fan",
        "Fastener Torque",
        "DE / NDE",
        "Completion Details",
        "Erection Completion Date is required",
    ):
        assert text in response.text
    db_session.refresh(report)
    assert report.status is EcrReportStatus.DRAFT and report.reviewed_at is None


@pytest.mark.parametrize("action", ["review", "submit", "back-to-edit"])
def test_workflow_ownership_csrf_status(client, user_factory, db_session, action):
    owner, other = user_factory(), user_factory()
    report = _create_report(db_session, owner)
    login(client, other.employee_id)
    token = csrf_from(client.get("/dashboard").text)
    assert (
        client.post(
            f"/ecr/reports/{report.id}/{action}", data={"csrf_token": token}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/ecr/reports/{report.id}/{action}", data={"csrf_token": "wrong"}
        ).status_code
        == 403
    )


@pytest.mark.parametrize("status", ["DRAFT", "REVIEWED", "APPROVED"])
def test_approval_rejects_wrong_state(client, db_session, user_factory, status):
    report = _create_report(db_session, user_factory())
    report.status = EcrReportStatus(status)
    db_session.commit()
    admin = user_factory(role=UserRole.SUPERADMIN)
    login(client, admin.employee_id)
    token = csrf_from(client.get("/dashboard").text)
    assert (
        client.post(
            f"/reports/{report.id}/approve", data={"csrf_token": token}
        ).status_code
        == 409
    )


@pytest.mark.parametrize("role", [UserRole.BRANCH_ADMIN, UserRole.SUPERADMIN])
@pytest.mark.parametrize("status", ["DRAFT", "REVIEWED", "SUBMITTED"])
def test_admin_edit_scope_status_and_audit(
    client, db_session, user_factory, role, status
):
    report = _create_report(db_session, user_factory())
    report.status = EcrReportStatus(status)
    db_session.commit()
    admin = user_factory(role=role, branch=report.branch)
    login(client, admin.employee_id)
    edit = client.get(f"/reports/{report.id}/edit")
    assert edit.status_code == 200
    assert "data-explicit-save" in edit.text and "data-signature-pad" not in edit.text
    assert 'name="cooling_tower_series"' not in edit.text
    token = csrf_from(edit.text)
    response = client.post(
        f"/reports/{report.id}/save",
        data={
            "csrf_token": token,
            "page3_present": "1",
            "team_leader_report": "Admin update\nSecond line",
            "erection_completion_date": "2026-10-01",
        },
    )
    assert response.status_code == 200, response.text
    db_session.refresh(report)
    assert report.status.value == status
    assert report.page3.team_leader_report == "Admin update\nSecond line"
    audit = db_session.scalar(
        select(ReportAuditEvent).where(
            ReportAuditEvent.report_id == report.id,
            ReportAuditEvent.field_name == "page3.team_leader_report",
        )
    )
    assert audit.actor_user_id == admin.id and "Admin update" in audit.new_value
    assert audit.action == "ADMIN_EDIT"


@pytest.mark.parametrize(
    "field",
    [
        "branch_id",
        "supervisor_user_id",
        "cooling_tower_series",
        "cooling_tower_serial_no",
        "tower_suffix",
        "cell_no",
        "model",
        "customer",
        "signature_png",
        "customer_signed_at",
    ],
)
def test_admin_crafted_identity_shared_signature_edits_blocked(
    client, user_factory, db_session, field
):
    report = _create_report(db_session, user_factory())
    admin = user_factory(role=UserRole.SUPERADMIN)
    login(client, admin.employee_id)
    token = csrf_from(client.get("/dashboard").text)
    assert (
        client.post(
            f"/reports/{report.id}/save", data={"csrf_token": token, field: "malicious"}
        ).status_code
        == 422
    )


def test_branch_edit_approval_and_search_no_cross_scope(
    client, db_session, user_factory, branch_factory
):
    report = complete_report(
        db_session,
        _create_report(db_session, user_factory(branch=branch_factory(code="DELHI"))),
    )
    report.status = EcrReportStatus.SUBMITTED
    db_session.commit()
    admin = user_factory(
        role=UserRole.BRANCH_ADMIN, branch=branch_factory(code="MUMBAI")
    )
    login(client, admin.employee_id)
    token = csrf_from(client.get("/dashboard").text)
    assert client.get(f"/reports/{report.id}/edit").status_code == 404
    assert (
        client.post(
            f"/reports/{report.id}/save", data={"csrf_token": token}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/reports/{report.id}/approve", data={"csrf_token": token}
        ).status_code
        == 404
    )
    response = client.get(
        "/reports/search", params={"value": report.tower.display_name}
    )
    assert "No matching reports found" in response.text
    assert report.supervisor.full_name not in response.text


@pytest.mark.parametrize(
    "existing,expected", [([], 1), ([1, 3], 2), ([1, 2, 3], 4), ([2, 4], 1)]
)
def test_smallest_positive_gap(existing, expected):
    assert smallest_gap(existing) == expected


def test_virtual_rows_occupancy_and_blank_cell_creation(
    client, db_session, user_factory, branch_factory
):
    owner = user_factory(branch=branch_factory(code="DELHI"))
    report = _create_report(db_session, owner, declared_no_of_cells=3)
    other = user_factory()
    _create_report(
        db_session,
        other,
        serial_no=report.tower.package.cooling_tower_serial_no,
        cell_no=3,
        declared_no_of_cells=3,
    )
    login(client, owner.employee_id)
    page = client.get("/dashboard")
    assert "Cell No." in page.text and "Single Tower" in page.text
    assert "Not started" in page.text and "Unavailable" in page.text
    group = grouped_reports(db_session, owner, visible_reports(db_session, owner))[0][
        "towers"
    ][0]
    assert group["next_cell"] == 2
    assert len(group["cells"]) == 3
    owner.branch_id = branch_factory(code="MUMBAI").id
    db_session.commit()
    token = csrf_from(page.text)
    response = client.post(
        f"/ecr/towers/{report.tower_id}/cells",
        data={
            "csrf_token": token,
            "cell_no": 2,
            "supervisor_user_id": other.id,
            "branch_id": other.branch_id,
        },
    )
    assert response.status_code == 303, response.text
    new = db_session.scalar(
        select(EcrReport).where(
            EcrReport.tower_id == report.tower_id, EcrReport.cell_no == 2
        )
    )
    assert new.supervisor_user_id == owner.id and new.branch_id == owner.branch_id
    assert new.erection_completion_date is None and new.erection_start_date is None
    assert (
        new.page1 is None
        and new.page2 is None
        and new.page3 is None
        and new.fastener_rows == []
    )
    assert new.status is EcrReportStatus.DRAFT
    assert report.branch_id != new.branch_id
    assert (
        client.post(
            f"/ecr/towers/{report.tower_id}/cells",
            data={"csrf_token": token, "cell_no": 2},
        ).headers["location"]
        == response.headers["location"]
    )
    assert (
        client.post(
            f"/ecr/towers/{report.tower_id}/cells",
            data={"csrf_token": token, "cell_no": 3},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/ecr/towers/{report.tower_id}/cells",
            data={"csrf_token": token, "cell_no": 4},
        ).status_code
        == 303
    )
    db_session.refresh(report.tower)
    assert report.tower.declared_no_of_cells == 3
    assert client.get(f"/ecr/reports/{new.id}/edit").status_code == 200
    assert (
        client.post(
            f"/ecr/reports/{new.id}/autosave",
            data={"csrf_token": token, "erection_completion_date": ""},
        ).json()["values"]["erection_completion_date"]
        is None
    )


@pytest.mark.parametrize(
    "role", [UserRole.SUPERVISOR, UserRole.BRANCH_ADMIN, UserRole.SUPERADMIN]
)
def test_cell_creation_access_guard(client, db_session, user_factory, role):
    report = _create_report(db_session, user_factory())
    stranger = user_factory(role=role)
    login(client, stranger.employee_id)
    token = csrf_from(client.get("/dashboard").text)
    assert client.post(
        f"/ecr/towers/{report.tower_id}/cells", data={"csrf_token": token, "cell_no": 2}
    ).status_code == (404 if role is UserRole.SUPERVISOR else 403)


def test_multi_tower_creator_only_no_post_z(client, db_session, user_factory):
    owner = user_factory()
    report = _create_report(db_session, owner, multiple_towers=True, tower_suffix="A")
    login(client, owner.employee_id)
    token = csrf_from(client.get("/dashboard").text)
    package_id = report.tower.package_id
    response = client.post(
        f"/ecr/packages/{package_id}/towers", data={"csrf_token": token}
    )
    assert response.status_code == 303
    tower_b = db_session.scalar(
        select(EcrTower).where(
            EcrTower.package_id == package_id, EcrTower.tower_suffix == "B"
        )
    )
    assert tower_b is not None
    assert tower_b.display_name.endswith(" B")
    db_session.add(
        EcrTower(package_id=package_id, tower_suffix="Z", normalized_suffix_key="Z")
    )
    db_session.commit()
    assert (
        client.post(
            f"/ecr/packages/{package_id}/towers", data={"csrf_token": token}
        ).status_code
        == 409
    )
    single = _create_report(db_session, owner)
    assert (
        client.post(
            f"/ecr/packages/{single.tower.package_id}/towers",
            data={"csrf_token": token},
        ).status_code
        == 409
    )
    client.cookies.clear()
    login(client, user_factory().employee_id)
    token = csrf_from(client.get("/dashboard").text)
    assert (
        client.post(
            f"/ecr/packages/{package_id}/towers", data={"csrf_token": token}
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "key,field",
    [
        ("Motor Serial No.", "motor_serial_no"),
        ("Gearbox Serial No.", "gearbox_serial_no"),
        ("Drive Shaft Serial No.", "drive_shaft_serial_no"),
    ],
)
def test_exact_equipment_search_unique_full_report(
    client, db_session, user_factory, key, field
):
    report = complete_report(db_session, _create_report(db_session, user_factory()))
    setattr(report.page1, field, "UniQue / Serial-X")
    db_session.commit()
    admin = user_factory(role=UserRole.SUPERADMIN)
    login(client, admin.employee_id)
    response = client.get(
        "/reports/search", params={"search_by": key, "value": " unique / serial-x "}
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == f"/reports/{report.id}"
    full = client.get(response.headers["location"])
    for section in (
        "Motor",
        "Fan",
        "Fan Hardware Fastener",
        "DE / NDE",
        "Completion Details",
        "Completed",
        "Erected / Commissioned by",
    ):
        assert section in full.text
    assert (
        "No matching reports"
        in client.get(
            "/reports/search", params={"search_by": key, "value": "UniQue / Serial"}
        ).text
    )


def test_base_operational_serial_multiple_match_scope(
    client, db_session, user_factory, branch_factory
):
    owner = user_factory(branch=branch_factory(code="DELHI"))
    a1 = _create_report(db_session, owner, multiple_towers=True, tower_suffix="A")
    serial = a1.tower.package.cooling_tower_serial_no
    a2 = _create_report(
        db_session,
        owner,
        serial_no=serial,
        multiple_towers=True,
        tower_suffix="A",
        cell_no=2,
    )
    b1 = _create_report(
        db_session,
        user_factory(branch=branch_factory(code="MUMBAI")),
        serial_no=serial,
        multiple_towers=True,
        tower_suffix="B",
    )
    admin = user_factory(role=UserRole.BRANCH_ADMIN, branch=owner.branch)
    reports, _ = search_reports(db_session, admin, "Cooling Tower Serial No.", serial)
    assert {r.id for r in reports} == {a1.id, a2.id}
    superadmin = user_factory(role=UserRole.SUPERADMIN)
    reports, _ = search_reports(
        db_session, superadmin, "Cooling Tower Serial No.", serial + " b"
    )
    assert [r.id for r in reports] == [b1.id]
    login(client, admin.employee_id)
    response = client.get("/reports/search", params={"value": serial})
    assert response.status_code == 200 and "Matching Reports" in response.text
    assert b1.supervisor.full_name not in response.text


def test_dashboards_historical_branch_status_filter(
    client, db_session, user_factory, branch_factory
):
    delhi, mumbai = branch_factory(code="DELHI"), branch_factory(code="MUMBAI")
    owner = user_factory(branch=delhi)
    old = _create_report(db_session, owner)
    owner.branch_id = mumbai.id
    db_session.commit()
    new = _create_report(db_session, owner)
    new.status = EcrReportStatus.SUBMITTED
    db_session.commit()
    login(client, owner.employee_id)
    text = client.get("/dashboard").text
    assert old.tower.display_name in text and new.tower.display_name in text
    client.cookies.clear()
    admin = user_factory(role=UserRole.BRANCH_ADMIN, branch=delhi)
    login(client, admin.employee_id)
    assert old.tower.display_name in client.get("/dashboard").text
    assert new.tower.display_name not in client.get("/dashboard").text
    assert (
        old.tower.display_name
        not in client.get("/dashboard?status_filter=SUBMITTED").text
    )
    assert client.get("/reports/search?search_by=Fan+Serial+No.").status_code == 422
    client.cookies.clear()
    superadmin = user_factory(role=UserRole.SUPERADMIN)
    login(client, superadmin.employee_id)
    text = client.get(f"/dashboard?branch_id={mumbai.id}&status_filter=SUBMITTED").text
    assert new.tower.display_name in text and old.tower.display_name not in text


def test_workflow_schema_nullable_and_serial_indexes(db_session):
    inspector = inspect(db_session.bind)
    columns = {c["name"]: c for c in inspector.get_columns("ecr_reports")}
    for name in (
        "erection_completion_date",
        "reviewed_at",
        "submitted_at",
        "approved_at",
        "approved_by_user_id",
    ):
        assert columns[name]["nullable"]
    assert "ecr_audit_events" in inspector.get_table_names()
    indexed = {
        c
        for index in inspector.get_indexes("ecr_page1_technical")
        for c in index["column_names"]
    }
    assert {"motor_serial_no", "drive_shaft_serial_no", "gearbox_serial_no"} <= indexed
