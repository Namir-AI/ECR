"""Real browser Page 1 partial Draft/autosave/reload and race verification."""

import pytest

from tests import test_draft_save_browser as browser_tests
from tests.test_page1 import FINAL_OPTIONAL, FINAL_REQUIRED

# Reuse the accepted real-HTTP/MySQL fixture without a second browser harness.
draft_browser = browser_tests.draft_browser
playwright = browser_tests.playwright


def wait_saved(page):
    page.wait_for_function(
        'document.querySelector("[data-autosave-status]").dataset.state === "saved"'
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"motor_mounting": "Foot"},
        {"motor_hp": "0.000123456789123456"},
        {"fan_serial_no": "Autosaved Fan / 01"},
        {"drive_shaft_oal": "125.0625", "drive_shaft_oal_unit": "cm"},
    ],
)
def test_representative_technical_autosaves_reload(draft_browser, fields):
    page, _report = draft_browser
    for name, value in fields.items():
        control = page.locator(f'[name="{name}"]')
        if control.evaluate("element => element.tagName") == "SELECT":
            control.select_option(value)
        else:
            control.fill(value)
    wait_saved(page)
    page.reload()
    for name, value in fields.items():
        assert page.locator(f'[name="{name}"]').input_value() == value


def test_blade_rows_autosave_add_and_remove(draft_browser):
    page, _report = draft_browser
    for serial in ["Auto blade 1", "Auto blade 2"]:
        page.locator("[data-add-blade]").click()
        page.locator('[name="blade_serials"]').last.fill(serial)
        wait_saved(page)
    page.reload()
    assert page.locator('[name="blade_serials"]').count() == 2
    page.locator("[data-remove-blade]").first.click()
    wait_saved(page)
    page.reload()
    assert page.locator('[name="blade_serials"]').count() == 1
    assert page.locator('[name="blade_serials"]').input_value() == "Auto blade 2"


def test_page1_browser_autosave_and_manual_save_reload(draft_browser):
    page, _report = draft_browser
    assert page.locator('[name="motor_hp"]').input_value() == ""
    for name in [
        "motor_mounting",
        "motor_speed",
        "fan_hardware",
        "fan_hub_cover",
        "drive_shaft_oal_unit",
        "fill_material",
    ]:
        assert page.locator(f'[name="{name}"]').input_value() == ""
    page.locator('[name="motor_make"]').fill("Browser motor")
    wait_saved(page)
    page.reload()
    assert page.locator('[name="motor_make"]').input_value() == "Browser motor"

    page.locator('[name="motor_mounting"]').select_option("Flange")
    page.locator('[name="motor_hp"]').fill("12.345678901234567891")
    page.locator('[name="fan_serial_no"]').fill("Browser Fan-01")
    page.locator('[name="fan_hub_cover"]').select_option("No")
    page.locator('[name="drive_shaft_oal"]').fill("0012.5000")
    page.locator('[name="drive_shaft_oal_unit"]').select_option("inches")
    page.locator("[data-add-blade]").click()
    page.locator('[name="blade_serials"]').fill("Browser blade")
    page.locator("[data-save-now]").click()
    wait_saved(page)
    page.reload()
    for name, value in {
        "motor_mounting": "Flange",
        "motor_hp": "12.345678901234567891",
        "fan_serial_no": "Browser Fan-01",
        "fan_hub_cover": "No",
        "drive_shaft_oal": "12.5",
        "drive_shaft_oal_unit": "inches",
        "blade_serials": "Browser blade",
    }.items():
        assert page.locator(f'[name="{name}"]').input_value() == value
    page.locator("[data-remove-blade]").click()
    wait_saved(page)
    page.reload()
    assert page.locator('[name="blade_serials"]').count() == 0
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_technical_stale_response_does_not_acknowledge_new_edit(draft_browser):
    page, _report = draft_browser
    page.evaluate("""() => {
      const fetchOriginal = window.fetch;
      let calls = 0;
      window.fetch = async (...args) => {
        const response = await fetchOriginal(...args);
        if (++calls === 1) await new Promise(resolve => window.firstGate = resolve);
        else if (calls === 2) await new Promise(resolve => window.secondGate = resolve);
        return response;
      };
    }""")
    page.locator('[name="motor_make"]').fill("First motor")
    page.locator("[data-save-now]").click()
    page.wait_for_function("typeof window.firstGate === 'function'")
    page.locator('[name="motor_make"]').fill("Newest motor")
    page.evaluate("window.firstGate()")
    page.wait_for_function("typeof window.secondGate === 'function'")
    assert page.locator("[data-autosave-status]").inner_text() != "Saved"
    page.evaluate("window.secondGate()")
    wait_saved(page)
    page.reload()
    assert page.locator('[name="motor_make"]').input_value() == "Newest motor"


