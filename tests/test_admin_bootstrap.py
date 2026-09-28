"""Interactive initial-Admin command tests."""

from collections.abc import Iterator
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.users.create_admin import run_interactive
from app.users.models import User, UserRole, UserStatus


def test_admin_bootstrap_creates_active_admin_without_printing_password(
    db_session: Session,
    capsys,
) -> None:
    db_session.execute(
        update(User).where(User.role == UserRole.ADMIN).values(role=UserRole.SUPERVISOR)
    )
    db_session.flush()
    connection = db_session.connection()
    factory = sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    suffix = uuid4().hex[:10].upper()
    employee_id = f"ADMIN-{suffix}"
    mobile_number = f"+91{int(uuid4().hex[:10], 16) % 10**10:010d}"
    email = f"admin-{suffix.lower()}@example.com"
    password = "Bootstrap-Secret-001"

    def input_values() -> Iterator[str]:
        yield "Initial Admin"
        yield employee_id
        yield mobile_number
        yield email

    values = input_values()
    result = run_interactive(
        factory,
        input_fn=lambda _prompt: next(values),
        password_fn=lambda _prompt: password,
    )
    output = capsys.readouterr().out

    assert result == 0, output
    assert password not in output
    admin = db_session.scalar(select(User).where(User.employee_id == employee_id))
    assert admin is not None
    assert admin.role is UserRole.ADMIN
    assert admin.status is UserStatus.ACTIVE
    assert admin.password_hash != password

    duplicate_values = iter(
        ["Another Admin", "ADMIN-002", "+919876543211", "another@example.com"]
    )
    duplicate_result = run_interactive(
        factory,
        input_fn=lambda _prompt: next(duplicate_values),
        password_fn=lambda _prompt: "Another-Secret-002",
    )
    assert duplicate_result == 1
    assert db_session.scalars(select(User).where(User.role == UserRole.ADMIN)).all() == [admin]
