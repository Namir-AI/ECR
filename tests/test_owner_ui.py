"""Owner attachment wording and scoped relational Erector output summaries."""

from datetime import date

import pytest
from sqlalchemy import event

from app.core.templates import file_size
from app.ecr.models import EcrReport, EcrReportStatus, EcrTower
from app.ecr.operations import erector_context, visible_reports
from app.users.models import UserRole
from tests import test_attachments as attachment_tests
from tests.conftest import login
from tests.test_attachments import image_bytes, send
from tests.test_phase3 import _create_report

evidence = attachment_tests.evidence


@pytest.mark.parametrize(
    "size, expected",
    [(85981, "85.98 KB"), (1377963, "1.38 MB"), (5_000_000, "5 MB"), (0, "0 KB")],
)
def test_reusable_size_presentation(size, expected):
    assert file_size(size) == expected


def test_attachment_owner_wording_sizes_and_picker(client, db_session, evidence):
    _, report, token, _ = evidence
    assert (
        send(
            client, report, token, files=[("jcc.png", image_bytes(exact_size=85981))]
        ).status_code
        == 200
    )
    assert (
        send(
            client,
            report,
            token,
            "TOWER_PHOTO_ADD",
            files=[("tower.png", image_bytes(exact_size=1377963))],
        ).status_code
        == 200
    )
    html = client.get(f"/ecr/packages/{report.tower.package_id}/attachments").text
    for text in (
        "<h1>JCC and Tower Photo Upload</h1>",
        "Multi-page PDF",
        "JPG/JPEG/PNG",
        "Do not upload Tower Photos here.",
        "Choose JCC",
        "Choose Photo",
        "jcc.png (85.98 KB)",
        "CT Photo 1",
        "tower.png (1.38 MB)",
    ):
        assert text in html
    for text in (
        "One logical document",
        "Both are optional",
        "Uploads are not compressed",
        "5,000,000 bytes",
        "85981 bytes",
        "1377963 bytes",
    ):
        assert text not in html
    assert 'data-byte-size="1377963"' in html


def test_five_photos_hide_add_controls_remove_restores(client, db_session, evidence):
    _, report, token, _ = evidence
    assert (
        send(
            client,
            report,
            token,
            "TOWER_PHOTO_ADD",
            files=[(f"p{i}.png", image_bytes()) for i in range(5)],
        ).status_code
        == 200
    )
    url = f"/ecr/packages/{report.tower.package_id}/attachments"
    html = client.get(url).text
    assert 'value="TOWER_PHOTO_ADD"' not in html and ">Add Photos<" not in html
    assert 'value="TOWER_PHOTO_REPLACE"' in html
    row = attachment_tests.records(db_session, report)[0]
    assert (
        send(client, report, token, "TOWER_PHOTO_REMOVE", target_id=row.id).status_code
        == 200
    )
    assert 'value="TOWER_PHOTO_ADD"' in client.get(url).text


@pytest.fixture
def performance(db_session, user_factory, branch_factory):
    west, east = branch_factory(code="PERF-WEST"), branch_factory(code="PERF-EAST")
    owner, other = user_factory(branch=west), user_factory(branch=west)
    owner.full_name, other.full_name = "Satadru Nath", "Naresh Kumar"
    db_session.commit()
    first = _create_report(db_session, owner)
    first.erection_completion_date = date(2026, 1, 1)
    rows = [first]
    for number, status, completed, supervisor in (
        (2, EcrReportStatus.REVIEWED, None, owner),
        (3, EcrReportStatus.SUBMITTED, date(2025, 12, 31), owner),
        (4, EcrReportStatus.APPROVED, date(2026, 12, 31), owner),
        (5, EcrReportStatus.DRAFT, date(2026, 2, 1), other),
    ):
        row = EcrReport(
            tower_id=first.tower_id,
            cell_no=number,
            supervisor_user_id=supervisor.id,
            branch_id=west.id,
            status=status,
            erection_completion_date=completed,
        )
        db_session.add(row)
        rows.append(row)
    db_session.commit()
    owner.branch_id = east.id
    db_session.commit()
    remote = _create_report(db_session, owner)
    remote.erection_completion_date = date(2026, 5, 1)
    db_session.commit()
    admin = user_factory(role=UserRole.BRANCH_ADMIN, branch=west)
    global_admin = user_factory(role=UserRole.SUPERADMIN, branch=east)
    return owner, other, rows, remote, admin, global_admin


