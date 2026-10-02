"""Page 2 capture, Series authority and package ownership regressions on MySQL."""

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.ecr import page1, page2
from app.ecr.models import EcrPage2Technical, EcrReportStatus
from app.ecr.series import COOLING_TOWER_SERIES, applicable_sections
from app.ecr.services import can_edit_series
from app.users.models import UserRole, UserStatus
from tests.conftest import csrf_from, login
from tests.test_page1 import FormInspector
from tests.test_phase3 import _create_report, _creation_payload, _draft_input, _serial


@pytest.fixture
def batch_draft(client, db_session, user_factory):
    owner = user_factory()
    report = _create_report(db_session, owner, cooling_tower_series="AQ-3800")
    login(client, owner.employee_id)
    token = csrf_from(client.get(f"/ecr/reports/{report.id}/edit").text)
    return owner, report, token


def post(client, report, token, **fields):
    return client.post(
        f"/ecr/reports/{report.id}/autosave",
        data={
            "csrf_token": token,
            "page2_present": "1",
            "erection_completion_date": "2026-10-02",
            **fields,
        },
    )


SAMPLES = {
    "eliminator_type": "Eliminator sample",
    "fc_valve_diameter": "2.123456789123456789",
    "fc_valve_count": "2",
    "nozzle_type": "Nozzle sample",
    "nozzle_count_per_cell": "12",
    "nozzle_part_no": "Part / 01",
    "bearing_housing_type": "Housing sample",
    "bearing_housing_serial_no": "BH-01",
    "belt_type": "Belt sample",
    "belt_section_length": "Combined nomenclature",
    "small_pulley_od": "0",
    "large_pulley_od": "12.5",
    "belts_used_count": "3",
    "uniform_belt_tension": "No",
    "pulley_construction": "With QD Bushing",
    "oil_type": "Owner oil text",
    "oil_level_checked": "Yes",
    "oil_seal_leakage": "No",
    "general_tower_hardware": "HDG",
}


@pytest.mark.parametrize("field,value", SAMPLES.items())
def test_partial_scalar_roundtrip(client, db_session, batch_draft, field, value):
    _, report, token = batch_draft
    response = post(client, report, token, **{field: value})
    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert page1.display_value(getattr(report.page2, field)) == value
    assert response.json()["values"]["page2"][field] == value
    html = client.get(f"/ecr/reports/{report.id}/edit").text
    assert f'value="{value}"' in html


@pytest.mark.parametrize(
    "field,value",
    [
        (name, option)
        for _, fields in page2.SECTIONS
        for name, _, kind, options in fields
        if kind == "select"
        for option in options
    ],
)
def test_approved_choices(client, batch_draft, field, value):
    _, report, token = batch_draft
    assert post(client, report, token, **{field: value}).status_code == 200


@pytest.mark.parametrize(
    "field,value",
    [
        *[
            (name, value)
            for name in ("fc_valve_count", "nozzle_count_per_cell", "belts_used_count")
            for value in ("0", "-1", "1.5", "9223372036854775808")
        ],
        *[
            (name, value)
            for name in ("fc_valve_diameter", "small_pulley_od", "large_pulley_od")
            for value in ("-0.1", "NaN", "Infinity", "0.0000000000000000001")
        ],
        ("general_tower_hardware", "STL/HDG"),
        ("general_tower_hardware", "arbitrary"),
        ("general_tower_hardware", "HDG,SS304"),
        ("pulley_construction", "arbitrary"),
        ("oil_level_checked", "Unknown"),
        ("oil_seal_leakage", "N/A"),
        ("uniform_belt_tension", "true"),
    ],
)
def test_invalid_supplied_values_preserve_committed_data(
    client, db_session, batch_draft, field, value
):
    _, report, token = batch_draft
    assert post(client, report, token, **SAMPLES).status_code == 200
    response = post(client, report, token, **{field: value})
    assert response.status_code == 422, response.text
    assert response.json()["ok"] is False
    db_session.expire_all()
    assert page2.page2_values(report) == SAMPLES


@pytest.mark.parametrize("series", COOLING_TOWER_SERIES)
def test_series_validation_applicability_and_requiredness(
    client, db_session, batch_draft, series
):
    owner, report, token = batch_draft
    response = post(client, report, token, cooling_tower_series=series)
    assert response.status_code == 200
    db_session.expire_all()
    assert report.tower.package.cooling_tower_series == series
    aq = series == "AQ-3800"
    gear = series not in ("AQ-3800", "CF-I")
    assert ("bearing_housing_type" in page2.required_fields(series)) is aq
    assert ("belts_used_count" in page2.required_fields(series)) is aq
    assert ("gearbox_series" in page1.required_fields(series)) is gear
    for field in ("drive_shaft_series", "drive_shaft_class", "drive_shaft_serial_no"):
        assert (field in page1.required_fields(series)) is gear
    assert "drive_shaft_oal" not in page1.required_fields(series)
    assert "drive_shaft_oal_unit" not in page1.required_fields(series)
    assert {
        "nozzle_part_no",
        "oil_type",
        "oil_level_checked",
        "oil_seal_leakage",
        "general_tower_hardware",
    } <= page2.required_fields(series)
    assert can_edit_series(db_session, report, owner)
    # Also validate normal create, independently of the editable Draft.
    assert (
        _draft_input(_serial(), cooling_tower_series=series).cooling_tower_series
        == series
    )


