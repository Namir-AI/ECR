"""Real Chromium operations on desktop/mobile, including workflow confirmations."""

import threading

import pytest

from app.db.session import get_db_session
from app.users.models import UserRole
from tests import test_draft_save_browser as browser_tests
from tests import test_owner_ui as owner_tests
from tests.test_page1_browser import wait_saved
from tests.test_phase3 import _create_report
from tests.test_phase5 import complete_report

draft_browser = browser_tests.draft_browser
playwright = browser_tests.playwright
performance = owner_tests.performance


@pytest.fixture
def operations_browser(draft_browser, client, db_session):
    lock = threading.Lock()

    def serialized_db():
        with lock:
            yield db_session

    client.app.dependency_overrides[get_db_session] = serialized_db
    return draft_browser


def base_url(page):
    return page.url.split("/ecr/")[0].split("/dashboard")[0].split("/reports")[0]


def browser_login(page, base, user):
    page.context.clear_cookies()
    page.goto(base + "/auth/login")
    page.locator('[name="identifier"]').fill(user.employee_id)
    page.locator('[name="password"]').fill("Correct-Horse-123")
    page.locator('button[type="submit"]').click()
    page.wait_for_url("**/dashboard")


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("role", ["branch", "global"])
def test_erector_search_mobile_desktop(
    operations_browser, performance, role, width, tmp_path
):
    page, _ = operations_browser
    viewer = performance[4] if role == "branch" else performance[5]
    browser_login(page, base_url(page), viewer)
    page.set_viewport_size({"width": width, "height": 950})
    page.get_by_label("Erector / Supervisor Name").fill("satadru")
    page.get_by_label("Year", exact=True).select_option("2026")
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.get_by_role("button", name="Search", exact=True).click()
    playwright.expect(page.locator("[data-erector-towers]")).to_have_text(
        "1" if role == "branch" else "2"
    )
    playwright.expect(page.locator("[data-erector-cells]")).to_have_text(
        "2" if role == "branch" else "3"
    )
    names = page.locator('td[data-label="Erected / Submitted By"]')
    assert names.count() == (2 if role == "branch" else 3)
    assert all(
        name.is_visible() and name.inner_text() == "Satadru Nath"
        for name in names.all()
    )
    assert (
        page.get_by_role(
            "link", name="Attachments JCC & Tower Photo", exact=True
        ).count()
        >= 1
    )
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.get_by_role("button", name="Approved", exact=True).click()
    playwright.expect(page.locator("[data-erector-cells]")).to_have_text("1")
    assert page.get_by_label("Erector / Supervisor Name").input_value() == "satadru"
    assert page.get_by_label("Year", exact=True).input_value() == "2026"
    assert page.locator(".dashboard-panel").evaluate(
        "el => el.scrollWidth <= el.clientWidth"
    )
    page.screenshot(path=str(tmp_path / f"erector-{role}-{width}.png"), full_page=True)


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize(
    "role", [UserRole.SUPERVISOR, UserRole.BRANCH_ADMIN, UserRole.SUPERADMIN]
)
def test_role_dashboard_shell_collapse_filters(
    operations_browser, db_session, user_factory, role, width, tmp_path
):
    page, report = operations_browser
    base = base_url(page)
    viewer = (
        report.supervisor
        if role is UserRole.SUPERVISOR
        else user_factory(role=role, branch=report.branch)
    )
    browser_login(page, base, viewer)
    page.set_viewport_size({"width": width, "height": 950})
    assert "Dashboard" in page.locator("h1").inner_text()
    assert page.locator(".package-card").count() >= 1
    assert page.locator(".cell-table th").filter(has_text="Cell No.").count() >= 1
    assert page.locator(".app-footer").inner_text() == "Developed by: Nazmul Khan"
    if width < 760:
        page.locator("[data-sidebar-toggle]").click()
    assert (
        page.get_by_role("link", name="Budget", exact=False).get_attribute(
            "aria-disabled"
        )
        == "true"
    )
    assert (
        page.get_by_role("link", name="Bills", exact=False).get_attribute(
            "aria-disabled"
        )
        == "true"
    )
    assert page.locator(".paharpur-logo").evaluate(
        "el => el.complete && el.naturalWidth > 0"
    )
    if width < 760:
        page.keyboard.press("Escape")
        assert (
            page.locator("[data-sidebar-toggle]").get_attribute("aria-expanded")
            == "false"
        )
    package = page.locator(f'[data-package="{report.tower.package_id}"]')
    collapse = package.locator(".package-heading [data-collapse]")
    collapse.click()
    assert collapse.get_attribute("aria-expanded") == "false"
    assert package.locator(".package-body").is_hidden()
    collapse.click()
    tower = package.locator(".tower-heading")
    tower.click()
    assert tower.get_attribute("aria-expanded") == "false"
    tower.click()
    if role is not UserRole.SUPERVISOR:
        page.get_by_role("button", name="Submitted", exact=True).click()
        assert page.locator(f'[data-package="{report.tower.package_id}"]').count() == 0
        page.get_by_role("button", name="Draft", exact=True).click()
        assert page.locator(".package-card").count() >= 1
        assert page.get_by_role("link", name="Search Reports", exact=True).is_visible()
        assert page.get_by_role(
            "link", name="Pending Approvals", exact=True
        ).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(
        path=str(tmp_path / f"dashboard-{role.value}-{width}.png"), full_page=True
    )


