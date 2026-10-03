"""Real browser text autosave and pointer-drawn optional private signatures."""

import threading

import pytest

from app.db.session import get_db_session
from app.storage import LocalProtectedStorage
from app.users.models import UserRole
from tests import test_draft_save_browser as browser_tests
from tests.test_page1_browser import wait_saved

draft_browser = browser_tests.draft_browser
playwright = browser_tests.playwright


@pytest.fixture
def completion_browser(draft_browser, client, db_session, tmp_path):
    client.app.state.protected_storage = LocalProtectedStorage(tmp_path / "private")
    # Production gives every request a separate Session. This test fixture shares
    # one outer MySQL transaction, so serialize its image and save HTTP requests.
    lock = threading.Lock()

    def serialized_db():
        with lock:
            yield db_session

    client.app.dependency_overrides[get_db_session] = serialized_db
    return draft_browser


def draw(page, *, offset=0):
    canvas = page.locator("[data-signature-pad]")
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.15, box["y"] + box["height"] * 0.6)
    page.mouse.down()
    for x, y in [(0.25, 0.2), (0.4, 0.8), (0.55, 0.3), (0.8, 0.5)]:
        page.mouse.move(
            box["x"] + box["width"] * x, box["y"] + box["height"] * y + offset, steps=3
        )
    page.mouse.up()


def wait_signature(page, state="saved"):
    page.wait_for_function(
        f'document.querySelector("[data-signature-status]").dataset.state === "{state}"'
    )
    if state == "saved" and page.locator("[data-saved-signature]").is_visible():
        page.wait_for_function(
            'document.querySelector("[data-signature-image]").complete && document.querySelector("[data-signature-image]").naturalWidth > 0'
        )


def assert_identity_alignment(page, owner_name):
    identity = page.locator(".completion-identity")
    labels = identity.locator("dt")
    colons = identity.locator(".identity-colon")
    values = identity.locator(".identity-value")
    assert labels.all_text_contents() == [
        "Erected / Commissioned by",
        "Name (in Block Letters)",
    ]
    assert colons.all_text_contents() == [":", ":"]
    assert values.all_text_contents() == [owner_name, owner_name.upper()]
    for column in (labels, colons, values):
        bounds = [element.bounding_box() for element in column.all()]
        assert abs(bounds[0]["x"] - bounds[1]["x"]) <= 1
    for index in range(2):
        label, colon, value = [
            column.nth(index).bounding_box() for column in (labels, colons, values)
        ]
        assert label["x"] + label["width"] < colon["x"]
        assert colon["x"] + colon["width"] < value["x"]
        assert abs(label["y"] - colon["y"]) <= 1
        assert abs(colon["y"] - value["y"]) <= 1
    assert identity.locator("input, textarea, select").count() == 0
    assert identity.evaluate("el => el.scrollWidth <= el.clientWidth")


