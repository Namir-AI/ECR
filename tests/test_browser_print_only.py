"""Temporary browser-print-only configuration; retained PDFs remain reversible."""

import base64
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from app.ecr import pdf_renderer, print_features, print_routes
from app.ecr.models import EcrReportStatus
from app.users.models import UserRole
from tests import test_official_print as official_tests
from tests.conftest import login
from tests.test_page3 import png

printed_report = official_tests.printed_report
pytestmark = pytest.mark.skipif(
    print_features.SERVER_PDF_ENABLED,
    reason="Temporary browser-print-only tests apply while server PDF is disabled",
)


def workflow_state(report):
    return (
        report.status,
        report.reviewed_at,
        report.submitted_at,
        report.approved_at,
        report.updated_at,
        report.supervisor_name_snapshot,
        report.supervisor_employee_id_snapshot,
        report.page3.customer_signature_storage_key,
        report.page3.customer_signed_at,
    )


@pytest.mark.parametrize("existing", [True, False])
def test_disabled_pdf_never_loads_evidence_or_runs_renderer(
    printed_report, client, db_session, monkeypatch, existing
):
    assert print_features.SERVER_PDF_ENABLED is False
    report = printed_report
    db_session.refresh(report)
    before = workflow_state(report)
    probes = []
    for module, name in (
        (print_routes, "official_context"),
        (print_routes, "render_pdf"),
        (pdf_renderer, "worker"),
        (pdf_renderer.subprocess, "run"),
        (client.app.state.protected_storage, "read"),
    ):
        probe = Mock(side_effect=AssertionError(f"Disabled PDF reached {name}"))
        monkeypatch.setattr(module, name, probe)
        probes.append(probe)
    slot = Mock()
    slot.acquire.side_effect = AssertionError("Disabled PDF acquired renderer slot")
    monkeypatch.setattr(pdf_renderer, "PDF_SLOT", slot)
    target = report.id if existing else 2**60
    response = client.get(f"/ecr/reports/{target}/pdf")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-type"] != "application/pdf"
    assert not response.content.startswith(b"%PDF-")
    for probe in probes:
        probe.assert_not_called()
    slot.acquire.assert_not_called()
    slot.release.assert_not_called()
    db_session.refresh(report)
    assert workflow_state(report) == before


@pytest.mark.parametrize(
    "status,signed,continued",
    [
        (EcrReportStatus.DRAFT, False, False),
        (EcrReportStatus.REVIEWED, True, True),
        (EcrReportStatus.SUBMITTED, True, False),
        (EcrReportStatus.APPROVED, False, True),
    ],
)
def test_browser_print_keeps_content_sign_status_and_continuations(
    printed_report, client, db_session, status, signed, continued, monkeypatch
):
    report = printed_report
    report.status = status
    if status in (EcrReportStatus.SUBMITTED, EcrReportStatus.APPROVED):
        report.supervisor_name_snapshot = report.supervisor.full_name
        report.supervisor_employee_id_snapshot = report.supervisor.employee_id
    if signed:
        report.page3.customer_signature_storage_key = (
            client.app.state.protected_storage.put(
                base64.b64decode(png().split(",")[1])
            )
        )
        report.page3.customer_signed_at = datetime(
            2026, 10, 6, 15, 45, tzinfo=UTC
        ).replace(tzinfo=None)
    if continued:
        report.page3.team_leader_report = "\n".join(
            f"Complete narrative line {i}" for i in range(70)
        )
    db_session.commit()
    db_session.refresh(report)
    before = workflow_state(report)
    renderer = Mock(side_effect=AssertionError("Print invoked server PDF"))
    subprocess = Mock(side_effect=AssertionError("Print spawned PDF subprocess"))
    monkeypatch.setattr(print_routes, "render_pdf", renderer)
    monkeypatch.setattr(pdf_renderer.subprocess, "run", subprocess)
    response = client.get(f"/ecr/reports/{report.id}/print")
    assert response.status_code == 200
    html = response.text
    assert html.count('class="sheet core-page"') == 3
    assert "HO/ENGG/EREC/01" in html and "ISSUE: 0" in html
    assert report.tower.display_name in html and "Cell No." in html
    assert "window.print()" in html and ">Print</button>" in html
    assert "Back to Report" in html
    assert "Download PDF" not in html
    assert f'href="/ecr/reports/{report.id}/pdf"' not in html
    assert html.count('class="red-ellipse"') == 2
    assert (
        f"{status.value} — NOT SUBMITTED" in html
        if status
        in (
            EcrReportStatus.DRAFT,
            EcrReportStatus.REVIEWED,
        )
        else f">{status.value}</div>" in html
    )
    assert html.count('class="customer-sign"') == (1 if signed else 0)
    if signed:
        assert "06-10-2026 21:15 IST" in html
        assert report.page3.customer_signature_storage_key not in html
    if continued:
        assert 'class="sheet continuation-page"' in html
        assert "Complete narrative line 0" in html
        assert "Complete narrative line 69" in html
    else:
        assert 'class="sheet continuation-page"' not in html
    detail = client.get(f"/ecr/reports/{report.id}")
    assert detail.status_code == 200
    assert f'href="/ecr/reports/{report.id}/print"' in detail.text
    assert "Download PDF" not in detail.text
    renderer.assert_not_called()
    subprocess.assert_not_called()
    db_session.refresh(report)
    assert workflow_state(report) == before


def test_retained_pdf_path_can_be_restored_by_one_switch(
    printed_report, client, monkeypatch
):
    # Exercise the retained route/markup using a stub, NOT a WeasyPrint worker.
    report = printed_report
    disabled_html = client.get(f"/ecr/reports/{report.id}/print").text
    monkeypatch.setattr(print_features, "SERVER_PDF_ENABLED", True)
    renderer = Mock(return_value=b"%PDF-test-only-stub")
    monkeypatch.setattr(print_routes, "render_pdf", renderer)
    enabled_html = client.get(f"/ecr/reports/{report.id}/print").text
    assert "Download PDF" in enabled_html
    assert (
        disabled_html[disabled_html.index("<main ") :]
        == enabled_html[enabled_html.index("<main ") :]
    )
    assert "Download PDF" in client.get(f"/ecr/reports/{report.id}").text
    response = client.get(f"/ecr/reports/{report.id}/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    renderer.assert_called_once()


@pytest.mark.parametrize("role", [UserRole.BRANCH_ADMIN, UserRole.SUPERADMIN])
def test_admin_views_hide_server_pdf_but_keep_browser_print(
    printed_report, client, user_factory, role
):
    report = printed_report
    admin = user_factory(role=role, branch=report.branch)
    client.cookies.clear()
    login(client, admin.employee_id)
    for url in (
        f"/reports/{report.id}",
        f"/ecr/reports/{report.id}/print",
    ):
        result = client.get(url)
        assert result.status_code == 200
        assert "Download PDF" not in result.text
        assert "Print" in result.text
        assert f'href="/ecr/reports/{report.id}/pdf"' not in result.text
    assert client.get(f"/ecr/reports/{report.id}/pdf").status_code == 404
