"""Accessible password visibility controls on Phase 2 forms."""

from app.users.models import UserRole
from tests.conftest import login


def _assert_password_controls(page_text: str, field_names: tuple[str, ...]) -> None:
    assert 'src="http://testserver/static/password_visibility.js"' in page_text
    for field_name in field_names:
        assert f'id="{field_name}" type="password" name="{field_name}"' in page_text
        assert f'data-password-target="{field_name}"' in page_text
        assert f'aria-controls="{field_name}"' in page_text
        assert 'type="button"' in page_text
        assert 'aria-pressed="false"' in page_text


def test_public_password_forms_have_masked_accessible_visibility_controls(client) -> None:
    signup = client.get("/auth/signup")
    login_page = client.get("/auth/login")

    _assert_password_controls(signup.text, ("password", "confirm_password"))
    _assert_password_controls(login_page.text, ("password",))
    assert signup.text.count("data-password-toggle") == 2
    assert login_page.text.count("data-password-toggle") == 1


def test_authenticated_password_forms_reuse_visibility_controls(client, user_factory) -> None:
    supervisor = user_factory(employee_id="EMP-PASSWORD-CONTROLS")
    login(client, supervisor.employee_id)
    change_page = client.get("/auth/change-password")
    _assert_password_controls(
        change_page.text,
        ("current_password", "new_password", "confirm_password"),
    )
    assert change_page.text.count("data-password-toggle") == 3

    client.cookies.clear()
    admin = user_factory(role=UserRole.ADMIN, employee_id="ADMIN-PASSWORD-CONTROLS")
    target = user_factory(employee_id="EMP-RESET-CONTROLS")
    login(client, admin.employee_id)
    reset_page = client.get(f"/admin/users/{target.id}/reset-password")
    _assert_password_controls(
        reset_page.text,
        ("temporary_password", "confirm_password"),
    )
    assert reset_page.text.count("data-password-toggle") == 2


def test_password_visibility_script_toggles_only_the_input_type(client) -> None:
    script = client.get("/static/password_visibility.js")

    assert script.status_code == 200
    assert 'input.type = willShow ? "text" : "password"' in script.text
    assert ".value" not in script.text
