"""Real-browser Batch A+B, series switching, guards and autosave confirmation."""

import pytest

from tests import test_draft_save_browser as browser_tests
from tests.test_page1_browser import wait_saved
from tests.test_phase3 import _create_report

draft_browser = browser_tests.draft_browser
playwright = browser_tests.playwright

SERIES = [
    "AQ-3800",
    "CF-I",
    "CF-II",
    "CF-III",
    "6.1 KF",
    "9 KF",
    "RXF",
    "Series 9",
    "Series 10",
    "Series 15",
    "Series 18",
]


def select_series(page, value):
    control = page.locator("[data-series-select]")
    if control.input_value() == value:
        return
    control.select_option(value)
    page.get_by_role("button", name="Change Series", exact=True).click()
    wait_saved(page)


def test_creation_series_list_exact_and_blank(draft_browser):
    page, _ = draft_browser
    page.goto(page.url.split("/ecr/")[0] + "/ecr/reports/new")
    page.locator('[name="cooling_tower_serial_no"]').fill("BROWSER-NEW-SERIES")
    page.get_by_role("button", name="Check Package").click()
    control = page.locator('[name="cooling_tower_series"]')
    assert control.input_value() == ""
    assert control.locator("option").evaluate_all("els => els.map(el => el.value)") == [
        "",
        *SERIES,
    ]


@pytest.mark.parametrize("series", ["AQ-3800", "CF-I", "Series 10", "CF-II"])
def test_conditional_visibility_no_defaults_reload(draft_browser, series):
    page, _ = draft_browser
    select_series(page, series)
    page.reload()
    assert page.locator("[data-series-select]").input_value() == series
    assert page.locator('[name="gearbox_series"]').is_visible() is (
        series not in ("AQ-3800", "CF-I")
    )
    assert page.locator('[name="drive_shaft_series"]').is_visible() is (
        series not in ("AQ-3800", "CF-I")
    )
    assert page.locator('[name="bearing_housing_type"]').is_visible() is (
        series == "AQ-3800"
    )
    assert page.locator('[name="belt_type"]').is_visible() is (series == "AQ-3800")
    controls = page.locator("[data-page2-field]")
    assert all(
        value == "" for value in controls.evaluate_all("els => els.map(el => el.value)")
    )
    hardware = page.locator('[name="general_tower_hardware"]')
    assert hardware.locator("option").evaluate_all(
        "els => els.map(el => el.value)"
    ) == ["", "HDG", "SS304", "SS316"]
    assert page.locator("text=Yes = leakage observed").is_visible()


def test_batch_autosave_and_save_now_switch_preserve(draft_browser, db_session):
    page, report = draft_browser
    page.locator('[name="gearbox_serial_no"]').fill("GEAR-preserved")
    page.locator('[name="drive_shaft_series"]').select_option("175")
    page.locator('[name="drive_shaft_class"]').select_option("CL III")
    page.locator('[name="drive_shaft_serial_no"]').fill("DS-preserved")
    page.locator('[name="drive_shaft_oal"]').fill("12.5")
    page.locator('[name="drive_shaft_oal_unit"]').select_option("inches")
    wait_saved(page)
    select_series(page, "AQ-3800")
    assert not page.locator('[name="drive_shaft_series"]').is_visible()
    entries = {
        "eliminator_type": "Browser eliminator",
        "fc_valve_diameter": "2.123456789123456789",
        "fc_valve_count": "2",
        "nozzle_type": "Browser nozzle",
        "nozzle_count_per_cell": "12",
        "nozzle_part_no": "Browser part",
        "bearing_housing_type": "Browser housing",
        "bearing_housing_serial_no": "BH-preserved",
        "belt_type": "Browser belt",
        "belt_section_length": "Section / length",
        "small_pulley_od": "0",
        "large_pulley_od": "12.5",
        "belts_used_count": "3",
        "oil_type": "Browser oil",
    }
    for name, value in entries.items():
        page.locator(f'[name="{name}"]').fill(value)
    for name, value in {
        "uniform_belt_tension": "No",
        "pulley_construction": "Without QD Bushing",
        "oil_level_checked": "Yes",
        "oil_seal_leakage": "Yes",
        "general_tower_hardware": "SS304",
    }.items():
        page.locator(f'[name="{name}"]').select_option(value)
        entries[name] = value
    page.locator("[data-save-now]").click()
    wait_saved(page)
    page.reload()
    for name, value in entries.items():
        assert page.locator(f'[name="{name}"]').input_value() == value
    select_series(page, "Series 10")
    assert page.locator('[name="gearbox_serial_no"]').input_value() == "GEAR-preserved"
    for field, value in {
        "drive_shaft_series": "175",
        "drive_shaft_class": "CL III",
        "drive_shaft_serial_no": "DS-preserved",
        "drive_shaft_oal": "12.5",
        "drive_shaft_oal_unit": "inches",
    }.items():
        assert page.locator(f'[name="{field}"]').input_value() == value
    select_series(page, "CF-I")
    page.reload()
    select_series(page, "AQ-3800")
    page.reload()
    assert (
        page.locator('[name="bearing_housing_serial_no"]').input_value()
        == "BH-preserved"
    )
    db_session.expire_all()
    assert report.page1.gearbox_serial_no == "GEAR-preserved"
    assert report.page1.drive_shaft_serial_no == "DS-preserved"
    assert report.page2.bearing_housing_serial_no == "BH-preserved"


