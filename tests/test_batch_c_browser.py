"""Batch C through Chromium, real HTTP and transaction-isolated MySQL."""

from decimal import Decimal

import pytest

from app.users.models import UserRole
from tests import test_draft_save_browser as browser_tests
from tests.test_page1_browser import wait_saved

draft_browser = browser_tests.draft_browser
playwright = browser_tests.playwright


def wait_error(page):
    page.wait_for_function(
        'document.querySelector("[data-autosave-status]").dataset.state === "error"'
    )


def assert_reading_instruments(page, *, editable):
    """Check rendered shape, side association and usable bounds, not CSS text."""
    for end in ("de", "nde"):
        diagram = page.locator(f'[data-reading-diagram="{end}"]')
        svg = diagram.locator("svg")
        circle = svg.locator("[data-reading-outline]")
        assert circle.count() == 1 and svg.locator("ellipse").count() == 0
        assert circle.evaluate("el => getComputedStyle(el).fill") == "none"
        assert circle.evaluate("el => getComputedStyle(el).stroke") != "none"
        assert svg.locator("line").count() == 2
        shape = circle.bounding_box()
        assert abs(shape["width"] - shape["height"]) <= 1
        cx, cy = shape["x"] + shape["width"] / 2, shape["y"] + shape["height"] / 2
        stroke_tolerance = shape["width"] * 2 / 108 + 1
        for line in svg.locator("line").all():
            bounds = line.bounding_box()
            if line.get_attribute("data-reading-horizontal") is not None:
                assert abs(bounds["x"] + bounds["width"] / 2 - cx) <= 1
                assert bounds["height"] <= stroke_tolerance
            else:
                assert abs(bounds["y"] + bounds["height"] / 2 - cy) <= 1
                assert bounds["width"] <= stroke_tolerance
            assert line.evaluate("el => getComputedStyle(el).stroke") != "none"
            assert "rotate" not in (line.get_attribute("transform") or "")
        for side in ("top", "right", "bottom", "left"):
            selector = (
                f'[name="{end}_{side}"]'
                if editable
                else f'[data-reading-value="{end}_{side}"]'
            )
            control = diagram.locator(selector)
            bounds = control.bounding_box()
            assert 64 <= bounds["width"] <= 88
            if editable:
                assert 29 <= bounds["height"] <= 31
                buttons = diagram.locator(
                    f'[data-reading-target="{end}_{side}"]'
                )
                for button in buttons.all():
                    assert abs(button.bounding_box()["height"] - bounds["height"]) <= 1
                typography = control.evaluate(
                    "el => { const style = getComputedStyle(el); "
                    "return [parseFloat(style.fontSize), parseFloat(style.lineHeight)]; }"
                )
                assert typography == pytest.approx([14, 16.8], abs=0.1)
                assert (
                    control.get_attribute("aria-label")
                    == f"{end.upper()} {side.capitalize()}"
                )
            if side == "top":
                assert bounds["y"] + bounds["height"] < shape["y"]
                assert abs(bounds["x"] + bounds["width"] / 2 - cx) <= 1
            elif side == "bottom":
                assert bounds["y"] > shape["y"] + shape["height"]
                assert abs(bounds["x"] + bounds["width"] / 2 - cx) <= 1
            elif side == "left":
                assert bounds["x"] + bounds["width"] < shape["x"]
                assert abs(bounds["y"] + bounds["height"] / 2 - cy) <= 1
            else:
                assert bounds["x"] > shape["x"] + shape["width"]
                assert abs(bounds["y"] + bounds["height"] / 2 - cy) <= 1
        card = diagram.bounding_box()
        assert card["height"] < 430 and card["width"] <= 420
        if page.viewport_size["width"] >= 1000:
            assert 180 <= shape["width"] <= 220
        else:
            assert 80 <= shape["width"] <= 220
        if editable:
            for button in diagram.locator("button").all():
                bounds = button.bounding_box()
                assert 26 <= bounds["width"] <= 32 and 26 <= bounds["height"] <= 32
                assert (
                    card["x"] <= bounds["x"]
                    and bounds["x"] + bounds["width"] <= card["x"] + card["width"]
                )
    diagrams = page.locator(".reading-diagrams")
    assert diagrams.evaluate("el => el.scrollWidth <= el.clientWidth")
    bounds = diagrams.bounding_box()
    assert (
        bounds["x"] >= 0
        and bounds["x"] + bounds["width"] <= page.viewport_size["width"]
    )