@pytest.mark.parametrize("value", ["", "Other Series", "aq-3800", "Series 99"])
def test_arbitrary_series_write_rejected(client, db_session, batch_draft, value):
    _, report, token = batch_draft
    with pytest.raises(ValidationError):
        _draft_input(_serial(), cooling_tower_series=value)
    response = post(client, report, token, cooling_tower_series=value)
    assert response.status_code == 422
    db_session.expire_all()
    assert report.tower.package.cooling_tower_series == "AQ-3800"


def test_no_defaults_and_conditional_metadata(client, batch_draft):
    _, report, token = batch_draft
    assert post(client, report, token).status_code == 200
    parser = FormInspector()
    parser.feed(client.get(f"/ecr/reports/{report.id}/edit").text)
    for name, field in page2.Page2DraftInput.model_fields.items():
        assert not field.is_required()
        assert getattr(page2.Page2DraftInput(), name) is None
        assert EcrPage2Technical.__table__.c[name].nullable
        if name in parser.inputs:
            assert parser.inputs[name]["value"] == ""
    assert "belt_section_length" not in page2.required_fields("AQ-3800")
    assert "pulley_construction" not in page2.required_fields("AQ-3800")
    assert "eliminator_type" not in page2.required_fields("AQ-3800")
    assert page2.required_fields("AQ-3800") == {
        "nozzle_part_no",
        "bearing_housing_type",
        "bearing_housing_serial_no",
        "belt_type",
        "belts_used_count",
        "uniform_belt_tension",
        "oil_type",
        "oil_level_checked",
        "oil_seal_leakage",
        "general_tower_hardware",
    }
    for name, choices in parser.selects.items():
        if name in page2.Page2DraftInput.model_fields:
            assert [option["value"] for option in choices if "selected" in option] == [
                ""
            ]


@pytest.mark.parametrize(
    "name", ["fc_valve_count", "nozzle_count_per_cell", "belts_used_count"]
)
@pytest.mark.parametrize("value", ["1.0", "2e1", "+2"])
def test_counts_reject_decimal_or_exponent_notation(client, batch_draft, name, value):
    _, report, token = batch_draft
    assert post(client, report, token, **{name: value}).status_code == 422


def test_series_switch_preserves_hidden_data_and_ignores_crafted_fields(
    client, db_session, batch_draft
):
    _, report, token = batch_draft
    assert post(client, report, token, **SAMPLES).status_code == 200
    response = post(
        client,
        report,
        token,
        cooling_tower_series="Series 10",
        page1_present="1",
        gearbox_series="22.2",
        gearbox_serial_no="G-SAVED",
        drive_shaft_series="6Q",
        drive_shaft_class="CL II",
        drive_shaft_serial_no="DS-SAVED",
        drive_shaft_oal="12.5",
        drive_shaft_oal_unit="inches",
        bearing_housing_type="CRAFTED",
        small_pulley_od="-5",
    )
    assert response.status_code == 200, response.text
    assert "bearing_housing_type" not in response.json()["values"]["page2"]
    db_session.expire_all()
    assert report.page2.bearing_housing_type == SAMPLES["bearing_housing_type"]
    assert report.page1.gearbox_serial_no == "G-SAVED"
    assert (
        post(
            client,
            report,
            token,
            cooling_tower_series="CF-I",
            page1_present="1",
            gearbox_serial_no="CRAFTED",
            drive_shaft_serial_no="CRAFTED",
            drive_shaft_series="INVALID",
            drive_shaft_oal="7",
        ).status_code
        == 200
    )
    db_session.expire_all()
    assert report.page1.gearbox_serial_no == "G-SAVED"
    assert report.page1.drive_shaft_serial_no == "DS-SAVED"
    assert page1.display_value(report.page1.drive_shaft_oal) == "12.5"
    assert report.page1.drive_shaft_oal_unit == "inches"
    # A Series-only update does not replace either technical snapshot.
    assert (
        post(
            client, report, token, cooling_tower_series="AQ-3800", page2_present="0"
        ).status_code
        == 200
    )
    html = client.get(f"/ecr/reports/{report.id}/edit").text
    assert f'value="{SAMPLES["bearing_housing_type"]}"' in html


