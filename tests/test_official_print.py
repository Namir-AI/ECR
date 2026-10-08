"""Official structured-data mapping, Letter PDF, privacy and immutable identity."""

import base64
import importlib.util
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pypdf import PdfReader
from sqlalchemy import create_engine, text

from app.ecr import batch_c, page1, print_features
from app.ecr.models import EcrReportStatus
from app.ecr.pdf_renderer import PDF_SLOT, PdfRenderError, render_pdf, resource_fetcher
from app.ecr.print_viewmodel import (
    COMMENT_CORE_LINES,
    CONTINUATION_LINES,
    TEAM_CORE_LINES,
    PrintDataError,
    alignment,
    browser_file_title,
    build_print_viewmodel,
    value_text,
    wrapped_lines,
)
from app.ecr.workflow import transition
from app.storage import LocalProtectedStorage
from app.users.models import UserRole
from tests.conftest import csrf_from, login
from tests.test_page3 import png, sign
from tests.test_phase3 import _create_report
from tests.test_phase5 import complete_report


@pytest.fixture
def printed_report(client, db_session, user_factory, tmp_path):
    owner = user_factory()
    owner.full_name = "Nazmul Khan"
    report = complete_report(
        db_session,
        _create_report(
            db_session,
            owner,
            serial_no=f"26-2-{uuid4().hex[:8]}",
            cell_no=12,
            declared_no_of_cells=3,
            customer_order_no="PO/2026/14",
        ),
    )
    client.app.state.protected_storage = LocalProtectedStorage(tmp_path / "private")
    # Normal fitting fixture; extreme 18-place decimals are tested separately.
    report.page1.motor_hp = Decimal(25)
    report.page2.fc_valve_diameter = Decimal(2)
    db_session.commit()
    login(client, owner.employee_id)
    return report


def sections(vm, page=1):
    return {
        s.name: {f.label: f.value for f in s.fields}
        for s in (vm.page1_sections if page == 1 else vm.page2_sections)
    }


def pdf(client, report):
    if not print_features.SERVER_PDF_ENABLED:
        pytest.skip("Server PDF temporarily disabled for owner browser-print review")
    response = client.get(f"/ecr/reports/{report.id}/pdf")
    assert response.status_code == 200, (
        response.text if response.status_code != 200 else ""
    )
    return response, PdfReader(BytesIO(response.content))


def test_identity_dates_blanks_zero_units(printed_report):
    report = printed_report
    report.page1.motor_current_drawn = Decimal(0)
    report.page1.drive_shaft_oal = Decimal("72.00")
    report.page1.drive_shaft_oal_unit = "inches"
    report.page1.gearbox_ratio = "12:1"
    report.erection_start_date = None
    vm = build_print_viewmodel(report)
    header = {f.label: f.value for f in vm.identity}
    assert vm.serial == report.tower.display_name and vm.cell == 12
    assert vm.filename == f"ECR_{vm.serial}_Cell-12.pdf"
    assert header["Cell No."] == "12" and header["No. of Cells"] == "3"
    assert header["Customer Order No."] == "PO/2026/14"
    assert header["Erection Start Date"] == ""
    assert header["Erection Completion Date"] == "01-10-2026"
    data = sections(vm)
    assert data["Motor"]["Current Drawn"] == "0 Amps"
    assert data["Motor"]["Frame"] == ""
    assert data["Motor"]["Motor Serial No."] == report.page1.motor_serial_no
    assert data["Drive Shafts"]["OAL"] == "72 inches"
    assert data["Gearboxes"]["Ratio"] == "12:1"
    assert value_text(Decimal("0.000000000000000001")) == "0.000000000000000001"
    assert value_text(False) == "No" and value_text(None) == ""


