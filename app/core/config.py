"""Environment-based application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class AppSettings(BaseSettings):
    """Application settings that do not require external service credentials."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Paharpur ECR"
    app_env: Literal["development", "test", "production"] = "development"
    app_debug: bool = False
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)


class Settings(AppSettings):
    """Complete settings, including required MySQL connection values."""

    db_host: str
    db_port: int = Field(default=3306, ge=1, le=65535)
    db_name: str
    db_user: str
    db_password: SecretStr
    db_charset: Literal["utf8mb4"] = "utf8mb4"
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_recycle_seconds: int = Field(default=1800, ge=1)
    db_connect_timeout_seconds: int = Field(default=10, ge=1)
    db_echo: bool = False

    @property
    def database_url(self) -> URL:
        """Build a safely encoded SQLAlchemy URL from individual settings."""
        return URL.create(
            drivername="mysql+pymysql",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": self.db_charset},
        )

    @property
    def alembic_database_url(self) -> str:
        """Render the URL safely for Alembic's interpolating configuration."""
        rendered = self.database_url.render_as_string(hide_password=False)
        return rendered.replace("%", "%%")


@lru_cache
def get_app_settings() -> AppSettings:
    """Return process-wide application settings without requiring MySQL."""
    return AppSettings()


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings instance per process."""
    return Settings()  # type: ignore[call-arg]
