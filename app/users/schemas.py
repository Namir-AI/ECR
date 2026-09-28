"""Validated browser-form data for users and authentication."""

import re

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


def normalize_full_name(value: str) -> str:
    """Collapse surrounding and repeated whitespace in a display name."""
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("Full Name is required.")
    return normalized


def normalize_employee_id(value: str) -> str:
    """Normalize employee identifiers for stable lookup and uniqueness."""
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError("Employee ID is required.")
    return normalized


def normalize_mobile_number(value: str) -> str:
    """Normalize common visual separators while preserving an optional plus."""
    normalized = re.sub(r"[\s()\-]", "", value)
    if not re.fullmatch(r"\+?[0-9]{7,15}", normalized):
        raise ValueError("Enter a valid mobile number with 7 to 15 digits.")
    return normalized


class SignupInput(BaseModel):
    """Supervisor self-registration input."""

    full_name: str = Field(min_length=1, max_length=150)
    employee_id: str = Field(min_length=1, max_length=50)
    mobile_number: str = Field(min_length=1, max_length=30)
    email: EmailStr | None = None
    password: str
    confirm_password: str

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        return normalize_full_name(value)

    @field_validator("employee_id")
    @classmethod
    def validate_employee_id(cls, value: str) -> str:
        return normalize_employee_id(value)

    @field_validator("mobile_number")
    @classmethod
    def validate_mobile(cls, value: str) -> str:
        return normalize_mobile_number(value)

    @field_validator("email", mode="before")
    @classmethod
    def empty_email_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr | None) -> str | None:
        return str(value).strip().lower() if value is not None else None

    @model_validator(mode="after")
    def passwords_match(self) -> "SignupInput":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class LoginInput(BaseModel):
    """Employee ID/mobile login input."""

    identifier: str = Field(min_length=1, max_length=50)
    password: str

    @field_validator("identifier")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Employee ID or Mobile Number is required.")
        return normalized


class PasswordChangeInput(BaseModel):
    """Authenticated password-change input."""

    current_password: str
    new_password: str
    confirm_password: str

    @model_validator(mode="after")
    def passwords_match(self) -> "PasswordChangeInput":
        if self.new_password != self.confirm_password:
            raise ValueError("New passwords do not match.")
        return self


class AdminPasswordResetInput(BaseModel):
    """Administrator-supplied temporary password input."""

    temporary_password: str
    confirm_password: str

    @model_validator(mode="after")
    def passwords_match(self) -> "AdminPasswordResetInput":
        if self.temporary_password != self.confirm_password:
            raise ValueError("Temporary passwords do not match.")
        return self
