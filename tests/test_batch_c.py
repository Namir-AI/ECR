"""MySQL-backed Batch C validation, snapshot integrity and authorization."""

from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, OperationalError

from app.ecr import batch_c
from app.ecr.models import EcrFastenerTorqueRow, EcrReportStatus
from app.users.models import UserRole, UserStatus
from tests import test_page2
from tests.conftest import csrf_from, login

batch_draft = test_page2.batch_draft


def post(client, report, token, rows=(), **fields):
    data = {
        "csrf_token": token,
        "batch_c_present": "1",
        "erection_completion_date": "2026-10-02",
        **fields,
    }
    for category, _ in batch_c.CATEGORIES:
        for name in ("diameter", "torque", "torque_unit"):
            data[f"torque_{category}_{name}"] = [
                r.get(name, "") for r in rows if r["category"] == category
            ]
    return client.post(f"/ecr/reports/{report.id}/autosave", data=data)


@pytest.mark.parametrize("field", ["radial_tir", "axial_tir"])
@pytest.mark.parametrize(
    "value",
    ["", "-1", "-1.000", "-0.001", "0", "0.000", "+0.005", "0.125", "1", "1.000"],
)
def test_tir_roundtrip(client, db_session, batch_draft, field, value):
    _, report, token = batch_draft
    result = post(client, report, token, **{field: value})
    assert result.status_code == 200, result.text
    db_session.expire_all()
    assert getattr(report.page2, field) == (Decimal(value) if value else None)
    assert result.json()["values"]["batch_c"][field] == batch_c.values(report)[field]


@pytest.mark.parametrize("field", ["radial_tir", "axial_tir"])
@pytest.mark.parametrize(
    "value", ["-1.001", "1.001", "0.0005", "NaN", "Infinity", "abc"]
)
def test_tir_rejected_preserves_committed(
    client, db_session, batch_draft, field, value
):
    _, report, token = batch_draft
    assert post(client, report, token, **{field: "0.125"}).status_code == 200
    result = post(client, report, token, **{field: value})
    assert result.status_code == 422 and not result.json()["ok"]
    db_session.expire_all()
    assert getattr(report.page2, field) == Decimal("0.125")


READINGS = [f"{end}_{p}" for end in ("de", "nde") for p in batch_c.POSITIONS]


@pytest.mark.parametrize("field", READINGS)
@pytest.mark.parametrize("value", ["", "-1.00", "+1.00", "0", "0.00", "-0.01", "0.25"])
def test_readings_exact_zero_roundtrip(client, db_session, batch_draft, field, value):
    _, report, token = batch_draft
    response = post(client, report, token, **{field: value})
    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert getattr(report.page2, field) == (Decimal(value) if value else None)
    readonly = client.get(f"/ecr/reports/{report.id}").text
    if value and Decimal(value) == 0:
        assert f'data-reading-value="{field}">0</dd>' in readonly


@pytest.mark.parametrize("field", READINGS)
@pytest.mark.parametrize("value", ["-1.01", "1.01", "0.001"])
def test_reading_range_and_increment_server_rejection(
    client, batch_draft, field, value
):
    _, report, token = batch_draft
    assert post(client, report, token, **{field: value}).status_code == 422


@pytest.mark.parametrize(
    "field,options",
    [
        ("de_nde_unit", ["", "inches", "mm"]),
        ("vibration_limit_switch", ["", "Yes", "No"]),
        ("oil_level_switch", ["", "Yes", "No"]),
    ],
)
def test_choices_and_nullable_booleans(client, db_session, batch_draft, field, options):
    _, report, token = batch_draft
    for value in options:
        assert post(client, report, token, **{field: value}).status_code == 200
        db_session.expire_all()
        assert batch_c.values(report)[field] == value
        if field in batch_c.SWITCH_FIELDS:
            assert getattr(report.page2, field) is (
                None if value == "" else value == "Yes"
            )
    assert post(client, report, token, **{field: "arbitrary"}).status_code == 422


@pytest.mark.parametrize("category", [c for c, _ in batch_c.CATEGORIES])
def test_dynamic_order_partial_no_max_repeat_remove(
    client, db_session, batch_draft, category
):
    _, report, token = batch_draft
    rows = [
        {
            "category": category,
            "diameter": f"M{10 + i}",
            "torque": "-65.123456789123456789",
            "torque_unit": "Nm" if i % 2 else "ft-lbs",
        }
        for i in range(6)
    ]
    rows += [{"category": category, "torque": "0"}, {"category": category}]
    for _ in range(2):
        result = post(client, report, token, rows)
        assert result.status_code == 200, result.text
        db_session.expire_all()
        assert len(report.fastener_rows) == 7
        assert [r.sequence_no for r in report.fastener_rows] == list(range(1, 8))
        assert report.fastener_rows[0].torque == Decimal("-65.123456789123456789")
        assert report.fastener_rows[-1].torque == Decimal(0)
    rows.pop(1)
    assert post(client, report, token, rows).status_code == 200
    db_session.expire_all()
    assert [r.diameter for r in report.fastener_rows] == [
        "M10",
        "M12",
        "M13",
        "M14",
        "M15",
        None,
    ]
    assert post(client, report, token).status_code == 200
    db_session.expire_all()
    assert report.fastener_rows == []