@pytest.mark.parametrize("width", [320, 390, 1280])
def test_defaults_graphics_and_control_sizing(draft_browser, tmp_path, width):
    page, _ = draft_browser
    page.set_viewport_size({"width": width, "height": 850})
    assert (
        page.locator('[data-torque-category="FAN_HARDWARE"] [data-torque-row]').count()
        == 1
    )
    assert page.locator('[data-torque-category="TOWER"] [data-torque-row]').count() == 0
    assert (
        page.locator(
            '[data-torque-category="MECH_HOLD_DOWN"] [data-torque-row]'
        ).count()
        == 0
    )
    assert all(
        v == ""
        for v in page.locator("[data-batch_c-field]").evaluate_all(
            "els => els.map(e => e.value)"
        )
    )
    assert page.locator('[data-torque-field="torque_unit"]').input_value() == ""
    assert page.locator('[name="de_nde_unit"] option').evaluate_all(
        "els => els.map(e => e.value)"
    ) == ["", "inches", "mm"]
    for name in ("radial_tir", "axial_tir"):
        field = page.locator(f'[name="{name}"]')
        assert field.get_attribute("type") == "text"
        assert field.get_attribute("inputmode") == "decimal"
        assert (
            field.locator("xpath=ancestor::details")
            .locator("button, [role=slider], input[type=number]")
            .count()
            == 0
        )
    heights = [
        page.locator(selector).bounding_box()["height"]
        for selector in (
            '[name="radial_tir"]',
            '[name="de_nde_unit"]',
            '[data-torque-field="diameter"]',
        )
    ]
    assert max(heights) - min(heights) <= 1 and min(heights) >= 44
    assert_reading_instruments(page, editable=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator(".reading-diagrams").scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path / f"batch-c-{width}.png"))


def test_dynamic_torque_all_categories_order_remove_and_reload(
    draft_browser, db_session
):
    page, report = draft_browser
    for category in ("FAN_HARDWARE", "TOWER", "MECH_HOLD_DOWN"):
        section = page.locator(f'[data-torque-category="{category}"]')
        for index in range(6 if category == "FAN_HARDWARE" else 1):
            if category != "FAN_HARDWARE" or index:
                section.locator("[data-add-torque]").click()
            row = section.locator("[data-torque-row]").last
            row.locator('[data-torque-field="diameter"]').fill(f"M{10 + index}")
            row.locator('[data-torque-field="torque"]').fill(
                "-65.125" if index else "0"
            )
            row.locator("select").select_option("Nm" if index else "ft-lbs")
    page.locator("[data-save-now]").click()
    wait_saved(page)
    page.reload()
    assert page.locator("[data-torque-row]").count() == 8
    assert page.locator('[data-torque-field="torque"]').first.input_value() == "0"
    page.locator('[data-torque-category="FAN_HARDWARE"] [data-remove-torque]').nth(
        1
    ).click()
    wait_saved(page)
    page.locator("[data-save-now]").click()
    wait_saved(page)
    page.reload()
    assert page.locator(
        '[data-torque-category="FAN_HARDWARE"] [data-torque-field="diameter"]'
    ).evaluate_all("els => els.map(e => e.value)") == [
        "M10",
        "M12",
        "M13",
        "M14",
        "M15",
    ]
    db_session.expire_all()
    assert len(report.fastener_rows) == 7


