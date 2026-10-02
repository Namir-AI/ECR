"""Real Chromium Draft-save regressions against FastAPI and the MySQL fixtures.

These tests require optional Playwright tooling and its Chromium browser.
"""

import os
import socket
import threading
from datetime import date

import pytest
import uvicorn

from tests.test_phase3 import _create_report

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def draft_browser(client, db_session, user_factory):
    """Run the application over real HTTP with transaction-isolated test records."""
    supervisor = user_factory()
    report = _create_report(db_session, supervisor)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(client.app, log_level="error", lifespan="off")
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    try:
        with playwright.sync_playwright() as driver:
            browser = driver.chromium.launch(
                headless=os.environ.get("ECR_BROWSER_HEADED") != "1"
            )
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/auth/login")
            page.locator('[name="identifier"]').fill(supervisor.employee_id)
            page.locator('[name="password"]').fill("Correct-Horse-123")
            page.locator('button[type="submit"]').click()
            page.wait_for_url("**/dashboard")
            page.goto(f"http://127.0.0.1:{port}/ecr/reports/{report.id}/edit")
            yield page, report
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


def test_browser_autosave_and_save_now_persist_after_reload(draft_browser, db_session):
    page, report = draft_browser
    assert page.locator("[data-autosave-status]").inner_text() == "All changes saved"
    page.locator('[name="erection_start_date"]').fill("2026-09-02")
    page.wait_for_function(
        'document.querySelector("[data-autosave-status]").dataset.state === "saved"'
    )
    page.reload()
    assert page.locator('[name="erection_start_date"]').input_value() == "2026-09-02"

    page.locator('[name="erection_completion_date"]').fill("2026-10-02")
    page.locator("[data-save-now]").click()
    page.wait_for_function(
        'document.querySelector("[data-autosave-status]").dataset.state === "saved"'
    )
    page.reload()
    assert page.locator('[name="erection_completion_date"]').input_value() == "2026-10-02"
    db_session.refresh(report)
    assert report.erection_start_date == date(2026, 9, 2)
    assert report.erection_completion_date == date(2026, 10, 2)


def test_browser_older_response_cannot_mark_newer_edit_saved(draft_browser):
    page, _report = draft_browser
    page.evaluate("""() => {
      const originalFetch = window.fetch;
      window.fetch = async (...args) => {
        const response = await originalFetch(...args);
        if (!window.firstSaveResponse) {
          window.firstSaveResponse = true;
          await new Promise(resolve => { window.releaseFirstSave = resolve; });
        } else if (!window.secondSaveResponse) {
          window.secondSaveResponse = true;
          await new Promise(resolve => { window.releaseSecondSave = resolve; });
        }
        return response;
      };
    }""")
    page.locator('[name="erection_start_date"]').fill("2026-09-03")
    page.locator("[data-save-now]").click()
    page.wait_for_function("typeof window.releaseFirstSave === 'function'")
    page.locator('[name="erection_completion_date"]').fill("2026-10-03")
    page.evaluate("() => window.releaseFirstSave()")
    # Acknowledging the old snapshot must not acknowledge the unsent new date.
    assert page.locator("[data-autosave-status]").inner_text() != "Saved"
    page.wait_for_function("typeof window.releaseSecondSave === 'function'")
    assert page.locator("[data-autosave-status]").inner_text() != "Saved"
    page.evaluate("() => window.releaseSecondSave()")
    page.wait_for_function(
        'document.querySelector("[data-autosave-status]").dataset.state === "saved"'
    )
    page.reload()
    assert page.locator('[name="erection_completion_date"]').input_value() == "2026-10-03"


@pytest.mark.parametrize("failure", ["network", "http", "html", "unconfirmed"])
def test_browser_failure_never_shows_saved_and_save_now_recovers(draft_browser, failure):
    page, _report = draft_browser

    def fail_request(route):
        if failure == "network":
            route.abort("failed")
        elif failure == "http":
            # Even an ok=true body cannot override an unsuccessful HTTP status.
            route.fulfill(status=503, json={"ok": True, "message": "Temporarily unavailable"})
        elif failure == "html":
            route.fulfill(status=200, content_type="text/html", body="<p>Login required</p>")
        else:
            route.fulfill(status=200, json={"ok": True, "message": "Saved"})

    page.route("**/autosave", fail_request)
    page.locator('[name="erection_start_date"]').fill("2026-09-04")
    page.locator("[data-save-now]").click()
    page.wait_for_function(
        'document.querySelector("[data-autosave-status]").dataset.state === "error"'
    )
    assert page.locator("[data-autosave-status]").inner_text().startswith("Unable to save")
    page.unroute("**/autosave", fail_request)
    page.locator("[data-save-now]").click()
    page.wait_for_function(
        'document.querySelector("[data-autosave-status]").dataset.state === "saved"'
    )
    page.reload()
    assert page.locator('[name="erection_start_date"]').input_value() == "2026-09-04"


def test_browser_network_failure_automatically_retries(draft_browser):
    page, _report = draft_browser
    page.route("**/autosave", lambda route: route.abort("failed"), times=1)
    page.locator('[name="erection_completion_date"]').fill("2026-10-04")
    page.locator("[data-save-now]").click()
    playwright.expect(page.locator("[data-autosave-status]")).to_have_text(
        "Unable to save — retrying"
    )
    page.wait_for_function(
        'document.querySelector("[data-autosave-status]").dataset.state === "saved"'
    )
    page.reload()
    assert page.locator('[name="erection_completion_date"]').input_value() == "2026-10-04"


def test_browser_database_commit_failure_has_no_success_state(
    draft_browser, db_session, monkeypatch
):
    from sqlalchemy.exc import OperationalError

    page, report = draft_browser
    original_date = report.erection_completion_date

    def failing_commit():
        raise OperationalError("commit", None, RuntimeError("Simulated commit failure"))

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", failing_commit)
        page.locator('[name="erection_completion_date"]').fill("2026-10-05")
        with page.expect_response("**/autosave") as response:
            page.locator("[data-save-now]").click()
        assert response.value.status == 503
        playwright.expect(page.locator("[data-autosave-status]")).to_have_text(
            "Unable to save — retrying"
        )
        db_session.refresh(report)
        assert report.erection_completion_date == original_date

    page.locator("[data-save-now]").click()
    page.wait_for_function(
        'document.querySelector("[data-autosave-status]").dataset.state === "saved"'
    )
    page.reload()
    assert page.locator('[name="erection_completion_date"]').input_value() == "2026-10-05"
