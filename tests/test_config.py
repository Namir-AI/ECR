"""Configuration tests."""

from pydantic import SecretStr

from app.core.config import AppSettings, Settings


def build_settings() -> Settings:
    return Settings(
        _env_file=None,
        db_host="127.0.0.1",
        db_name="ecr_test",
        db_user="ecr_test",
        db_password=SecretStr("not-a-real-password"),
    )


def test_database_url_uses_mysql_pymysql_and_utf8mb4() -> None:
    settings = build_settings()

    assert settings.database_url.drivername == "mysql+pymysql"
    assert settings.database_url.database == "ecr_test"
    assert settings.database_url.query == {"charset": "utf8mb4"}


def test_database_url_masks_password_when_rendered_for_display() -> None:
    rendered = build_settings().database_url.render_as_string(hide_password=True)

    assert "not-a-real-password" not in rendered
    assert "***" in rendered


def test_application_settings_do_not_require_database_credentials() -> None:
    settings = AppSettings(_env_file=None)

    assert settings.app_name == "Paharpur ECR"


def test_alembic_url_escapes_config_interpolation_characters() -> None:
    settings = Settings(
        _env_file=None,
        db_host="127.0.0.1",
        db_name="ecr_test",
        db_user="ecr_test",
        db_password=SecretStr("placeholder%value"),
    )

    assert "%%25" in settings.alembic_database_url