@pytest.mark.parametrize(
    "series,na",
    [
        ("AQ-3800", {"Gearboxes", "Drive Shafts"}),
        ("CF-I", {"Gearboxes", "Drive Shafts", "Bearing Housing", "Belt & Pulleys"}),
        ("Series 10", {"Bearing Housing", "Belt & Pulleys"}),
        ("legacy", set()),
    ],
)
def test_series_central_applicability_no_stale_values(printed_report, series, na):
    report = printed_report
    report.tower.package.cooling_tower_series = series
    report.page1.drive_shaft_serial_no = "HIDDEN-SHAFT"
    report.page2.bearing_housing_serial_no = "HIDDEN-BEARING"
    vm = build_print_viewmodel(report)
    data = sections(vm) | sections(vm, 2)
    for section in na:
        assert set(data[section].values()) == {"N/A"}
    if series == "legacy":
        for section in (
            "Gearboxes",
            "Drive Shafts",
            "Bearing Housing",
            "Belt & Pulleys",
        ):
            assert set(data[section].values()) == {""}


def test_blades_torque_order_and_continuations(printed_report, db_session):
    report = printed_report
    values = page1.page1_values(report)
    values["blade_serials"] = [f"B-{i}" for i in range(1, 13)]
    page1.save_page1(db_session, report, page1.Page1DraftInput(**values))
    batch_c.save(
        db_session,
        report,
        batch_c.BatchCDraftInput(
            fastener_rows=[
                {
                    "category": category,
                    "diameter": f"M{i}",
                    "torque": str(i),
                    "torque_unit": "Nm" if i % 2 else "ft-lbs",
                }
                for category in ("FAN_HARDWARE", "MECH_HOLD_DOWN", "TOWER")
                for i in range(1, 7)
            ]
        ),
    )
    vm = build_print_viewmodel(report)
    assert vm.blades == tuple(f"B-{i}" for i in range(1, 9))
    assert vm.blades_overflow
    assert [c[0] for c in vm.torque] == [
        "Tower Fasteners",
        "Mechanical Equipment Hold Down Fasteners",
        "Fan Hardware Fastener",
    ]
    assert vm.torque[0][1] == (
        ("M1", "1 Nm"),
        ("M2", "2 ft-lbs"),
        ("M3", "3 Nm"),
        ("M4", "4 ft-lbs"),
    )
    overflow = "\n".join("\n".join(c.lines) for c in vm.continuations)
    assert "9: B-9" in overflow and "12: B-12" in overflow
    assert "Row 6: Diameter: M6; Torque: 6 ft-lbs" in overflow


def test_alignment_zero_negative_signed_and_independent_tir(printed_report):
    report = printed_report
    for end in ("de", "nde"):
        for side, val in zip(
            ("top", "right", "bottom", "left"), ("0.40", "0.00", "-0.10", "-1.00")
        ):
            setattr(report.page2, f"{end}_{side}", Decimal(val))
    report.page2.radial_tir, report.page2.axial_tir = (
        Decimal("-0.001"),
        Decimal("0.003"),
    )
    vm = build_print_viewmodel(report)
    assert vm.radial == "-0.001" and vm.axial == "0.003"
    assert vm.alignment_unit == "mm"
    for d in vm.alignment:
        assert (d.top, d.right, d.bottom, d.left) == ("0.40", "0.00", "-0.10", "-1.00")
        assert d.shift_y == "4.5" and d.shift_x == "9"
    report.page2.de_top = None
    report.page2.de_bottom = None
    neutral = build_print_viewmodel(report).alignment[0]
    assert neutral.top == neutral.bottom == "" and neutral.shift_y == "0"
    assert [f.value for f in vm.switches] == ["No", "No"]
    report.page2.oil_level_switch = None
    assert build_print_viewmodel(report).switches[1].value == ""