def test_technical_validation_failure_preserves_committed_data(draft_browser):
    page, _report = draft_browser
    page.locator('[name="motor_make"]').fill("Committed motor")
    page.locator("[data-save-now]").click()
    wait_saved(page)
    page.locator('[name="drive_shaft_oal"]').fill("20")
    page.locator("[data-save-now]").click()
    playwright.expect(page.locator("[data-autosave-status]")).to_contain_text(
        "OAL requires a unit"
    )
    assert page.locator("[data-autosave-status]").inner_text() != "Saved"
    page.locator('[name="drive_shaft_oal_unit"]').select_option("cm")
    wait_saved(page)
    page.reload()
    assert page.locator('[name="motor_make"]').input_value() == "Committed motor"
    assert page.locator('[name="drive_shaft_oal"]').input_value() == "20"


def test_technical_failed_http_never_shows_saved(draft_browser):
    page, _report = draft_browser
    page.route(
        "**/autosave",
        lambda route: route.fulfill(
            status=503, json={"ok": True, "message": "not committed"}
        ),
        times=1,
    )
    page.locator('[name="motor_make"]').fill("Retried motor")
    page.locator("[data-save-now]").click()
    playwright.expect(page.locator("[data-autosave-status]")).to_have_text(
        "Unable to save — retrying"
    )
    wait_saved(page)
    page.reload()
    assert page.locator('[name="motor_make"]').input_value() == "Retried motor"


def test_dates_only_acknowledgement_cannot_confirm_technical_values(draft_browser):
    page, _report = draft_browser
    page.route(
        "**/autosave",
        lambda route: route.fulfill(
            status=200,
            json={
                "ok": True,
                "values": {
                    "erection_start_date": None,
                    "erection_completion_date": "2026-09-30",
                },
            },
        ),
    )
    page.locator('[name="motor_make"]').fill("Unconfirmed motor")
    page.locator("[data-save-now]").click()
    playwright.expect(page.locator("[data-autosave-status]")).to_contain_text(
        "server did not confirm"
    )
    assert page.locator("[data-autosave-status]").inner_text() != "Saved"


def test_corrected_requiredness_and_blank_optional_draft_save(draft_browser):
    page, _report = draft_browser
    for field in FINAL_OPTIONAL - {"blade_serials"}:
        assert "*" not in page.locator(f'label[for="{field}"]').inner_text()
        assert page.locator(f'[name="{field}"]').input_value() == ""
    for field in FINAL_REQUIRED:
        assert "*" in page.locator(f'label[for="{field}"]').inner_text()
    assert "*" not in page.locator(".blade-serial-editor h3").inner_text()
    page.locator('[name="motor_make"]').fill("Optional fields left blank")
    page.locator("[data-save-now]").click()
    wait_saved(page)
    page.reload()
    for field in FINAL_OPTIONAL - {"blade_serials"}:
        assert page.locator(f'[name="{field}"]').input_value() == ""