@pytest.mark.parametrize(
    "name",
    ["fc_valve_diameter", "small_pulley_od", "large_pulley_od", "fc_valve_count"],
)
def test_numeric_guards_typing_paste_mobile(draft_browser, name):
    page, _ = draft_browser
    select_series(page, "AQ-3800")
    field = page.locator(f'[name="{name}"]')
    field.fill("5")
    field.press("Home")
    field.press("-")
    assert field.input_value() == "5"
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    for text in ["-29", "+2", "1e2", "1,5"]:
        page.evaluate("text => navigator.clipboard.writeText(text)", text)
        field.press("Control+a")
        field.press("Control+v")
        assert field.input_value() == "5"
    field.evaluate(
        "el => { el.value = '-29'; el.dispatchEvent(new InputEvent('input', {bubbles: true, data:'-29', inputType:'insertText'})); }"
    )
    assert field.input_value() == "5"
    expected = "12" if name == "fc_valve_count" else "12.5"
    field.fill(expected)
    wait_saved(page)
    page.reload()
    assert page.locator(f'[name="{name}"]').input_value() == expected


def test_stale_series_response_does_not_confirm_newer_batch_values(draft_browser):
    page, _ = draft_browser
    page.evaluate("""() => {
      const original = window.fetch; let calls = 0;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (++calls === 1) await new Promise(resolve => window.firstGate = resolve);
        else if (calls === 2) await new Promise(resolve => window.secondGate = resolve);
        return response;
      };
    }""")
    page.locator("[data-series-select]").select_option("AQ-3800")
    page.get_by_role("button", name="Change Series", exact=True).click()
    page.locator("[data-save-now]").click()
    page.wait_for_function("typeof window.firstGate === 'function'")
    page.locator('[name="bearing_housing_type"]').fill("Newest housing")
    page.evaluate("window.firstGate()")
    page.wait_for_function("typeof window.secondGate === 'function'")
    assert page.locator("[data-autosave-status]").inner_text() != "Saved"
    page.evaluate("window.secondGate()")
    wait_saved(page)
    page.reload()
    assert (
        page.locator('[name="bearing_housing_type"]').input_value() == "Newest housing"
    )


def test_stale_open_draft_cannot_change_newly_shared_package(draft_browser, db_session):
    page, report = draft_browser
    _create_report(
        db_session,
        report.supervisor,
        serial_no=report.tower.package.cooling_tower_serial_no,
        cell_no=2,
    )
    page.locator("[data-series-select]").select_option("AQ-3800")
    page.get_by_role("button", name="Change Series", exact=True).click()
    with page.expect_response("**/autosave") as result:
        page.locator("[data-save-now]").click()
    assert result.value.status == 409
    playwright.expect(page.locator("[data-autosave-status]")).to_contain_text(
        "read-only"
    )
    assert page.locator("[data-series-select]").input_value() == "Series 10"
    assert page.locator("[data-series-select]").is_disabled()
    page.reload()
    assert page.locator("[data-series-select]").count() == 0
    assert page.locator('[name="gearbox_series"]').is_visible()
    page.locator('[name="oil_type"]').fill("Shared package technical edit")
    wait_saved(page)


@pytest.mark.parametrize("width", [1280, 390])
def test_batch_form_alignment(draft_browser, tmp_path, width):
    page, _ = draft_browser
    select_series(page, "AQ-3800")
    page.set_viewport_size({"width": width, "height": 900})
    heights = page.locator(".form-control:visible").evaluate_all(
        "els => els.map(el => el.getBoundingClientRect().height)"
    )
    assert min(heights) >= 44 and max(heights) - min(heights) <= 1
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(tmp_path / f"page2-{width}.png"), full_page=True)
