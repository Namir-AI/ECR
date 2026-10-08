"""Series confirmation UX and authoritative save reconciliation in real Chromium."""

from uuid import uuid4

import pytest

from tests import test_draft_save_browser as browser_tests
from tests.test_page1_browser import wait_saved

draft_browser = browser_tests.draft_browser
playwright = browser_tests.playwright


def autosaves(page):
    requests = []
    page.on(
        "request",
        lambda request: (
            requests.append(request) if request.url.endswith("/autosave") else None
        ),
    )
    return requests


@pytest.mark.parametrize("dismiss", ["Cancel", "Escape"])
def test_cancel_keeps_existing_series_without_autosave(
    draft_browser, db_session, dismiss
):
    page, report = draft_browser
    requests = autosaves(page)
    control = page.locator("[data-series-select]")
    control.select_option("AQ-3800")
    dialog = page.get_by_role("dialog", name="Change Cooling Tower Series?")
    playwright.expect(dialog).to_be_visible()
    assert "Series 10" in dialog.inner_text() and "AQ-3800" in dialog.inner_text()
    assert control.input_value() == "Series 10"
    assert page.locator('[name="drive_shaft_series"]').is_visible()
    page.wait_for_timeout(900)  # Longer than the real autosave debounce.
    assert not requests
    if dismiss == "Escape":
        page.keyboard.press("Escape")
    else:
        dialog.get_by_role("button", name="Cancel", exact=True).click()
    playwright.expect(dialog).not_to_be_visible()
    playwright.expect(control).to_be_focused()
    page.wait_for_timeout(900)
    assert not requests
    db_session.refresh(report.tower.package)
    assert report.tower.package.cooling_tower_series == "Series 10"


def test_confirm_waits_for_commit_acknowledgement_and_reloads(draft_browser):
    page, _ = draft_browser
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const result = await original(...args);
        if (String(args[0]).endsWith('/autosave')) await new Promise(resolve => window.acknowledge = resolve);
        return result;
      };
    }""")
    page.locator("[data-series-select]").select_option("AQ-3800")
    assert page.evaluate("typeof window.acknowledge") == "undefined"
    page.get_by_role("button", name="Change Series", exact=True).click()
    page.wait_for_function("typeof window.acknowledge === 'function'")
    assert page.locator("[data-autosave-status]").inner_text() != "Saved"
    page.evaluate("window.acknowledge()")
    wait_saved(page)
    page.reload()
    assert page.locator("[data-series-select]").input_value() == "AQ-3800"
    assert not page.locator('[name="drive_shaft_series"]').is_visible()


def test_same_series_does_not_open_dialog_or_schedule_save(draft_browser):
    page, _ = draft_browser
    requests = autosaves(page)
    page.locator("[data-series-select]").select_option("Series 10")
    assert not page.locator("[data-series-dialog]").is_visible()
    page.wait_for_timeout(900)
    assert not requests
    page.locator("[data-series-select]").select_option("")
    assert page.locator("[data-series-select]").input_value() == "Series 10"
    page.locator("[data-series-select]").select_option("AQ-3800")
    playwright.expect(page.get_by_role("dialog")).to_be_visible()
    page.get_by_role("button", name="Cancel", exact=True).click()


def test_first_selection_needs_no_confirmation_and_creates_normally(draft_browser):
    page, _ = draft_browser
    page.goto(page.url.split("/ecr/")[0] + "/ecr/reports/new")
    page.locator('[name="cooling_tower_serial_no"]').fill(f"MODAL-{uuid4().hex[:12]}")
    page.get_by_role("button", name="Start Report").click()
    series = page.locator('[name="cooling_tower_series"]')
    assert series.input_value() == ""
    series.select_option("CF-I")
    assert not page.locator("[data-series-dialog]").is_visible()
    for name, value in {
        "customer": "Modal test customer",
        "model": "Test Model",
        "place_of_installation": "Test site",
        "cell_no": "1",
        "erection_completion_date": "2026-10-02",
    }.items():
        page.locator(f'[name="{name}"]').fill(value)
    page.locator('[name="multiple_towers"]').select_option("false")
    page.get_by_role("button", name="Create or Resume Draft").click()
    page.wait_for_url("**/edit?notice=created")
    assert page.locator("[data-series-select]").input_value() == "CF-I"
    assert page.locator('[name="model"]').count() == 0


@pytest.mark.parametrize("width", [1280, 390])
def test_modal_mobile_keyboard_focus_and_layout(draft_browser, tmp_path, width):
    page, _ = draft_browser
    page.set_viewport_size({"width": width, "height": 844})
    control = page.locator("[data-series-select]")
    control.focus()
    # Use the keyboard to move Series 10 to the adjacent Series 15 selection.
    control.press("ArrowDown")
    dialog = page.get_by_role("dialog")
    playwright.expect(dialog).to_be_visible()
    cancel = dialog.get_by_role("button", name="Cancel", exact=True)
    confirm = dialog.get_by_role("button", name="Change Series", exact=True)
    playwright.expect(cancel).to_be_focused()
    page.keyboard.press("Tab")
    playwright.expect(confirm).to_be_focused()
    page.keyboard.press("Tab")
    assert page.evaluate(
        "document.querySelector('[data-series-dialog]').contains(document.activeElement)"
    )
    page.keyboard.press("Shift+Tab")
    playwright.expect(confirm).to_be_focused()
    box = dialog.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(tmp_path / f"series-modal-{width}.png"))
    confirm.focus()
    page.keyboard.press("Enter")
    wait_saved(page)
    page.reload()
    assert control.input_value() == "Series 15"


def test_rejected_change_reads_authoritative_series_without_second_save(
    draft_browser, db_session
):
    page, report = draft_browser
    # Simulate another authorized tab changing Series after this page loaded.
    report.tower.package.cooling_tower_series = "CF-II"
    db_session.commit()
    requests = autosaves(page)
    page.route(
        "**/autosave",
        lambda route: route.fulfill(
            status=409, json={"ok": False, "message": "Change rejected"}
        ),
    )
    page.locator("[data-series-select]").select_option("AQ-3800")
    page.get_by_role("button", name="Change Series", exact=True).click()
    playwright.expect(page.locator("[data-autosave-status]")).to_contain_text(
        "Saved Series restored"
    )
    assert page.locator("[data-series-select]").input_value() == "CF-II"
    assert page.locator('[name="drive_shaft_series"]').is_visible()
    page.wait_for_timeout(1100)
    assert len(requests) == 1
    assert (
        page.locator("[data-autosave-status]").inner_text().startswith("Unable to save")
    )
    page.unroute("**/autosave")
    page.reload()
    assert page.locator("[data-series-select]").input_value() == "CF-II"


def test_failed_reconciliation_requires_reload_and_never_claims_saved(draft_browser):
    page, _ = draft_browser
    requests = autosaves(page)
    page.route("**/autosave", lambda route: route.abort("failed"))
    page.route("**/series-state", lambda route: route.abort("failed"))
    page.locator("[data-series-select]").select_option("AQ-3800")
    page.get_by_role("button", name="Change Series", exact=True).click()
    playwright.expect(page.locator("[data-autosave-status]")).to_contain_text(
        "reload to verify"
    )
    assert page.locator("[data-series-select]").input_value() == "Series 10"
    assert page.locator("[data-series-select]").is_disabled()
    page.locator("[data-save-now]").click()
    page.wait_for_timeout(900)
    assert len(requests) == 1
