"""Desktop/mobile visual, keyboard and real package-check button regressions."""

from uuid import uuid4

import pytest

from app.users.models import UserRole
from tests import test_phase5_browser as operations

operations_browser = operations.operations_browser
draft_browser = operations.draft_browser
playwright = operations.playwright


def product_name(page, width):
    if width == 390:
        page.get_by_role("button", name="Toggle navigation").click()
    name = page.locator(".logo-link .product-name")
    assert name.is_visible() and name.inner_text() == "Service Management"
    assert name.evaluate("el => Number(getComputedStyle(el).fontWeight)") >= 700
    assert name.evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 16
    assert (
        name.bounding_box()["width"]
        <= page.locator(".logo-link").bounding_box()["width"]
    )
    if width == 390:
        page.get_by_role("button", name="Toggle navigation").click()


def check_action(page, action, primary=False):
    assert action.is_visible()
    assert "action-button" in action.get_attribute("class").split()
    assert ("action-primary" if primary else "secondary") in action.get_attribute(
        "class"
    ).split()
    svg = action.locator("svg")
    assert svg.count() == 1 and svg.get_attribute("aria-hidden") == "true"
    icon, text = svg.bounding_box(), action.locator("span").bounding_box()
    assert 14 <= icon["width"] <= 24
    assert icon["x"] + icon["width"] <= text["x"] + 1
    assert action.bounding_box()["height"] >= 44
    action.focus()
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")
    assert action.evaluate("el => el.matches(':focus-visible')")
    assert action.evaluate("el => getComputedStyle(el).outlineStyle") != "none"
    assert page.locator(".page-shell").evaluate(
        "el => el.scrollWidth <= el.clientWidth"
    )


@pytest.mark.parametrize("width", [390, 1440])
def test_report_and_new_report_actions_preserve_print_and_package_check(
    operations_browser, width, tmp_path
):
    page, report = operations_browser
    base = operations.base_url(page)
    page.set_viewport_size({"width": width, "height": 950})
    page.goto(base + f"/ecr/reports/{report.id}")
    product_name(page, width)
    back = page.get_by_role("link", name="Back to Reports", exact=True)
    check_action(page, back)
    assert back.get_attribute("href") == "/dashboard"
    printing = page.get_by_role("link", name="Print", exact=True)
    check_action(page, printing, primary=True)
    page.screenshot(path=str(tmp_path / f"report-actions-{width}.png"))
    printing.click()
    page.wait_for_url("**/print")
    assert page.locator(".core-page").count() == 3
    assert page.get_by_role("link", name="Download PDF").count() == 0
    page.evaluate("window.print = () => window.ownerPrintInvoked = true")
    page.get_by_role("button", name="Print", exact=True).click()
    assert page.evaluate("window.ownerPrintInvoked") is True
    for existing in (False, True):
        page.goto(base + "/ecr/reports/new")
        assert page.get_by_text("First identify the ECR Package.").count() == 0
        assert page.get_by_role("button", name="Check Package").count() == 0
        back = page.get_by_role("link", name="Back to Dashboard", exact=True)
        check_action(page, back)
        assert back.get_attribute("href") == "/dashboard"
        start = page.get_by_role("button", name="Start Report", exact=True)
        check_action(page, start, primary=True)
        page.screenshot(path=str(tmp_path / f"new-report-actions-{width}.png"))
        serial = (
            report.tower.package.cooling_tower_serial_no
            if existing
            else f"POLISH-BROWSER-{uuid4().hex}"
        )
        page.get_by_label("Cooling Tower Serial No.", exact=True).fill(serial)
        start.click()
        page.wait_for_url("**/ecr/reports/new/check")
        assert page.locator("[data-new-report-form]").is_visible()
        assert page.get_by_text(
            "Existing Package — shared information is read-only"
        ).count() == (1 if existing else 0)


@pytest.mark.parametrize("width", [390, 1440])
def test_create_branch_admin_back_is_shared_secondary_button(
    operations_browser, user_factory, width, tmp_path
):
    page, _ = operations_browser
    base = operations.base_url(page)
    admin = user_factory(role=UserRole.SUPERADMIN)
    operations.browser_login(page, base, admin)
    page.set_viewport_size({"width": width, "height": 950})
    page.goto(base + "/admin/users/create-branch-admin")
    product_name(page, width)
    back = page.get_by_role("link", name="Back", exact=True)
    check_action(page, back)
    assert back.get_attribute("href") == "/admin/users"
    page.screenshot(path=str(tmp_path / f"branch-admin-actions-{width}.png"))
    back.press("Enter")
    page.wait_for_url("**/admin/users")
