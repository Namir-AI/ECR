"""Interactive initial Admin bootstrap command."""

import getpass
from collections.abc import Callable

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.passwords import PasswordManager, PasswordPolicyError
from app.core.config import get_app_settings
from app.db.session import get_session_factory
from app.users.services import (
    AdminBootstrapInput,
    UserActionError,
    UserConflictError,
    create_initial_admin,
)


def run_interactive(
    session_factory: sessionmaker[Session],
    *,
    input_fn: Callable[[str], str] = input,
    password_fn: Callable[[str], str] = getpass.getpass,
) -> int:
    """Prompt securely and create the first Admin account."""
    print("Create initial ECR Admin")
    full_name = input_fn("Full Name: ").strip()
    employee_id = input_fn("Employee ID: ").strip()
    mobile_number = input_fn("Mobile Number: ").strip()
    email_input = input_fn("Email (optional): ").strip()
    password = password_fn("Password: ")
    confirmation = password_fn("Confirm Password: ")

    if password != confirmation:
        print("Admin creation failed: passwords do not match.")
        return 1

    try:
        email = (
            str(TypeAdapter(EmailStr).validate_python(email_input)).lower()
            if email_input
            else None
        )
        with session_factory() as db:
            create_initial_admin(
                db,
                AdminBootstrapInput(
                    full_name=full_name,
                    employee_id=employee_id,
                    mobile_number=mobile_number,
                    email=email,
                    password=password,
                ),
                PasswordManager(get_app_settings()),
            )
            db.commit()
    except (
        PasswordPolicyError,
        UserActionError,
        UserConflictError,
        ValidationError,
        ValueError,
        SQLAlchemyError,
    ) as exc:
        print(f"Admin creation failed: {exc}")
        return 1

    print("Admin account created successfully.")
    return 0


def main() -> int:
    """CLI entry point using the configured MySQL session factory."""
    return run_interactive(get_session_factory())


if __name__ == "__main__":
    raise SystemExit(main())