@pytest.mark.parametrize("end", ["de", "nde"])
@pytest.mark.parametrize("a,b,axis", [("top", "bottom", "y"), ("right", "left", "x")])
@pytest.mark.parametrize(
    "first,second,expected",
    [
        (None, "0.80", "0"),
        ("0.80", None, "0"),
        (None, None, "0"),
        ("0.00", "0.80", "-7.2"),
        ("0.80", "0.00", "7.2"),
        ("0.40", "-0.10", "4.5"),
        ("-0.10", "0.40", "-4.5"),
        ("1.00", "-1.00", "18"),
        ("-1.00", "1.00", "-18"),
    ],
)
def test_alignment_pair_completeness_and_explicit_zero(
    end, a, b, axis, first, second, expected
):
    values = {side: None for side in ("top", "right", "bottom", "left")}
    values[a] = None if first is None else Decimal(first)
    values[b] = None if second is None else Decimal(second)
    record = SimpleNamespace(**{f"{end}_{k}": v for k, v in values.items()})
    before = vars(record).copy()
    result = alignment(record, end)
    assert getattr(result, f"shift_{axis}") == expected
    assert getattr(result, f"shift_{'x' if axis == 'y' else 'y'}") == "0"
    for side, value in values.items():
        assert getattr(result, side) == ("" if value is None else f"{value:.2f}")
    assert vars(record) == before


@pytest.mark.parametrize(
    "suffix,installation,expected",
    [
        (None, "Test site", "26-2-0001 Test site ECR"),
        ("A", "Test site", "26-2-0001 A Test site ECR"),
        (None, "Test Site / Unit-2", "26-2-0001 Test Site Unit-2 ECR"),
        ("A", "  Test\tSite\n(Unit_2)  ", "26-2-0001 A Test Site (Unit_2) ECR"),
    ],
)
def test_browser_title_uses_authoritative_identity_not_cell(
    printed_report, suffix, installation, expected
):
    report = printed_report
    package = report.tower.package
    package.cooling_tower_serial_no = "26-2-0001"
    package.multiple_towers = suffix is not None
    package.place_of_installation = installation
    report.tower.tower_suffix = suffix
    report.tower.normalized_suffix_key = suffix or ""
    vm = build_print_viewmodel(report)
    assert vm.browser_document_title == expected
    assert not vm.browser_document_title.endswith(".pdf")
    assert "Cell" not in vm.browser_document_title
    assert package.place_of_installation == installation
    assert package.cooling_tower_serial_no == "26-2-0001"
    assert vm.cell == 12
    if installation == "Test Site / Unit-2":
        assert {f.label: f.value for f in vm.identity}[
            "Place of Installation"
        ] == installation


def test_browser_title_sanitizer_retains_safe_letters_and_spacing():
    assert browser_file_title('  Test<>:"/\\|?*\x00\x7f Site\n(Unit_2) - Café  ') == (
        "Test Site (Unit_2) - Café"
    )


def test_long_core_torque_preserves_row_identity(printed_report, db_session):
    batch_c.save(
        db_session,
        printed_report,
        batch_c.BatchCDraftInput(
            fastener_rows=[
                {
                    "category": "TOWER",
                    "diameter": "W" * 250,
                    "torque": str(i),
                    "torque_unit": "Nm",
                }
                for i in (1, 2)
            ]
        ),
    )
    vm = build_print_viewmodel(printed_report)
    assert vm.torque[0][1] == (
        ("See Continuation", "1 Nm"),
        ("See Continuation", "2 Nm"),
    )
    assert [c.section for c in vm.continuations] == [
        "Tower Fasteners — Row 1 — Diameter",
        "Tower Fasteners — Row 2 — Diameter",
    ]
    assert all("".join(c.lines) == "W" * 250 for c in vm.continuations)


def test_text_capacities_full_continuation_not_truncated(printed_report):
    report = printed_report
    text_value = "\n".join(
        f"Paragraph {i} — Ω <script> & punctuation." for i in range(100)
    )
    report.page3.team_leader_report = text_value
    report.page3.customer_comment = "VeryLongWord" * 600
    vm = build_print_viewmodel(report)
    assert vm.team_lines == vm.comment_lines == ("See Continuation",)
    continued = [c for c in vm.continuations if c.section.startswith("DETAILED REPORT")]
    assert "\n".join(line for c in continued for line in c.lines) == text_value
    assert all(len(c.lines) <= CONTINUATION_LINES for c in vm.continuations)
    report.page3.team_leader_report = "\n".join(["Line"] * TEAM_CORE_LINES)
    report.page3.customer_comment = "\n".join(["Line"] * COMMENT_CORE_LINES)
    vm = build_print_viewmodel(report)
    assert (
        len(vm.team_lines) == TEAM_CORE_LINES
        and len(vm.comment_lines) == COMMENT_CORE_LINES
    )
    assert "".join(wrapped_lines("Ω & " * 100, 100)) == "Ω & " * 100


