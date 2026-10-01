"""Validated branch administration inputs."""

from pydantic import BaseModel, Field, field_validator


def normalize_branch_code(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError("Branch Code is required.")
    return normalized


def normalize_branch_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("Branch Name is required.")
    return normalized


def normalized_branch_name_key(value: str) -> str:
    return normalize_branch_name(value).casefold()


class BranchCreateInput(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=100)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return normalize_branch_code(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return normalize_branch_name(value)