@pytest.mark.parametrize(
    "restriction", ["second_report", "other_creator", "other_owner", "non_draft"]
)
def test_series_owner_restrictions(
    client, db_session, batch_draft, user_factory, restriction
):
    owner, report, token = batch_draft
    if restriction == "second_report":
        _create_report(
            db_session,
            user_factory(),
            serial_no=report.tower.package.cooling_tower_serial_no,
            cooling_tower_series="AQ-3800",
            cell_no=2,
        )
    elif restriction == "other_creator":
        report.tower.package.created_by_user_id = user_factory().id
    elif restriction == "other_owner":
        report.supervisor_user_id = user_factory().id
    else:
        report.status = EcrReportStatus.SUBMITTED
    db_session.commit()
    assert not can_edit_series(db_session, report, owner)
    response = post(client, report, token, cooling_tower_series="Series 10")
    assert response.status_code == (404 if restriction == "other_owner" else 409)
    db_session.expire_all()
    assert report.tower.package.cooling_tower_series == "AQ-3800"
    if restriction in ("second_report", "other_creator"):
        html = client.get(f"/ecr/reports/{report.id}/edit").text
        assert "data-series-select" not in html
        assert (
            post(client, report, token, oil_type="Still editable Draft").status_code
            == 200
        )


def test_legacy_safe_read_reuse_and_no_silent_conversion(
    client, db_session, batch_draft
):
    _, report, token = batch_draft
    report.tower.package.cooling_tower_series = "Legacy Unknown"
    db_session.commit()
    assert (
        post(
            client,
            report,
            token,
            oil_type="Safe legacy save",
            cooling_tower_series="Legacy Unknown",
        ).status_code
        == 200
    )
    html = client.get(f"/ecr/reports/{report.id}/edit").text
    assert "Legacy Unknown" in html and "unclassified" in html
    response = client.post(
        "/ecr/reports",
        data=_creation_payload(
            token,
            report.tower.package.cooling_tower_serial_no,
            cooling_tower_series="Legacy Unknown",
            cell_no=2,
        ),
    )
    assert response.status_code == 303, response.text
    db_session.expire_all()
    assert report.tower.package.cooling_tower_series == "Legacy Unknown"
    assert applicable_sections("Legacy Unknown") == frozenset()
    assert (
        client.post(
            "/ecr/reports",
            data=_creation_payload(
                token, _serial(), cooling_tower_series="Legacy Unknown"
            ),
        ).status_code
        == 400
    )


@pytest.mark.parametrize("series", ["AQ-3800", "CF-I", "Series 10", "CF-II"])
def test_readonly_scope_and_no_edit(
    client, db_session, batch_draft, user_factory, branch_factory, series
):
    owner, report, token = batch_draft
    assert post(client, report, token, **SAMPLES).status_code == 200
    assert (
        post(
            client, report, token, cooling_tower_series=series, page2_present="0"
        ).status_code
        == 200
    )
    for role in (UserRole.BRANCH_ADMIN, UserRole.SUPERADMIN):
        admin = user_factory(role=role, branch=owner.branch)
        client.cookies.clear()
        login(client, admin.employee_id)
        html = client.get(f"/reports/{report.id}").text
        for section in (
            "Gearboxes",
            "Drive Shafts",
            "Bearing Housing",
            "Belt &amp; Pulleys",
        ):
            assert (f"<h2>{section}</h2>" in html) is (
                section.replace("&amp;", "&") in applicable_sections(series)
            )
        assert "Yes = leakage observed" in html
        assert "Safe legacy" not in html
        assert (
            post(
                client,
                report,
                csrf_from(client.get("/dashboard").text),
                oil_type="Forbidden",
            ).status_code
            == 403
        )
    outsider = user_factory(
        role=UserRole.BRANCH_ADMIN, branch=branch_factory(code="DELHI")
    )
    client.cookies.clear()
    login(client, outsider.employee_id)
    assert client.get(f"/reports/{report.id}").status_code == 404
    client.cookies.clear()
    login(client, user_factory().employee_id)
    assert client.get(f"/ecr/reports/{report.id}/edit").status_code == 404
    assert (
        post(
            client,
            report,
            csrf_from(client.get("/dashboard").text),
            oil_type="Forbidden",
        ).status_code
        == 404
    )