@pytest.mark.parametrize("action", ["missing", "another"])
def test_create_cell_confirmation_blank_no_copy(operations_browser, db_session, action):
    page, report = operations_browser
    base = base_url(page)
    report.tower.declared_no_of_cells = 3
    db_session.commit()
    page.goto(base + "/dashboard")
    trigger = (
        page.locator('[data-cell="2"] button').first
        if action == "missing"
        else page.get_by_role("button", name="+ Add another Cell", exact=True)
    )
    trigger.click()
    assert page.locator("[data-action-dialog]").is_visible()
    assert "Cell-2" in page.locator("#action-message").inner_text()
    page.locator("[data-action-cancel]").click()
    assert not page.locator("[data-action-dialog]").is_visible()
    trigger.click()
    page.locator("[data-action-submit]").click()
    page.wait_for_url("**/edit?notice=created")
    assert "Cell-2" in page.locator("h1").inner_text()
    for name in (
        "erection_completion_date",
        "erection_start_date",
        "motor_make",
        "team_leader_report",
        "de_top",
    ):
        assert page.locator(f'[name="{name}"]').input_value() == ""
    page.locator('[name="motor_make"]').fill("New cell motor")
    wait_saved(page)
    page.reload()
    assert page.locator('[name="motor_make"]').input_value() == "New cell motor"
    assert page.locator('[name="erection_completion_date"]').input_value() == ""
    page.goto(base + "/dashboard")
    assert page.locator('[data-cell="2"] .status-badge').inner_text() == "DRAFT"


def test_review_back_edit_submit_then_admin_approve(
    operations_browser, db_session, user_factory
):
    page, report = operations_browser
    base = base_url(page)
    complete_report(db_session, report)
    page.reload()
    page.locator("[data-review-report]").click()
    page.wait_for_url(base + f"/ecr/reports/{report.id}")
    assert page.get_by_role("button", name="Confirm & Submit", exact=True).is_visible()
    assert page.locator("[data-autosave-form]").count() == 0
    page.get_by_role("button", name="Back to Edit", exact=True).click()
    page.wait_for_url("**/edit")
    page.locator("[data-review-report]").click()
    page.wait_for_url(base + f"/ecr/reports/{report.id}")
    page.get_by_role("button", name="Confirm & Submit", exact=True).click()
    assert page.locator("#action-title").inner_text() == "Submit E&C Report?"
    assert report.display_identity in page.locator("#action-message").inner_text()
    page.keyboard.press("Escape")
    assert page.locator("[data-action-dialog]").is_hidden()
    page.get_by_role("button", name="Confirm & Submit", exact=True).click()
    page.locator("[data-action-submit]").focus()
    page.keyboard.press("Enter")
    page.wait_for_function(
        "document.querySelector('.status-badge').textContent === 'SUBMITTED'"
    )
    assert page.locator('a[href$="/edit"]').count() == 0
    admin = user_factory(role=UserRole.BRANCH_ADMIN, branch=report.branch)
    browser_login(page, base, admin)
    page.locator(f'a[href="/reports/{report.id}/edit"]').click()
    page.locator('[name="team_leader_report"]').fill("Admin clarified report")
    assert page.locator("[data-autosave-status]").inner_text() == "Unsaved changes"
    page.locator("[data-save-now]").click()
    wait_saved(page)
    page.goto(base + f"/reports/{report.id}")
    assert page.locator(".status-badge").first.inner_text() == "SUBMITTED"
    assert (
        "Admin clarified report"
        in page.locator(".plain-report-text").first.inner_text()
    )
    page.get_by_role("button", name="Approve", exact=True).click()
    assert page.locator("#action-title").inner_text() == "Approve this E&C Report?"
    page.locator("[data-action-submit]").click()
    page.wait_for_function(
        "document.querySelector('.status-badge').textContent === 'APPROVED'"
    )
    assert page.get_by_role("link", name="Edit Report", exact=True).count() == 0


