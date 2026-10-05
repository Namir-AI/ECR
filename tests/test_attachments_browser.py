"""Real browser package evidence upload, ordering, limits and read-only access."""

import threading

import pytest

from app.db.session import get_db_session
from app.ecr.models import EcrReportStatus
from app.storage.attachments import LocalAttachmentStorage
from app.users.models import UserRole
from tests import test_draft_save_browser as browser_tests
from tests.test_attachments import extra_cell, image_bytes, pdf_bytes

draft_browser = browser_tests.draft_browser
playwright = browser_tests.playwright


@pytest.fixture
def evidence_browser(draft_browser, client, db_session, tmp_path):
    page, report = draft_browser
    client.app.state.attachment_storage = LocalAttachmentStorage(tmp_path / "protected")
    lock = threading.Lock()

    def serialized_db():
        with lock:
            yield db_session

    client.app.dependency_overrides[get_db_session] = serialized_db
    page.goto(
        page.url.split("/ecr/")[0]
        + f"/ecr/packages/{report.tower.package_id}/attachments"
    )
    return page, report


def upload_files(form, files):
    form.locator('[name="files"]').set_input_files(
        [
            {"name": name, "mimeType": "application/octet-stream", "buffer": content}
            for name, content in files
        ]
    )


def saved_action(page, control):
    with page.expect_navigation(wait_until="domcontentloaded"):
        control.click()


@pytest.mark.parametrize("width", [390, 1280])
def test_browser_jcc_images_order_photos_replace_remove_reload(
    evidence_browser, tmp_path, width
):
    page, _ = evidence_browser
    page.set_viewport_size({"width": width, "height": 850})
    assert page.locator("h1").inner_text() == "JCC and Tower Photo Upload"
    assert page.title().startswith("JCC and Tower Photo Upload")
    assert (
        "Multi-page PDF or JPG/JPEG/PNG"
        in page.locator("#jcc-title").locator("..").inner_text()
    )
    assert (
        "One logical document"
        not in page.locator("[data-package-attachments]").inner_text()
    )
    assert (
        "5,000,000 bytes" not in page.locator("[data-package-attachments]").inner_text()
    )
    picker = page.locator(".file-picker").filter(has_text="Choose JCC")
    assert picker.bounding_box()["width"] < 200
    file_input = picker.locator('input[type="file"]')
    file_input.focus()
    with page.expect_file_chooser() as chooser:
        file_input.press("Space")
    chooser.value.set_files(
        {"name": "keyboard.png", "mimeType": "image/png", "buffer": image_bytes()}
    )
    playwright.expect(
        picker.locator("..").locator("[data-selected-filenames]")
    ).to_have_text("keyboard.png")
    assert page.locator("[data-photo-summary]").inner_text().startswith("0 of 5")
    jcc_form = page.locator('[data-attachment-form][data-kind="JCC"]')
    upload_files(
        jcc_form,
        [("page-one.png", image_bytes()), ("page-two.jpg", image_bytes("JPEG"))],
    )
    saved_action(page, jcc_form.locator('button[type="submit"]').first)
    jcc = page.locator('[data-attachment-list="JCC"]')
    playwright.expect(jcc.locator("li")).to_have_count(2)
    assert jcc.locator(".attachment-filename").all_text_contents() == [
        "page-one.png (0.08 KB)",
        "page-two.jpg (0.63 KB)",
    ]
    saved_action(page, jcc.locator('[data-direction="1"]').first)
    assert jcc.locator(".attachment-filename").all_text_contents() == [
        "page-two.jpg (0.63 KB)",
        "page-one.png (0.08 KB)",
    ]
    assert "0 of 5" in page.locator("[data-photo-summary]").inner_text()
    photos_form = page.locator('form:has(input[value="TOWER_PHOTO_ADD"])')
    upload_files(photos_form, [(f"tower-{i}.png", image_bytes()) for i in range(5)])
    saved_action(page, photos_form.locator('button[type="submit"]'))
    photos = page.locator('[data-attachment-list="TOWER_PHOTO"]')
    playwright.expect(photos.locator("li")).to_have_count(5)
    assert "5 of 5" in page.locator("[data-photo-summary]").inner_text()
    assert photos.locator("li").first.locator("strong").inner_text() == "CT Photo 1"
    assert all(
        button.bounding_box()["width"] < 200
        for button in photos.locator('form button[type="submit"]').all()
    )
    playwright.expect(photos_form).to_have_count(0)
    assert page.get_by_role("button", name="Add Photos", exact=True).count() == 0
    result = page.evaluate(
        """async bytes => {
      const form = new FormData(); form.set('action', 'TOWER_PHOTO_ADD');
      form.append('files', new Blob([new Uint8Array(bytes)], {type:'image/png'}), 'sixth.png');
      const response = await fetch(location.pathname, {method:'POST', body:form, headers:{'X-CSRF-Token':document.querySelector('[name=csrf_token]').value}});
      return {status:response.status, body:await response.json()};
    }""",
        list(image_bytes()),
    )
    assert result["status"] == 422 and "at most 5" in result["body"]["message"]
    assert photos.locator("li").count() == 5
    replacement = photos.locator("li").first.locator("form")
    upload_files(replacement, [("replacement.jpg", image_bytes("JPEG"))])
    saved_action(page, replacement.locator('button[type="submit"]'))
    assert (
        photos.locator(".attachment-filename").first.inner_text()
        == "replacement.jpg (0.63 KB)"
    )
    saved_action(
        page, photos.locator('[data-attachment-action="TOWER_PHOTO_REMOVE"]').first
    )
    assert "4 of 5" in page.locator("[data-photo-summary]").inner_text()
    playwright.expect(photos_form).to_have_count(1)
    page.reload()
    assert photos.locator("li").count() == 4
    page.wait_for_function(
        "[...document.querySelectorAll('.attachment-preview')].every(i => i.complete && i.naturalWidth > 0)"
    )
    assert page.locator("[data-package-attachments]").evaluate(
        "el => el.scrollWidth <= el.clientWidth"
    )
    page.screenshot(
        path=str(tmp_path / f"package-evidence-{width}.png"), full_page=True
    )