def test_submission_snapshots_atomic_and_approval_unchanged(
    printed_report, client, db_session, user_factory
):
    report = printed_report
    owner = report.supervisor
    assert (
        report.supervisor_name_snapshot is None
        and report.supervisor_employee_id_snapshot is None
    )
    token = csrf_from(client.get("/dashboard").text)
    assert (
        client.post(
            f"/ecr/reports/{report.id}/review", data={"csrf_token": token}
        ).status_code
        == 303
    )
    assert report.supervisor_name_snapshot is None
    response = client.post(
        f"/ecr/reports/{report.id}/submit",
        data={
            "csrf_token": token,
            "supervisor_name_snapshot": "FORGED",
            "supervisor_employee_id_snapshot": "FORGED",
        },
    )
    assert response.status_code == 303
    db_session.refresh(report)
    old_id = owner.employee_id
    assert report.supervisor_name_snapshot == "Nazmul Khan"
    assert report.supervisor_employee_id_snapshot == old_id
    owner.full_name, owner.employee_id = "Different Person", "EDITED-EMPLOYEE"
    db_session.commit()
    vm = build_print_viewmodel(report)
    assert vm.supervisor_name == "Nazmul Khan" and vm.employee_id == old_id
    admin = user_factory(role=UserRole.SUPERADMIN)
    assert transition(db_session, report, admin, "approve") == {}
    db_session.commit()
    assert build_print_viewmodel(report).supervisor_name == "Nazmul Khan"


def test_snapshot_transition_rollback_is_atomic(printed_report, db_session):
    report = printed_report
    assert transition(db_session, report, report.supervisor, "review") == {}
    db_session.flush()
    with db_session.begin_nested() as tx:
        assert transition(db_session, report, report.supervisor, "submit") == {}
        db_session.flush()
        tx.rollback()
    db_session.refresh(report)
    assert report.status is EcrReportStatus.REVIEWED
    assert report.supervisor_name_snapshot is None and report.submitted_at is None


@pytest.mark.parametrize(
    "status", [EcrReportStatus.SUBMITTED, EcrReportStatus.APPROVED]
)
def test_missing_historical_snapshot_fails_both_routes(
    printed_report, client, db_session, status
):
    report = printed_report
    report.status = status
    db_session.commit()
    for route in ("print", "pdf"):
        response = client.get(f"/ecr/reports/{report.id}/{route}")
        if route == "pdf" and not print_features.SERVER_PDF_ENABLED:
            assert response.status_code == 404
        else:
            assert response.status_code == 409 and "snapshot" in response.text
    with pytest.raises(PrintDataError):
        build_print_viewmodel(report)


@pytest.mark.parametrize("status", list(EcrReportStatus))
def test_status_pdf_three_letter_pages_readonly(
    printed_report, client, db_session, status
):
    report = printed_report
    report.status = status
    if status in (EcrReportStatus.SUBMITTED, EcrReportStatus.APPROVED):
        report.supervisor_name_snapshot = report.supervisor.full_name
        report.supervisor_employee_id_snapshot = report.supervisor.employee_id
    db_session.commit()
    db_session.refresh(report)
    before = (
        report.status,
        report.reviewed_at,
        report.submitted_at,
        report.approved_at,
        report.updated_at,
    )
    response, reader = pdf(client, report)
    assert len(reader.pages) == 3
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="ECR_{report.tower.display_name}_Cell-12.pdf"'
    )
    for i, p in enumerate(reader.pages):
        assert tuple(map(float, (p.mediabox.width, p.mediabox.height))) == (
            612.0,
            792.0,
        )
        content = p.extract_text()
        assert f"{i + 1} of 3" in content
        assert "HO/ENGG/EREC/01" in content and "ISSUE: 0" in content
        assert report.tower.display_name in content and "Cell No." in content
        label = (
            f"{status.value} — NOT SUBMITTED"
            if status in (EcrReportStatus.DRAFT, EcrReportStatus.REVIEWED)
            else status.value
        )
        assert label in content
        if status is EcrReportStatus.SUBMITTED:
            assert "APPROVED" not in content
    assert reader.metadata.title == build_print_viewmodel(report).browser_document_title
    db_session.refresh(report)
    assert before == (
        report.status,
        report.reviewed_at,
        report.submitted_at,
        report.approved_at,
        report.updated_at,
    )