def test_motor_current_zero_and_rejected_values_in_browser(draft_browser, db_session):
    from decimal import Decimal

    page, report = draft_browser
    status = page.locator("[data-autosave-status]")
    page.locator('[name="motor_full_load_current"]').fill("12.5")
    page.locator('[name="motor_current_drawn"]').fill("12.5")
    page.locator("[data-save-now]").click()
    wait_saved(page)
    page.locator('[name="motor_current_drawn"]').fill("0")
    # Wait for actual autosave, without clicking Save now.
    wait_saved(page)
    page.reload()
    assert page.locator('[name="motor_current_drawn"]').input_value() == "0"
    assert page.locator('[name="motor_full_load_current"]').input_value() == "12.5"
    db_session.expire_all()
    assert report.page1.motor_current_drawn == Decimal(0)

    # Crafted DOM bypass: keep server rejection covered despite the new UI guard.
    page.locator('[name="motor_current_drawn"]').evaluate(
        "field => { field.removeAttribute('min'); field.value = '-29'; }"
    )
    page.locator("[data-save-now]").click()
    playwright.expect(status).to_contain_text("Current Drawn must be 0 Amps or greater")
    assert status.inner_text() != "Saved"
    db_session.expire_all()
    assert report.page1.motor_current_drawn == Decimal(0)
    page.once("dialog", lambda dialog: dialog.accept())
    page.reload()
    assert page.locator('[name="motor_current_drawn"]').input_value() == "0"

    for invalid in ["0", "-5"]:
        page.locator('[name="motor_full_load_current"]').evaluate(
            "(field, value) => { field.removeAttribute('min'); field.value = value; }",
            invalid,
        )
        page.locator("[data-save-now]").click()
        playwright.expect(status).to_contain_text(
            "Full Load Current must be greater than 0 Amps"
        )
        assert status.inner_text() != "Saved"
        db_session.expire_all()
        assert report.page1.motor_full_load_current == Decimal("12.5")
    page.locator('[name="motor_full_load_current"]').fill("12.5")
    page.locator("[data-save-now]").click()
    wait_saved(page)
    page.reload()
    assert page.locator('[name="motor_full_load_current"]').input_value() == "12.5"
    assert page.locator('[name="motor_current_drawn"]').input_value() == "0"


def test_optional_oal_pair_validation_in_browser(draft_browser, db_session):
    page, report = draft_browser
    status = page.locator("[data-autosave-status]")
    page.locator("[data-save-now]").click()
    wait_saved(page)  # Both blank is valid, including blank Mounting/Fill Type.
    page.locator('[name="drive_shaft_oal"]').fill("125.5")
    page.locator("[data-save-now]").click()
    playwright.expect(status).to_contain_text("OAL requires a unit")
    assert status.inner_text() != "Saved"
    page.locator('[name="drive_shaft_oal_unit"]').select_option("inches")
    wait_saved(page)
    page.reload()
    assert page.locator('[name="drive_shaft_oal"]').input_value() == "125.5"
    assert page.locator('[name="drive_shaft_oal_unit"]').input_value() == "inches"
    page.locator('[name="drive_shaft_oal"]').fill("")
    page.locator("[data-save-now]").click()
    playwright.expect(status).to_contain_text("OAL Unit requires an OAL value")
    assert status.inner_text() != "Saved"
    db_session.expire_all()
    assert str(report.page1.drive_shaft_oal).startswith("125.5")
    assert report.page1.drive_shaft_oal_unit == "inches"
    page.locator('[name="drive_shaft_oal_unit"]').select_option("")
    wait_saved(page)
    page.reload()
    assert page.locator('[name="drive_shaft_oal"]').input_value() == ""
    assert page.locator('[name="drive_shaft_oal_unit"]').input_value() == ""


@pytest.mark.parametrize(
    "name", ["motor_hp", "motor_full_load_current", "motor_current_drawn"]
)
def test_plain_nonnegative_decimal_typing_paste_mobile_and_delete(draft_browser, name):
    from decimal import Decimal

    page, _report = draft_browser
    control = page.locator(f'[name="{name}"]')
    assert control.get_attribute("inputmode") == "decimal"
    minimum = Decimal(control.get_attribute("min"))
    assert minimum == (
        Decimal("0.000000000000000001")
        if name == "motor_full_load_current"
        else Decimal(0)
    )
    assert control.input_value() == ""
    control.press_sequentially("12.5")
    assert control.input_value() == "12.5"
    for forbidden in ["-", "+", "e", "E"]:
        control.press(forbidden)
        assert control.input_value() == "12.5"

    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    for invalid in ["-29", "+12", "1e2", "1E2", "12,5"]:
        page.evaluate("text => navigator.clipboard.writeText(text)", invalid)
        control.select_text()
        control.press("Control+V")
        assert control.input_value() == "12.5"  # Rejected wholesale, not made positive.
    page.evaluate("() => navigator.clipboard.writeText('18.75')")
    control.select_text()
    control.press("Control+V")
    playwright.expect(control).to_have_value("18.75")

    # Non-keyboard/mobile beforeinput and an uncancellable input fallback.
    assert (
        control.evaluate(
            "field => field.dispatchEvent(new InputEvent('beforeinput', {data: '-10', inputType: 'insertText', bubbles: true, cancelable: true}))"
        )
        is False
    )
    control.evaluate(
        "field => { field.value = '-10'; field.dispatchEvent(new InputEvent('input', {data: '-10', inputType: 'insertReplacementText', bubbles: true})); }"
    )
    assert control.input_value() == "18.75"
    control.select_text()
    control.press("Backspace")
    assert control.input_value() == ""
    control.press_sequentially(".5")
    assert Decimal(control.input_value()) == Decimal("0.5")
    control.fill(format(minimum, "f"))
    assert control.input_value() == format(minimum, "f")
    control.press("ArrowDown")
    assert Decimal(control.input_value()) >= minimum


