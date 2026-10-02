"""Owner-approved Page 2 Batch A+B, partial Draft validation and persistence."""

import re
from typing import Annotated, Literal

from fastapi import Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.ecr.models import EcrPage2Technical, EcrReport
from app.ecr.page1 import Number, Text, display_value, draft_field
from app.ecr.series import active_fields

Count = Annotated[int, Field(gt=0, le=2**63 - 1)]  # BIGINT storage capacity only.
Measurement = Annotated[Number, Field(ge=0)]
YesNo = Literal["Yes", "No"]


class Page2DraftInput(BaseModel):
    eliminator_type: Text | None = draft_field(final_required=False)
    fc_valve_diameter: Measurement | None = draft_field(final_required=False)
    fc_valve_count: Count | None = draft_field(final_required=False)
    nozzle_type: Text | None = draft_field(final_required=False)
    nozzle_count_per_cell: Count | None = draft_field(final_required=False)
    nozzle_part_no: Text | None = draft_field(final_required=True)
    bearing_housing_type: Text | None = draft_field(final_required=True)
    bearing_housing_serial_no: Text | None = draft_field(final_required=True)
    belt_type: Text | None = draft_field(final_required=True)
    belt_section_length: Text | None = draft_field(final_required=False)
    small_pulley_od: Measurement | None = draft_field(final_required=False)
    large_pulley_od: Measurement | None = draft_field(final_required=False)
    belts_used_count: Count | None = draft_field(final_required=True)
    uniform_belt_tension: YesNo | None = draft_field(final_required=True)
    pulley_construction: Literal["With QD Bushing", "Without QD Bushing"] | None = (
        draft_field(final_required=False)
    )
    oil_type: Text | None = draft_field(final_required=True)
    oil_level_checked: YesNo | None = draft_field(final_required=True)
    oil_seal_leakage: YesNo | None = draft_field(final_required=True)
    general_tower_hardware: Literal["HDG", "SS304", "SS316"] | None = draft_field(
        final_required=True
    )

    @field_validator("*", mode="before")
    @classmethod
    def strip_blanks(cls, value):
        return (value.strip() or None) if isinstance(value, str) else value

    @field_validator(
        "fc_valve_count", "nozzle_count_per_cell", "belts_used_count", mode="before"
    )
    @classmethod
    def whole_count(cls, value):
        if (
            isinstance(value, str)
            and value.strip()
            and not re.fullmatch(r"[0-9]+", value.strip())
        ):
            raise ValueError("Counts must be positive whole numbers.")
        return value


SECTIONS = (
    ("Eliminator", (("eliminator_type", "Type (Specify nomenclature)", "text", ""),)),
    (
        "FC Valves",
        (
            ("fc_valve_diameter", "Diameter", "number", "Inches"),
            ("fc_valve_count", "No. of valves/cells", "integer", "Count"),
        ),
    ),
    (
        "Nozzles",
        (
            ("nozzle_type", "Type", "text", ""),
            ("nozzle_count_per_cell", "No. of nozzles/cell", "integer", "Count"),
            ("nozzle_part_no", "Part No.", "text", ""),
        ),
    ),
    (
        "Bearing Housing",
        (
            ("bearing_housing_type", "Type", "text", ""),
            ("bearing_housing_serial_no", "Sl. No.", "text", ""),
        ),
    ),
    (
        "Belt & Pulleys",
        (
            ("belt_type", "Belt Type", "text", ""),
            ("belt_section_length", "Belt Section & Length", "text", ""),
            ("small_pulley_od", "Small Pulley OD", "number", "Inches"),
            ("large_pulley_od", "Large Pulley OD", "number", "Inches"),
            ("belts_used_count", "No. of Belts Used", "integer", "Count"),
            ("uniform_belt_tension", "Uniform Belt Tension", "select", ("Yes", "No")),
            (
                "pulley_construction",
                "Pulley Construction",
                "select",
                ("With QD Bushing", "Without QD Bushing"),
            ),
        ),
    ),
    (
        "Lubricant for GRDR / Bearing Housing",
        (
            ("oil_type", "Type of Oil Used", "text", ""),
            ("oil_level_checked", "Check Oil Level", "select", ("Yes", "No")),
            (
                "oil_seal_leakage",
                "Leakage through Oil Seal, etc.",
                "select",
                ("Yes", "No"),
            ),
        ),
    ),
    (
        "General Tower Hardware",
        (
            (
                "general_tower_hardware",
                "General Tower Hardware",
                "select",
                ("HDG", "SS304", "SS316"),
            ),
        ),
    ),
)


def required_fields(series: str) -> frozenset[str]:
    return frozenset(
        name
        for name in active_fields(SECTIONS, series)
        if Page2DraftInput.model_fields[name].json_schema_extra["final_required"]
    )


def page2_values(report: EcrReport) -> dict:
    return {
        name: display_value(getattr(report.page2, name, None))
        for name in Page2DraftInput.model_fields
    }


async def page2_form_snapshot(request: Request) -> dict | None:
    form = await request.form()
    if form.get("page2_present") != "1":
        return None
    return {name: form.get(name, "") for name in Page2DraftInput.model_fields}


def save_page2(db: Session, report: EcrReport, data: Page2DraftInput) -> None:
    if report.page2 is None:
        report.page2 = EcrPage2Technical(report=report)
        db.add(report.page2)
    # Non-applicable data is preserved, never erased or changed by crafted fields.
    for name in active_fields(SECTIONS, report.tower.package.cooling_tower_series):
        setattr(report.page2, name, getattr(data, name))
    report.updated_at = utc_now()
