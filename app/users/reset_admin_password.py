"""Interactive server-side recovery command for an existing Admin."""

import getpass
from collections.abc import Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.passwords import PasswordManager, PasswordPolicyError
from app.core.config import get_app_settings
from app.db.session import get_session_factory
from app.users.services import UserActionError, recover_admin_password


def run_interactive(
    session_factory: sessionmaker[Session],
    *,
    input_fn: Callable[[str], str] = input,
    password_fn: Callable[[str], str] = getpass.getpass,
    password_manager: PasswordManager | None = None,
) -> int:
    """Prompt securely and reset one existing Admin password."""
    print("Reset existing Admin password")
    employee_id = input_fn("Admin Employee ID: ").strip()
    new_password = password_fn("New password: ")
    confirmation = password_fn("Confirm new password: ")

    if new_password != confirmation:
        print("Admin password reset failed: passwords do not match.")
        return 1

    manager = password_manager or PasswordManager(get_app_settings())
    try:
        with session_factory() as db:
            recover_admin_password(db, employee_id, new_password, manager)
            db.commit()
    except SQLAlchemyError:
        print("Admin password reset failed due to a database error.")
        return 1
    except (PasswordPolicyError, UserActionError, ValueError) as exc:
        print(f"Admin password reset failed: {exc}")
        return 1

    print("Admin password reset successfully. Existing sessions were revoked.")
    return 0


def main() -> int:
    """CLI entry point using the configured MySQL session factory."""
    return run_interactive(get_session_factory())


if __name__ == "__main__":
    raise SystemExit(main())
