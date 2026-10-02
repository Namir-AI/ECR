"""Frozen Page 1 capture, MySQL integrity, authorization and save regressions."""

import re
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.ecr.models import EcrFanBladeSerial, EcrPage1Technical, EcrReportStatus
from app.ecr.page1 import (
    FINAL_REQUIRED_FIELDS,
    SECTIONS,
    Page1DraftInput,
    display_value,
    page1_values,
    save_page1,
)
from app.users.models import UserRole, UserStatus
from tests.conftest import csrf_from, login
from tests.test_phase3 import _create_report


@pytest.fixture
def technical_draft(client, db_session, user_factory):
    owner = user_factory()
    report = _create_report(db_session, owner)
    login(client, owner.employee_id)
    token = csrf_from(client.get(f"/ecr/reports/{report.id}/edit").text)
    return owner, report, token


def post_snapshot(client, report, token, **fields):
    return client.post(
        f"/ecr/reports/{report.id}/autosave",
        data={
            "csrf_token": token,
            "page1_present": "1",
            "erection_completion_date": "2026-10-02",
            **fields,
        },
    )


SAMPLES = {
    "motor_make": "Test motor",
    "motor_serial_no": "M/01",
    "motor_hp": "12.34567890123456789",
    "motor_frame": "Frame-X",
    "motor_insulation": "Insulation-X",
    "motor_mounting": "Foot",
    "motor_speed": "Single",
    "motor_rpm": "1450",
    "motor_full_load_current": "12.75",
    "motor_current_drawn": "11.5",
    "fan_serial_no": "F-01",
    "fan_diameter_type": "192IN XX-XX-XX",
    "fan_hardware": "HDG",
    "fan_no_of_blades": "12",
    "fan_pitch_angle": "13.125",
    "fan_hub_cover": "No",
    "fan_cylinder_height": "80.125",
    "fan_cylinder_material": "FRP",
    "blade_tip_clearance": "0.125",
    "blade_tip_track_variation": "0.0625",
    "drive_shaft_series": "6Q",
    "drive_shaft_class": "CL I",
    "drive_shaft_oal": "123.456",
    "drive_shaft_oal_unit": "cm",
    "drive_shaft_serial_no": "D-01",
    "gearbox_series": "22.2",
    "gearbox_ratio": "owner-style / ratio",
    "gearbox_serial_no": "G-01",
    "gearbox_model_no": "GM-01",
    "fill_type": "Fill nomenclature",
    "fill_material": "PVC",
}


@pytest.mark.parametrize("field,value", list(SAMPLES.items()))
def test_scalar_round_trip_and_partial_draft(
    client, db_session, technical_draft, field, value
):
    _owner, report, token = technical_draft
    payload = {field: value}
    if field == "drive_shaft_oal":
        payload["drive_shaft_oal_unit"] = "cm"
    elif field == "drive_shaft_oal_unit":
        payload["drive_shaft_oal"] = "123"
    response = post_snapshot(client, report, token, **payload)
    assert response.status_code == 200, response.text
    assert response.json()["values"]["page1"][field] == value
    db_session.expire_all()
    persisted = db_session.scalar(
        select(EcrPage1Technical).where(EcrPage1Technical.report_id == report.id)
    )
    assert display_value(getattr(persisted, field)) == value
    page = client.get(f"/ecr/reports/{report.id}/edit")
    assert f'value="{value}"' in page.text or f'value="{value}" selected' in page.text
    # Unrelated final-required fields remain blank; partial Draft is allowed.
    other = "motor_serial_no" if field != "motor_serial_no" else "motor_make"
    assert getattr(persisted, other) is None


CHOICES = [
    (name, option)
    for _section, fields in SECTIONS
    for name, _label, kind, options in fields
    if kind == "select"
    for option in options
]


@pytest.mark.parametrize("field,value", CHOICES)
def test_all_approved_choices(client, technical_draft, field, value):
    _owner, report, token = technical_draft
    payload = {field: value}
    if field == "drive_shaft_oal_unit":
        payload["drive_shaft_oal"] = "123"
    assert post_snapshot(client, report, token, **payload).status_code == 200


