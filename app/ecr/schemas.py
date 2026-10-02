"""Validation and normalization for Phase 3 ECR identity and Draft fields."""

import re
from datetime import date

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from app.ecr.series import COOLING_TOWER_SERIES


def normalize_serial_display(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Cooling Tower Serial No. is required.")
    return normalized


def normalized_serial_key(value: str) -> str:
    return normalize_serial_display(value).casefold()


def normalize_required_text(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} is required.")
    return normalized


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def normalize_tower_suffix(value: str | None) -> str | None:
    normalized = normalize_optional_text(value)
    if normalized is None:
        return None
    if not re.fullmatch(r"[A-Za-z]+", normalized):
        raise ValueError("Tower Suffix must contain alphabetic letters only.")
    return normalized.upper()


class SerialLookupInput(BaseModel):
    cooling_tower_serial_no: str = Field(min_length=1, max_length=100)

    @field_validator("cooling_tower_serial_no")
    @classmethod
    def validate_serial(cls, value: str) -> str:
        return normalize_serial_display(value)


class DraftCreateInput(BaseModel):
    cooling_tower_serial_no: str = Field(min_length=1, max_length=100)
    customer: str = Field(min_length=1, max_length=200)
    customer_order_no: str | None = Field(default=None, max_length=100)
    cooling_tower_series: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    place_of_installation: str = Field(min_length=1, max_length=250)
    multiple_towers: bool
    tower_suffix: str | None = Field(default=None, max_length=20)
    declared_no_of_cells: int | None = Field(default=None, gt=0)
    cell_no: int = Field(gt=0)
    erection_start_date: date | None = None
    erection_completion_date: date

    @field_validator("cooling_tower_serial_no")
    @classmethod
    def validate_serial(cls, value: str) -> str:
        return normalize_serial_display(value)

    @field_validator("customer")
    @classmethod
    def validate_customer(cls, value: str) -> str:
        return normalize_required_text(value, "Customer")

    @field_validator("cooling_tower_series")
    @classmethod
    def validate_series(cls, value: str, info: ValidationInfo) -> str:
        value = normalize_required_text(value, "Cooling Tower Series")
        # Only the server may authorize unchanged reuse of an existing legacy value.
        legacy = (info.context or {}).get("existing_series")
        if value not in COOLING_TOWER_SERIES and value != legacy:
            raise ValueError("Select an approved Cooling Tower Series.")
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        return normalize_required_text(value, "Model")

    @field_validator("place_of_installation")
    @classmethod
    def validate_place(cls, value: str) -> str:
        return normalize_required_text(value, "Place of Installation")

    @field_validator("customer_order_no", mode="before")
    @classmethod
    def validate_order_no(cls, value: object) -> object:
        return normalize_optional_text(value if isinstance(value, str) else None)

    @field_validator("tower_suffix", mode="before")
    @classmethod
    def validate_suffix(cls, value: object) -> str | None:
        return normalize_tower_suffix(value if isinstance(value, str) else None)

    @field_validator("declared_no_of_cells", "erection_start_date", mode="before")
    @classmethod
    def blank_optional_values_are_none(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_tower_structure(self) -> "DraftCreateInput":
        if self.multiple_towers and self.tower_suffix is None:
            raise ValueError("Tower Suffix is required for a multi-tower package.")
        if not self.multiple_towers and self.tower_suffix is not None:
            raise ValueError("Tower Suffix is not allowed for a single-tower package.")
        return self


class DraftAutosaveInput(BaseModel):
    erection_start_date: date | None = None
    erection_completion_date: date

    @field_validator("erection_start_date", mode="before")
    @classmethod
    def blank_start_date_is_none(cls, value: object) -> object:
        return None if value == "" else value