@pytest.mark.parametrize("width", [320, 390, 1280])
def test_text_signature_replace_clear_reload_mobile(
    completion_browser, db_session, tmp_path, width
):
    page, report = completion_browser
    page.set_viewport_size({"width": width, "height": 850})
    assert_identity_alignment(page, report.supervisor.full_name)
    page.locator(".completion-identity").screenshot(
        path=str(tmp_path / f"identity-{width}.png")
    )
    assert (
        page.locator("[data-signature-clear-drawing]").inner_text() == "Clear Signature"
    )
    assert page.locator('[name="team_leader_report"]').input_value() == ""
    assert page.locator('[name="customer_comment"]').input_value() == ""
    assert "optional" in page.locator("#signature-heading").inner_text()
    for field in ("team_leader_report", "customer_comment"):
        value = (
            "\nParagraph one\n\nParagraph two <script>window.page3xss=1</script> & Ω"
        )
        page.locator(f'[name="{field}"]').fill(value)
        page.locator("[data-save-now]").click()
        wait_saved(page)
        page.reload()
        assert page.locator(f'[name="{field}"]').input_value() == value
        assert page.evaluate("window.page3xss") is None
    # Nothing is created by blank canvas Save Signature or normal Save now.
    page.locator("[data-signature-save]").click()
    wait_signature(page, "error")
    db_session.expire_all()
    assert report.page3.customer_signed_at is None
    draw(page)
    assert "not saved" in page.locator("[data-signature-status]").inner_text()
    page.locator("[data-save-now]").click()
    wait_saved(page)
    db_session.expire_all()
    assert report.page3.customer_signature_storage_key is None
    # Drawing survives resize without distortion/data loss.
    page.set_viewport_size({"width": width + 20, "height": 850})
    page.locator("[data-signature-save]").click()
    wait_signature(page)
    assert "Signed on:" in page.locator("[data-signed-at]").inner_text()
    db_session.expire_all()
    first_key = report.page3.customer_signature_storage_key
    assert first_key and report.page3.customer_signed_at is not None
    page.reload()
    image = page.locator("[data-signature-image]")
    page.wait_for_function(
        'document.querySelector("[data-signature-image]").naturalWidth > 0'
    )
    assert image.evaluate("el => el.naturalWidth") == 1800
    assert image.evaluate("el => el.naturalHeight") == 600
    page.locator("[data-signature-card]").screenshot(
        path=str(tmp_path / f"signed-{width}.png")
    )
    # Clear unsaved drawing keeps stored signature and needs no confirmation.
    draw(page, offset=2)
    page.locator("[data-signature-clear-drawing]").click()
    assert not page.locator("[data-signature-remove-dialog]").is_visible()
    db_session.expire_all()
    assert report.page3.customer_signature_storage_key == first_key
    draw(page, offset=6)
    page.locator("[data-signature-save]").click()
    wait_signature(page)
    db_session.expire_all()
    assert report.page3.customer_signature_storage_key != first_key
    page.locator("[data-signature-remove]").click()
    assert page.locator("[data-signature-remove-dialog]").is_visible()
    page.keyboard.press("Escape")
    assert not page.locator("[data-signature-remove-dialog]").is_visible()
    assert page.locator("[data-signature-remove]").evaluate(
        "el => el === document.activeElement"
    )
    page.locator("[data-signature-remove]").click()
    page.locator("[data-signature-remove-confirm]").focus()
    page.keyboard.press("Enter")
    wait_signature(page)
    page.reload()
    assert page.locator("[data-saved-signature]").is_hidden()
    assert page.locator("[data-signed-at]").inner_text() == ""
    db_session.expire_all()
    assert (
        report.page3.customer_signed_at is None
        and report.page3.customer_signature_storage_key is None
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("[data-signature-card]").screenshot(
        path=str(tmp_path / f"signature-{width}.png")
    )


@pytest.mark.parametrize("failure", ["http", "network", "unconfirmed"])
def test_signature_failure_retains_drawing_no_false_saved(completion_browser, failure):
    page, _ = completion_browser

    def fail(route):
        if route.request.method != "POST":
            route.continue_()
        elif failure == "network":
            route.abort()
        else:
            route.fulfill(status=503 if failure == "http" else 200, json={"ok": True})

    page.route("**/signature", fail)
    draw(page)
    page.locator("[data-signature-save]").click()
    wait_signature(page, "error")
    assert "Signature saved" not in page.locator("[data-signature-status]").inner_text()
    assert page.locator("[data-signed-at]").inner_text() == ""
    page.unroute("**/signature")
    page.locator("[data-signature-save]").click()
    wait_signature(page)


@pytest.mark.parametrize("role", [UserRole.BRANCH_ADMIN, UserRole.SUPERADMIN])
def test_page3_admin_readonly_identity_signature(
    completion_browser, user_factory, role
):
    page, report = completion_browser
    page.locator('[name="team_leader_report"]').fill("Team report\nLine two")
    wait_saved(page)
    draw(page)
    page.locator("[data-signature-save]").click()
    wait_signature(page)
    owner_name = report.supervisor.full_name
    admin = user_factory(role=role, branch=report.branch)
    base = page.url.split("/ecr/")[0]
    page.context.clear_cookies()
    page.goto(base + "/auth/login")
    page.locator('[name="identifier"]').fill(admin.employee_id)
    page.locator('[name="password"]').fill("Correct-Horse-123")
    page.locator('button[type="submit"]').click()
    page.wait_for_url("**/dashboard")
    page.goto(base + f"/reports/{report.id}")
    assert page.get_by_text(owner_name.upper(), exact=True).is_visible()
    assert_identity_alignment(page, owner_name)
    page.set_viewport_size({"width": 390, "height": 850})
    assert_identity_alignment(page, owner_name)
    assert (
        page.locator(".plain-report-text").first.inner_text() == "Team report\nLine two"
    )
    page.wait_for_function(
        'document.querySelector(".customer-signature-image").naturalWidth > 0'
    )
    assert (
        page.locator("[data-signature-pad], [data-signature-save], textarea").count()
        == 0
    )


def test_text_older_acknowledgement_cannot_mark_new_edit_saved(completion_browser):
    page, _ = completion_browser
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (!window.firstTextSave) { window.firstTextSave = true; await new Promise(resolve => { window.releaseFirst = resolve; }); }
        else if (!window.secondTextSave) { window.secondTextSave = true; await new Promise(resolve => { window.releaseSecond = resolve; }); }
        return response;
      };
    }""")
    page.locator('[name="team_leader_report"]').fill("First")
    page.locator("[data-save-now]").click()
    page.wait_for_function("typeof window.releaseFirst === 'function'")
    page.locator('[name="team_leader_report"]').fill("Latest\nText")
    page.evaluate("window.releaseFirst()")
    assert page.locator("[data-autosave-status]").inner_text() != "Saved"
    page.wait_for_function("typeof window.releaseSecond === 'function'")
    assert page.locator("[data-autosave-status]").inner_text() != "Saved"
    page.evaluate("window.releaseSecond()")
    wait_saved(page)
    page.reload()
    assert page.locator('[name="team_leader_report"]').input_value() == "Latest\nText"


def test_cross_branch_admin_signature_is_private(
    completion_browser, user_factory, branch_factory
):
    page, report = completion_browser
    draw(page)
    page.locator("[data-signature-save]").click()
    wait_signature(page)
    admin = user_factory(
        role=UserRole.BRANCH_ADMIN, branch=branch_factory(code="MUMBAI")
    )
    base = page.url.split("/ecr/")[0]
    page.context.clear_cookies()
    page.goto(base + "/auth/login")
    page.locator('[name="identifier"]').fill(admin.employee_id)
    page.locator('[name="password"]').fill("Correct-Horse-123")
    page.locator('button[type="submit"]').click()
    page.wait_for_url("**/dashboard")
    assert page.request.get(base + f"/ecr/reports/{report.id}/signature").status == 404
    assert page.goto(base + f"/reports/{report.id}").status == 404
    assert page.locator(".customer-signature-image, [data-signature-pad]").count() == 0


@pytest.mark.parametrize("pointer", ["touch", "pen"])
def test_touch_and_stylus_pointer_drawing(completion_browser, pointer):
    page, _ = completion_browser
    page.set_viewport_size({"width": 390, "height": 850})
    canvas = page.locator("[data-signature-pad]")
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    cdp = page.context.new_cdp_session(page)
    if pointer == "touch":
        cdp.send(
            "Input.dispatchTouchEvent",
            {
                "type": "touchStart",
                "touchPoints": [{"x": box["x"] + 40, "y": box["y"] + 40}],
            },
        )
        cdp.send(
            "Input.dispatchTouchEvent",
            {
                "type": "touchMove",
                "touchPoints": [{"x": box["x"] + 150, "y": box["y"] + 55}],
            },
        )
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    else:
        cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": box["x"] + 40,
                "y": box["y"] + 40,
                "button": "left",
                "clickCount": 1,
                "pointerType": "pen",
            },
        )
        cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseMoved",
                "x": box["x"] + 150,
                "y": box["y"] + 55,
                "button": "left",
                "buttons": 1,
                "pointerType": "pen",
            },
        )
        cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "x": box["x"] + 150,
                "y": box["y"] + 55,
                "button": "left",
                "pointerType": "pen",
            },
        )
    page.locator("[data-signature-save]").click()
    wait_signature(page)
    page.reload()
    page.wait_for_function(
        'document.querySelector("[data-signature-image]").naturalWidth > 0'
    )