@pytest.mark.parametrize("role", ["branch", "global"])
@pytest.mark.parametrize("name", ["satadru", "SATADRU NATH", "  Satadru Nath  "])
@pytest.mark.parametrize("route", ["/dashboard", "/reports"])
def test_scoped_erector_search_grouping_and_distinct_counts(
    client, db_session, performance, role, name, route
):
    _owner, _, rows, remote, admin, global_admin = performance
    viewer = admin if role == "branch" else global_admin
    login(client, viewer.employee_id)
    response = client.get(
        route,
        params={
            "erector": name,
            "branch_id": "",
            "year": "",
            "supervisor_name": "Forged identity",
        },
    )
    assert response.status_code == 200
    assert "Erected / Submitted By" in response.text and "Satadru Nath" in response.text
    assert (
        "Naresh Kumar" not in response.text and "Forged identity" not in response.text
    )
    assert 'data-package="' in response.text and 'data-tower="' in response.text
    assert (
        remote.tower.display_name in response.text
        if role == "global"
        else remote.tower.display_name not in response.text
    )
    scoped = visible_reports(db_session, viewer, erector=name.strip())
    assert {r.id for r in scoped} == {r.id for r in rows[:4]} | (
        {remote.id} if role == "global" else set()
    )
    context = erector_context(db_session, viewer, erector=name.strip())
    assert context["erector_totals"].towers == (2 if role == "global" else 1)
    assert context["erector_totals"].cells == (5 if role == "global" else 4)
    assert {r.status for r in scoped} == set(EcrReportStatus)
    assert "Attachments JCC &amp; Tower Photo" in response.text


@pytest.mark.parametrize("year, cells", [(2026, 2), (2025, 1), (2024, 0)])
def test_completion_year_excludes_null_and_uses_date_not_status(
    db_session, performance, year, cells
):
    _, _, _, _, admin, _ = performance
    results = visible_reports(db_session, admin, erector="Satadru", year=year)
    assert len(results) == cells
    assert all(r.erection_completion_date.year == year for r in results)
    context = erector_context(db_session, admin, erector="Satadru", year=year)
    assert context["erector_totals"].cells == cells
    assert context["erector_totals"].towers == int(cells > 0)


def test_status_branch_filters_and_partial_literal_names(
    client, db_session, performance
):
    owner, other, rows, remote, admin, global_admin = performance
    login(client, global_admin.employee_id)
    response = client.get(
        "/dashboard",
        params={
            "erector": "Satadru",
            "year": 2026,
            "status_filter": "APPROVED",
            "branch_id": admin.branch_id,
        },
    )
    assert response.status_code == 200
    assert "data-erector-cells>1<" in response.text
    assert remote.tower.display_name not in response.text
    owner.full_name, other.full_name = "A%_Supervisor", "Another Supervisor"
    db_session.commit()
    assert {r.id for r in visible_reports(db_session, global_admin, erector="%_")} == {
        r.id for r in rows[:4]
    } | {remote.id}
    context = erector_context(db_session, global_admin, erector="Supervisor")
    assert len(context["erector_summaries"]) == 2
    assert (
        context["erector_totals"].towers == 2 and context["erector_totals"].cells == 6
    )


def test_erector_result_render_avoids_n_plus_one(client, db_session, performance):
    _, _, _, _, _, admin = performance
    login(client, admin.employee_id)
    queries = []

    def capture(*args):
        queries.append(args[2])

    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        assert client.get("/dashboard?erector=Satadru").status_code == 200
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    # Fixed queries for session, scoped reports, years, grouped counts, totals,
    # branches; rendering Supervisor/Package/Tower/Branch adds no per-row SELECTs.
    assert len(queries) <= 10


def test_tower_count_not_package_count(db_session, performance):
    owner, _, rows, _, admin, _ = performance
    package = rows[0].tower.package
    package.multiple_towers = True
    rows[0].tower.tower_suffix = "A"
    rows[0].tower.normalized_suffix_key = "A"
    second = EcrTower(
        package_id=package.id, tower_suffix="B", normalized_suffix_key="B"
    )
    db_session.add(second)
    db_session.flush()
    db_session.add(
        EcrReport(
            tower_id=second.id,
            cell_no=1,
            supervisor_user_id=owner.id,
            branch_id=admin.branch_id,
            status=EcrReportStatus.DRAFT,
        )
    )
    db_session.commit()
    context = erector_context(db_session, admin, erector="Satadru")
    assert (
        context["erector_totals"].towers == 2 and context["erector_totals"].cells == 5
    )


@pytest.mark.parametrize("role", ["branch", "global"])
def test_admin_identity_column_without_search_and_supervisor_scope(
    client, db_session, performance, role
):
    owner, other, _rows, remote, admin, global_admin = performance
    login(client, (admin if role == "branch" else global_admin).employee_id)
    html = client.get("/dashboard").text
    assert "<th>Erected / Submitted By</th>" in html
    assert 'data-label="Erected / Submitted By">Satadru Nath</td>' in html
    assert 'data-label="Erected / Submitted By">Naresh Kumar</td>' in html
    client.cookies.clear()
    login(client, other.employee_id)
    html = client.get("/dashboard?erector=Satadru&year=2026").text
    assert "Search by Erector" not in html and "Erector Performance" not in html
    assert remote.tower.display_name not in html
    assert owner.full_name == "Satadru Nath"


@pytest.mark.parametrize("value", ["bad", "0", "10000", "-1"])
def test_invalid_year_is_rejected(client, performance, value):
    login(client, performance[4].employee_id)
    assert (
        client.get(
            "/dashboard", params={"erector": "Satadru", "year": value}
        ).status_code
        == 422
    )