def test_manual_alignment_signed_validation_and_reload(draft_browser):
    page, _ = draft_browser
    page.locator('[name="radial_tir"]').fill("-0.001")
    page.locator('[name="axial_tir"]').fill("+0.005")
    wait_saved(page)
    page.reload()
    assert page.locator('[name="radial_tir"]').input_value() == "-0.001"
    assert page.locator('[name="axial_tir"]').input_value() == "0.005"
    for invalid in ("1.001", "0.0005"):
        page.locator('[name="radial_tir"]').fill(invalid)
        page.locator("[data-save-now]").click()
        wait_error(page)
        assert page.locator("[data-autosave-status]").inner_text() != "Saved"
        page.reload()
        assert page.locator('[name="radial_tir"]').input_value() == "-0.001"


@pytest.mark.parametrize("unit", ["inches", "mm"])
def test_all_readings_steps_bounds_zero_and_switches(draft_browser, unit):
    page, _ = draft_browser
    page.set_viewport_size({"width": 390, "height": 850})
    page.locator('[name="de_nde_unit"]').select_option(unit)
    entries = {
        "de_top": "-1.00",
        "de_right": "-0.01",
        "de_bottom": "0.00",
        "de_left": "0.01",
        "nde_top": "1.00",
        "nde_right": "0.25",
        "nde_bottom": "0.00",
        "nde_left": "-0.75",
    }
    for name, value in entries.items():
        page.locator(f'[name="{name}"]').fill(value)
    page.locator('[name="vibration_limit_switch"]').select_option("No")
    page.locator('[name="oil_level_switch"]').select_option("Yes")
    wait_saved(page)
    for name, original_value in entries.items():
        direction = "-1" if name == "nde_top" else "1"
        button = page.locator(
            f'[data-reading-target="{name}"][data-reading-step="{direction}"]'
        )
        button.focus()
        page.keyboard.press("Enter")
        wait_saved(page)
        assert (
            Decimal(page.locator(f'[name="{name}"]').input_value())
            == Decimal(original_value) + Decimal(direction) / 100
        )
        page.locator(
            f'[data-reading-target="{name}"][data-reading-step="{int(direction) * -1}"]'
        ).click()
        wait_saved(page)
    # At either bound the outward button must not change the value.
    page.locator('[data-reading-target="de_top"][data-reading-step="-1"]').click()
    page.locator('[data-reading-target="nde_top"][data-reading-step="1"]').click()
    page.reload()
    for name, value in entries.items():
        assert Decimal(page.locator(f'[name="{name}"]').input_value()) == Decimal(value)
    assert page.locator('[name="de_bottom"]').input_value() == "0.00"
    assert page.locator('[name="vibration_limit_switch"]').input_value() == "No"
    assert page.locator('[name="oil_level_switch"]').input_value() == "Yes"
    assert page.locator('[name="de_nde_unit"]').input_value() == unit


