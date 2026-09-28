"""Admin authorization, account actions, recovery, and session tests."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserSession
from app.auth.sessions import digest_session_token
from app.users.models import PasswordResetRequest, UserRole, UserStatus
from tests.conftest import csrf_from, login


def _admin_csrf(client, admin_employee_id: str) -> str:
    login(client, admin_employee_id)
    page = client.get("/admin/users")
    assert page.status_code == 200
    return csrf_from(page.text)


def test_non_admin_cannot_access_admin_routes(client, user_factory) -> None:
    supervisor = user_factory(employee_id="EMP-NON-ADMIN")
    login(client, supervisor.employee_id)

    assert client.get("/admin/users").status_code == 403


def test_admin_can_activate_disable_and_reenable_user(
    client,
    db_session: Session,
    user_factory,
) -> None:
    admin = user_factory(role=UserRole.ADMIN, employee_id="ADMIN-STATUS")
    target = user_factory(status=UserStatus.PENDING, employee_id="EMP-STATUS")
    csrf_token = _admin_csrf(client, admin.employee_id)
    detail_page = client.get(f"/admin/users/{target.id}")
    assert target.password_hash not in detail_page.text
    assert "password_hash" not in detail_page.text

    without_csrf = client.post(f"/admin/users/{target.id}/activate", data={})
    assert without_csrf.status_code == 422

    activated = client.post(
        f"/admin/users/{target.id}/activate",
        data={"csrf_token": csrf_token},
    )
    assert activated.status_code == 303
    activation_notice = client.get(activated.headers["location"])
    assert f"{target.full_name} ({target.employee_id}) has been activated." in activation_notice.text
    db_session.refresh(target)
    assert target.status is UserStatus.ACTIVE

    csrf_token = csrf_from(client.get(f"/admin/users/{target.id}").text)
    disabled = client.post(
        f"/admin/users/{target.id}/disable",
        data={"csrf_token": csrf_token},
    )
    assert disabled.status_code == 303
    disabled_notice = client.get(disabled.headers["location"])
    assert f"{target.full_name} ({target.employee_id}) has been disabled." in disabled_notice.text
    db_session.refresh(target)
    assert target.status is UserStatus.DISABLED

    csrf_token = csrf_from(client.get(f"/admin/users/{target.id}").text)
    enabled = client.post(
        f"/admin/users/{target.id}/enable",
        data={"csrf_token": csrf_token},
    )
    assert enabled.status_code == 303
    enabled_notice = client.get(enabled.headers["location"])
    assert f"{target.full_name} ({target.employee_id}) has been re-enabled." in enabled_notice.text
    db_session.refresh(target)
    assert target.status is UserStatus.ACTIVE


def test_disabled_user_cannot_authenticate_or_use_existing_session(
    client,
    db_session: Session,
    user_factory,
) -> None:
    target = user_factory(employee_id="EMP-DISABLE-SESSION")
    target_token = login(client, target.employee_id)
    client.cookies.clear()

    admin = user_factory(role=UserRole.ADMIN, employee_id="ADMIN-DISABLE")
    csrf_token = _admin_csrf(client, admin.employee_id)
    response = client.post(
        f"/admin/users/{target.id}/disable",
        data={"csrf_token": csrf_token},
    )
    assert response.status_code == 303

    target_session = db_session.scalar(
        select(UserSession).where(UserSession.token_digest == digest_session_token(target_token))
    )
    assert target_session.revoked_at is not None
    client.cookies.clear()
    page = client.get("/auth/login")
    rejected = client.post(
        "/auth/login",
        data={
            "csrf_token": csrf_from(page.text),
            "identifier": target.employee_id,
            "password": "Correct-Horse-123",
        },
    )
    assert rejected.status_code == 400


def test_admin_password_reset_sets_flag_revokes_sessions_and_resolves_request(
    client,
    db_session: Session,
    user_factory,
    password_manager,
) -> None:
    target = user_factory(employee_id="EMP-RESET")
    target_token = login(client, target.employee_id)
    reset_request = PasswordResetRequest(user_id=target.id)
    db_session.add(reset_request)
    db_session.commit()
    client.cookies.clear()

    admin = user_factory(role=UserRole.ADMIN, employee_id="ADMIN-RESET")
    _admin_csrf(client, admin.employee_id)
    form = client.get(f"/admin/users/{target.id}/reset-password")
    temporary_password = "Temporary-Reset-999"
    response = client.post(
        f"/admin/users/{target.id}/reset-password",
        data={
            "csrf_token": csrf_from(form.text),
            "temporary_password": temporary_password,
            "confirm_password": temporary_password,
        },
    )

    assert response.status_code == 303
    reset_notice = client.get(response.headers["location"])
    assert f"{target.full_name} ({target.employee_id}) password has been reset." in reset_notice.text
    db_session.refresh(target)
    db_session.refresh(reset_request)
    assert target.must_change_password is True
    assert password_manager.verify(target.password_hash, temporary_password)
    assert reset_request.resolved_at is not None
    assert reset_request.resolved_by_user_id == admin.id

    target_session = db_session.scalar(
        select(UserSession).where(UserSession.token_digest == digest_session_token(target_token))
    )
    assert target_session.revoked_at is not None

    client.cookies.clear()
    login_response_token = login(client, target.employee_id, temporary_password)
    assert login_response_token
    assert client.get("/dashboard").headers["location"] == "/auth/change-password"


def test_admin_force_logout_revokes_all_target_sessions(
    client,
    db_session: Session,
    user_factory,
) -> None:
    target = user_factory(employee_id="EMP-FORCE-LOGOUT")
    target.full_name = "Affected <User>"
    db_session.commit()
    first_token = login(client, target.employee_id)
    client.cookies.clear()
    second_token = login(client, target.employee_id)
    client.cookies.clear()

    admin = user_factory(role=UserRole.ADMIN, employee_id="ADMIN-FORCE-LOGOUT")
    csrf_token = _admin_csrf(client, admin.employee_id)
    response = client.post(
        f"/admin/users/{target.id}/force-logout",
        data={"csrf_token": csrf_token},
    )

    assert response.status_code == 303
    confirmation = client.get(response.headers["location"])
    assert "Affected &lt;User&gt; (EMP-FORCE-LOGOUT) has been forced to log out." in (
        confirmation.text
    )
    assert "Affected <User>" not in confirmation.text

    untrusted_notice = client.get(
        f"/admin/users/{target.id}?notice=not-an-action&message=False+success"
    )
    assert "False success" not in untrusted_notice.text
    sessions = db_session.scalars(
        select(UserSession).where(
            UserSession.token_digest.in_(
                [digest_session_token(first_token), digest_session_token(second_token)]
            )
        )
    ).all()
    assert len(sessions) == 2
    assert all(item.revoked_at is not None for item in sessions)


def test_forgot_password_response_is_generic_and_existing_request_is_visible(
    client,
    db_session: Session,
    user_factory,
) -> None:
    target = user_factory(employee_id="EMP-FORGOT")
    for identifier in (target.employee_id, "EMP-DOES-NOT-EXIST"):
        page = client.get("/auth/forgot-password")
        response = client.post(
            "/auth/forgot-password",
            data={"csrf_token": csrf_from(page.text), "identifier": identifier},
        )
        assert response.status_code == 200
        assert "If an account matches those details" in response.text

    requests = db_session.scalars(
        select(PasswordResetRequest).where(PasswordResetRequest.user_id == target.id)
    ).all()
    assert len(requests) == 1
    assert requests[0].user_id == target.id