def test_print_signature_once_ist_missing_evidence_fails(
    printed_report, client, db_session
):
    report = printed_report
    token = csrf_from(client.get("/dashboard").text)
    assert sign(client, report, token).status_code == 200
    report.page3.customer_signed_at = datetime(2026, 10, 6, 15, 45, tzinfo=UTC).replace(
        tzinfo=None
    )
    db_session.commit()
    html = client.get(f"/ecr/reports/{report.id}/print").text
    assert html.count('class="customer-sign"') == 1
    assert "06-10-2026 21:15 IST" in html
    assert report.page3.customer_signature_storage_key not in html
    assert "Seal and date" not in html and "Customer Seal" not in html
    _, reader = pdf(client, report)
    assert "06-10-2026 21:15 IST" in reader.pages[2].extract_text()
    client.app.state.protected_storage.delete(
        report.page3.customer_signature_storage_key
    )
    for route in ("print", "pdf"):
        assert client.get(f"/ecr/reports/{report.id}/{route}").status_code == 503


@pytest.mark.parametrize(
    "role,authorized",
    [
        ("anonymous", False),
        (UserRole.SUPERVISOR, False),
        (UserRole.BRANCH_ADMIN, True),
        ("cross-branch", False),
        (UserRole.SUPERADMIN, True),
    ],
)
def test_print_pdf_visibility_before_storage(
    printed_report,
    client,
    db_session,
    user_factory,
    branch_factory,
    role,
    authorized,
    monkeypatch,
):
    report = printed_report
    report.page3.customer_signature_storage_key = "a" * 32 + ".png"
    report.page3.customer_signed_at = datetime(2026, 10, 6, tzinfo=UTC).replace(
        tzinfo=None
    )
    db_session.commit()
    client.cookies.clear()
    if role != "anonymous":
        viewer = user_factory(
            role=UserRole.BRANCH_ADMIN if role == "cross-branch" else role,
            branch=branch_factory(code="PRINT-OTHER")
            if role == "cross-branch"
            else report.branch,
        )
        login(client, viewer.employee_id)
        if role is UserRole.SUPERVISOR:
            _create_report(
                db_session,
                viewer,
                serial_no=report.tower.package.cooling_tower_serial_no,
                cell_no=13,
                declared_no_of_cells=3,
                customer_order_no="PO/2026/14",
            )
    reads = []

    def read(key):
        reads.append(key)
        return base64.b64decode(png().split(",")[1])

    monkeypatch.setattr(client.app.state.protected_storage, "read", read)
    for route in ("print", "pdf"):
        result = client.get(f"/ecr/reports/{report.id}/{route}")
        assert result.status_code == (
            303
            if role == "anonymous"
            else 404
            if route == "pdf" and not print_features.SERVER_PDF_ENABLED
            else 200
            if authorized
            else 404
        )
    assert len(reads) == (
        (2 if print_features.SERVER_PDF_ENABLED else 1) if authorized else 0
    )