def test_batch_c_older_response_never_acknowledges_newer_rows(draft_browser):
    page, _ = draft_browser
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (!window.held) { window.held = true; await new Promise(r => window.releaseSave = r); }
        return response;
      };
    }""")
    page.locator('[data-torque-field="diameter"]').fill("M10")
    page.locator("[data-save-now]").click()
    page.wait_for_function("typeof window.releaseSave === 'function'")
    page.locator('[data-torque-field="diameter"]').fill("M12")
    page.locator('[name="de_top"]').fill("0")
    page.evaluate("window.releaseSave()")
    assert page.locator("[data-autosave-status]").inner_text() != "Saved"
    wait_saved(page)
    page.reload()
    assert page.locator('[data-torque-field="diameter"]').input_value() == "M12"
    assert page.locator('[name="de_top"]').input_value() == "0.00"


def test_batch_c_retry_and_http_failure_no_false_saved(draft_browser):
    page, _ = draft_browser
    page.route(
        "**/autosave",
        lambda route: route.fulfill(status=503, json={"ok": False}),
        times=1,
    )
    page.locator('[data-torque-field="diameter"]').fill("M16")
    page.locator('[name="nde_left"]').fill("0")
    page.locator("[data-save-now]").click()
    wait_error(page)
    wait_saved(page)
    page.reload()
    assert page.locator("[data-torque-row]").count() == 1
    assert page.locator('[data-torque-field="diameter"]').input_value() == "M16"
    assert page.locator('[name="nde_left"]').input_value() == "0.00"


@pytest.mark.parametrize("role", [UserRole.BRANCH_ADMIN, UserRole.SUPERADMIN])
def test_admin_readonly_batch_c_browser(draft_browser, user_factory, role):
    page, report = draft_browser
    page.locator('[name="de_top"]').fill("0")
    page.locator('[name="radial_tir"]').fill("-0.001")
    page.locator('[name="vibration_limit_switch"]').select_option("No")
    page.locator('[name="oil_level_switch"]').select_option("Yes")
    page.locator('[data-torque-field="diameter"]').fill("M16")
    wait_saved(page)
    admin = user_factory(role=role, branch=report.branch)
    base = page.url.split("/ecr/")[0]
    page.context.clear_cookies()
    page.goto(base + "/auth/login")
    page.locator('[name="identifier"]').fill(admin.employee_id)
    page.locator('[name="password"]').fill("Correct-Horse-123")
    page.locator('button[type="submit"]').click()
    page.wait_for_url("**/dashboard")
    page.goto(base + f"/reports/{report.id}")
    assert page.locator('[data-reading-value="de_top"]').inner_text() == "0.00"
    assert page.get_by_text("M16", exact=True).is_visible()
    assert page.get_by_text("-0.001", exact=True).is_visible()
    assert page.locator("[data-autosave-form], [data-batch_c-field]").count() == 0
    assert_reading_instruments(page, editable=False)


@pytest.mark.parametrize("width", [320, 390, 1280])
def test_supervisor_readonly_instrument_layout(draft_browser, width, tmp_path):
    page, _report = draft_browser
    page.set_viewport_size({"width": width, "height": 850})
    page.locator('[name="de_top"]').fill("0")
    page.locator('[name="nde_left"]').fill("-0.25")
    wait_saved(page)
    page.goto(page.url.removesuffix("/edit"))
    assert_reading_instruments(page, editable=False)
    assert page.locator('[data-reading-value="de_top"]').inner_text() == "0.00"
    assert page.locator('[data-reading-value="nde_left"]').inner_text() == "-0.25"
    assert page.locator('[data-reading-value="de_left"]').inner_text() == "—"
    page.locator(".reading-diagrams").screenshot(
        path=str(tmp_path / f"readonly-{width}.png")
    )


def crosshair_state(instrument):
    return instrument.evaluate("""el => {
      const horizontal = el.querySelector('[data-reading-horizontal]');
      const vertical = el.querySelector('[data-reading-vertical]');
      const matrix = line => {
        const m = line.transform.baseVal.consolidate().matrix;
        return [m.a, m.b, m.c, m.d, m.e, m.f];
      };
      const coordinates = line => ['x1', 'y1', 'x2', 'y2'].map(name => Number(line.getAttribute(name)));
      return {horizontal: matrix(horizontal), vertical: matrix(vertical),
        hCoordinates: coordinates(horizontal), vCoordinates: coordinates(vertical),
        clip: horizontal.parentElement.getAttribute('clip-path'),
        circle: ['cx', 'cy', 'r'].map(name => Number(el.querySelector('[data-reading-outline]').getAttribute(name)))};
    }""")


def assert_b1(instrument, x, y):
    state = crosshair_state(instrument)
    assert state["horizontal"] == [1, 0, 0, 1, 0, y]
    assert state["vertical"] == [1, 0, 0, 1, x, 0]
    assert state["hCoordinates"] == [6, 60, 114, 60]
    assert state["vCoordinates"] == [60, 6, 60, 114]
    assert state["circle"] == [60, 60, 54]
    assert (
        state["clip"]
        == f"url(#reading-clip-{instrument.get_attribute('data-reading-diagram')})"
    )
    assert abs(x) <= 18 and abs(y) <= 18
    # Fixed line coordinates plus a stationary circular clip keep the whole
    # translated segments contained, and their intersection remains inside it.
    assert x * x + y * y < 53 * 53


@pytest.mark.parametrize("end", ["de", "nde"])
@pytest.mark.parametrize("width", [390, 1280])
def test_b1_movement_original_values_save_reload_and_readonly(
    draft_browser, db_session, end, width, tmp_path
):
    page, report = draft_browser
    page.set_viewport_size({"width": width, "height": 850})
    instrument = page.locator(f'[data-reading-diagram="{end}"]')
    assert_b1(instrument, 0, 0)
    assert all(
        instrument.locator(f'[name="{end}_{side}"]').input_value() == ""
        for side in ("top", "right", "bottom", "left")
    )
    page.locator('[name="de_nde_unit"]').select_option("mm")
    for side, value in {"top": "0.80", "bottom": "-0.20"}.items():
        instrument.locator(f'[name="{end}_{side}"]').fill(value)
    assert_b1(instrument, 0, 9)
    for side, value in {"right": "0.60", "left": "-0.40"}.items():
        instrument.locator(f'[name="{end}_{side}"]').fill(value)
    assert_b1(instrument, 9, 9)
    # Equal readings center the corresponding diameter, even when nonzero.
    for side in ("top", "bottom"):
        instrument.locator(f'[name="{end}_{side}"]').fill("0.40")
    assert_b1(instrument, 9, 0)
    for side in ("right", "left"):
        instrument.locator(f'[name="{end}_{side}"]').fill("-0.50")
    assert_b1(instrument, 0, 0)
    values = {"top": "1.00", "bottom": "-1.00", "right": "-1.00", "left": "1.00"}
    for side, value in values.items():
        instrument.locator(f'[name="{end}_{side}"]').fill(value)
    assert_b1(instrument, -18, 18)
    page.locator("[data-save-now]").click()
    wait_saved(page)
    db_session.expire_all()
    for side, value in values.items():
        assert getattr(report.page2, f"{end}_{side}") == Decimal(value)
    page.reload()
    instrument = page.locator(f'[data-reading-diagram="{end}"]')
    assert_b1(instrument, -18, 18)
    assert instrument.locator("[data-reading-unit]").inner_text() == "mm"
    for side, value in values.items():
        assert instrument.locator(f'[name="{end}_{side}"]').input_value() == value
    assert_reading_instruments(page, editable=True)
    page.locator(".reading-diagrams").screenshot(
        path=str(tmp_path / f"b1-edit-{end}-{width}.png")
    )
    page.goto(page.url.removesuffix("/edit"))
    instrument = page.locator(f'[data-reading-diagram="{end}"]')
    assert_b1(instrument, -18, 18)
    assert_reading_instruments(page, editable=False)
    assert instrument.locator("input, button").count() == 0
    assert instrument.locator("[data-reading-unit]").inner_text() == "mm"
    for side, value in values.items():
        assert (
            instrument.locator(f'[data-reading-value="{end}_{side}"]').inner_text()
            == value
        )
    page.locator(".reading-diagrams").screenshot(
        path=str(tmp_path / f"b1-readonly-{end}-{width}.png")
    )


@pytest.mark.parametrize("invalid", ["0.001", "0.14000000000000001"])
def test_visual_formatting_never_rounds_invalid_reading_into_validity(
    draft_browser, invalid
):
    page, _ = draft_browser
    field = page.locator('[name="de_top"]')
    field.fill("0.14")
    wait_saved(page)
    field.fill(invalid)
    page.locator("[data-save-now]").click()
    wait_error(page)
    assert field.input_value() == invalid
    page.reload()
    assert field.input_value() == "0.14"