@pytest.mark.parametrize(
    "key,field",
    [
        ("Cooling Tower Serial No.", None),
        ("Motor Serial No.", "motor_serial_no"),
        ("Gearbox Serial No.", "gearbox_serial_no"),
        ("Drive Shaft Serial No.", "drive_shaft_serial_no"),
    ],
)
def test_search_unique_full_report(
    operations_browser, db_session, user_factory, key, field
):
    page, report = operations_browser
    base = base_url(page)
    complete_report(db_session, report)
    admin = user_factory(role=UserRole.SUPERADMIN)
    browser_login(page, base, admin)
    page.get_by_role("link", name="Search Reports", exact=True).click()
    page.locator('[name="search_by"]').select_option(key)
    value = getattr(report.page1, field) if field else report.tower.display_name
    page.locator('[name="value"]').fill(value)
    page.get_by_role("button", name="Search", exact=True).click()
    page.wait_for_url(base + f"/reports/{report.id}")
    assert page.locator("h1").inner_text() == report.display_identity
    assert page.locator("[data-reading-diagram]").count() == 2
    assert (
        page.locator(".plain-report-text").first.inner_text()
        == "Completed\nSecond paragraph"
    )


def test_search_multiple_chooser_and_branch_filter(
    operations_browser, db_session, user_factory, branch_factory
):
    page, report = operations_browser
    base = base_url(page)
    _create_report(
        db_session,
        report.supervisor,
        serial_no=report.tower.package.cooling_tower_serial_no,
        cell_no=2,
    )
    remote = _create_report(
        db_session, user_factory(branch=branch_factory(code="MUMBAI"))
    )
    admin = user_factory(role=UserRole.SUPERADMIN)
    browser_login(page, base, admin)
    page.locator('[name="branch_id"]').select_option(str(remote.branch_id))
    page.get_by_role("button", name="All", exact=True).click()
    assert page.locator(f'[data-package="{remote.tower.package_id}"]').count() == 1
    assert page.locator(f'[data-package="{report.tower.package_id}"]').count() == 0
    page.goto(base + "/reports/search")
    page.locator('[name="value"]').fill(report.tower.package.cooling_tower_serial_no)
    page.get_by_role("button", name="Search", exact=True).click()
    assert page.get_by_role("link", name="View Full Report", exact=True).count() == 2
    page.get_by_role("link", name="View Full Report", exact=True).first.click()
    assert page.locator("h1").inner_text().startswith(report.tower.display_name)


def test_review_waits_for_save_and_invalid_data_feedback(operations_browser):
    page, _ = operations_browser
    page.locator('[name="motor_make"]').fill("Unsaved motor")
    assert page.locator("[data-review-report]").is_disabled()
    wait_saved(page)
    assert page.locator("[data-review-report]").is_enabled()
    page.locator("[data-review-report]").click()
    page.locator(".validation-summary").wait_for(state="visible")
    assert "Fields to complete" in page.locator(".validation-summary").inner_text()