def test_html_inert_resources_and_safe_filename(printed_report, client, db_session):
    report = printed_report
    report.page3.team_leader_report = '<script>alert(1)</script>\n<img src="file:///etc/passwd">\nhttp://example.invalid\nbody{background:url(http://example.invalid)}'
    report.page3.customer_comment = "Ω & <img src=x>"
    db_session.commit()
    result = client.get(f"/ecr/reports/{report.id}/print")
    assert "&lt;script&gt;" in result.text and "<script>" not in result.text
    assert "&lt;img" in result.text
    assert ".css?v=" in result.text
    assert "sidebar" not in result.text and "Developed by" not in result.text
    assert "STORAGE_ROOT" not in result.text
    _, reader = pdf(client, report)
    assert "<script>" in reader.pages[2].extract_text()
    report.tower.package.cooling_tower_serial_no = 'unsafe/"serial..'
    assert "/" not in build_print_viewmodel(report).filename
    fetch = resource_fetcher({"known": base64.b64encode(b"png").decode()})
    assert fetch("known").read() == b"png"
    for url in (
        "file:///etc/passwd",
        "http://example.invalid",
        "https://example.invalid",
        "data:image/png;base64,YQ==",
    ):
        with pytest.raises(ValueError, match="denied"):
            fetch(url)


@pytest.mark.skipif(
    not print_features.SERVER_PDF_ENABLED,
    reason="Server PDF temporarily disabled for owner browser-print review",
)
def test_pdf_busy_and_unavailable_fail_clearly(monkeypatch):
    PDF_SLOT.acquire()
    try:
        with pytest.raises(PdfRenderError, match="busy"):
            render_pdf("", {}, 3)
    finally:
        PDF_SLOT.release()

    def fail(*args, **kwargs):
        assert kwargs["env"].keys() == {"PATH", "LANG", "PYTHONDONTWRITEBYTECODE"}
        raise OSError("private implementation detail")

    monkeypatch.setattr("app.ecr.pdf_renderer.subprocess.run", fail)
    with pytest.raises(PdfRenderError, match="unavailable"):
        render_pdf("", {}, 3)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1:1/private",
        "https://example.invalid/private",
    ],
)
@pytest.mark.skipif(
    not print_features.SERVER_PDF_ENABLED,
    reason="Server PDF temporarily disabled for owner browser-print review",
)
def test_renderer_forbidden_resources_fail_closed(url):
    with pytest.raises(PdfRenderError, match="rendering failed"):
        render_pdf(f'<style>@page {{size: Letter}}</style><img src="{url}">', {}, 1)


def test_unsigned_current_identity_and_long_fields_intact(
    printed_report, client, db_session
):
    report = printed_report
    report.cell_no = 1
    report.erection_start_date = date(2026, 9, 2)
    report.supervisor.full_name = "Current Supervisor"
    report.tower.package.customer = "Long customer Ω & " * 10
    report.tower.package.place_of_installation = "Installation " * 18
    report.page1.motor_frame = "W" * 250
    db_session.commit()
    vm = build_print_viewmodel(report)
    assert vm.cell == 1 and vm.supervisor_name == "Current Supervisor"
    assert vm.sign_date == "" and report.supervisor_name_snapshot is None
    assert {f.label: f.value for f in vm.identity}[
        "Erection Start Date"
    ] == "02-09-2026"
    assert sections(vm)["Motor"]["Frame"] == "See Continuation"
    _, reader = pdf(client, report)
    assert "Sign Date:" in reader.pages[2].extract_text()
    # WeasyPrint shares resource dictionaries; absence of image painting, not
    # dictionary membership, proves an unsigned Page 3 stays blank.
    assert b" Do" not in reader.pages[2].get_contents().get_data()
    full = "\n".join(p.extract_text() for p in reader.pages[3:])
    assert full.count("W") == 250
    assert "Installation" in full and "Long customer" in full


def test_corrupt_signature_fails_and_no_missing_multi_suffix(
    printed_report, client, db_session, monkeypatch
):
    report = printed_report
    report.tower.package.multiple_towers = True
    with pytest.raises(PrintDataError, match="suffix"):
        build_print_viewmodel(report)
    report.tower.package.multiple_towers = False
    report.page3.customer_signature_storage_key = "a" * 32 + ".png"
    report.page3.customer_signed_at = datetime.now(UTC).replace(tzinfo=None)
    db_session.commit()
    monkeypatch.setattr(
        client.app.state.protected_storage, "read", lambda key: b"corrupt PNG"
    )
    assert client.get(f"/ecr/reports/{report.id}/print").status_code == 503
    assert client.get(f"/ecr/reports/{report.id}/pdf").status_code == (
        503 if print_features.SERVER_PDF_ENABLED else 404
    )


