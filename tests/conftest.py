"""Shared MySQL-backed fixtures for authentication tests."""

import re
from collections.abc import Callable, Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

from app.auth.passwords import PasswordManager
from app.branches.models import Branch
from app.core.config import AppSettings
from app.db.session import get_db_session, get_engine
from app.main import create_app
from app.users.models import User, UserRole, UserStatus


@pytest.fixture
def app_settings() -> AppSettings:
    """Use valid but inexpensive Argon2id costs during automated tests."""
    return AppSettings(
        _env_file=None,
        app_env="test",
        session_cookie_name="ecr_test_session",
        csrf_cookie_name="ecr_test_csrf",
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
    )


@pytest.fixture
def db_connection() -> Generator[Connection, None, None]:
    connection = get_engine().connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def db_session(db_connection: Connection) -> Generator[Session, None, None]:
    session = Session(
        bind=db_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def password_manager(app_settings: AppSettings) -> PasswordManager:
    return PasswordManager(app_settings)


@pytest.fixture
def branch_factory(db_session: Session) -> Callable[..., Branch]:
    def get_or_create(
        *,
        code: str = "HO-KOL",
        name: str | None = None,
        is_active: bool = True,
    ) -> Branch:
        branch = db_session.scalar(select(Branch).where(Branch.code == code))
        if branch is None:
            branch = Branch(
                code=code,
                name=name or code.title(),
                normalized_name=(name or code.title()).casefold(),
                is_active=is_active,
            )
            db_session.add(branch)
            db_session.commit()
        return branch

    return get_or_create


@pytest.fixture
def user_factory(
    db_session: Session,
    password_manager: PasswordManager,
    branch_factory: Callable[..., Branch],
) -> Callable[..., User]:
    def create_user(
        *,
        role: UserRole = UserRole.SUPERVISOR,
        status: UserStatus = UserStatus.ACTIVE,
        must_change_password: bool = False,
        password: str = "Correct-Horse-123",
        employee_id: str | None = None,
        mobile_number: str | None = None,
        branch: Branch | None = None,
    ) -> User:
        suffix = uuid4().hex[:10].upper()
        user = User(
            full_name=f"Test User {suffix}",
            employee_id=employee_id or f"EMP-{suffix}",
            mobile_number=mobile_number or f"+91{int(uuid4().hex[:10], 16) % 10**10:010d}",
            email=f"{suffix.lower()}@example.invalid",
            branch_id=(branch or branch_factory()).id,
            password_hash=password_manager.hash(password),
            role=role,
            status=status,
            must_change_password=must_change_password,
        )
        db_session.add(user)
        db_session.commit()
        return user

    return create_user


@pytest.fixture
def client(
    db_session: Session,
    app_settings: AppSettings,
) -> Generator[TestClient, None, None]:
    app = create_app(app_settings)

    def override_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def csrf_from(response_text: str) -> str:
    """Extract the server-rendered CSRF hidden-field value."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


def login(
    client: TestClient,
    identifier: str,
    password: str = "Correct-Horse-123",
) -> str:
    """Submit the real login form and return its session cookie value."""
    page = client.get("/auth/login")
    csrf_token = csrf_from(page.text)
    response = client.post(
        "/auth/login",
        data={
            "csrf_token": csrf_token,
            "identifier": identifier,
            "password": password,
        },
    )
    assert response.status_code == 303
    session_cookie = client.cookies.get("ecr_test_session")
    assert session_cookie is not None
    return session_cookie