@pytest.mark.parametrize(
    "field,value",
    [
        *((name, "unapproved") for name in dict(CHOICES)),
        ("fan_no_of_blades", "0"),
        ("fan_no_of_blades", "-1"),
        ("fan_no_of_blades", "1.5"),
        ("fan_no_of_blades", "9223372036854775808"),
        ("motor_hp", "not-numeric"),
        ("motor_hp", "NaN"),
        ("motor_hp", "Infinity"),
        ("motor_hp", "0.1234567890123456789"),
        ("motor_hp", "100000000000000000000"),
        ("motor_hp", "-0.1"),
        ("motor_hp", "-5"),
        ("drive_shaft_oal", "20"),
        ("drive_shaft_oal_unit", "cm"),
    ],
)
def test_invalid_save_does_not_change_committed_values(
    client, db_session, technical_draft, field, value
):
    _owner, report, token = technical_draft
    assert (
        post_snapshot(
            client, report, token, motor_make="Previously committed"
        ).status_code
        == 200
    )
    response = post_snapshot(
        client, report, token, motor_make="Must not commit", **{field: value}
    )
    assert response.status_code == 422
    assert response.json()["ok"] is False
    db_session.expire_all()
    assert report.page1.motor_make == "Previously committed"
    assert report.erection_completion_date.isoformat() == "2026-10-02"


def test_blade_snapshot_order_removal_blank_filter_and_idempotency(
    client, db_session, technical_draft
):
    _owner, report, token = technical_draft
    blades = [f"Blade-{i}" for i in range(12)] + ["", "   ", " Blade-12 "]
    for _ in range(2):
        response = post_snapshot(
            client, report, token, blade_serials=blades, fan_no_of_blades="2"
        )
        assert response.status_code == 200
    db_session.expire_all()
    assert [b.serial_no for b in report.page1.blade_serials] == [
        f"Blade-{i}" for i in range(13)
    ]
    assert [b.position for b in report.page1.blade_serials] == list(range(1, 14))
    assert (
        post_snapshot(
            client, report, token, blade_serials=["Last", "First", "Last"]
        ).status_code
        == 200
    )
    db_session.expire_all()
    assert [b.serial_no for b in report.page1.blade_serials] == [
        "Last",
        "First",
        "Last",
    ]
    assert "Last" in client.get(f"/ecr/reports/{report.id}/edit").text
    assert post_snapshot(client, report, token).status_code == 200
    db_session.expire_all()
    assert report.page1.blade_serials == []


def test_decimal_and_oal_no_conversion_or_rounding(client, db_session, technical_draft):
    _owner, report, token = technical_draft
    for unit in ["cm", "inches"]:
        response = post_snapshot(
            client,
            report,
            token,
            drive_shaft_oal="12.345678901234567891",
            drive_shaft_oal_unit=unit,
            motor_hp="0",
            fan_pitch_angle="-1.25",
        )
        assert response.status_code == 200
        db_session.expire_all()
        assert report.page1.drive_shaft_oal == Decimal("12.345678901234567891")
        assert report.page1.drive_shaft_oal_unit == unit


class FormInspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs, self.selects = {}, {}
        self.current_select = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and "data-page1-field" in attrs:
            self.inputs[attrs["name"]] = attrs
        if tag == "select":
            self.current_select = attrs.get("name")
            self.selects[self.current_select] = []
        if tag == "option" and self.current_select:
            self.selects[self.current_select].append(attrs)

    def handle_endtag(self, tag):
        if tag == "select":
            self.current_select = None


def test_new_form_has_no_technical_defaults(client, technical_draft):
    _owner, report, _token = technical_draft
    parser = FormInspector()
    parser.feed(client.get(f"/ecr/reports/{report.id}/edit").text)
    assert all(field["value"] == "" for field in parser.inputs.values())
    assert all("required" not in field for field in parser.inputs.values())
    for choices in parser.selects.values():
        assert [c["value"] for c in choices if "selected" in c] == [""]
    for name in ["gearbox_series", "gearbox_ratio"]:
        assert parser.inputs[name]["type"] == "text"