def test_representative_native_font_available():
    from app.ecr.print_viewmodel import print_font

    assert print_font().getname()[0] == "DejaVu Sans"


def test_snapshot_migration_roundtrip_and_legacy_backfill(tmp_path):
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/8b3f1a7c902d_supervisor_print_snapshots.py"
    )
    spec = importlib.util.spec_from_file_location("snapshot_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, full_name VARCHAR(150), employee_id VARCHAR(50))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE ecr_reports (id INTEGER PRIMARY KEY, supervisor_user_id INTEGER, status VARCHAR(20))"
            )
        )
        connection.execute(
            text("INSERT INTO users VALUES (1, 'Legacy Supervisor', 'EMP-OLD')")
        )
        for i, state in enumerate(EcrReportStatus, 1):
            connection.execute(
                text("INSERT INTO ecr_reports VALUES (:id, 1, :state)"),
                {"id": i, "state": state.value},
            )
        before = list(connection.execute(text("SELECT * FROM ecr_reports")))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            rows = connection.execute(
                text(
                    "SELECT status, supervisor_name_snapshot, supervisor_employee_id_snapshot FROM ecr_reports"
                )
            ).all()
            for state, name, employee in rows:
                assert (name, employee) == (
                    ("Legacy Supervisor", "EMP-OLD")
                    if state in ("SUBMITTED", "APPROVED")
                    else (None, None)
                )
            migration.downgrade()
            assert list(connection.execute(text("SELECT * FROM ecr_reports"))) == before
            migration.upgrade()


def test_continuation_pdf_and_owner_samples(printed_report, client, db_session):
    report = printed_report
    outputs = {}
    outputs["01_single_tower_unsigned.pdf"] = pdf(client, report)[0].content
    report.tower.package.multiple_towers = True
    report.tower.tower_suffix = report.tower.normalized_suffix_key = "A"
    token = csrf_from(client.get("/dashboard").text)
    assert sign(client, report, token).status_code == 200
    db_session.commit()
    response, reader = pdf(client, report)
    assert len(reader.pages) == 3
    assert (
        f"ECR_{report.tower.package.cooling_tower_serial_no}_A_Cell-12.pdf"
        in response.headers["content-disposition"]
    )
    outputs["02_multi_tower_signed.pdf"] = response.content
    report.page3.team_leader_report = "\n".join(
        f"Line {i}: Work completed. Ω & verified." for i in range(120)
    )
    report.page3.customer_comment = "\n".join(
        f"Customer comment {i}" for i in range(50)
    )
    values = page1.page1_values(report)
    values["blade_serials"] = [f"B-{i}" for i in range(1, 13)]
    page1.save_page1(db_session, report, page1.Page1DraftInput(**values))
    batch_c.save(
        db_session,
        report,
        batch_c.BatchCDraftInput(
            fastener_rows=[
                {
                    "category": "FAN_HARDWARE",
                    "diameter": f"M{i}",
                    "torque": str(i),
                    "torque_unit": "Nm",
                }
                for i in range(1, 7)
            ]
        ),
    )
    db_session.commit()
    response, reader = pdf(client, report)
    assert len(reader.pages) > 3
    for i in range(3):
        assert f"{i + 1} of 3" in reader.pages[i].extract_text()
    continued = "\n".join(p.extract_text() for p in reader.pages[3:])
    assert "CONTINUATION" in continued
    assert "Line 0:" in continued and "Line 119:" in continued
    assert "Customer comment 49" in continued
    assert "12: B-12" in continued and "Row 6: Diameter: M6" in continued
    outputs["03_continuation_example.pdf"] = response.content
    if os.environ.get("ECR_PRINT_SAMPLES"):
        root = Path(os.environ["ECR_PRINT_SAMPLES"])
        root.mkdir(parents=True, exist_ok=True)
        for filename, content in outputs.items():
            (root / filename).write_bytes(content)