@pytest.mark.parametrize("pages", [1, 3])
def test_browser_pdf_and_malformed_content(evidence_browser, pages):
    page, _ = evidence_browser
    form = page.locator('[data-kind="JCC"]')
    upload_files(form, [("jcc.pdf", pdf_bytes(pages))])
    saved_action(page, form.locator('button[type="submit"]').first)
    assert (
        f"PDF · {pages} page"
        in page.locator('[data-attachment-list="JCC"]').inner_text()
    )
    upload_files(form, [("broken.pdf", b"%PDF-broken")])
    form.locator('button[type="submit"]').first.click()
    playwright.expect(page.locator("[data-attachment-status]")).to_contain_text(
        "Malformed PDF"
    )
    page.reload()
    assert (
        f"PDF · {pages} page"
        in page.locator('[data-attachment-list="JCC"]').inner_text()
    )


@pytest.mark.parametrize(
    "role,related",
    [
        (UserRole.SUPERVISOR, True),
        (UserRole.BRANCH_ADMIN, True),
        (UserRole.BRANCH_ADMIN, False),
        (UserRole.SUPERADMIN, False),
    ],
)
def test_browser_authorized_read_only_and_cross_branch(
    evidence_browser, db_session, user_factory, branch_factory, role, related
):
    page, report = evidence_browser
    form = page.locator('[data-kind="JCC"]')
    upload_files(form, [("jcc.pdf", pdf_bytes())])
    saved_action(page, form.locator('button[type="submit"]').first)
    package_url = page.url
    viewer = user_factory(
        role=role,
        branch=report.branch if related else branch_factory(code="ATT-BROWSER-OTHER"),
    )
    if role is UserRole.SUPERVISOR:
        extra_cell(db_session, report, viewer)
    page.context.clear_cookies()
    page.goto(package_url.split("/ecr/")[0] + "/auth/login")
    page.locator('[name="identifier"]').fill(viewer.employee_id)
    page.locator('[name="password"]').fill("Correct-Horse-123")
    page.locator('button[type="submit"]').click()
    page.wait_for_url("**/dashboard")
    response = page.goto(package_url)
    if role is UserRole.BRANCH_ADMIN and not related:
        assert response.status == 404
        assert "jcc.pdf" not in page.content()
    else:
        assert response.status == 200
        assert page.locator("[data-attachment-form]").count() == 0
        assert "jcc.pdf" in page.content()


@pytest.mark.parametrize(
    "status", [EcrReportStatus.REVIEWED, EcrReportStatus.SUBMITTED]
)
def test_browser_package_wide_submission_lock(
    evidence_browser, db_session, user_factory, status
):
    page, report = evidence_browser
    extra_cell(db_session, report, user_factory(), status=status, another_tower=True)
    page.reload()
    assert (page.locator("[data-attachment-form]").count() > 0) == (
        status is EcrReportStatus.REVIEWED
    )


def test_browser_photo_size_guard_and_crafted_negative_content(evidence_browser):
    page, _ = evidence_browser
    form = page.locator('form:has(input[value="TOWER_PHOTO_ADD"])')
    upload_files(form, [("oversized.png", image_bytes(exact_size=5_000_001))])
    form.locator('button[type="submit"]').click()
    playwright.expect(page.locator("[data-attachment-status]")).to_contain_text(
        "5 MB combined"
    )
    assert "0 of 5" in page.locator("[data-photo-summary]").inner_text()
    upload_files(form, [("fake.png", b"<script>not an image</script>")])
    form.locator('button[type="submit"]').click()
    playwright.expect(page.locator("[data-attachment-status]")).to_contain_text(
        "valid JPG/JPEG"
    )
    assert "0 of 5" in page.locator("[data-photo-summary]").inner_text()