def test_database_structure_constraints_and_history(db_session, user_factory):
    owner = user_factory()
    report = _create_report(db_session, owner)
    save_page1(
        db_session,
        report,
        Page1DraftInput(**SAMPLES, blade_serials=["First", "Second"]),
    )
    db_session.commit()
    indexes = inspect(db_session.bind).get_indexes("ecr_page1_technical")
    for field in [
        "motor_serial_no",
        "fan_serial_no",
        "drive_shaft_serial_no",
        "gearbox_serial_no",
    ]:
        assert any(i["column_names"] == [field] and not i["unique"] for i in indexes)
    second = _create_report(db_session, owner)
    save_page1(db_session, second, Page1DraftInput(**SAMPLES))
    db_session.commit()  # Identical equipment serials are allowed in another report.
    for invalid in [
        EcrPage1Technical(report_id=report.id),
        EcrPage1Technical(report_id=999999999999),
        EcrFanBladeSerial(
            technical_id=report.page1.id, position=1, serial_no="Duplicate position"
        ),
    ]:
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.add(invalid)
            db_session.flush()
    owner.status = UserStatus.DISABLED
    db_session.commit()
    assert page1_values(report)["motor_make"] == SAMPLES["motor_make"]
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.delete(owner)
        db_session.flush()
    assert db_session.get(EcrPage1Technical, report.page1.id) is not None


def test_owner_identity_csrf_status_and_date_client_compatibility(
    client, db_session, technical_draft, user_factory
):
    owner, report, token = technical_draft
    original_branch = report.branch_id
    other = user_factory()
    assert (
        post_snapshot(
            client,
            report,
            token,
            motor_make="Safe",
            supervisor_user_id=other.id,
            branch_id=other.branch_id,
            report_id=999,
        ).status_code
        == 200
    )
    assert report.supervisor_user_id == owner.id and report.branch_id == original_branch
    assert post_snapshot(client, report, "invalid", motor_make="Bad").status_code == 403
    assert (
        client.post(
            f"/ecr/reports/{report.id}/autosave",
            data={"csrf_token": token, "erection_completion_date": "2026-10-03"},
        ).status_code
        == 200
    )
    assert report.page1.motor_make == "Safe"
    report.status = EcrReportStatus.SUBMITTED
    db_session.commit()
    assert post_snapshot(client, report, token, motor_make="Bad").status_code == 409
    assert report.page1.motor_make == "Safe"
    report.status = EcrReportStatus.DRAFT
    db_session.commit()
    client.cookies.clear()
    login(client, other.employee_id)
    token = csrf_from(client.get("/dashboard").text)
    assert client.get(f"/ecr/reports/{report.id}/edit").status_code == 404
    assert post_snapshot(client, report, token, motor_make="Bad").status_code == 404


def test_admin_read_only_scope_after_supervisor_transfer(
    client, db_session, user_factory, branch_factory
):
    delhi, mumbai = branch_factory(code="DELHI"), branch_factory(code="MUMBAI")
    owner = user_factory(branch=delhi)
    report = _create_report(db_session, owner)
    save_page1(
        db_session,
        report,
        Page1DraftInput(motor_make="Scoped motor", blade_serials=["B1"]),
    )
    owner.branch_id = mumbai.id
    db_session.commit()
    for role, branch, expected in [
        (UserRole.BRANCH_ADMIN, delhi, 200),
        (UserRole.BRANCH_ADMIN, mumbai, 404),
        (UserRole.SUPERADMIN, mumbai, 200),
    ]:
        admin = user_factory(role=role, branch=branch)
        client.cookies.clear()
        login(client, admin.employee_id)
        response = client.get(f"/reports/{report.id}")
        assert response.status_code == expected
        if expected == 200:
            assert "Scoped motor" in response.text and "B1" in response.text
            assert "data-autosave-form" not in response.text
        token = csrf_from(client.get("/dashboard").text)
        assert post_snapshot(client, report, token, motor_make="Bad").status_code == 403


