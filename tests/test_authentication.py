"""Login, session lifecycle, logout, and password-change tests."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserSession
from app.auth.sessions import create_session, digest_session_token
from app.core.config import AppSettings
from app.core.time import utc_now
from app.users.models import UserStatus
from tests.conftest import csrf_from, login


def test_login_cookie_is_persistent_httponly_and_samesite_lax(client, user_factory) -> None:
    user = user_factory(employee_id="EMP-COOKIE")
    page = client.get("/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "csrf_token": csrf_from(page.text),
            "identifier": user.employee_id,
            "password": "Correct-Horse-123",
        },
    )

    cookie_header = response.headers["set-cookie"].lower()
    assert "ecr_test_session=" in cookie_header
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header
    assert "max-age=2592000" in cookie_header


def test_active_user_can_login_by_employee_id_and_mobile(client, user_factory) -> None:
    user = user_factory(employee_id="EMP-LOGIN", mobile_number="+919876541111")

    first_token = login(client, "emp-login")
    assert len(first_token) >= 40

    page = client.get("/dashboard")
    assert page.status_code == 200
    assert user.full_name in page.text

    logout_page = client.get("/dashboard")
    client.post("/auth/logout", data={"csrf_token": csrf_from(logout_page.text)})
    second_token = login(client, "+91 98765-41111")
    assert second_token != first_token


def test_wrong_password_pending_and_disabled_users_are_rejected(client, user_factory) -> None:
    active = user_factory(employee_id="EMP-ACTIVE")
    pending = user_factory(employee_id="EMP-PENDING", status=UserStatus.PENDING)
    disabled = user_factory(employee_id="EMP-DISABLED", status=UserStatus.DISABLED)

    for identifier, password in [
        (active.employee_id, "Wrong-Password-123"),
        (pending.employee_id, "Correct-Horse-123"),
        (disabled.employee_id, "Correct-Horse-123"),
    ]:
        page = client.get("/auth/login")
        response = client.post(
            "/auth/login",
            data={
                "csrf_token": csrf_from(page.text),
                "identifier": identifier,
                "password": password,
            },
        )
        assert response.status_code == 400
        assert "Invalid login credentials or account unavailable" in response.text


def test_raw_session_token_is_not_stored_and_session_persists(
    client,
    db_session: Session,
    user_factory,
) -> None:
    user = user_factory(employee_id="EMP-SESSION")
    raw_token = login(client, user.employee_id)
    stored = db_session.scalar(
        select(UserSession).where(UserSession.user_id == user.id).order_by(UserSession.id.desc())
    )

    assert stored is not None
    assert stored.token_digest == digest_session_token(raw_token)
    assert stored.token_digest != raw_token
    assert raw_token not in stored.token_digest
    assert client.get("/dashboard").status_code == 200
    assert client.get("/dashboard").status_code == 200


def test_logout_revokes_current_session(client, db_session: Session, user_factory) -> None:
    user = user_factory(employee_id="EMP-LOGOUT")
    raw_token = login(client, user.employee_id)
    page = client.get("/dashboard")
    response = client.post(
        "/auth/logout",
        data={"csrf_token": csrf_from(page.text)},
    )

    assert response.status_code == 303
    stored = db_session.scalar(
        select(UserSession).where(UserSession.token_digest == digest_session_token(raw_token))
    )
    db_session.refresh(stored)
    assert stored.revoked_at is not None
    assert client.get("/dashboard").status_code == 303


def test_revoked_expired_and_disabled_sessions_are_rejected(
    client,
    db_session: Session,
    user_factory,
    app_settings: AppSettings,
) -> None:
    for case in ("revoked", "expired", "disabled"):
        user = user_factory(employee_id=f"EMP-{case.upper()}")
        issued = create_session(db_session, user, app_settings)
        if case == "revoked":
            issued.record.revoked_at = utc_now()
        elif case == "expired":
            issued.record.expires_at = utc_now() - timedelta(seconds=1)
        else:
            user.status = UserStatus.DISABLED
        db_session.commit()

        client.cookies.set(app_settings.session_cookie_name, issued.raw_token)
        response = client.get("/dashboard")
        assert response.status_code == 303
        assert response.headers["location"] == "/auth/login"
        client.cookies.delete(app_settings.session_cookie_name)


def test_must_change_password_is_enforced_and_successful_change_rotates_session(
    client,
    db_session: Session,
    user_factory,
    password_manager,
) -> None:
    old_password = "Temporary-Password-001"
    new_password = "Permanent-Password-002"
    user = user_factory(
        employee_id="EMP-MUST-CHANGE",
        must_change_password=True,
        password=old_password,
    )
    old_token = login(client, user.employee_id, old_password)

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 303
    assert dashboard.headers["location"] == "/auth/change-password"

    form = client.get("/auth/change-password")
    response = client.post(
        "/auth/change-password",
        data={
            "csrf_token": csrf_from(form.text),
            "current_password": old_password,
            "new_password": new_password,
            "confirm_password": new_password,
        },
    )

    assert response.status_code == 303
    new_token = client.cookies.get("ecr_test_session")
    assert new_token and new_token != old_token
    db_session.refresh(user)
    assert user.must_change_password is False
    assert password_manager.verify(user.password_hash, new_password)
    assert not password_manager.verify(user.password_hash, old_password)

    old_session = db_session.scalar(
        select(UserSession).where(UserSession.token_digest == digest_session_token(old_token))
    )
    assert old_session.revoked_at is not None
    assert client.get("/dashboard").status_code == 200
