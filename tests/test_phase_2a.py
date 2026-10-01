"""Branch hierarchy, scoped administrator, and Phase 2A security tests."""

import re

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.branches.models import Branch
from app.branches.schemas import BranchCreateInput
from app.branches.services import create_branch
from app.auth.models import UserSession
from app.auth.sessions import digest_session_token
from app.users.models import PasswordResetRequest, User, UserRole, UserStatus
from tests.conftest import csrf_from, login


EXPECTED_BRANCHES = {
    "HO-KOL": "HO-Kolkata",
    "DELHI": "Delhi",
    "BARODA": "Baroda",
    "MUMBAI": "Mumbai",
    "BANGALORE": "Bangalore",
    "CHENNAI": "Chennai",
    "HYDERABAD": "Hyderabad",
}


def _management_csrf(client, employee_id: str) -> str:
    client.cookies.clear()
    login(client, employee_id)
    page = client.get("/admin/users")
    assert page.status_code == 200
    return csrf_from(page.text)


def test_phase_2a_roles_branches_and_indexes_are_present(db_session: Session) -> None:
    branches = list(db_session.scalars(select(Branch).order_by(Branch.code)))
    assert EXPECTED_BRANCHES.items() <= {
        branch.code: branch.name for branch in branches
    }.items()
    assert all(branch.is_active for branch in branches if branch.code in EXPECTED_BRANCHES)
    assert {role.value for role in UserRole} == {
        "SUPERADMIN",
        "BRANCH_ADMIN",
        "SUPERVISOR",
    }
    assert "ADMIN" not in {role.value for role in UserRole}
    indexes = {item["name"] for item in inspect(db_session.bind).get_indexes("users")}
    assert {"ix_users_branch_id", "ix_users_branch_role_status"} <= indexes


def test_duplicate_normalized_branch_identity_is_rejected(
    db_session: Session,
) -> None:
    create_branch(db_session, BranchCreateInput(code="TEST-A", name="Test Branch Alpha"))
    db_session.commit()
    with pytest.raises(ValueError):
        create_branch(
            db_session,
            BranchCreateInput(code=" test-a ", name="Another Name"),
        )
    with pytest.raises(ValueError):
        create_branch(
            db_session,
            BranchCreateInput(code="TEST-B", name="  test branch alpha  "),
        )


def test_database_rejects_legacy_admin_role(db_session: Session, user_factory) -> None:
    user = user_factory(employee_id="ROLE-CHECK")
    # MySQL reports CHECK violations through the driver as OperationalError,
    # while other supported test dialects may classify them as IntegrityError.
    with pytest.raises((IntegrityError, OperationalError)):
        db_session.execute(
            text("UPDATE users SET role = 'ADMIN' WHERE id = :user_id"),
            {"user_id": user.id},
        )
        db_session.commit()
    db_session.rollback()


def test_signup_lists_active_branches_and_rejects_invalid_or_inactive_branch(
    client,
    db_session: Session,
    branch_factory,
) -> None:
    inactive = branch_factory(code="INACTIVE", name="Inactive Branch", is_active=False)
    page = client.get("/auth/signup")
    for display_name in EXPECTED_BRANCHES.values():
        assert display_name in page.text
    assert inactive.name not in page.text

    base = {
        "csrf_token": csrf_from(page.text),
        "full_name": "Crafted Signup",
        "employee_id": "EMP-CRAFTED-BRANCH",
        "mobile_number": "+919800001234",
        "email": "",
        "password": "Signup-Password-001",
        "confirm_password": "Signup-Password-001",
        "role": "SUPERADMIN",
    }
    for branch_id in (999999999, inactive.id):
        response = client.post("/auth/signup", data={**base, "branch_id": branch_id})
        assert response.status_code == 400
        assert "valid active branch" in response.text
    assert db_session.scalar(select(User).where(User.employee_id == "EMP-CRAFTED-BRANCH")) is None