def test_invalid_torque_and_metadata(client, batch_draft):
    _, report, token = batch_draft
    for row in [
        {"torque_unit": "kg"},
        {"torque": "NaN"},
        {"torque": "0.0000000000000000001"},
    ]:
        assert (
            post(
                client, report, token, [{"category": "FAN_HARDWARE", **row}]
            ).status_code
            == 422
        )
    with pytest.raises(ValidationError):
        batch_c.TorqueRowInput(category="OTHER")
    assert batch_c.FINAL_REQUIRED_FIELDS == frozenset(
        [*READINGS, "de_nde_unit", *batch_c.SWITCH_FIELDS]
    )
    assert batch_c.BatchCDraftInput().model_dump()["radial_tir"] is None
    assert batch_c.FINAL_REQUIRED_CATEGORIES == {"FAN_HARDWARE"}
    assert all(
        batch_c.TorqueRowInput.model_fields[n].json_schema_extra["final_required"]
        for n in ("diameter", "torque", "torque_unit")
    )
    response = client.post(
        f"/ecr/reports/{report.id}/autosave",
        data={
            "csrf_token": token,
            "batch_c_present": "1",
            "erection_completion_date": "2026-10-02",
            "torque_TOWER_diameter": "M12",
        },
    )
    assert response.status_code == 422


def test_batch_c_scope_csrf_history_and_commit_failure(
    client, db_session, batch_draft, user_factory, branch_factory, monkeypatch
):
    owner, report, token = batch_draft
    row = [
        {
            "category": "FAN_HARDWARE",
            "diameter": "M12",
            "torque": "65",
            "torque_unit": "Nm",
        }
    ]
    assert post(client, report, "invalid", row).status_code == 403
    assert (
        post(
            client,
            report,
            token,
            row,
            de_top="0",
            supervisor_user_id=999,
            branch_id=999,
        ).status_code
        == 200
    )
    assert report.supervisor_user_id == owner.id and report.branch_id == owner.branch_id
    # Older A+B clients cannot erase Batch C.
    assert (
        post(
            client,
            report,
            token,
            batch_c_present="0",
            page2_present="1",
            oil_type="Oil",
        ).status_code
        == 200
    )
    db_session.expire_all()
    assert len(report.fastener_rows) == 1 and report.page2.de_top == 0
    with monkeypatch.context() as patch:

        def fail():
            raise OperationalError("commit", None, RuntimeError("simulated"))

        patch.setattr(db_session, "commit", fail)
        assert post(client, report, token, de_top="0.5").status_code == 503
    db_session.expire_all()
    assert report.page2.de_top == 0 and len(report.fastener_rows) == 1
    report.status = EcrReportStatus.REVIEWED
    db_session.commit()
    assert post(client, report, token, de_top="0.5").status_code == 409
    for role, branch, allowed in [
        (UserRole.SUPERADMIN, owner.branch, True),
        (UserRole.BRANCH_ADMIN, owner.branch, True),
        (UserRole.BRANCH_ADMIN, branch_factory(code="DELHI"), False),
        (UserRole.SUPERVISOR, owner.branch, False),
    ]:
        user = user_factory(role=role, branch=branch)
        client.cookies.clear()
        login(client, user.employee_id)
        path = (
            f"/ecr/reports/{report.id}"
            if role == UserRole.SUPERVISOR
            else f"/reports/{report.id}"
        )
        response = client.get(path)
        assert response.status_code == (200 if allowed else 404)
        if allowed:
            assert (
                "M12" in response.text
                and 'data-reading-value="de_top">0</dd>' in response.text
            )
            assert "data-batch_c-field" not in response.text
        assert post(
            client, report, csrf_from(client.get("/dashboard").text), de_top="0.5"
        ).status_code == (404 if role == UserRole.SUPERVISOR else 403)
    owner.status = UserStatus.DISABLED
    db_session.commit()
    assert report.page2.de_top == 0 and report.fastener_rows[0].diameter == "M12"


def test_mysql_constraints_and_storage(db_session, batch_draft):
    _, report, _ = batch_draft
    inspector = inspect(db_session.get_bind())
    columns = {c["name"]: c for c in inspector.get_columns("ecr_page2_technical")}
    assert columns["radial_tir"]["type"].scale == 3
    assert columns["de_top"]["type"].scale == 2
    assert all(columns[n]["nullable"] for n in batch_c.SCALAR_FIELDS)
    assert (
        inspector.get_foreign_keys("ecr_fastener_torque_rows")[0]["options"]["ondelete"]
        == "RESTRICT"
    )
    for overrides in [
        {"category": "OTHER"},
        {"sequence_no": 0},
        {"torque_unit": "kg"},
        {"report_id": 2**63 - 1},
    ]:
        with (
            pytest.raises((IntegrityError, OperationalError)),
            db_session.begin_nested(),
        ):
            db_session.add(
                EcrFastenerTorqueRow(
                    **{
                        "report_id": report.id,
                        "category": "TOWER",
                        "sequence_no": 1,
                        **overrides,
                    }
                )
            )
            db_session.flush()
    db_session.add(
        EcrFastenerTorqueRow(report_id=report.id, category="TOWER", sequence_no=1)
    )
    db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            EcrFastenerTorqueRow(report_id=report.id, category="TOWER", sequence_no=1)
        )
        db_session.flush()
