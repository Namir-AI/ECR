"""User model, signup, uniqueness, and password-storage tests."""

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.passwords import PasswordManager
from app.users.models import User, UserRole, UserStatus
from app.users.schemas import SignupInput
from app.users.services import create_supervisor
from tests.conftest import csrf_from


def test_user_creation_hashes_password_and_has_no_plaintext_column(
    db_session: Session,
    password_manager: PasswordManager,
) -> None:
    plaintext = "Never-Store-This-123"
    data = SignupInput(
        full_name="Site Supervisor",
        employee_id="emp-1001",
        mobile_number="+91 98765-41001",
        email="Supervisor@example.com",
        password=plaintext,
        confirm_password=plaintext,
    )

    user = create_supervisor(db_session, data, password_manager)
    db_session.commit()

    assert user.password_hash != plaintext
    assert user.password_hash.startswith("$argon2id$")
    assert password_manager.verify(user.password_hash, plaintext)
    assert user.employee_id == "EMP-1001"
    assert user.mobile_number == "+919876541001"
    assert user.email == "supervisor@example.com"
    assert user.role is UserRole.SUPERVISOR
    assert user.status is UserStatus.PENDING
    assert "password" not in {
        column["name"] for column in inspect(db_session.bind).get_columns("users")
    }


@pytest.mark.parametrize("field", ["employee_id", "mobile_number"])
def test_database_rejects_duplicate_required_identifiers(
    db_session: Session,
    password_manager: PasswordManager,
    field: str,
) -> None:
    common = {
        "employee_id": "EMP-DUPLICATE",
        "mobile_number": "+919999990001",
    }
    first = User(
        full_name="First User",
        email=None,
        password_hash=password_manager.hash("Strong-Password-001"),
        role=UserRole.SUPERVISOR,
        status=UserStatus.PENDING,
        must_change_password=False,
        **common,
    )
    db_session.add(first)
    db_session.commit()

    second_values = {
        "employee_id": "EMP-OTHER",
        "mobile_number": "+919999990002",
    }
    second_values[field] = common[field]
    db_session.add(
        User(
            full_name="Second User",
            email=None,
            password_hash=password_manager.hash("Strong-Password-002"),
            role=UserRole.SUPERVISOR,
            status=UserStatus.PENDING,
            must_change_password=False,
            **second_values,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_signup_page_creates_pending_supervisor(client, db_session: Session) -> None:
    page = client.get("/auth/signup")
    csrf_token = csrf_from(page.text)
    response = client.post(
        "/auth/signup",
        data={
            "csrf_token": csrf_token,
            "full_name": "New Supervisor",
            "employee_id": "emp-signup-1",
            "mobile_number": "+919876540001",
            "email": "new.supervisor@example.com",
            "password": "Signup-Password-001",
            "confirm_password": "Signup-Password-001",
        },
    )

    assert response.status_code == 303
    user = db_session.scalar(select(User).where(User.employee_id == "EMP-SIGNUP-1"))
    assert user is not None
    assert user.status is UserStatus.PENDING
    assert user.role is UserRole.SUPERVISOR


@pytest.mark.parametrize(
    ("employee_id", "mobile_number", "expected"),
    [
        ("emp-existing", "+919876540099", "Employee ID is already registered"),
        ("emp-new", "+919876540010", "Mobile Number is already registered"),
    ],
)
def test_signup_rejects_duplicate_employee_or_mobile(
    client,
    user_factory,
    employee_id: str,
    mobile_number: str,
    expected: str,
) -> None:
    user_factory(employee_id="EMP-EXISTING", mobile_number="+919876540010")
    page = client.get("/auth/signup")
    response = client.post(
        "/auth/signup",
        data={
            "csrf_token": csrf_from(page.text),
            "full_name": "Duplicate User",
            "employee_id": employee_id,
            "mobile_number": mobile_number,
            "email": "",
            "password": "Signup-Password-001",
            "confirm_password": "Signup-Password-001",
        },
    )

    assert response.status_code == 400
    assert expected in response.text


def test_signup_rejects_password_confirmation_mismatch(client) -> None:
    page = client.get("/auth/signup")
    response = client.post(
        "/auth/signup",
        data={
            "csrf_token": csrf_from(page.text),
            "full_name": "Mismatch User",
            "employee_id": "EMP-MISMATCH",
            "mobile_number": "+919876540020",
            "email": "",
            "password": "Signup-Password-001",
            "confirm_password": "Signup-Password-002",
        },
    )

    assert response.status_code == 400
    assert "Passwords do not match" in response.text