def test_csrf_ownership_and_rollback(
    client, db_session, batch_draft, user_factory, monkeypatch
):
    from sqlalchemy.exc import OperationalError

    owner, report, token = batch_draft
    assert post(client, report, "invalid", oil_type="bad").status_code == 403
    assert (
        post(
            client,
            report,
            token,
            **SAMPLES,
            supervisor_user_id=user_factory().id,
            branch_id=999,
        ).status_code
        == 200
    )
    assert report.supervisor_user_id == owner.id and report.branch_id == owner.branch_id
    with monkeypatch.context() as patch:

        def fail():
            raise OperationalError("commit", None, RuntimeError("simulated failure"))

        patch.setattr(db_session, "commit", fail)
        response = post(
            client,
            report,
            token,
            cooling_tower_series="Series 10",
            oil_type="Not committed",
        )
        assert response.status_code == 503 and response.json()["ok"] is False
    db_session.expire_all()
    assert report.page2.oil_type == SAMPLES["oil_type"]
    assert report.tower.package.cooling_tower_series == "AQ-3800"
    owner.status = UserStatus.DISABLED
    db_session.commit()
    assert db_session.get(EcrPage2Technical, report.page2.id) is not None


def test_table_integrity_and_numeric_storage(db_session, batch_draft):
    _, report, _ = batch_draft
    db_session.add(EcrPage2Technical(report_id=report.id))
    db_session.commit()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(EcrPage2Technical(report_id=report.id))
        db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(EcrPage2Technical(report_id=2**63 - 1))
        db_session.flush()
    for name in ("fc_valve_diameter", "small_pulley_od", "large_pulley_od"):
        column = EcrPage2Technical.__table__.c[name]
        assert (column.type.precision, column.type.scale) == (38, 18)
    assert "ecr_page2_technical" in inspect(db_session.bind).get_table_names()


def test_supervisor_readonly_own_report_and_non_draft(
    client, db_session, batch_draft, user_factory
):
    _, report, token = batch_draft
    assert post(client, report, token, **SAMPLES).status_code == 200
    for status in EcrReportStatus:
        report.status = status
        db_session.commit()
        response = client.get(f"/ecr/reports/{report.id}")
        assert response.status_code == 200
        assert "<h2>Bearing Housing</h2>" in response.text
        assert "data-autosave-form" not in response.text
        if status != EcrReportStatus.DRAFT:
            assert post(client, report, token, oil_type="Forbidden").status_code == 409
    client.cookies.clear()
    login(client, user_factory().employee_id)
    assert client.get(f"/ecr/reports/{report.id}").status_code == 404


@pytest.mark.parametrize("hidden_series", ["AQ-3800", "CF-I"])
def test_drive_shaft_hidden_snapshot_cannot_clear_or_override_values(
    client, db_session, batch_draft, hidden_series
):
    _, report, token = batch_draft
    values = {
        "drive_shaft_series": "6Q",
        "drive_shaft_class": "CL I",
        "drive_shaft_serial_no": "PRESERVED-SHAFT-TEST",
        "drive_shaft_oal": "125.0625",
        "drive_shaft_oal_unit": "cm",
    }
    assert (
        post(
            client,
            report,
            token,
            cooling_tower_series="Series 10",
            page1_present="1",
            **values,
        ).status_code
        == 200
    )
    assert (
        post(
            client,
            report,
            token,
            cooling_tower_series=hidden_series,
            page1_present="1",
            drive_shaft_series="INVALID",
            drive_shaft_oal="7",
            show_drive_shaft="true",
        ).status_code
        == 200
    )
    db_session.expire_all()
    for name, value in values.items():
        assert page1.display_value(getattr(report.page1, name)) == value
        assert name not in page1.required_fields(hidden_series)
    readonly = client.get(f"/ecr/reports/{report.id}").text
    assert "<h2>Drive Shafts</h2>" not in readonly
    assert "PRESERVED-SHAFT-TEST" not in readonly
    assert (
        post(
            client, report, token, cooling_tower_series="Series 10", page2_present="0"
        ).status_code
        == 200
    )
    readonly = client.get(f"/ecr/reports/{report.id}").text
    assert "<h2>Drive Shafts</h2>" in readonly
    assert "PRESERVED-SHAFT-TEST" in readonly


def test_series_reconciliation_is_readonly_owner_scoped(
    client, db_session, batch_draft, user_factory
):
    owner, report, _ = batch_draft
    url = f"/ecr/reports/{report.id}/series-state"
    response = client.get(url)
    assert response.status_code == 200
    assert response.json() == {"series": "AQ-3800", "editable": True}
    assert response.headers["cache-control"] == "no-store"
    report.status = EcrReportStatus.SUBMITTED
    db_session.commit()
    assert client.get(url).json() == {"series": "AQ-3800", "editable": False}
    for role in (UserRole.SUPERVISOR, UserRole.BRANCH_ADMIN, UserRole.SUPERADMIN):
        other = user_factory(role=role, branch=owner.branch)
        client.cookies.clear()
        login(client, other.employee_id)
        assert client.get(url).status_code == (
            404 if role == UserRole.SUPERVISOR else 403
        )
    client.cookies.clear()
    assert client.get(url).status_code in (303, 401)