def test_superadmin_creates_branch_and_branch_admin_with_forced_change(
    client,
    db_session: Session,
    user_factory,
    branch_factory,
    password_manager,
) -> None:
    superadmin = user_factory(role=UserRole.SUPERADMIN, employee_id="SUPER-CREATE")
    delhi = branch_factory(code="DELHI")
    _management_csrf(client, superadmin.employee_id)

    branch_page = client.get("/admin/branches")
    for display_name in EXPECTED_BRANCHES.values():
        assert display_name in branch_page.text
    created_branch = client.post(
        "/admin/branches",
        data={
            "csrf_token": csrf_from(branch_page.text),
            "code": " test-new ",
            "name": " Test Branch ",
        },
    )
    assert created_branch.status_code == 303
    branch = db_session.scalar(select(Branch).where(Branch.code == "TEST-NEW"))
    assert branch is not None and branch.name == "Test Branch" and branch.is_active

    form = client.get("/admin/users/create-branch-admin")
    temporary_password = "Temporary-Branch-Admin-001"
    response = client.post(
        "/admin/users/create-branch-admin",
        data={
            "csrf_token": csrf_from(form.text),
            "full_name": "Delhi Administrator",
            "employee_id": "ADMIN-DELHI-CREATED",
            "mobile_number": "+919800001235",
            "email": "delhi.admin@example.com",
            "branch_id": delhi.id,
            "temporary_password": temporary_password,
            "confirm_password": temporary_password,
            "role": "SUPERADMIN",
        },
    )
    assert response.status_code == 303
    admin = db_session.scalar(select(User).where(User.employee_id == "ADMIN-DELHI-CREATED"))
    assert admin is not None
    assert admin.role is UserRole.BRANCH_ADMIN
    assert admin.branch_id == delhi.id
    assert admin.status is UserStatus.ACTIVE
    assert admin.must_change_password is True
    assert password_manager.verify(admin.password_hash, temporary_password)

    client.cookies.clear()
    signup_page = client.get("/auth/signup")
    assert "Test Branch" in signup_page.text
    login(client, admin.employee_id, temporary_password)
    forced = client.get("/dashboard")
    assert forced.status_code == 303
    assert forced.headers["location"] == "/auth/change-password"


def test_branch_admin_scope_and_cross_branch_actions_are_non_leaking(
    client,
    user_factory,
    branch_factory,
) -> None:
    delhi = branch_factory(code="DELHI")
    mumbai = branch_factory(code="MUMBAI")
    delhi_admin = user_factory(
        role=UserRole.BRANCH_ADMIN,
        employee_id="ADMIN-DELHI-SCOPE",
        branch=delhi,
    )
    mumbai_admin = user_factory(
        role=UserRole.BRANCH_ADMIN,
        employee_id="ADMIN-MUMBAI-SCOPE",
        branch=mumbai,
    )
    delhi_supervisor = user_factory(employee_id="SUP-DELHI-SCOPE", branch=delhi)
    mumbai_supervisor = user_factory(employee_id="SUP-MUMBAI-SCOPE", branch=mumbai)
    csrf_token = _management_csrf(client, delhi_admin.employee_id)
    listing = client.get("/admin/users")
    assert "Delhi — Supervisor Management" in listing.text
    assert delhi_supervisor.employee_id in listing.text
    assert mumbai_supervisor.employee_id not in listing.text
    assert delhi_admin.employee_id not in listing.text

    assert client.get(f"/admin/users/{mumbai_supervisor.id}").status_code == 404
    assert client.get("/admin/users/create-branch-admin").status_code == 403
    assert client.get("/admin/branches").status_code == 403
    endpoints = [
        ("activate", {}),
        ("disable", {}),
        ("enable", {}),
        (
            "reset-password",
            {
                "temporary_password": "Cross-Branch-Reset-001",
                "confirm_password": "Cross-Branch-Reset-001",
            },
        ),
        ("force-logout", {}),
    ]
    for action, extra in endpoints:
        response = client.post(
            f"/admin/users/{mumbai_supervisor.id}/{action}",
            data={"csrf_token": csrf_token, **extra},
        )
        assert response.status_code == 404

    branch_change = client.post(
        f"/admin/users/{delhi_supervisor.id}/branch",
        data={"csrf_token": csrf_token, "branch_id": mumbai.id},
    )
    assert branch_change.status_code == 403

    _management_csrf(client, mumbai_admin.employee_id)
    mumbai_listing = client.get("/admin/users")
    assert mumbai_supervisor.employee_id in mumbai_listing.text
    assert delhi_supervisor.employee_id not in mumbai_listing.text


