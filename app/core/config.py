"""Environment-based application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    session_cookie_name: str = "ecr_session"
    session_ttl_days: int = Field(default=30, ge=1, le=365)
    session_secure_cookie: bool = False
    session_touch_interval_seconds: int = Field(default=300, ge=60, le=3600)
    csrf_cookie_name: str = "ecr_csrf"
    password_min_length: int = Field(default=12, ge=8, le=128)
    password_max_length: int = Field(default=128, ge=32, le=1024)
    argon2_time_cost: int = Field(default=3, ge=1, le=10)
    argon2_memory_cost_kib: int = Field(default=65536, ge=8192, le=1048576)
    argon2_parallelism: int = Field(default=4, ge=1, le=16)

    @model_validator(mode="after")
    def password_length_range_is_valid(self) -> "AppSettings":
        if self.password_min_length > self.password_max_length:
            raise ValueError("PASSWORD_MIN_LENGTH cannot exceed PASSWORD_MAX_LENGTH.")
        return self


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
