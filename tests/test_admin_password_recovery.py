"""Server-side existing-Admin password recovery tests."""

from collections.abc import Iterator

from sqlalchemy.orm import Session, sessionmaker

from app.auth.models import UserSession
from app.auth.sessions import create_session
from app.users.models import PasswordResetRequest, UserRole
from app.users.reset_admin_password import run_interactive
from app.users.services import authenticate_user


def _session_factory(db_session: Session) -> sessionmaker[Session]:
    return sessionmaker(
        bind=db_session.connection(),
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


def _password_prompts(password: str) -> Iterator[str]:
    yield password
    yield password


def test_admin_self_recovery_replaces_password_revokes_sessions_and_clears_state(
    db_session: Session,
    user_factory,
    password_manager,
    app_settings,
    capsys,
) -> None:
    old_password = "Old-Admin-Password-001"
    new_password = "New-Admin-Password-002"
    admin = user_factory(
        role=UserRole.SUPERADMIN,
        employee_id="ADMIN-SELF-RECOVERY",
        password=old_password,
        must_change_password=True,
    )
    issued = create_session(db_session, admin, app_settings)
    reset_request = PasswordResetRequest(user_id=admin.id)
    db_session.add(reset_request)
    db_session.commit()

    passwords = _password_prompts(new_password)
    result = run_interactive(
        _session_factory(db_session),
        input_fn=lambda _prompt: admin.employee_id.lower(),
        password_fn=lambda _prompt: next(passwords),
        password_manager=password_manager,
    )
    output = capsys.readouterr().out
    db_session.expire_all()
    recovered_admin = db_session.get(type(admin), admin.id)
    stored_session = db_session.get(UserSession, issued.record.id)
    stored_request = db_session.get(PasswordResetRequest, reset_request.id)

    assert result == 0, output
    assert old_password not in output
    assert new_password not in output
    assert recovered_admin is not None
    assert recovered_admin.password_hash.startswith("$argon2id$")
    assert recovered_admin.password_hash != new_password
    assert recovered_admin.must_change_password is False
    assert authenticate_user(
        db_session,
        recovered_admin.employee_id,
        old_password,
        password_manager,
    ) is None
    assert authenticate_user(
        db_session,
        recovered_admin.employee_id,
        new_password,
        password_manager,
    ) == recovered_admin
    assert stored_session is not None and stored_session.revoked_at is not None
    assert stored_request is not None and stored_request.resolved_at is not None
    assert stored_request.resolved_by_user_id == recovered_admin.id


def test_admin_self_recovery_rejects_non_admin_without_exposing_password(
    db_session: Session,
    user_factory,
    password_manager,
    capsys,
) -> None:
    supervisor = user_factory(employee_id="EMP-NOT-ADMIN")
    original_hash = supervisor.password_hash
    proposed_password = "Must-Not-Be-Printed-003"
    passwords = _password_prompts(proposed_password)

    result = run_interactive(
        _session_factory(db_session),
        input_fn=lambda _prompt: supervisor.employee_id,
        password_fn=lambda _prompt: next(passwords),
        password_manager=password_manager,
    )
    output = capsys.readouterr().out
    db_session.refresh(supervisor)

    assert result == 1
    assert "not an administrator" in output
    assert proposed_password not in output
    assert supervisor.password_hash == original_hash


def test_admin_self_recovery_rejects_nonexistent_user_without_exposing_password(
    db_session: Session,
    password_manager,
    capsys,
) -> None:
    proposed_password = "Must-Remain-Secret-004"
    passwords = _password_prompts(proposed_password)

    result = run_interactive(
        _session_factory(db_session),
        input_fn=lambda _prompt: "ADMIN-DOES-NOT-EXIST",
        password_fn=lambda _prompt: next(passwords),
        password_manager=password_manager,
    )
    output = capsys.readouterr().out

    assert result == 1
    assert "No user exists" in output
    assert proposed_password not in output


def test_admin_self_recovery_supports_branch_admin(
    db_session: Session,
    user_factory,
    password_manager,
    capsys,
) -> None:
    admin = user_factory(
        role=UserRole.BRANCH_ADMIN,
        employee_id="BRANCH-ADMIN-RECOVERY",
        password="Old-Branch-Admin-001",
    )
    new_password = "New-Branch-Admin-002"
    passwords = _password_prompts(new_password)
    result = run_interactive(
        _session_factory(db_session),
        input_fn=lambda _prompt: admin.employee_id,
        password_fn=lambda _prompt: next(passwords),
        password_manager=password_manager,
    )
    output = capsys.readouterr().out
    db_session.refresh(admin)
    assert result == 0
    assert new_password not in output
    assert password_manager.verify(admin.password_hash, new_password)