@pytest.mark.parametrize(
    "name", ["motor_hp", "motor_full_load_current", "motor_current_drawn"]
)
def test_numeric_guard_retains_save_reload_and_zero_rules(draft_browser, name):
    page, _report = draft_browser
    control = page.locator(f'[name="{name}"]')
    control.fill("12.5")
    page.locator("[data-save-now]").click()
    wait_saved(page)
    page.reload()
    assert control.input_value() == "12.5"
    control.fill("0")
    page.locator("[data-save-now]").click()
    if name == "motor_full_load_current":
        playwright.expect(page.locator("[data-autosave-status]")).to_contain_text(
            "Unable to save"
        )
        assert page.locator("[data-autosave-status]").inner_text() != "Saved"
        page.once("dialog", lambda dialog: dialog.accept())
    else:
        wait_saved(page)
    page.reload()
    assert control.input_value() == (
        "12.5" if name == "motor_full_load_current" else "0"
    )


def test_drive_shaft_series_dropdown_without_default_and_reload(draft_browser):
    page, _report = draft_browser
    control = page.locator('[name="drive_shaft_series"]')
    assert control.evaluate("element => element.tagName") == "SELECT"
    assert control.locator("option").evaluate_all(
        "options => options.map(option => option.value)"
    ) == ["", "6Q", "175"]
    assert control.input_value() == ""
    for value in ["6Q", "175", ""]:
        control.select_option(value)
        page.locator("[data-save-now]").click()
        wait_saved(page)
        page.reload()
        assert control.input_value() == value


@pytest.mark.parametrize("width", [1280, 390])
def test_shared_control_height_and_label_alignment(draft_browser, tmp_path, width):
    page, _report = draft_browser
    page.set_viewport_size({"width": width, "height": 900})
    controls = page.locator(".form-control").evaluate_all(
        "elements => elements.map(element => ({name: element.name, height: element.getBoundingClientRect().height, y: element.getBoundingClientRect().y}))"
    )
    assert (
        controls
        and max(c["height"] for c in controls) - min(c["height"] for c in controls) <= 1
    )
    assert min(c["height"] for c in controls) >= 44
    by_name = {control["name"]: control for control in controls}
    pairs = [
        ("motor_hp", "motor_frame"),
        ("motor_speed", "motor_rpm"),
        ("motor_full_load_current", "motor_current_drawn"),
        ("fan_pitch_angle", "fan_hub_cover"),
        ("fan_cylinder_height", "fan_cylinder_material"),
        ("blade_tip_clearance", "blade_tip_track_variation"),
        ("erection_start_date", "erection_completion_date"),
        ("drive_shaft_series", "drive_shaft_class"),
        ("drive_shaft_oal", "drive_shaft_oal_unit"),
        ("gearbox_series", "gearbox_ratio"),
        ("gearbox_serial_no", "gearbox_model_no"),
        ("fill_type", "fill_material"),
    ]
    for left, right in pairs:
        assert abs(by_name[left]["height"] - by_name[right]["height"]) <= 1
        if width == 1280:
            assert abs(by_name[left]["y"] - by_name[right]["y"]) <= 1
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(tmp_path / f"page1-{width}.png"), full_page=True)
