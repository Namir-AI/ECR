"""Real Chromium inspection of the SAME Letter HTML used for server PDFs."""

from datetime import UTC
from decimal import Decimal

import pytest

from app.ecr.print_features import server_pdf_enabled
from tests import test_draft_save_browser as browser_tests
from tests.test_phase5 import complete_report

draft_browser = browser_tests.draft_browser
playwright = browser_tests.playwright


@pytest.mark.parametrize("continuation", [False, True, "maximum-width"])
def test_official_preview_print_media_geometry(
    draft_browser, db_session, tmp_path, continuation
):
    page, report = draft_browser
    complete_report(db_session, report)
    report.page1.motor_hp = 25
    report.page2.fc_valve_diameter = 2
    db_session.commit()
    if continuation:
        report.page3.team_leader_report = "\n".join(
            f"Continuation line {i}: Verified" for i in range(70)
        )
        db_session.commit()
    else:
        report.page3.team_leader_report = "Completed\n<script>window.ecrPrintXss=1</script>\n<img src='http://127.0.0.1:1/private'>"
        db_session.commit()
    if continuation == "maximum-width":
        from app.ecr.models import EcrFanBladeSerial

        report.tower.package.cooling_tower_serial_no = "W" * 100
        report.tower.package.multiple_towers = True
        report.tower.tower_suffix = "A"
        report.tower.normalized_suffix_key = "A"
        report.page1.blade_serials = [
            EcrFanBladeSerial(position=i, serial_no="W" * 250) for i in range(1, 9)
        ]
        db_session.commit()
    base = page.url.split("/ecr/")[0]
    page.goto(base + f"/ecr/reports/{report.id}/print")
    assert page.locator(".core-page").count() == 3
    count = page.locator(".continuation-page").count()
    assert count >= 2 if continuation else count == 0
    assert page.evaluate("window.ecrPrintXss") is None
    assert page.locator("script").count() == 0
    if server_pdf_enabled():
        assert page.get_by_role("link", name="Download PDF").is_visible()
    else:
        assert page.get_by_role("link", name="Download PDF").count() == 0
        assert page.request.get(base + f"/ecr/reports/{report.id}/pdf").status == 404
    assert page.get_by_role("button", name="Print", exact=True).is_visible()
    page.evaluate("window.print = () => window.ecrPrintInvoked = true")
    page.get_by_role("button", name="Print", exact=True).click()
    assert page.evaluate("window.ecrPrintInvoked") is True
    assert (
        page.locator(
            ".sidebar, .app-footer, [data-signature-pad], .customer-sign"
        ).count()
        == 0
    )
    assert page.locator(".red-ellipse").count() == 2
    assert page.locator('svg line[data-axis="horizontal"]').count() == 2
    assert page.locator('svg line[data-axis="vertical"]').count() == 2
    page.emulate_media(media="print")
    assert not page.locator(".print-controls").is_visible()
    violations = page.evaluate("""() => {
      const problems = [];
      for (const sheet of document.querySelectorAll('.sheet')) {
        const s = sheet.getBoundingClientRect();
        const footer = sheet.querySelector('.page-footer').getBoundingClientRect();
        for (const e of sheet.querySelectorAll('.equipment, .torque-section, .alignment-pair, .team-report, .completion-pair, .continuation-text')) {
          const b = e.getBoundingClientRect();
          if (b.left < s.left || b.right > s.right || b.bottom > footer.top - 5)
            problems.push([e.className, b.bottom, footer.top]);
        }
        if (Math.abs(s.width - 816) > 1 || Math.abs(s.height - 1056) > 1) problems.push('Letter dimensions');
      }
      for (const e of document.querySelectorAll('.blade-grid span')) {
        if (e.scrollHeight > e.clientHeight + 1) problems.push('blade cell overflow');
      }
      return problems;
    }""")
    assert violations == []
    if continuation == "maximum-width" and server_pdf_enabled():
        from io import BytesIO

        from pypdf import PdfReader

        response = page.request.get(base + f"/ecr/reports/{report.id}/pdf")
        assert response.status == 200, response.text() if response.status != 200 else ""
        reader = PdfReader(BytesIO(response.body()))
        assert len(reader.pages) == 3 + count
        assert all(
            float(p.mediabox.width) == 612 and float(p.mediabox.height) == 792
            for p in reader.pages
        )
    for sheet in page.locator(".sheet").all():
        sheet.screenshot(
            path=str(
                tmp_path
                / f"print-{sheet.get_attribute('data-page') or sheet.get_attribute('data-continuation')}-{continuation}.png"
            )
        )
    page.emulate_media(media="screen")
    assert page.locator(".print-controls").is_visible()