def test_superadmin_global_visibility_filtering_reassignment_and_self_protection(
    client,
    db_session: Session,
    user_factory,
    branch_factory,
) -> None:
    ho = branch_factory()
    delhi = branch_factory(code="DELHI")
    mumbai = branch_factory(code="MUMBAI")
    inactive_filter = branch_factory(
        code="INACTIVE-FILTER",
        name="Inactive Filter Branch",
        is_active=False,
    )
    superadmin = user_factory(
        role=UserRole.SUPERADMIN,
        employee_id="SUPER-GLOBAL",
        branch=ho,
    )
    delhi_supervisor = user_factory(employee_id="SUP-GLOBAL-DELHI", branch=delhi)
    mumbai_supervisor = user_factory(employee_id="SUP-GLOBAL-MUMBAI", branch=mumbai)
    csrf_token = _management_csrf(client, superadmin.employee_id)
    listing = client.get("/admin/users")
    assert delhi_supervisor.employee_id in listing.text
    assert mumbai_supervisor.employee_id in listing.text
    assert inactive_filter.name in listing.text

    filtered = client.get(f"/admin/users?branch_id={delhi.id}&role=SUPERVISOR")
    assert delhi_supervisor.employee_id in filtered.text
    assert mumbai_supervisor.employee_id not in filtered.text

    detail = client.get(f"/admin/users/{delhi_supervisor.id}")
    moved = client.post(
        f"/admin/users/{delhi_supervisor.id}/branch",
        data={"csrf_token": csrf_from(detail.text), "branch_id": mumbai.id},
    )
    assert moved.status_code == 303
    db_session.refresh(delhi_supervisor)
    assert delhi_supervisor.branch_id == mumbai.id

    own_detail = client.get(f"/admin/users/{superadmin.id}")
    blocked = client.post(
        f"/admin/users/{superadmin.id}/disable",
        data={"csrf_token": csrf_from(own_detail.text)},
    )
    assert blocked.status_code == 400
    db_session.refresh(superadmin)
    assert superadmin.status is UserStatus.ACTIVE

    inactive = branch_factory(code="INACTIVE-MOVE", name="Inactive Move", is_active=False)
    invalid_move = client.post(
        f"/admin/users/{mumbai_supervisor.id}/branch",
        data={"csrf_token": csrf_token, "branch_id": inactive.id},
    )
    assert invalid_move.status_code == 400


def test_phase_2a_mutations_require_csrf(
    client,
    user_factory,
    branch_factory,
) -> None:
    superadmin = user_factory(role=UserRole.SUPERADMIN, employee_id="SUPER-CSRF-2A")
    supervisor = user_factory(employee_id="SUP-CSRF-2A")
    destination = branch_factory(code="CSRF-BRANCH", name="CSRF Branch")
    _management_csrf(client, superadmin.employee_id)

    assert client.post(
        "/admin/branches",
        data={"csrf_token": "invalid", "code": "NOPE", "name": "Nope"},
    ).status_code == 403
    assert client.post(
        f"/admin/users/{supervisor.id}/branch",
        data={"csrf_token": "invalid", "branch_id": destination.id},
    ).status_code == 403
    assert client.post(
        "/admin/users/create-branch-admin",
        data={
            "csrf_token": "invalid",
            "full_name": "No CSRF Admin",
            "employee_id": "NO-CSRF-ADMIN",
            "mobile_number": "+919800009999",
            "branch_id": destination.id,
            "temporary_password": "No-CSRF-Password-001",
            "confirm_password": "No-CSRF-Password-001",
        },
    ).status_code == 403