def test_database_failure_is_not_acknowledged(
    client, db_session, technical_draft, monkeypatch
):
    _owner, report, token = technical_draft
    assert (
        post_snapshot(client, report, token, motor_make="Committed").status_code == 200
    )

    def fail_commit():
        raise OperationalError("test failure", None, Exception("simulated"))

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail_commit)
        response = post_snapshot(client, report, token, motor_make="Uncommitted")
        assert response.status_code == 503 and response.json()["ok"] is False
    db_session.expire_all()
    assert report.page1.motor_make == "Committed"


FINAL_OPTIONAL = {
    "motor_frame",
    "motor_insulation",
    "motor_mounting",
    "drive_shaft_oal",
    "drive_shaft_oal_unit",
    "gearbox_model_no",
    "fill_type",
    "blade_serials",
}
FINAL_REQUIRED = {
    "motor_make",
    "motor_serial_no",
    "motor_hp",
    "motor_speed",
    "motor_rpm",
    "motor_full_load_current",
    "motor_current_drawn",
    "fan_serial_no",
    "fan_diameter_type",
    "fan_hardware",
    "fan_no_of_blades",
    "fan_pitch_angle",
    "fan_hub_cover",
    "fan_cylinder_height",
    "fan_cylinder_material",
    "blade_tip_clearance",
    "blade_tip_track_variation",
    "drive_shaft_series",
    "drive_shaft_class",
    "drive_shaft_serial_no",
    "gearbox_series",
    "gearbox_ratio",
    "gearbox_serial_no",
    "fill_material",
}


@pytest.mark.parametrize("field", sorted(FINAL_OPTIONAL | FINAL_REQUIRED))
def test_final_requiredness_schema_ui_and_nullable_draft(
    client,
    technical_draft,
    field,
):
    _owner, report, _token = technical_draft
    expected = field in FINAL_REQUIRED
    assert (field in FINAL_REQUIRED_FIELDS) is expected
    model_field = Page1DraftInput.model_fields[field]
    assert model_field.json_schema_extra["final_required"] is expected
    assert not model_field.is_required()  # Final-required is NOT Draft-required.
    schema = Page1DraftInput.model_json_schema()
    assert schema["properties"][field]["final_required"] is expected
    if field != "blade_serials":
        assert EcrPage1Technical.__table__.c[field].nullable
        assert getattr(Page1DraftInput(), field) is None
        html = client.get(f"/ecr/reports/{report.id}/edit").text
        label = re.search(rf'<label for="{field}"[^>]*>(.*?)</label>', html, re.DOTALL)
        assert label is not None
        assert ("*" in label.group(1)) is expected
        assert " required" not in label.group(1)


@pytest.mark.parametrize(
    "field,value,accepted",
    [
        ("motor_full_load_current", "", True),
        ("motor_full_load_current", "0", False),
        ("motor_full_load_current", "0.0", False),
        ("motor_full_load_current", "-0.1", False),
        ("motor_full_load_current", "-10", False),
        ("motor_full_load_current", "0.1", True),
        ("motor_full_load_current", "1", True),
        ("motor_full_load_current", "12.5", True),
        ("motor_full_load_current", "18.75", True),
        ("motor_current_drawn", "", True),
        ("motor_current_drawn", "0", True),
        ("motor_current_drawn", "0.0", True),
        ("motor_current_drawn", "0.1", True),
        ("motor_current_drawn", "12", True),
        ("motor_current_drawn", "12.5", True),
        ("motor_current_drawn", "18.75", True),
        ("motor_current_drawn", "-0.1", False),
        ("motor_current_drawn", "-29", False),
        ("motor_full_load_current", "0.000000000000000001", True),
        ("motor_current_drawn", "12.345678901234567891", True),
        ("motor_full_load_current", "0.0000000000000000001", False),
        ("motor_current_drawn", "0.0000000000000000001", False),
        ("motor_full_load_current", "100000000000000000000", False),
        ("motor_current_drawn", "100000000000000000000", False),
    ],
)
def test_motor_current_rules_persistence_and_atomic_failure(
    client,
    db_session,
    technical_draft,
    field,
    value,
    accepted,
):
    _owner, report, token = technical_draft
    previous = "12.345678901234567891"
    assert (
        post_snapshot(
            client, report, token, motor_make="Committed motor", **{field: previous}
        ).status_code
        == 200
    )
    response = post_snapshot(
        client, report, token, motor_make="Changed motor", **{field: value}
    )
    assert response.status_code == (200 if accepted else 422), response.text
    assert response.json()["ok"] is accepted
    db_session.expire_all()
    persisted = getattr(report.page1, field)
    if accepted:
        assert persisted == (Decimal(value) if value else None)
        assert response.json()["values"]["page1"][field] == display_value(persisted)
    else:
        assert persisted == Decimal(previous)
        assert report.page1.motor_make == "Committed motor"
    page = client.get(f"/ecr/reports/{report.id}/edit")
    parser = FormInspector()
    parser.feed(page.text)
    assert parser.inputs[field]["value"] == display_value(persisted)
    if accepted and value in ["0", "0.0"]:
        assert persisted is not None and persisted == Decimal(0)
        assert parser.inputs[field]["value"] == "0"