def test_official_signed_preview_aspect_ratio(
    draft_browser, client, db_session, tmp_path
):
    import base64
    from datetime import datetime

    from app.storage import LocalProtectedStorage
    from tests.test_page3 import png

    page, report = draft_browser
    complete_report(db_session, report)
    store = LocalProtectedStorage(tmp_path / "private")
    client.app.state.protected_storage = store
    report.page3.customer_signature_storage_key = store.put(
        base64.b64decode(png().split(",")[1])
    )
    report.page3.customer_signed_at = datetime.now(UTC).replace(tzinfo=None)
    db_session.commit()
    page.goto(page.url.split("/ecr/")[0] + f"/ecr/reports/{report.id}/print")
    sign = page.locator(".customer-sign")
    assert sign.count() == 1
    page.wait_for_function("document.querySelector('.customer-sign').naturalWidth > 0")
    box = sign.bounding_box()
    parent = sign.locator("..").bounding_box()
    assert abs(box["width"] / box["height"] - 3) < 0.01
    assert box["width"] <= parent["width"] and box["height"] <= parent["height"]
    assert page.locator(".sign-date").inner_text().endswith("IST")


@pytest.mark.parametrize(
    "suffix,installation,title",
    [
        (None, "Test site", "26-2-0001 Test site ECR"),
        ("A", "Test site", "26-2-0001 A Test site ECR"),
        ("A", "Test Site / Unit-2", "26-2-0001 A Test Site Unit-2 ECR"),
    ],
)
def test_browser_save_pdf_title_and_incomplete_alignment(
    draft_browser, db_session, suffix, installation, title
):
    page, report = draft_browser
    complete_report(db_session, report)
    package = report.tower.package
    package.cooling_tower_serial_no = "26-2-0001"
    package.place_of_installation = installation
    package.multiple_towers = suffix is not None
    report.tower.tower_suffix = suffix
    report.tower.normalized_suffix_key = suffix or ""
    report.page2.de_top = None
    report.page2.de_bottom = Decimal("0.80")
    report.page2.de_right = Decimal("0.00")
    report.page2.de_left = Decimal("0.80")
    report.page2.nde_top = Decimal("0.00")
    report.page2.nde_bottom = Decimal("0.80")
    report.page2.nde_right = None
    report.page2.nde_left = Decimal("0.80")
    db_session.commit()
    page.goto(page.url.split("/ecr/")[0] + f"/ecr/reports/{report.id}/print")
    assert page.title() == title
    assert "Cell" not in page.title() and not page.title().endswith(".pdf")
    assert page.locator(".core-page").count() == 3
    place = page.locator(".identification tr").filter(has_text="Place of Installation")
    assert place.locator("td").inner_text() == installation
    de, nde = page.locator(".print-instrument svg").all()
    assert de.locator("text").all_text_contents() == [
        "Top",
        "",
        "Right",
        "0.00",
        "0.80",
        "Bottom",
        "Left",
        "0.80",
    ]
    assert nde.locator("text").all_text_contents() == [
        "Top",
        "0.00",
        "Right",
        "",
        "0.80",
        "Bottom",
        "Left",
        "0.80",
    ]
    assert (
        de.locator('[data-axis="horizontal"]').get_attribute("transform")
        == "translate(0 0)"
    )
    assert (
        de.locator('[data-axis="vertical"]').get_attribute("transform")
        == "translate(-7.2 0)"
    )
    assert (
        nde.locator('[data-axis="horizontal"]').get_attribute("transform")
        == "translate(0 -7.2)"
    )
    assert (
        nde.locator('[data-axis="vertical"]').get_attribute("transform")
        == "translate(0 0)"
    )
    if not server_pdf_enabled():
        assert page.get_by_role("link", name="Download PDF").count() == 0
    page.evaluate("window.print = () => window.ecrPrintInvoked = true")
    page.get_by_role("button", name="Print", exact=True).click()
    assert page.evaluate("window.ecrPrintInvoked") is True