def test_password_reset_requests_follow_administrator_scope(
    client,
    db_session: Session,
    user_factory,
    branch_factory,
) -> None:
    delhi = branch_factory(code="DELHI")
    mumbai = branch_factory(code="MUMBAI")
    delhi_admin = user_factory(role=UserRole.BRANCH_ADMIN, branch=delhi)
    other_admin = user_factory(role=UserRole.BRANCH_ADMIN, branch=mumbai)
    delhi_supervisor = user_factory(employee_id="RESET-DELHI", branch=delhi)
    mumbai_supervisor = user_factory(employee_id="RESET-MUMBAI", branch=mumbai)
    superadmin = user_factory(role=UserRole.SUPERADMIN, employee_id="SUPER-RESET")
    db_session.add_all(
        [
            PasswordResetRequest(user_id=delhi_supervisor.id),
            PasswordResetRequest(user_id=mumbai_supervisor.id),
            PasswordResetRequest(user_id=other_admin.id),
        ]
    )
    db_session.commit()

    _management_csrf(client, delhi_admin.employee_id)
    scoped = client.get("/admin/users")
    assert delhi_supervisor.employee_id in scoped.text and "Requested" in scoped.text
    assert mumbai_supervisor.employee_id not in scoped.text
    assert other_admin.employee_id not in scoped.text

    _management_csrf(client, superadmin.employee_id)
    global_page = client.get("/admin/users")
    assert delhi_supervisor.employee_id in global_page.text
    assert mumbai_supervisor.employee_id in global_page.text
    assert other_admin.employee_id in global_page.text


def test_superadmin_manages_branch_admin_turnover_without_deleting_history(
    client,
    db_session: Session,
    user_factory,
    branch_factory,
) -> None:
    delhi = branch_factory(code="DELHI")
    superadmin = user_factory(
        role=UserRole.SUPERADMIN,
        employee_id="SUPER-TURNOVER",
    )
    departing_admin = user_factory(
        role=UserRole.BRANCH_ADMIN,
        employee_id="ADMIN-DELHI-DEPARTING",
        branch=delhi,
    )
    departing_token = login(client, departing_admin.employee_id)
    client.cookies.clear()

    csrf_token = _management_csrf(client, superadmin.employee_id)
    disabled = client.post(
        f"/admin/users/{departing_admin.id}/disable",
        data={"csrf_token": csrf_token},
    )
    assert disabled.status_code == 303
    db_session.refresh(departing_admin)
    assert departing_admin.status is UserStatus.DISABLED
    assert db_session.get(User, departing_admin.id) is not None
    stored_session = db_session.scalar(
        select(UserSession).where(
            UserSession.token_digest == digest_session_token(departing_token)
        )
    )
    assert stored_session is not None and stored_session.revoked_at is not None
    disabled_detail = client.get(disabled.headers["location"])
    assert "Account disabled" in disabled_detail.text
    assert ">DISABLED<" in disabled_detail.text

    client.cookies.clear()
    login_page = client.get("/auth/login")
    rejected = client.post(
        "/auth/login",
        data={
            "csrf_token": csrf_from(login_page.text),
            "identifier": departing_admin.employee_id,
            "password": "Correct-Horse-123",
        },
    )
    assert rejected.status_code == 400

    client.cookies.clear()
    _management_csrf(client, superadmin.employee_id)
    detail = client.get(f"/admin/users/{departing_admin.id}")
    enabled = client.post(
        f"/admin/users/{departing_admin.id}/enable",
        data={"csrf_token": csrf_from(detail.text)},
    )
    assert enabled.status_code == 303
    db_session.refresh(departing_admin)
    assert departing_admin.status is UserStatus.ACTIVE

    form = client.get("/admin/users/create-branch-admin")
    new_password = "Replacement-Admin-Password-001"
    created = client.post(
        "/admin/users/create-branch-admin",
        data={
            "csrf_token": csrf_from(form.text),
            "full_name": "Delhi Replacement Admin",
            "employee_id": "ADMIN-DELHI-REPLACEMENT",
            "mobile_number": "+919800007777",
            "email": "replacement.delhi@example.com",
            "branch_id": delhi.id,
            "temporary_password": new_password,
            "confirm_password": new_password,
        },
    )
    assert created.status_code == 303
    branch_admins = list(
        db_session.scalars(
            select(User).where(
                User.branch_id == delhi.id,
                User.role == UserRole.BRANCH_ADMIN,
            )
        )
    )
    assert {user.employee_id for user in branch_admins} >= {
        departing_admin.employee_id,
        "ADMIN-DELHI-REPLACEMENT",
    }