@pytest.mark.parametrize(
    "oal,unit,accepted",
    [
        ("", "", True),
        ("12.5", "cm", True),
        ("12.5", "inches", True),
        ("12.5", "", False),
        ("", "cm", False),
        ("", "inches", False),
        ("0", "cm", True),
    ],
)
def test_optional_oal_pair_rules(
    client, db_session, technical_draft, oal, unit, accepted
):
    _owner, report, token = technical_draft
    response = post_snapshot(
        client, report, token, drive_shaft_oal=oal, drive_shaft_oal_unit=unit
    )
    assert response.status_code == (200 if accepted else 422)
    if accepted:
        db_session.expire_all()
        assert report.page1.drive_shaft_oal == (Decimal(oal) if oal else None)
        assert report.page1.drive_shaft_oal_unit == (unit or None)


def test_current_zero_and_optional_blanks_in_admin_readonly_view(
    client,
    db_session,
    technical_draft,
    user_factory,
):
    owner, report, token = technical_draft
    assert (
        post_snapshot(client, report, token, motor_current_drawn="0").status_code == 200
    )
    for role in [UserRole.BRANCH_ADMIN, UserRole.SUPERADMIN]:
        admin = user_factory(role=role, branch=owner.branch)
        client.cookies.clear()
        login(client, admin.employee_id)
        html = client.get(f"/reports/{report.id}").text
        assert "<dt>Current Drawn</dt><dd>0 Amps</dd>" in html
        for label in [
            "Frame",
            "Insulation",
            "Mounting",
            "OAL (Overall Length)",
            "OAL Unit",
            "Model No.",
            "Type (Specify nomenclature)",
        ]:
            assert f"<dt>{label}</dt><dd>Not entered</dd>" in html
    for field in ["motor_current_drawn", "motor_full_load_current"]:
        column = EcrPage1Technical.__table__.c[field]
        assert column.type.precision == 38 and column.type.scale == 18


def test_wholly_blank_optional_fields_do_not_prevent_draft_save(
    client, technical_draft
):
    _owner, report, token = technical_draft
    response = post_snapshot(client, report, token)
    assert response.status_code == 200
    for field in FINAL_OPTIONAL - {"blade_serials"}:
        assert response.json()["values"]["page1"][field] == ""


@pytest.mark.parametrize("value", ["", "0", "0.0", "5.5"])
def test_nonnegative_hp_remains_partial_and_exact(
    client, db_session, technical_draft, value
):
    _owner, report, token = technical_draft
    response = post_snapshot(client, report, token, motor_hp=value)
    assert response.status_code == 200
    db_session.expire_all()
    assert report.page1.motor_hp == (Decimal(value) if value else None)


@pytest.mark.parametrize(
    "value,accepted",
    [("", True), ("6Q", True), ("175", True), ("6q", False), ("arbitrary", False)],
)
def test_controlled_drive_shaft_series(
    client, db_session, technical_draft, value, accepted
):
    _owner, report, token = technical_draft
    response = post_snapshot(client, report, token, drive_shaft_series=value)
    assert response.status_code == (200 if accepted else 422)
    assert "drive_shaft_series" in FINAL_REQUIRED_FIELDS
    if accepted:
        db_session.expire_all()
        assert report.page1.drive_shaft_series == (value or None)
