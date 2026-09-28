"""CSRF enforcement tests for state-changing browser requests."""

from tests.conftest import csrf_from


def _signup_payload(csrf_token: str) -> dict[str, str]:
    return {
        "csrf_token": csrf_token,
        "full_name": "CSRF User",
        "employee_id": "EMP-CSRF",
        "mobile_number": "+919876549999",
        "email": "",
        "password": "CSRF-Password-001",
        "confirm_password": "CSRF-Password-001",
    }


def test_state_changing_request_without_matching_csrf_is_rejected(client) -> None:
    response = client.post("/auth/signup", data=_signup_payload("invalid-token"))

    assert response.status_code == 403


def test_state_changing_request_with_valid_csrf_succeeds(client) -> None:
    page = client.get("/auth/signup")
    response = client.post(
        "/auth/signup",
        data=_signup_payload(csrf_from(page.text)),
    )

    assert response.status_code == 303