def test_branch_admin_cannot_manage_administrators(
    client,
    user_factory,
    branch_factory,
) -> None:
    delhi = branch_factory(code="DELHI")
    actor = user_factory(
        role=UserRole.BRANCH_ADMIN,
        employee_id="ADMIN-SCOPE-ACTOR",
        branch=delhi,
    )
    peer = user_factory(
        role=UserRole.BRANCH_ADMIN,
        employee_id="ADMIN-SCOPE-PEER",
        branch=delhi,
    )
    superadmin = user_factory(
        role=UserRole.SUPERADMIN,
        employee_id="SUPER-SCOPE-TARGET",
    )
    csrf_token = _management_csrf(client, actor.employee_id)

    for target in (peer, superadmin):
        assert client.get(f"/admin/users/{target.id}").status_code == 404
        assert client.post(
            f"/admin/users/{target.id}/disable",
            data={"csrf_token": csrf_token},
        ).status_code == 404
        assert client.post(
            f"/admin/users/{target.id}/enable",
            data={"csrf_token": csrf_token},
        ).status_code == 404


def test_supervisor_disable_preserves_record_and_no_delete_route_exists(
    client,
    db_session: Session,
    user_factory,
) -> None:
    superadmin = user_factory(role=UserRole.SUPERADMIN, employee_id="SUPER-PRESERVE")
    supervisor = user_factory(employee_id="SUP-PRESERVE")
    csrf_token = _management_csrf(client, superadmin.employee_id)
    response = client.post(
        f"/admin/users/{supervisor.id}/disable",
        data={"csrf_token": csrf_token},
    )
    assert response.status_code == 303
    preserved = db_session.get(User, supervisor.id)
    assert preserved is not None
    assert preserved.status is UserStatus.DISABLED

    for route in client.app.routes:
        methods = getattr(route, "methods", set()) or set()
        path = getattr(route, "path", "").lower()
        assert "DELETE" not in methods
        assert "/delete" not in path


def test_navigation_uses_icons_and_marks_the_current_page(
    client,
    user_factory,
) -> None:
    superadmin = user_factory(role=UserRole.SUPERADMIN, employee_id="SUPER-NAV")
    login(client, superadmin.employee_id)

    pages = (
        ("/dashboard", "/dashboard"),
        ("/admin/users", "/admin/users"),
        ("/admin/branches", "/admin/branches"),
        ("/admin/users/create-branch-admin", "/admin/users/create-branch-admin"),
        ("/auth/change-password", "/auth/change-password"),
    )
    for page_path, active_href in pages:
        response = client.get(page_path)
        assert response.status_code == 200
        assert 'class="primary-nav"' in response.text
        assert response.text.count('aria-current="page"') == 1
        active_pattern = rf'<a class="nav-item is-active" href="{re.escape(active_href)}" aria-current="page">'
        assert re.search(active_pattern, response.text)
        assert '<svg class="nav-icon"' in response.text
        assert 'class="nav-item nav-logout"' in response.text

    stylesheet = client.get("/static/app.css")
    assert ".nav-item:hover" in stylesheet.text
    assert ".nav-item:focus-visible" in stylesheet.text
    assert "overflow-x: auto" in stylesheet.text
